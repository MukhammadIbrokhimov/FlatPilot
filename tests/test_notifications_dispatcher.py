"""Unit coverage for notifications.dispatcher."""
from __future__ import annotations

from datetime import UTC, datetime

import flatpilot.notifications.dispatcher as disp
from flatpilot.profile import Profile, profile_hash


def _seed_flat(conn, *, external_id="e1"):
    # canonical_flat_id is intentionally not inserted (defaults to NULL)
    # so dispatch_pending's COALESCE(canonical_flat_id, id) falls back to
    # the flat's own id and the dedup-by-canonical branch doesn't fire.
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
        VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, ?, 0)
        """,
        (external_id, now, now),
    )
    return cur.lastrowid


def _seed_match(conn, *, flat_id, profile_hash, decision="match"):
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO matches
            (flat_id, profile_version_hash, decision, decision_reasons_json,
             decided_at)
        VALUES (?, ?, ?, '[]', ?)
        """,
        (flat_id, profile_hash, decision, now),
    )


def test_dispatch_pending_skips_stale_hash_rows(tmp_db, monkeypatch):
    profile = Profile.load_example()
    current = profile_hash(profile)
    stale = "deadbeef" * 4  # any string != current

    flat_a = _seed_flat(tmp_db, external_id="a")
    flat_b = _seed_flat(tmp_db, external_id="b")
    _seed_match(tmp_db, flat_id=flat_a, profile_hash=current)
    _seed_match(tmp_db, flat_id=flat_b, profile_hash=stale)

    sends: list[tuple[str, int]] = []

    def fake_send(channel, flat, profile):
        sends.append((channel, flat["id"]))

    monkeypatch.setattr(disp, "_send", fake_send)
    monkeypatch.setattr(disp, "enabled_channels", lambda _p: ["telegram"])

    disp.dispatch_pending(profile)
    assert sends == [("telegram", flat_a)]


def test_mark_stale_flips_notified_at_without_send(tmp_db, monkeypatch):
    """Stale-hash rows get notified_at stamped via _mark_stale_matches_notified.

    The mark-stale step only runs when channels are enabled (dispatcher.py:121
    early-returns on empty channels), so the test enables telegram but
    points _send at a no-op. Stale rows should be silently stamped without
    invoking _send; current-hash rows should not be stamped because the no-
    op _send doesn't add anything to ``notified``.
    """
    profile = Profile.load_example()
    current = profile_hash(profile)
    stale = "00" * 8

    flat_a = _seed_flat(tmp_db, external_id="a")
    flat_b = _seed_flat(tmp_db, external_id="b")
    _seed_match(tmp_db, flat_id=flat_a, profile_hash=current)
    _seed_match(tmp_db, flat_id=flat_b, profile_hash=stale)

    sent: list[str] = []

    def fake_send(channel, flat, profile):
        sent.append(channel)

    monkeypatch.setattr(disp, "_send", fake_send)
    monkeypatch.setattr(disp, "enabled_channels", lambda _p: ["telegram"])

    disp.dispatch_pending(profile)

    rows = {
        r["flat_id"]: r["notified_at"]
        for r in tmp_db.execute(
            "SELECT flat_id, notified_at FROM matches"
        ).fetchall()
    }
    # Stale row was marked silently.
    assert rows[flat_b] is not None and rows[flat_b]
    # Current-hash row was processed and stamped (because _send succeeded).
    assert rows[flat_a] is not None and rows[flat_a]
    # Only the current-hash row was actually sent on.
    assert sent == ["telegram"]


def _notifications(*, telegram: bool, email: bool):
    """Build a Notifications model with the given enabled flags.

    Real schema (profile.py:39-60): Notifications has nested
    TelegramNotification(enabled=...) and EmailNotification(enabled=...)
    children. Passing a flat ``{"telegram": bool}`` dict would fail
    pydantic validation — use the proper child models.
    """
    from flatpilot.profile import (
        EmailNotification,
        Notifications,
        TelegramNotification,
    )
    return Notifications(
        telegram=TelegramNotification(enabled=telegram, chat_id="x"),
        email=EmailNotification(enabled=email),
    )


def test_enabled_channels_empty_when_neither_configured():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=False, email=False)}
    )
    assert disp.enabled_channels(profile) == []


def test_enabled_channels_positional_order_both_on():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=True, email=True)}
    )
    # Order is positional (telegram first, email second per dispatcher.py:49-55),
    # not alphabetically sorted.
    assert disp.enabled_channels(profile) == ["telegram", "email"]


def test_enabled_channels_telegram_only():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=True, email=False)}
    )
    assert disp.enabled_channels(profile) == ["telegram"]


def test_enabled_channels_email_only():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=False, email=True)}
    )
    assert disp.enabled_channels(profile) == ["email"]


def test_send_test_invokes_each_enabled_channel_once(monkeypatch):
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=True, email=True)}
    )

    calls: list[str] = []

    def fake_send(channel, flat, profile):
        calls.append(channel)

    monkeypatch.setattr(disp, "_send", fake_send)

    result = disp.send_test(profile)
    assert sorted(calls) == ["email", "telegram"]
    assert isinstance(result, dict)
    assert set(result.keys()) >= {"telegram", "email"}
    # On success each entry is "sent" (dispatcher.py:213).
    assert all(v == "sent" for v in result.values())


def test_send_test_returns_empty_when_no_channels_enabled():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=False, email=False)}
    )
    assert disp.send_test(profile) == {}
