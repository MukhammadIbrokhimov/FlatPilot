"""Tests for flatpilot.matcher.dedup."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flatpilot.matcher.dedup import (
    assign_canonical,
    find_canonical,
    normalize_address,
    rebuild,
)


def _minimal_profile(*, telegram_enabled: bool = False):
    """Return a Profile that accepts any reasonably-priced 1+ room flat.

    Home coords are left unset so the distance filter is skipped — keeps
    these tests from needing a Nominatim mock.
    """
    from flatpilot.profile import Profile

    return Profile.model_validate(
        {
            "city": "Berlin",
            "radius_km": 50,
            "rent_min_warm": 0,
            "rent_max_warm": 2000,
            "rooms_min": 1,
            "rooms_max": 10,
            "household_size": 1,
            "kids": 0,
            "status": "student",
            "net_income_eur": 1500,
            "move_in_date": "2026-01-01",
            "notifications": {"telegram": {"enabled": telegram_enabled}},
        }
    )


def _insert(conn, **overrides) -> int:
    """Insert a minimal flat row and return its id. Tests override fields."""
    now = datetime.now(UTC).isoformat()
    row = {
        "external_id": "ext",
        "platform": "wg_gesucht",
        "listing_url": "https://example.com/1",
        "title": "test flat",
        "rent_warm_eur": 800.0,
        "size_sqm": 50.0,
        "address": "Greifswalder Str. 42",
        "scraped_at": now,
        "first_seen_at": now,
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{c}" for c in row)
    cursor = conn.execute(
        f"INSERT INTO flats ({cols}) VALUES ({placeholders})", row
    )
    return cursor.lastrowid


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Straße family → "str"
        ("Greifswalder Straße 42", "greifswalder str 42"),
        ("greifswalder strasse 42", "greifswalder str 42"),
        ("Greifswalder Str. 42", "greifswalder str 42"),
        ("  Greifswalder  Strasse, 42 ", "greifswalder str 42"),
        # Postcode + city prefix (Kleinanzeigen flavour)
        ("10435 Berlin, Greifswalder Str. 42", "greifswalder str 42"),
        ("Greifswalder Str. 42, Berlin", "greifswalder str 42"),
        # House-number suffix collapsed when written with a space
        ("Greifswalder Str. 42 a", "greifswalder str 42a"),
        ("Greifswalder Str. 42A", "greifswalder str 42a"),
        # Preserved distinction: "42" and "42a" must stay different
        ("Greifswalder Str. 42", "greifswalder str 42"),
        # Empty / whitespace / None → None
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_address(raw, expected):
    assert normalize_address(raw) == expected


def test_normalize_preserves_distinct_house_numbers():
    """42 vs 42a must produce different outputs — different buildings."""
    assert normalize_address("Greifswalder Str. 42") != normalize_address(
        "Greifswalder Str. 42a"
    )


def test_normalize_preserves_umlauts():
    """Non-Straße umlauts stay put."""
    assert normalize_address("Schöneberger Ufer 1") == "schöneberger ufer 1"


def _flat(conn, flat_id):
    row = conn.execute("SELECT * FROM flats WHERE id = ?", (flat_id,)).fetchone()
    return dict(row)


def test_find_canonical_returns_none_when_no_twin(tmp_db):
    a = _insert(tmp_db, external_id="a")
    assert find_canonical(tmp_db, _flat(tmp_db, a)) is None


def test_find_canonical_matches_cross_platform_twin(tmp_db):
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    assert find_canonical(tmp_db, _flat(tmp_db, b)) == a


def test_find_canonical_never_matches_same_platform(tmp_db):
    _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(
        tmp_db, external_id="b", platform="wg_gesucht", listing_url="u2"
    )
    assert find_canonical(tmp_db, _flat(tmp_db, b)) is None


@pytest.mark.parametrize("rent_delta, should_match", [
    (49.0, True),
    (50.0, True),
    (51.0, False),
    (-50.0, True),
    (-51.0, False),
])
def test_find_canonical_rent_boundary(tmp_db, rent_delta, should_match):
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht", rent_warm_eur=800.0)
    b = _insert(
        tmp_db,
        external_id="b",
        platform="kleinanzeigen",
        rent_warm_eur=800.0 + rent_delta,
    )
    result = find_canonical(tmp_db, _flat(tmp_db, b))
    assert (result == a) is should_match


@pytest.mark.parametrize("size_delta, should_match", [
    (2.9, True),
    (3.0, True),
    (3.1, False),
    (-3.0, True),
    (-3.1, False),
])
def test_find_canonical_size_boundary(tmp_db, size_delta, should_match):
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht", size_sqm=50.0)
    b = _insert(
        tmp_db,
        external_id="b",
        platform="kleinanzeigen",
        size_sqm=50.0 + size_delta,
    )
    result = find_canonical(tmp_db, _flat(tmp_db, b))
    assert (result == a) is should_match


def test_find_canonical_different_address_no_match(tmp_db):
    _insert(tmp_db, external_id="a", platform="wg_gesucht",
            address="Greifswalder Str. 42")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen",
                address="Kastanienallee 3")
    assert find_canonical(tmp_db, _flat(tmp_db, b)) is None


@pytest.mark.parametrize("missing_field", ["address", "rent_warm_eur", "size_sqm"])
def test_find_canonical_missing_field_returns_none(tmp_db, missing_field):
    _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(
        tmp_db, external_id="b", platform="kleinanzeigen",
        **{missing_field: None},
    )
    assert find_canonical(tmp_db, _flat(tmp_db, b)) is None


def test_find_canonical_chain_follow(tmp_db):
    """A -> B -> C: C matches B but not A. Canonical of C must be A."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht", rent_warm_eur=800.0)
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen",
                rent_warm_eur=840.0)
    # Link B to A to simulate a previous assign_canonical call.
    tmp_db.execute("UPDATE flats SET canonical_flat_id = ? WHERE id = ?", (a, b))
    # C matches B (rent delta 50) but not A (rent delta 100).
    c = _insert(tmp_db, external_id="c", platform="immoscout",
                rent_warm_eur=890.0)
    assert find_canonical(tmp_db, _flat(tmp_db, c)) == a


