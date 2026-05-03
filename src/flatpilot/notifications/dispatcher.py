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
import sqlite3
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


# Per-match channel resolution under semantic A″ (per-channel replace).
# See docs/superpowers/specs/2026-05-02-saved-searches-power-user-design.md §4.
#
# Output tuple shape: (channel, signature, transport_kwargs).
# - channel: "telegram" or "email"
# - signature: canonicalized string used as the dedup key in
#   notified_channels_json. Always "<channel>:base" when the resolved
#   transport equals base profile's transport for every field.
# - transport_kwargs: dict passed to the adapter's send(); keys are
#   override fields that differ from base. Empty dict when signature is
#   "<channel>:base" — signals "no override needed."

_TELEGRAM_FIELDS = ("bot_token_env", "chat_id")
_EMAIL_FIELDS = ("smtp_env",)


def _resolve_channel(
    *,
    channel: str,
    fields: tuple[str, ...],
    base_cfg: Any,
    overrides: list[Any],
) -> list[tuple[str, str, dict[str, str]]]:
    """Resolve one channel for one match row. Pure function.

    ``base_cfg`` is the corresponding ``profile.notifications.<channel>``
    block (e.g. ``profile.notifications.telegram``). ``overrides`` is the
    ordered list of non-None per-search override blocks for this channel
    (from definers in the matched-search list).

    Returns 0+ (channel, signature, transport_kwargs) tuples, deduped by
    canonicalized signature.
    """
    if not overrides:
        # No definers for this channel → base fires if enabled.
        if base_cfg.enabled:
            return [(channel, f"{channel}:base", {})]
        return []

    # Definers replace base. Filter to enabled=True overrides.
    enabled_overrides = [o for o in overrides if o.enabled]
    if not enabled_overrides:
        # All definers actively suppressed the channel.
        return []

    seen: dict[str, tuple[str, str, dict[str, str]]] = {}
    for override in enabled_overrides:
        kwargs: dict[str, str] = {}
        differs_from_base = False
        for field in fields:
            override_value = getattr(override, field)
            base_value = getattr(base_cfg, field)
            resolved = override_value if override_value is not None else base_value
            kwargs[field] = resolved
            if resolved != base_value:
                differs_from_base = True

        if not differs_from_base:
            signature = f"{channel}:base"
            transport_kwargs: dict[str, str] = {}
        else:
            signature = f"{channel}:" + ",".join(
                f"{field.split('_')[0]}={kwargs[field]}"
                for field in fields
                if kwargs[field] != getattr(base_cfg, field)
            )
            # transport_kwargs threads through ALL resolved values so the
            # adapter doesn't have to consult the profile for the fields
            # the dispatcher already resolved.
            transport_kwargs = dict(kwargs)

        seen[signature] = (channel, signature, transport_kwargs)

    return list(seen.values())


def _resolve_channels_for_match(
    profile: Profile,
    matched_names: list[str],
) -> list[tuple[str, str, dict[str, str]]]:
    """Top-level per-match resolver. See module-level docstring."""
    saved_by_name = {ss.name: ss for ss in profile.saved_searches}

    telegram_overrides = []
    email_overrides = []
    for name in matched_names:
        ss = saved_by_name.get(name)
        if ss is None:
            logger.debug("dispatch: matched-search name %r not in profile (stale row)", name)
            continue
        if ss.notifications is None:
            continue
        if ss.notifications.telegram is not None:
            telegram_overrides.append(ss.notifications.telegram)
        if ss.notifications.email is not None:
            email_overrides.append(ss.notifications.email)

    out: list[tuple[str, str, dict[str, str]]] = []
    out.extend(_resolve_channel(
        channel="telegram",
        fields=_TELEGRAM_FIELDS,
        base_cfg=profile.notifications.telegram,
        overrides=telegram_overrides,
    ))
    out.extend(_resolve_channel(
        channel="email",
        fields=_EMAIL_FIELDS,
        base_cfg=profile.notifications.email,
        overrides=email_overrides,
    ))
    return out


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


def _email_recipient(smtp_env: str | None = None) -> str | None:
    """``EMAIL_TO`` is a global override; otherwise resolve <prefix>_FROM."""
    prefix = smtp_env if smtp_env is not None else "SMTP"
    return os.environ.get("EMAIL_TO") or os.environ.get(f"{prefix}_FROM")


def _subject_for(flat: dict[str, Any]) -> str:
    title = flat.get("title") or "new listing"
    return f"FlatPilot: {title}"


def _send(channel: str, flat: dict[str, Any], profile: Profile) -> None:
    if channel == "telegram":
        telegram_adapter.send(profile, template.render_html(flat), parse_mode="HTML")
    elif channel == "email":
        # smtp_env threaded through in Task 6 once dispatcher resolves overrides per match.
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


def _mark_stale_matches_notified(conn: sqlite3.Connection, current_hash: str) -> None:
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
    # once per run; this inheritance is in-memory only, so if a
    # canonical's match row is later deleted (e.g. the root flat
    # is delisted, cascading ON DELETE), a surviving sibling becomes
    # the new live canonical and fires at its next dispatch — the
    # "one ping per live canonical" semantic from the k40y plan.
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
