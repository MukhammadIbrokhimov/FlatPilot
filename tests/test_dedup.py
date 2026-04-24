"""Tests for flatpilot.matcher.dedup."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from flatpilot.matcher.dedup import assign_canonical, find_canonical, normalize_address


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
    from flatpilot.cli import _insert_flat

    now = datetime.now(UTC).isoformat()
    _insert_flat(
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
    _insert_flat(
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
    from flatpilot.cli import _insert_flat

    now = datetime.now(UTC).isoformat()
    _insert_flat(
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