def test_find_canonical_only_looks_at_older_rows(tmp_db):
    """Oldest row (lowest id) must never get linked to a younger twin."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    assert find_canonical(tmp_db, _flat(tmp_db, a)) is None


def test_assign_canonical_links_twin(tmp_db):
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    assign_canonical(tmp_db, b)
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (b,)
    ).fetchone()
    assert row["canonical_flat_id"] == a


def test_assign_canonical_is_noop_when_no_twin(tmp_db):
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    assign_canonical(tmp_db, a)
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (a,)
    ).fetchone()
    assert row["canonical_flat_id"] is None


def test_assign_canonical_never_self_links(tmp_db):
    """The canonical row (oldest) must stay with canonical_flat_id = NULL."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    assign_canonical(tmp_db, a)
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (a,)
    ).fetchone()
    assert row["canonical_flat_id"] is None


def test_assign_canonical_noop_when_row_missing_rent(tmp_db):
    """Address present but rent missing → no-op at the wrapper layer."""
    _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(
        tmp_db,
        external_id="b",
        platform="kleinanzeigen",
        rent_warm_eur=None,
    )
    assign_canonical(tmp_db, b)
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (b,)
    ).fetchone()
    assert row["canonical_flat_id"] is None


def test_assign_canonical_noop_when_row_missing_size(tmp_db):
    """Address present but size missing → no-op at the wrapper layer."""
    _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(
        tmp_db,
        external_id="b",
        platform="kleinanzeigen",
        size_sqm=None,
    )
    assign_canonical(tmp_db, b)
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (b,)
    ).fetchone()
    assert row["canonical_flat_id"] is None


