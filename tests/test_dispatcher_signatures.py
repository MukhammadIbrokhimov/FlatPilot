"""Signature-based dedup, canonicalization, backwards-compat parse.

Higher-level than test_dispatcher_resolve_channels.py: exercises
dispatch_pending against a real DB (tmp_db fixture) so the writeback
format and read-side parse are covered end-to-end.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import flatpilot.notifications.dispatcher as disp
from flatpilot.profile import (
    EmailNotification,
    Notifications,
    Profile,
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotification,
    TelegramNotificationOverride,
    profile_hash,
)


def _profile_with(*, telegram=False, email=False, saved_searches=()) -> Profile:
    base = Profile.load_example()
    return base.model_copy(
        update={
            "notifications": Notifications(
                telegram=TelegramNotification(
                    enabled=telegram, bot_token_env="TELEGRAM_BOT_TOKEN", chat_id="base_chat",
                ),
                email=EmailNotification(enabled=email, smtp_env="SMTP"),
            ),
            "saved_searches": list(saved_searches),
        }
    )


def _seed_flat_match(conn, *, profile_hash_value, matched_names="[]", external_id="e1"):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
           VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, ?, 0)""",
        (external_id, now, now),
    )
    flat_id = cur.lastrowid
    conn.execute(
        """INSERT INTO matches
            (flat_id, profile_version_hash, decision, decision_reasons_json,
             decided_at, matched_saved_searches_json)
           VALUES (?, ?, 'match', '[]', ?, ?)""",
        (flat_id, profile_hash_value, now, matched_names),
    )
    return flat_id


def test_dispatch_no_matched_searches_writes_signature_format(tmp_db, monkeypatch):
    """Even legacy-style matches now persist new-format signatures."""
    profile = _profile_with(telegram=True, email=True)
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(tmp_db, profile_hash_value=phash)

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    row = tmp_db.execute(
        "SELECT notified_channels_json FROM matches WHERE flat_id=?",
        (flat_id,),
    ).fetchone()
    notified = sorted(json.loads(row["notified_channels_json"]))
    assert notified == ["email:base", "telegram:base"]


def test_dispatch_legacy_bare_names_dedup(tmp_db, monkeypatch):
    """An existing row with ['telegram'] is treated as already-fired for telegram:base."""
    profile = _profile_with(telegram=True, email=True)
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(tmp_db, profile_hash_value=phash)
    # Pre-set notified_channels_json to legacy format with telegram only.
    tmp_db.execute(
        "UPDATE matches SET notified_channels_json='[\"telegram\"]' WHERE flat_id=?",
        (flat_id,),
    )

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    # Only email should fire — telegram is already in legacy notified set.
    sent_channels = [s[0] for s in sends]
    assert sent_channels == ["email"]

    # Writeback upgraded the row to signature format.
    row = tmp_db.execute(
        "SELECT notified_channels_json FROM matches WHERE flat_id=?",
        (flat_id,),
    ).fetchone()
    notified = sorted(json.loads(row["notified_channels_json"]))
    assert notified == ["email:base", "telegram:base"]


def test_dispatch_definer_replaces_base_telegram(tmp_db, monkeypatch):
    """telegram override + email base = override telegram fires, base email fires."""
    ss = SavedSearch(
        name="kreuzberg-2br",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="k_chat"),
        ),
    )
    profile = _profile_with(telegram=True, email=True, saved_searches=[ss])
    phash = profile_hash(profile)
    _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='["kreuzberg-2br"]',
    )

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    by_channel = {s[0]: s[1] for s in sends}
    assert "telegram" in by_channel
    assert by_channel["telegram"].get("chat_id") == "k_chat"
    assert "email" in by_channel
    assert by_channel["email"] == {}


def test_dispatch_explicit_suppress_via_enabled_false(tmp_db, monkeypatch):
    ss = SavedSearch(
        name="quiet",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
        ),
    )
    profile = _profile_with(telegram=True, email=True, saved_searches=[ss])
    phash = profile_hash(profile)
    _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='["quiet"]',
    )

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    sent_channels = [s[0] for s in sends]
    assert "telegram" not in sent_channels
    assert "email" in sent_channels


def test_canonical_dedup_uses_signature(tmp_db, monkeypatch):
    """Two flats in same canonical cluster, one with override, one without:
    each fires once because their signatures differ."""
    ss = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="A"),
        ),
    )
    profile = _profile_with(telegram=True, email=False, saved_searches=[ss])
    phash = profile_hash(profile)

    flat1 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='["s1"]', external_id="e1",
    )
    flat2 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='[]', external_id="e2",
    )
    # Both flats share canonical_id = flat1 (flat2 points at flat1 as canonical)
    tmp_db.execute("UPDATE flats SET canonical_flat_id=? WHERE id=?", (flat1, flat2))

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    # Two distinct telegram signatures → both fire.
    telegram_sends = [s for s in sends if s[0] == "telegram"]
    assert len(telegram_sends) == 2
    # The override-driven send threads chat_id through transport_kwargs.
    # The base-only send passes empty kwargs (telegram adapter reads
    # chat_id off the profile). Resolved chat_ids: "A" and base "base_chat".
    chat_ids = sorted(
        s[1].get("chat_id", profile.notifications.telegram.chat_id)
        for s in telegram_sends
    )
    assert chat_ids == ["A", "base_chat"]


def test_canonical_dedup_collapses_identical_signatures(tmp_db, monkeypatch):
    """Two flats in same canonical cluster, both no override → telegram:base fires once."""
    profile = _profile_with(telegram=True, email=False)
    phash = profile_hash(profile)

    flat1 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names="[]", external_id="e1",
    )
    flat2 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names="[]", external_id="e2",
    )
    tmp_db.execute("UPDATE flats SET canonical_flat_id=? WHERE id=?", (flat1, flat2))

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    telegram_sends = [s for s in sends if s[0] == "telegram"]
    assert len(telegram_sends) == 1


def test_empty_array_notified_channels_parses_clean(tmp_db, monkeypatch):
    """Empty/NULL notified_channels_json parses to empty set; pending dispatch fires normally."""
    profile = _profile_with(telegram=True)
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(tmp_db, profile_hash_value=phash)
    # Schema-level default for notified_channels_json is NULL on insert; an
    # explicit '[]' would also parse cleanly. _parse_signatures handles both.
    tmp_db.execute(
        "UPDATE matches SET notified_channels_json='[]' WHERE flat_id=?",
        (flat_id,),
    )
    row = tmp_db.execute(
        "SELECT notified_channels_json FROM matches WHERE flat_id=?", (flat_id,),
    ).fetchone()
    assert row["notified_channels_json"] == "[]"

    sends: list = []
    monkeypatch.setattr(disp, "_send", lambda *a, **kw: sends.append(a[0]))
    disp.dispatch_pending(profile)
    assert sends == ["telegram"]
