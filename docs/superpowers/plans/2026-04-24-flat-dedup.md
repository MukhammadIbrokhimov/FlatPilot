# Cross-Platform Flat Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `flats.canonical_flat_id` so the downstream bead (FlatPilot-k40y) can collapse duplicate notifications when the same apartment is posted on more than one platform.

**Architecture:** A new pure-Python module `flatpilot/matcher/dedup.py` exposes three functions (`normalize_address`, `find_canonical`, `assign_canonical`). Scrape ingest calls `assign_canonical` after each successful INSERT. A `flatpilot dedup --rebuild` CLI command re-clusters existing rows. No changes to the matcher or notifier in this bead.

**Tech Stack:** Python 3.11+, SQLite (WAL mode), pytest, typer, ruff. The `flats` schema already declares `canonical_flat_id INTEGER REFERENCES flats(id) ON DELETE SET NULL` — no migration needed.

**Spec:** `docs/superpowers/specs/2026-04-24-flat-dedup-design.md`

**Bead:** FlatPilot-0ktm

---

## File Structure

- **Create** `src/flatpilot/matcher/dedup.py` — the three dedup functions.
- **Create** `tests/conftest.py` — shared pytest fixture for a tempfile SQLite DB that doesn't touch `~/.flatpilot`.
- **Create** `tests/test_dedup.py` — unit + integration tests for all dedup behavior.
- **Modify** `src/flatpilot/cli.py` — hook `assign_canonical` into `_insert_flat`; add a `dedup` typer command.

Files stay small on purpose: `dedup.py` is the matcher-adjacent logic; the CLI entry point lives with the rest of the CLI; tests are in one file because all the coverage is tightly related.

---

## Task 1: Pytest fixture for an isolated SQLite DB

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/test_conftest_smoke.py`

The fixture must point `DB_PATH` at a temp file *before* `init_db` runs and clear the thread-local connection cache between tests. Without this, tests would silently read/write `~/.flatpilot/flatpilot.db`.

- [ ] **Step 1: Create the shared fixture**

Write `tests/conftest.py`:

```python
"""Shared pytest fixtures for FlatPilot tests.

The project stores user state under ``~/.flatpilot`` in production; tests
must never touch that directory. ``tmp_db`` redirects ``DB_PATH`` in
both ``flatpilot.config`` and ``flatpilot.database`` (each holds its own
reference) and clears the thread-local connection cache so each test
starts from a clean, isolated database.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flatpilot import config, database

    db_path = tmp_path / "flatpilot.db"

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.close_conn()
    database.init_db()
    conn = database.get_conn()
    try:
        yield conn
    finally:
        database.close_conn()
```

- [ ] **Step 2: Write a smoke test that exercises the fixture**

Write `tests/test_conftest_smoke.py`:

```python
"""Sanity-check that the tmp_db fixture creates an isolated DB."""

from __future__ import annotations


def test_tmp_db_has_flats_table(tmp_db):
    row = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='flats'"
    ).fetchone()
    assert row is not None


def test_tmp_db_is_fresh_per_test_a(tmp_db):
    tmp_db.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at) VALUES ('x', 'wg_gesucht', 'u', 't', 'now', 'now')"
    )
    assert tmp_db.execute("SELECT COUNT(*) FROM flats").fetchone()[0] == 1


def test_tmp_db_is_fresh_per_test_b(tmp_db):
    assert tmp_db.execute("SELECT COUNT(*) FROM flats").fetchone()[0] == 0
```

- [ ] **Step 3: Run the smoke tests**

Run: `pytest tests/test_conftest_smoke.py -v`

Expected: 3 passed. If test_b fails with `COUNT = 1`, the fixture isn't actually isolating between tests and must be fixed before continuing.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_conftest_smoke.py
git commit -m "FlatPilot-0ktm: add tmp_db pytest fixture with isolated SQLite path"
```

---

## Task 2: `normalize_address`

**Files:**
- Create: `src/flatpilot/matcher/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dedup.py`:

```python
"""Tests for flatpilot.matcher.dedup."""

from __future__ import annotations

import pytest

from flatpilot.matcher.dedup import normalize_address


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
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `pytest tests/test_dedup.py -v`

Expected: ModuleNotFoundError or ImportError on `flatpilot.matcher.dedup`.

- [ ] **Step 3: Write the implementation**

Create `src/flatpilot/matcher/dedup.py`:

```python
"""Cross-platform flat deduplication.