def test_assign_canonical_missing_row_noop(tmp_db):
    """Unknown ids must not raise and must not mutate the table."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    before = tmp_db.execute(
        "SELECT id, canonical_flat_id FROM flats ORDER BY id"
    ).fetchall()

    assign_canonical(tmp_db, 99999)

    after = tmp_db.execute(
        "SELECT id, canonical_flat_id FROM flats ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert before[0]["id"] == a


def test_insert_flat_populates_canonical_link(tmp_db):
    from flatpilot.pipeline import insert_flat

    now = datetime.now(UTC).isoformat()
    insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )
    rows = tmp_db.execute(
        "SELECT id, platform, canonical_flat_id FROM flats ORDER BY id"
    ).fetchall()
    assert rows[0]["canonical_flat_id"] is None
    assert rows[1]["canonical_flat_id"] == rows[0]["id"]


def test_insert_flat_without_twin_leaves_link_null(tmp_db):
    from flatpilot.pipeline import insert_flat

    now = datetime.now(UTC).isoformat()
    insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    row = tmp_db.execute("SELECT canonical_flat_id FROM flats").fetchone()
    assert row["canonical_flat_id"] is None


def test_rebuild_restores_tampered_link(tmp_db):
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    tmp_db.execute("UPDATE flats SET canonical_flat_id = ? WHERE id = ?", (a, b))

    tmp_db.execute("UPDATE flats SET canonical_flat_id = NULL")

    flats, clusters = rebuild(tmp_db)
    assert (flats, clusters) == (2, 1)

    link = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (b,)
    ).fetchone()["canonical_flat_id"]
    assert link == a


def test_rebuild_is_idempotent(tmp_db):
    _insert(tmp_db, external_id="a", platform="wg_gesucht")
    _insert(tmp_db, external_id="b", platform="kleinanzeigen")

    rebuild(tmp_db)
    snapshot_a = tmp_db.execute(
        "SELECT id, canonical_flat_id FROM flats ORDER BY id"
    ).fetchall()

    rebuild(tmp_db)
    snapshot_b = tmp_db.execute(
        "SELECT id, canonical_flat_id FROM flats ORDER BY id"
    ).fetchall()

    assert [tuple(r) for r in snapshot_a] == [tuple(r) for r in snapshot_b]


def test_rebuild_re_roots_after_deleted_canonical(tmp_db):
    """If the canonical row is deleted, rebuild picks the oldest survivor."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    c = _insert(
        tmp_db, external_id="c", platform="immoscout", listing_url="u3"
    )

    rebuild(tmp_db)
    tmp_db.execute("DELETE FROM flats WHERE id = ?", (a,))

    rebuild(tmp_db)
    # b is now the oldest survivor; c should link to b.
    rows = {
        r["id"]: r["canonical_flat_id"]
        for r in tmp_db.execute(
            "SELECT id, canonical_flat_id FROM flats ORDER BY id"
        ).fetchall()
    }
    assert rows[b] is None
    assert rows[c] == b


