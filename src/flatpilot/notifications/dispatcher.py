"""Channel dispatcher with per-flat, per-channel dedup.

``dispatch_pending`` walks every matched flat and sends it on the user's
enabled channels that haven't already been notified for that match.
Successful deliveries append the channel to ``matches.notified_channels_json``
and refresh ``notified_at``; failures only log and the match stays in the
pending set so the next run retries.

``send_test`` is the ``flatpilot notify --test`` path — it renders a
hardcoded synthetic flat and pings every enabled channel without touching
the DB, so the user can verify creds before live notifications start
arriving.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, TypedDict

from flatpilot.database import get_conn, init_db
from flatpilot.notifications import email as email_adapter
from flatpilot.notifications import telegram as telegram_adapter
from flatpilot.notifications import template
from flatpilot.profile import Profile, profile_hash

logger = logging.getLogger(__name__)


_SYNTHETIC_FLAT: dict[str, Any] = {
    "title": "[FlatPilot test] Sunny 2.5-room flat, Kreuzberg",
    "rent_warm_eur": 1200,
    "rooms": 2.5,
    "district": "Kreuzberg",
    "online_since": "2026-04-20",
    "listing_url": "https://example.com/flatpilot-test",
}


class DispatchSummary(TypedDict):
    processed: int
    sent: dict[str, int]
    failed: dict[str, int]


def enabled_channels(profile: Profile) -> list[str]:
    channels: list[str] = []
    if profile.notifications.telegram.enabled:
        channels.append("telegram")
    if profile.notifications.email.enabled:
        channels.append("email")
    return channels


def _parse_channels(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (TypeError, json.JSONDecodeError):
        return set()


def _email_recipient() -> str | None:
    return os.environ.get("EMAIL_TO") or os.environ.get("SMTP_FROM")


def _subject_for(flat: dict[str, Any]) -> str:
    title = flat.get("title") or "new listing"
    return f"FlatPilot: {title}"


def _send(channel: str, flat: dict[str, Any], profile: Profile) -> None:
    if channel == "telegram":
        telegram_adapter.send(profile, template.render_html(flat), parse_mode="HTML")
    elif channel == "email":
        recipient = _email_recipient()
        if not recipient:
            raise email_adapter.EmailError("no recipient — set EMAIL_TO or SMTP_FROM")
        email_adapter.send(
            recipient,
            _subject_for(flat),
            template.render_plain(flat),
            template.render_html(flat),
        )
    else:
        raise ValueError(f"unknown channel: {channel!r}")


def _mark_stale_matches_notified(conn, current_hash: str) -> None:
    """Suppress pending matches whose profile hash is no longer current.

    Stamps notified_at (without touching notified_channels_json) so these
    rows stay in the DB as historical data but never fire dispatches
    again. Idempotent — a second call is a no-op because the WHERE clause
    excludes rows with a non-null notified_at.
    """
    now = datetime.now(UTC).isoformat()
    cursor = conn.execute(
        """
        UPDATE matches
           SET notified_at = ?
         WHERE decision = 'match'
           AND notified_at IS NULL
           AND profile_version_hash != ?
        """,
        (now, current_hash),
    )
    if cursor.rowcount:
        logger.info(
            "dispatch: suppressed %d pending match(es) from stale profile hash(es)",
            cursor.rowcount,
        )


def dispatch_pending(profile: Profile) -> DispatchSummary:
    channels = enabled_channels(profile)
    if not channels:
        logger.info("no channels enabled in profile; nothing to send")
        return {"processed": 0, "sent": {}, "failed": {}}

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)

    # Scope to matches evaluated under the *current* profile — otherwise
    # a profile change (e.g. lowering rent_max_warm) would still notify
    # matches that were valid under the previous profile's rules but
    # never got a notified_at stamp. See FlatPilot-usm for the scenario.
    _mark_stale_matches_notified(conn, phash)

    rows = conn.execute(
        """
        SELECT m.id AS match_id,
               m.notified_channels_json,
               COALESCE(f.canonical_flat_id, f.id) AS canonical_id,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        WHERE m.decision = 'match' AND m.profile_version_hash = ?
        ORDER BY canonical_id, f.id
        """,
        (phash,),
    ).fetchall()

    # Dedup across sibling match rows that reference the same canonical
    # cluster. Earned by two scenarios the matcher's root-only filter
    # can't cover: legacy match rows written before PR #21, and any
    # row churn from a future `dedup --rebuild` that re-clusters already
    # matched flats. For each (canonical_id, channel), we send at most
    # once; later siblings inherit the notified stamp without firing.
    sent_canonicals: dict[int, set[str]] = {}
    sent: dict[str, int] = {}
    failed: dict[str, int] = {}
    processed = 0

    for row in rows:
        flat = dict(row)
        match_id = flat.pop("match_id")
        canonical_id = flat.pop("canonical_id")
        notified = _parse_channels(flat.pop("notified_channels_json", None))
        already_for_canonical = sent_canonicals.setdefault(canonical_id, set())
        effective = notified | already_for_canonical
        pending = [c for c in channels if c not in effective]
        if not pending:
            continue

        processed += 1
        for channel in pending:
            try:
                _send(channel, flat, profile)
            except (telegram_adapter.TelegramError, email_adapter.EmailError) as exc:
                logger.warning("match %d channel %s failed: %s", match_id, channel, exc)
                failed[channel] = failed.get(channel, 0) + 1
                continue
            notified.add(channel)
            already_for_canonical.add(channel)
            sent[channel] = sent.get(channel, 0) + 1

        if notified:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE matches SET notified_channels_json = ?, notified_at = ? WHERE id = ?",
                (json.dumps(sorted(notified)), now, match_id),
            )

    logger.info(
        "dispatch: processed=%d sent=%s failed=%s", processed, sent, failed
    )
    return {"processed": processed, "sent": sent, "failed": failed}


def send_test(profile: Profile) -> dict[str, str]:
    channels = enabled_channels(profile)
    if not channels:
        return {}

    results: dict[str, str] = {}
    for channel in channels:
        try:
            _send(channel, dict(_SYNTHETIC_FLAT), profile)
        except (telegram_adapter.TelegramError, email_adapter.EmailError, ValueError) as exc:
            results[channel] = f"failed: {exc}"
            logger.warning("test send on %s failed: %s", channel, exc)
        else:
            results[channel] = "sent"
            logger.info("test send on %s delivered", channel)
    return results