Populates ``flats.canonical_flat_id`` using a deterministic fuzzy key over
``(normalized_address, rent_warm_eur, size_sqm)``. See the design spec at
``docs/superpowers/specs/2026-04-24-flat-dedup-design.md`` for the full
rule set and the rationale behind each clause.
"""

from __future__ import annotations

import re
import sqlite3

# Matches the Straße family as a whole word. The trailing ``\.?`` catches
# the abbreviated ``Str.`` and ``str.`` forms; ``straße`` and ``strasse``
# match without the dot.
_STRASSE_RE = re.compile(r"\b(?:straße|strasse|str\.?)\b")

# Collapse "42 a" / "42 A" into "42a" so both spellings of the same
# house number cluster. Only applies to a single trailing letter —
# keeps "42 Berlin" unchanged.
_HOUSE_NUMBER_SPACE_RE = re.compile(r"(\d+)\s+([a-z])\b")

# A 5-digit postcode surrounded by word boundaries.
_POSTCODE_RE = re.compile(r"\b\d{5}\b")

# ", berlin" tail or "berlin, " prefix. Other cities are out of scope
# for this bead — FlatPilot is Berlin-first.
_BERLIN_SUFFIX_RE = re.compile(r",\s*berlin\b")
_BERLIN_PREFIX_RE = re.compile(r"\bberlin\s*,\s*")


def normalize_address(raw: str | None) -> str | None:
    """Return the canonical form of a German rental-listing address.

    Returns ``None`` for ``None`` / empty / whitespace-only input. See
    the design spec for the rule set and examples.
    """
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None

    s = _POSTCODE_RE.sub("", s)
    s = _BERLIN_PREFIX_RE.sub("", s)
    s = _BERLIN_SUFFIX_RE.sub("", s)
    s = _STRASSE_RE.sub("str", s)
    s = s.replace(".", "").replace(",", "")
    s = _HOUSE_NUMBER_SPACE_RE.sub(r"\1\2", s)
    s = re.sub(r"\s+", " ", s).strip()

    return s or None
```

Also create the empty `src/flatpilot/matcher/dedup.py` neighbours if needed (there isn't a missing `__init__.py` — the matcher package already has one).

- [ ] **Step 4: Run the tests and see them pass**

Run: `pytest tests/test_dedup.py -v`

Expected: all `test_normalize_address[...]` parametrizations pass, plus the two standalone tests.

- [ ] **Step 5: Lint**

Run: `ruff check src/flatpilot/matcher/dedup.py tests/test_dedup.py tests/conftest.py`

Expected: `All checks passed!`. Fix any warning before committing.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/matcher/dedup.py tests/test_dedup.py
git commit -m "FlatPilot-0ktm: add normalize_address with Berlin rental rules"
```

---

## Task 3: `find_canonical`

**Files:**
- Modify: `src/flatpilot/matcher/dedup.py`
- Modify: `tests/test_dedup.py`

- [ ] **Step 1: Add a helper to insert a flat row in tests**

Append to the top of `tests/test_dedup.py`, below the existing imports:

```python
from datetime import UTC, datetime


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
```

- [ ] **Step 2: Write the failing tests for `find_canonical`**

Append to `tests/test_dedup.py`:

```python
from flatpilot.matcher.dedup import find_canonical


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
```

- [ ] **Step 3: Run the tests and see them fail**

Run: `pytest tests/test_dedup.py -v`

Expected: the `find_canonical` tests all fail with ImportError.

- [ ] **Step 4: Implement `find_canonical`**

Append to `src/flatpilot/matcher/dedup.py`:

```python
def find_canonical(conn: sqlite3.Connection, flat: dict) -> int | None:
    """Return the canonical flat id this row should link to, or ``None``.

    Returns ``None`` when the row cannot be safely deduped (missing
    address/rent/size) or when no existing older row matches on the
    fuzzy key.
    """
    normalized = normalize_address(flat.get("address"))
    rent = flat.get("rent_warm_eur")
    size = flat.get("size_sqm")
    if normalized is None or rent is None or size is None:
        return None

    rows = conn.execute(
        """
        SELECT id, canonical_flat_id, address
          FROM flats
         WHERE id < :self_id
           AND platform != :platform
           AND rent_warm_eur IS NOT NULL
           AND size_sqm IS NOT NULL
           AND address IS NOT NULL
           AND ABS(rent_warm_eur - :rent) <= 50
           AND ABS(size_sqm - :size)      <= 3
         ORDER BY id ASC
        """,
        {
            "self_id": flat["id"],
            "platform": flat["platform"],
            "rent": rent,
            "size": size,
        },
    ).fetchall()

    for row in rows:
        if normalize_address(row["address"]) == normalized:
            return row["canonical_flat_id"] or row["id"]
    return None
```

- [ ] **Step 5: Run the tests and see them pass**

Run: `pytest tests/test_dedup.py -v`

Expected: all tests pass.

- [ ] **Step 6: Lint**

Run: `ruff check src/flatpilot/matcher/dedup.py tests/test_dedup.py`

Expected: `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add src/flatpilot/matcher/dedup.py tests/test_dedup.py
git commit -m "FlatPilot-0ktm: add find_canonical with fuzzy key + chain follow"
```

---

## Task 4: `assign_canonical`

**Files:**
- Modify: `src/flatpilot/matcher/dedup.py`
- Modify: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dedup.py`:

```python
from flatpilot.matcher.dedup import assign_canonical


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


def test_assign_canonical_missing_row_noop(tmp_db):
    assign_canonical(tmp_db, 99999)  # should not raise
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `pytest tests/test_dedup.py -k assign_canonical -v`

Expected: ImportError on `assign_canonical`.

- [ ] **Step 3: Implement `assign_canonical`**

Append to `src/flatpilot/matcher/dedup.py`:

```python
def assign_canonical(conn: sqlite3.Connection, flat_id: int) -> None:
    """Look up a twin for the row with ``flat_id`` and stamp the link.

    Safe to call on any flat id — a missing row, a row that cannot be
    deduped (missing fields), or the oldest member of a new cluster
    all result in a no-op.
    """
    row = conn.execute("SELECT * FROM flats WHERE id = ?", (flat_id,)).fetchone()
    if row is None:
        return
    canonical = find_canonical(conn, dict(row))
    if canonical is None or canonical == flat_id:
        return
    conn.execute(
        "UPDATE flats SET canonical_flat_id = ? WHERE id = ?",
        (canonical, flat_id),
    )
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `pytest tests/test_dedup.py -v`

Expected: all tests pass.

- [ ] **Step 5: Lint**

Run: `ruff check src/flatpilot/matcher/dedup.py tests/test_dedup.py`

Expected: `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/matcher/dedup.py tests/test_dedup.py
git commit -m "FlatPilot-0ktm: add assign_canonical and wire find_canonical"
```

---

## Task 5: Hook `assign_canonical` into `_insert_flat`

**Files:**
- Modify: `src/flatpilot/cli.py:334-346`
- Modify: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_dedup.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `pytest tests/test_dedup.py::test_insert_flat_populates_canonical_link -v`

Expected: FAIL — `rows[1]["canonical_flat_id"]` is `None` because the hook doesn't exist yet.

- [ ] **Step 3: Modify `_insert_flat`**

Open `src/flatpilot/cli.py`. Find `_insert_flat` (currently near line 334). Replace the body:

```python
def _insert_flat(conn, flat, platform: str, now: str) -> bool:
    row = dict(flat)
    row["platform"] = platform
    row["scraped_at"] = now
    row["first_seen_at"] = now
    cols = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = (
        f"INSERT OR IGNORE INTO flats ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    cursor = conn.execute(sql, row)
    if cursor.rowcount == 0:
        return False
    from flatpilot.matcher.dedup import assign_canonical

    assign_canonical(conn, cursor.lastrowid)
    return True
```

The deferred import keeps `dedup` out of the module-load path for commands that don't scrape.

- [ ] **Step 4: Run the tests and see them pass**

Run: `pytest tests/test_dedup.py -v`

Expected: all tests pass.

- [ ] **Step 5: Lint**

Run: `ruff check src/flatpilot/cli.py tests/test_dedup.py`

Expected: `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/cli.py tests/test_dedup.py
git commit -m "FlatPilot-0ktm: stamp canonical_flat_id on each new scrape insert"
```

---

## Task 6: `flatpilot dedup --rebuild` CLI command

**Files:**
- Modify: `src/flatpilot/matcher/dedup.py`
- Modify: `src/flatpilot/cli.py`
- Modify: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing tests for `rebuild`**

Append to `tests/test_dedup.py`:

```python
from flatpilot.matcher.dedup import rebuild


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
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `pytest tests/test_dedup.py -k rebuild -v`

Expected: ImportError on `rebuild`.

- [ ] **Step 3: Implement `rebuild`**

Append to `src/flatpilot/matcher/dedup.py`:

```python
def rebuild(conn: sqlite3.Connection) -> tuple[int, int]:
    """Re-compute ``canonical_flat_id`` across every row in ``flats``.

    Returns ``(total_flats, total_clusters)`` where ``total_clusters`` is
    the number of distinct canonical rows (each cluster of twins is one
    cluster; singleton flats each count as their own cluster).
    """
    conn.execute("UPDATE flats SET canonical_flat_id = NULL")
    ids = [r["id"] for r in conn.execute("SELECT id FROM flats ORDER BY id ASC")]
    for flat_id in ids:
        assign_canonical(conn, flat_id)
    total = len(ids)
    clusters = conn.execute(
        "SELECT COUNT(*) FROM flats WHERE canonical_flat_id IS NULL"
    ).fetchone()[0]
    return total, clusters
```

- [ ] **Step 4: Run the dedup tests**

Run: `pytest tests/test_dedup.py -v`

Expected: all tests pass.

- [ ] **Step 5: Wire the CLI command**

Open `src/flatpilot/cli.py`. Find the `@app.command()` block for `match` (around line 349) and add a new command just before it:

```python
@app.command()
def dedup(
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Recompute canonical_flat_id for every flat."
    ),
) -> None:
    """Populate flats.canonical_flat_id across the database."""
    from rich.console import Console

    from flatpilot.database import get_conn, init_db
    from flatpilot.matcher.dedup import rebuild as do_rebuild

    console = Console()
    if not rebuild:
        console.print("[yellow]Nothing to do. Pass --rebuild to re-cluster.[/yellow]")
        raise typer.Exit(code=0)

    init_db()
    conn = get_conn()
    total, clusters = do_rebuild(conn)
    console.print(f"rebuilt [bold]{total}[/bold] flats → [bold]{clusters}[/bold] clusters")
```

The local name `rebuild` shadowing the imported `rebuild` is why we alias the import to `do_rebuild`.

- [ ] **Step 6: Verify the command wires up**

Run: `python -m flatpilot dedup --help`

Expected: output shows the `--rebuild` flag with the help text. Exit code 0.

Run: `python -m flatpilot dedup` (no flag)

Expected: "Nothing to do. Pass --rebuild to re-cluster." Exit code 0.

- [ ] **Step 7: Lint**

Run: `ruff check src/flatpilot/matcher/dedup.py src/flatpilot/cli.py tests/test_dedup.py`

Expected: `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add src/flatpilot/matcher/dedup.py src/flatpilot/cli.py tests/test_dedup.py
git commit -m "FlatPilot-0ktm: add flatpilot dedup --rebuild CLI command"
```

---

## Task 7: Edge-case integration tests (3-platform cluster, deleted canonical)

**Files:**
- Modify: `tests/test_dedup.py`

- [ ] **Step 1: Write the remaining spec-mandated tests**

Append to `tests/test_dedup.py`:

```python
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
```

- [ ] **Step 2: Run the full test file**

Run: `pytest tests/test_dedup.py -v`

Expected: every test passes. Total should be in the mid-30s, dominated by parametrizations.

- [ ] **Step 3: Lint**

Run: `ruff check tests/test_dedup.py`

Expected: `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_dedup.py
git commit -m "FlatPilot-0ktm: cover three-platform + deleted-canonical integration paths"
```

---

## Task 8: Close the bead and open the PR

- [ ] **Step 1: Run the full test suite one more time**

Run: `pytest`

Expected: all tests pass. If the smoke file is still present, that's fine — it's cheap.

- [ ] **Step 2: Run the full linter**

Run: `ruff check`

Expected: `All checks passed!`.

- [ ] **Step 3: Confirm the branch has the right commits**

Run: `git log --oneline origin/main..HEAD`

Expected: the spec commit, the spec-review amendment commit, and one commit per task above (Tasks 1–7). Every commit message starts with `FlatPilot-0ktm:`.

- [ ] **Step 4: Push the branch**

Run: `git push -u origin feat/i-bis-1-dedup`

- [ ] **Step 5: Open the PR**

Run:

```bash
gh pr create --base main --head feat/i-bis-1-dedup --title "FlatPilot-0ktm: cross-platform flat dedup (address normalization + fuzzy match)" --body "$(cat <<'EOF'
## Summary

Populates `flats.canonical_flat_id` so the same apartment cross-posted on WG-Gesucht and Kleinanzeigen is linked to a single canonical row. Closes FlatPilot-0ktm; unblocks FlatPilot-k40y (matcher + notifier keyed on canonical).

- New `flatpilot.matcher.dedup` module: `normalize_address`, `find_canonical`, `assign_canonical`, `rebuild`.
- Scrape ingest (`_insert_flat`) stamps `canonical_flat_id` after each new row.
- New `flatpilot dedup --rebuild` CLI command re-clusters existing rows.
- `tests/conftest.py` fixture isolates tests from `~/.flatpilot`.

Design spec: `docs/superpowers/specs/2026-04-24-flat-dedup-design.md`

Out of scope (FlatPilot-k40y): changing `matcher/runner.py` or `notifications/dispatcher.py` to read the canonical link.

## Test plan

- [x] `pytest` — full suite green
- [x] `ruff check` — clean
- [ ] Manual smoke: `flatpilot dedup --rebuild` on a real `~/.flatpilot/flatpilot.db` with live WG-Gesucht data
EOF
)"
```

- [ ] **Step 6: Close the bead**

Run: `bd close FlatPilot-0ktm --reason="Merged in PR <url>"`

(Use the PR URL printed by `gh pr create`.)

- [ ] **Step 7: Sync beads**

Run: `bd dolt push` (skip if no dolt remote is configured — see CLAUDE.md note).

---

## Self-Review Notes

**Spec coverage:**
- normalize_address rules 1–7 → Tasks 2 (primary) covers all seven; examples table fully tested.
- `find_canonical` SQL (including `id < :self_id`) → Task 3.
- `assign_canonical` semantics → Task 4.
- `_insert_flat` hook → Task 5.
- `flatpilot dedup --rebuild` → Task 6.
- Test groups 1–9 from spec → distributed across Tasks 2, 3, 4, 5, 6, 7.

**Type consistency:** The `flat` argument to `find_canonical` is always a dict with the row columns as keys. Tests use `_flat()` helper that does `dict(row)`. `assign_canonical` does the same conversion internally. The tuple returned by `rebuild` is `(total: int, clusters: int)` everywhere it's used.

**Risks flagged by the review:** deleted canonical (Task 7), three-platform cluster (Task 7), rebuild idempotency (Task 6), ingest hook integration (Task 5).