def test_three_platform_cluster_all_link_to_oldest(tmp_db):
    """All three platforms share one apartment → one canonical row."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    c = _insert(tmp_db, external_id="c", platform="immoscout")
    for flat_id in (b, c):
        assign_canonical(tmp_db, flat_id)
    rows = {
        r["id"]: r["canonical_flat_id"]
        for r in tmp_db.execute(
            "SELECT id, canonical_flat_id FROM flats ORDER BY id"
        ).fetchall()
    }
    assert rows[a] is None
    assert rows[b] == a
    assert rows[c] == a


def test_deleted_canonical_leaves_survivor_self_canonical(tmp_db):
    """ON DELETE SET NULL + ingest of a new twin after deletion."""
    a = _insert(tmp_db, external_id="a", platform="wg_gesucht")
    b = _insert(tmp_db, external_id="b", platform="kleinanzeigen")
    assign_canonical(tmp_db, b)
    tmp_db.execute("DELETE FROM flats WHERE id = ?", (a,))
    # B should now have canonical_flat_id = NULL (from SET NULL).
    row_b = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (b,)
    ).fetchone()
    assert row_b["canonical_flat_id"] is None

    # A new row C that matches B should link to B, not to the dead A.
    c = _insert(tmp_db, external_id="c", platform="immoscout")
    assign_canonical(tmp_db, c)
    row_c = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = ?", (c,)
    ).fetchone()
    assert row_c["canonical_flat_id"] == b


def test_matcher_writes_one_match_per_canonical(tmp_db, monkeypatch):
    """Twin flats on two platforms → one match row, keyed on the canonical root."""
    from flatpilot.matcher import runner
    from flatpilot.pipeline import insert_flat

    profile = _minimal_profile()
    monkeypatch.setattr(runner, "load_profile", lambda: profile)

    now = datetime.now(UTC).isoformat()
    insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )

    summary = runner.run_match()

    assert summary["processed"] == 1  # only the canonical root
    rows = tmp_db.execute(
        "SELECT m.flat_id, f.platform "
        "FROM matches m JOIN flats f ON f.id = m.flat_id"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["platform"] == "wg_gesucht"  # the older, canonical row


def test_notifier_dedups_by_canonical(tmp_db, monkeypatch):
    """Two match rows for the same canonical cluster → one dispatch call."""
    from flatpilot.notifications import dispatcher
    from flatpilot.pipeline import insert_flat

    now = datetime.now(UTC).isoformat()
    insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )

    profile = _minimal_profile(telegram_enabled=True)
    phash = "test-hash"

    # Simulate legacy state: two match rows, one per flat_id, both under the same profile hash.
    for flat_id in (1, 2):
        tmp_db.execute(
            "INSERT INTO matches (flat_id, profile_version_hash, decision, "
            "decision_reasons_json, decided_at) VALUES (?, ?, 'match', '[]', ?)",
            (flat_id, phash, now),
        )
    monkeypatch.setattr(dispatcher, "profile_hash", lambda _p: phash)

    calls: list[tuple[str, int]] = []

    def fake_send(channel, flat, _profile):
        calls.append((channel, flat["id"]))

    monkeypatch.setattr(dispatcher, "_send", fake_send)

    summary = dispatcher.dispatch_pending(profile)

    assert summary["sent"] == {"telegram": 1}
    assert len(calls) == 1
    assert calls[0] == ("telegram", 1)  # the canonical root wins


def test_three_platform_cluster_produces_one_match_and_one_notification(tmp_db, monkeypatch):
    """All three platforms → one match row, one notification per channel."""
    from flatpilot.matcher import runner
    from flatpilot.notifications import dispatcher
    from flatpilot.pipeline import insert_flat

    profile_obj = _minimal_profile(telegram_enabled=True)
    monkeypatch.setattr(runner, "load_profile", lambda: profile_obj)

    now = datetime.now(UTC).isoformat()
    for ext, plat, rent in (
        ("wg-1", "wg_gesucht", 800.0),
        ("ka-1", "kleinanzeigen", 810.0),
        ("is-1", "immoscout", 820.0),
    ):
        insert_flat(
            tmp_db,
            {
                "external_id": ext,
                "listing_url": f"https://example.com/{ext}",
                "title": "A",
                "rent_warm_eur": rent,
                "size_sqm": 50.0,
                "rooms": 2.0,
                "address": "Greifswalder Str. 42",
            },
            plat,
            now,
        )

    runner.run_match()
    assert tmp_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        dispatcher, "_send", lambda c, f, _p: calls.append((c, f["id"]))
    )
    summary = dispatcher.dispatch_pending(profile_obj)
    assert summary["sent"] == {"telegram": 1}
    assert calls == [("telegram", 1)]


def test_deleted_canonical_releases_survivor_for_fresh_matching(tmp_db, monkeypatch):
    """When the canonical is deleted, the surviving duplicate becomes a root
    and gets its own match — "one ping per *live* canonical" semantics."""
    from flatpilot.matcher import runner
    from flatpilot.pipeline import insert_flat

    profile = _minimal_profile()
    monkeypatch.setattr(runner, "load_profile", lambda: profile)

    now = datetime.now(UTC).isoformat()
    insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "rooms": 2.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "rooms": 2.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )

    # First pass: matcher writes one match for the root (flat id 1).
    runner.run_match()
    assert tmp_db.execute(
        "SELECT flat_id FROM matches"
    ).fetchone()["flat_id"] == 1

    # Delete the canonical — ON DELETE SET NULL releases the duplicate.
    tmp_db.execute("DELETE FROM flats WHERE id = 1")
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = 2"
    ).fetchone()
    assert row["canonical_flat_id"] is None

    # Second pass: the survivor is now a root and gets its own match.
    runner.run_match()
    rows = tmp_db.execute(
        "SELECT flat_id FROM matches ORDER BY flat_id"
    ).fetchall()
    assert [r["flat_id"] for r in rows] == [2]  # old row cascaded away
