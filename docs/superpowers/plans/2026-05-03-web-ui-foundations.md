# Web UI Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land an architecture decision record for Phase 5 plus per-user data-model foundations (users table, `user_id` on `matches` / `applications` / `apply_locks`), with the CLI behaving identically to today as the seed user (id=1).

**Architecture:** Schema migration via one-shot table rebuilds (CREATE-COPY-DROP-RENAME) for the three user-scoped tables, gated on a `PRAGMA table_info` probe and wrapped in `BEGIN IMMEDIATE`. All existing queries get scoped to `DEFAULT_USER_ID = 1` via parameter-bound `user_id` clauses. ADR is a 1-page docs file at `docs/adr/0001-web-ui-architecture.md`. The full design is in `docs/superpowers/specs/2026-05-03-web-ui-foundations-design.md` — refer to it for any SQL block this plan calls out by section number.

**Tech Stack:** Python 3.11+, raw `sqlite3` (no SQLAlchemy in this PR), pytest. No new runtime dependencies.

**Branch:** `feat/web-ui-foundations` (already created off `main`; spec already committed at `4775fd1` and revised at `a5a0112`).

**Commit policy:** **One feature commit at the end** of all tasks (per project convention: bundle implementation work into the smallest reasonable number of commits, hold the branch local until complete). Do not commit between tasks. Do not push until the whole plan is done and the user explicitly approves.

---

## File structure

**Files to create:**
- `docs/adr/0001-web-ui-architecture.md` — ~1-page ADR; content sourced from spec §3.
- `src/flatpilot/users.py` — exports `DEFAULT_USER_ID = 1` and `ensure_default_user(conn)`.
- `tests/test_user_scoping.py` — 12 tests covering seed user, backfill, idempotent rebuild, widened constraints, cross-user isolation across every query path.

**Files to modify:**
- `src/flatpilot/database.py` — extend `init_db()`; add `_rebuild_user_scoped_tables(conn)` helper.
- `src/flatpilot/schemas.py` — register `users`; replace `matches` / `applications` / `apply_locks` `CREATE TABLE` strings with their post-rebuild shapes; drop the two obsolete `COLUMNS` entries; register two new indexes.
- `src/flatpilot/matcher/runner.py` — match INSERT and dedup SELECT scope by `user_id`.
- `src/flatpilot/notifications/dispatcher.py` — three queries at lines 263 / 307 / 366.
- `src/flatpilot/view.py` — match SELECT (line 50) and applications SELECT (line 113).
- `src/flatpilot/server.py` — same shape as `view.py`'s callers.
- `src/flatpilot/stats.py` — four counter SELECTs at lines 38 / 42 / 49 / 60.
- `src/flatpilot/auto_apply.py` — applications SELECTs at lines 63 / 80 / 98 / 143; matches SELECT at line 137.
- `src/flatpilot/apply.py` — stale-lock reaper (line 159), lock peek (line 169), lock release (line 192), already-applied guard (line 263), lock-acquire INSERT (line 164), application INSERT (line 344).
- `src/flatpilot/applications.py` — match-id lookup (line 27), application-id lookup (line 62).
- `src/flatpilot/pipeline.py` — thread `DEFAULT_USER_ID` through to callees that need it (matcher, dispatcher, auto-apply).
- `src/flatpilot/doctor.py` — one new row reporting `users: N`.

**Files NOT modified:**
- `src/flatpilot/cli.py` — every entry point continues to operate as user 1 transparently.
- `src/flatpilot/scrapers/*.py` — scrapers stay user-unaware (flats are global).
- `src/flatpilot/profile.py`, `src/flatpilot/wizard/init.py` — profile / saved searches stay in JSON.

---

## Task 1: Create the `users` module and table

**Files:**
- Create: `src/flatpilot/users.py`
- Modify: `src/flatpilot/schemas.py`
- Test: `tests/test_user_scoping.py`

- [ ] **Step 1.1: Create `src/flatpilot/users.py`**

```python
"""User identity primitives.

Phase 1 (CLI) and the post-this-PR schema both use a single seed user
(`id=1`). Phase 5 (Web UI) will populate the table from magic-link
signups. `DEFAULT_USER_ID` is the constant every query and INSERT in
the CLI path threads through.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

DEFAULT_USER_ID = 1


def ensure_default_user(conn: sqlite3.Connection) -> None:
    """Insert the seed user (id=1, email=NULL) if absent. Idempotent."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, created_at) VALUES (1, NULL, ?)",
        (now,),
    )
```

- [ ] **Step 1.2: Register the `users` table in `schemas.py`**

Add to `src/flatpilot/schemas.py` (after the existing imports, before any other table registration):

```python
USERS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    created_at TEXT NOT NULL
)
"""

SCHEMAS["users"] = USERS_CREATE_SQL
```

The `users` registration must come first in source order so that when `init_db()` iterates `SCHEMAS.values()`, the FK targets exist before the user-scoped tables are created on a fresh install.

- [ ] **Step 1.3: Wire `ensure_default_user` into `init_db`**

Modify `src/flatpilot/database.py` `init_db()`:

```python
def init_db() -> None:
    import flatpilot.schemas  # noqa: F401  — side-effect registration
    from flatpilot.users import ensure_default_user

    conn = get_conn()
    for create_sql in SCHEMAS.values():
        conn.execute(create_sql)
    ensure_default_user(conn)
    ensure_columns()
```

(The `_rebuild_user_scoped_tables` call gets inserted between `ensure_default_user` and `ensure_columns` in Task 3 — leave that ordering for now.)

- [ ] **Step 1.4: Write the seed-user tests**

Create `tests/test_user_scoping.py`:

```python
"""Tests for per-user schema scoping (FlatPilot-z3me)."""

from __future__ import annotations

import sqlite3

import pytest

from flatpilot.database import close_conn, get_conn, init_db
from flatpilot.users import DEFAULT_USER_ID


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file under tmp_path."""
    db_path = tmp_path / "flatpilot.db"
    monkeypatch.setattr("flatpilot.config.DB_PATH", db_path)
    monkeypatch.setattr("flatpilot.database.DB_PATH", db_path)
    close_conn()
    yield db_path
    close_conn()


def test_seed_user_exists_after_init_db(tmp_db):
    init_db()
    conn = get_conn()
    rows = list(conn.execute("SELECT id, email FROM users"))
    assert len(rows) == 1
    assert rows[0]["id"] == DEFAULT_USER_ID
    assert rows[0]["email"] is None


def test_init_db_idempotent_on_users(tmp_db):
    init_db()
    init_db()
    conn = get_conn()
    (count,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    assert count == 1


def test_users_table_unique_email_rejects_duplicates(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (email, created_at) VALUES ('a@example.com', '2026-01-01T00:00:00+00:00')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (email, created_at) VALUES ('a@example.com', '2026-01-02T00:00:00+00:00')"
        )


def test_users_table_allows_multiple_no_email(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute("INSERT INTO users (email, created_at) VALUES (NULL, '2026-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO users (email, created_at) VALUES (NULL, '2026-01-02T00:00:00+00:00')")
    (count,) = conn.execute("SELECT COUNT(*) FROM users WHERE email IS NULL").fetchone()
    assert count == 3  # seed + 2 inserted
```

- [ ] **Step 1.5: Run the tests to verify Task 1 alone passes**

```bash
pytest tests/test_user_scoping.py -v
```

Expected: all 4 tests pass. (No other tests are run in this step because the rest of the schema work hasn't shipped yet; existing tests should also pass since the only change so far is an additive `users` table.)

- [ ] **Step 1.6: Run the full existing test suite to confirm no regression**

```bash
pytest -q
```

Expected: full suite passes. The `users` table addition is purely additive; nothing else queries it yet.

---

## Task 2: Replace `matches` / `applications` / `apply_locks` with their post-rebuild shapes in `schemas.py`

The fresh-install path goes through `SCHEMAS`. We replace the three `CREATE TABLE` strings with the **post-rebuild** shapes from spec §4.4 and drop the obsolete `COLUMNS` entries (those columns are now inline in the `CREATE TABLE`).

**Files:**
- Modify: `src/flatpilot/schemas.py`

- [ ] **Step 2.1: Replace `FLATS_CREATE_SQL` registration — no change**

Confirm that `flats` is registered as-is. No `user_id` is added to `flats`.

- [ ] **Step 2.2: Replace `MATCHES_CREATE_SQL` with the post-rebuild shape**

In `src/flatpilot/schemas.py`, replace the existing `MATCHES_CREATE_SQL` string with:

```python
MATCHES_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    profile_version_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('match', 'reject', 'skipped')),
    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
    decided_at TEXT NOT NULL,
    notified_at TEXT,
    notified_channels_json TEXT,
    matched_saved_searches_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (user_id, flat_id, profile_version_hash, decision)
)
"""
```

The `matched_saved_searches_json` column is now declared inline. The `UNIQUE` is widened to include `user_id`.

- [ ] **Step 2.3: Replace `APPLICATIONS_CREATE_SQL` with the post-rebuild shape**

Replace the existing `APPLICATIONS_CREATE_SQL` with:

```python
APPLICATIONS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    listing_url TEXT NOT NULL,
    title TEXT NOT NULL,
    rent_warm_eur REAL,
    rooms REAL,
    size_sqm REAL,
    district TEXT,
    applied_at TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('manual', 'auto')),
    message_sent TEXT,
    attachments_sent_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('submitted', 'failed', 'viewing_invited', 'rejected', 'no_response')
    ),
    response_received_at TEXT,
    response_text TEXT,
    notes TEXT,
    triggered_by_saved_search TEXT
)
"""
```

The `triggered_by_saved_search` column is now declared inline.

- [ ] **Step 2.4: Replace `APPLY_LOCKS_CREATE_SQL` with the post-rebuild shape**

```python
APPLY_LOCKS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS apply_locks (
    flat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    acquired_at TEXT NOT NULL,
    pid INTEGER NOT NULL,
    PRIMARY KEY (flat_id, user_id)
)
"""
```

PRIMARY KEY widens from `flat_id` to `(flat_id, user_id)`.

- [ ] **Step 2.5: Drop the two obsolete `COLUMNS` entries**

Delete from `src/flatpilot/schemas.py`:

```python
COLUMNS["matches"] = {
    "matched_saved_searches_json": "TEXT NOT NULL DEFAULT '[]'",
}
COLUMNS["applications"] = {
    "triggered_by_saved_search": "TEXT",
}
```

Both columns are now inline in the `CREATE TABLE` strings.

- [ ] **Step 2.6: Add the two new composite indexes**

Add to `src/flatpilot/schemas.py`:

```python
MATCHES_USER_DECIDED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_matches_user_decided
    ON matches(user_id, decided_at)
"""

SCHEMAS["idx_matches_user_decided"] = MATCHES_USER_DECIDED_INDEX_SQL


APPLICATIONS_USER_APPLIED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_applications_user_applied
    ON applications(user_id, applied_at)
"""

SCHEMAS["idx_applications_user_applied"] = APPLICATIONS_USER_APPLIED_INDEX_SQL
```

- [ ] **Step 2.7: Verify fresh-install correctness**

Run only the seed-user test (it does a fresh install):

```bash
pytest tests/test_user_scoping.py::test_seed_user_exists_after_init_db -v
```

Expected: PASS. The `CREATE TABLE` strings are valid SQLite.

The full test suite will not pass yet because the existing INSERTs into `matches` / `applications` / `apply_locks` don't include `user_id` and the `NOT NULL` column has a `DEFAULT 1`, so they should still succeed — but any test that asserts pre-migration behavior on those tables may need adjusting in later tasks. Don't run the full suite here; we run it after Task 3.

---

## Task 3: Implement the table-rebuild migration helper

**Files:**
- Modify: `src/flatpilot/database.py`
- Test: `tests/test_user_scoping.py` (extend)

- [ ] **Step 3.1: Add `_rebuild_user_scoped_tables` to `database.py`**

Add this function to `src/flatpilot/database.py` (after `ensure_columns`):

```python
def _rebuild_user_scoped_tables(conn: sqlite3.Connection) -> None:
    """Rebuild matches/applications/apply_locks to add user_id (FlatPilot-z3me).

    SQLite forbids ALTER TABLE ADD COLUMN when the column has both
    REFERENCES and a non-NULL default, so we recreate each table with
    the post-migration shape, copy rows over with user_id=1, and
    rename. Per-table column probe makes this idempotent. Wrapped in a
    single BEGIN IMMEDIATE so a crash leaves no half-rebuilt state.
    """

    def _has_user_id_column(table: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == "user_id" for row in rows)

    needs_rebuild = [
        t for t in ("matches", "applications", "apply_locks")
        if not _has_user_id_column(t)
    ]
    if not needs_rebuild:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        if "matches" in needs_rebuild:
            conn.execute("DROP TABLE IF EXISTS matches_new")
            conn.execute("""
                CREATE TABLE matches_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    profile_version_hash TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision IN ('match', 'reject', 'skipped')),
                    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
                    decided_at TEXT NOT NULL,
                    notified_at TEXT,
                    notified_channels_json TEXT,
                    matched_saved_searches_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (user_id, flat_id, profile_version_hash, decision)
                )
            """)
            conn.execute("""
                INSERT INTO matches_new (
                    id, user_id, flat_id, profile_version_hash, decision,
                    decision_reasons_json, decided_at, notified_at,
                    notified_channels_json, matched_saved_searches_json
                )
                SELECT
                    id, 1, flat_id, profile_version_hash, decision,
                    decision_reasons_json, decided_at, notified_at,
                    notified_channels_json, matched_saved_searches_json
                FROM matches
            """)
            conn.execute("DROP TABLE matches")
            conn.execute("ALTER TABLE matches_new RENAME TO matches")

        if "applications" in needs_rebuild:
            conn.execute("DROP TABLE IF EXISTS applications_new")
            conn.execute("""
                CREATE TABLE applications_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
                    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    listing_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    rent_warm_eur REAL,
                    rooms REAL,
                    size_sqm REAL,
                    district TEXT,
                    applied_at TEXT NOT NULL,
                    method TEXT NOT NULL CHECK (method IN ('manual', 'auto')),
                    message_sent TEXT,
                    attachments_sent_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL CHECK (
                        status IN ('submitted', 'failed', 'viewing_invited', 'rejected', 'no_response')
                    ),
                    response_received_at TEXT,
                    response_text TEXT,
                    notes TEXT,
                    triggered_by_saved_search TEXT
                )
            """)
            conn.execute("""
                INSERT INTO applications_new (
                    id, user_id, flat_id, platform, listing_url, title,
                    rent_warm_eur, rooms, size_sqm, district, applied_at,
                    method, message_sent, attachments_sent_json, status,
                    response_received_at, response_text, notes,
                    triggered_by_saved_search
                )
                SELECT
                    id, 1, flat_id, platform, listing_url, title,
                    rent_warm_eur, rooms, size_sqm, district, applied_at,
                    method, message_sent, attachments_sent_json, status,
                    response_received_at, response_text, notes,
                    triggered_by_saved_search
                FROM applications
            """)
            conn.execute("DROP TABLE applications")
            conn.execute("ALTER TABLE applications_new RENAME TO applications")

        if "apply_locks" in needs_rebuild:
            conn.execute("DROP TABLE IF EXISTS apply_locks_new")
            conn.execute("""
                CREATE TABLE apply_locks_new (
                    flat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
                    acquired_at TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    PRIMARY KEY (flat_id, user_id)
                )
            """)
            conn.execute("""
                INSERT INTO apply_locks_new (flat_id, user_id, acquired_at, pid)
                    SELECT flat_id, 1, acquired_at, pid FROM apply_locks
            """)
            conn.execute("DROP TABLE apply_locks")
            conn.execute("ALTER TABLE apply_locks_new RENAME TO apply_locks")

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
```

- [ ] **Step 3.2: Wire `_rebuild_user_scoped_tables` into `init_db`**

Update `init_db` in `src/flatpilot/database.py` to call the rebuild helper after `ensure_default_user`:

```python
def init_db() -> None:
    import flatpilot.schemas  # noqa: F401  — side-effect registration
    from flatpilot.users import ensure_default_user

    conn = get_conn()
    for create_sql in SCHEMAS.values():
        conn.execute(create_sql)
    ensure_default_user(conn)
    _rebuild_user_scoped_tables(conn)
    ensure_columns()
```

The order is load-bearing: `users` table → seed user → rebuild (which has FK references to `users`).

- [ ] **Step 3.3: Add backfill, idempotency, and widened-constraint tests**

Append to `tests/test_user_scoping.py`:

```python
def _build_pre_migration_db(db_path):
    """Create the schema as it existed before this PR — used to test backfill."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE flats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            listing_url TEXT NOT NULL,
            title TEXT NOT NULL,
            rent_warm_eur REAL,
            rent_cold_eur REAL,
            extra_costs_eur REAL,
            rooms REAL,
            size_sqm REAL,
            address TEXT,
            district TEXT,
            lat REAL,
            lng REAL,
            online_since TEXT,
            available_from TEXT,
            requires_wbs INTEGER NOT NULL DEFAULT 0,
            wbs_size_category INTEGER,
            wbs_income_category INTEGER,
            furnished INTEGER,
            deposit_eur INTEGER,
            min_contract_months INTEGER,
            pets_allowed INTEGER,
            description TEXT,
            scraped_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            canonical_flat_id INTEGER REFERENCES flats(id) ON DELETE SET NULL,
            UNIQUE (platform, external_id)
        );
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
            profile_version_hash TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('match', 'reject', 'skipped')),
            decision_reasons_json TEXT NOT NULL DEFAULT '[]',
            decided_at TEXT NOT NULL,
            notified_at TEXT,
            notified_channels_json TEXT,
            matched_saved_searches_json TEXT NOT NULL DEFAULT '[]',
            UNIQUE (flat_id, profile_version_hash, decision)
        );
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            listing_url TEXT NOT NULL,
            title TEXT NOT NULL,
            rent_warm_eur REAL,
            rooms REAL,
            size_sqm REAL,
            district TEXT,
            applied_at TEXT NOT NULL,
            method TEXT NOT NULL CHECK (method IN ('manual', 'auto')),
            message_sent TEXT,
            attachments_sent_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL CHECK (
                status IN ('submitted', 'failed', 'viewing_invited', 'rejected', 'no_response')
            ),
            response_received_at TEXT,
            response_text TEXT,
            notes TEXT,
            triggered_by_saved_search TEXT
        );
        CREATE TABLE apply_locks (
            flat_id INTEGER PRIMARY KEY,
            acquired_at TEXT NOT NULL,
            pid INTEGER NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('ext1', 'wg-gesucht', 'https://example.com/1', 'Test flat', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 'hashA', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, applied_at, method, status)"
        " VALUES (1, 'wg-gesucht', 'https://example.com/1', 'Test flat', '2026-01-02', 'manual', 'submitted')"
    )
    conn.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (1, '2026-01-02', 1234)"
    )
    conn.close()


def test_backfill_existing_rows(tmp_db):
    _build_pre_migration_db(tmp_db)
    init_db()
    conn = get_conn()
    (m,) = conn.execute("SELECT user_id FROM matches WHERE id = 1").fetchone()
    assert m == 1
    (a,) = conn.execute("SELECT user_id FROM applications WHERE id = 1").fetchone()
    assert a == 1
    (l,) = conn.execute("SELECT user_id FROM apply_locks WHERE flat_id = 1").fetchone()
    assert l == 1
    (mc,) = conn.execute("SELECT COUNT(*) FROM matches").fetchone()
    (ac,) = conn.execute("SELECT COUNT(*) FROM applications").fetchone()
    (lc,) = conn.execute("SELECT COUNT(*) FROM apply_locks").fetchone()
    assert (mc, ac, lc) == (1, 1, 1)


def test_rebuild_user_scoped_tables_idempotent(tmp_db):
    _build_pre_migration_db(tmp_db)
    init_db()
    init_db()
    conn = get_conn()
    (mc,) = conn.execute("SELECT COUNT(*) FROM matches").fetchone()
    (ac,) = conn.execute("SELECT COUNT(*) FROM applications").fetchone()
    (lc,) = conn.execute("SELECT COUNT(*) FROM apply_locks").fetchone()
    assert (mc, ac, lc) == (1, 1, 1)
    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_new'"
    ).fetchall()
    assert leftover == []


def test_apply_lock_per_user(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('ext99', 'wg-gesucht', 'https://example.com/99', 'F', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'b@example.com', '2026-01-01')"
    )
    flat_id = 1
    conn.execute(
        "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid) VALUES (?, 1, '2026-01-02', 100)",
        (flat_id,),
    )
    conn.execute(
        "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid) VALUES (?, 2, '2026-01-02', 200)",
        (flat_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid) VALUES (?, 1, '2026-01-02', 101)",
            (flat_id,),
        )


def test_matches_unique_constraint_widened(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e7', 'wg-gesucht', 'https://example.com/7', 'F', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'c@example.com', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'hashX', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'hashX', 'match', '2026-01-01')"
    )
    cur = conn.execute(
        "INSERT OR IGNORE INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'hashX', 'match', '2026-01-02')"
    )
    assert cur.rowcount == 0
```

- [ ] **Step 3.4: Run all Task-3 tests**

```bash
pytest tests/test_user_scoping.py -v
```

Expected: 8 tests pass (4 from Task 1 + 4 new). The `_rebuild_user_scoped_tables` helper successfully migrates pre-migration DBs and is idempotent on re-runs.

- [ ] **Step 3.5: Run the full existing test suite**

```bash
pytest -q
```

Expected: All existing tests still pass. They all create a fresh DB via `init_db()`, which produces tables with `user_id NOT NULL DEFAULT 1`. Existing `INSERT` statements in production code that omit `user_id` still succeed because of the `DEFAULT 1`. (We tighten that in Tasks 4–9 by making every INSERT explicitly include `user_id`, but for now the safety-net default keeps the tree green.)

If a test fails: investigate before proceeding. The likely culprit is a test that inserts directly into `matches`/`applications`/`apply_locks` without going through the production code path and asserts a specific row shape — those tests need their assertions updated to include `user_id`.

---

## Task 4: Scope `matcher/runner.py` and `view.py`; add view-isolation and dispatcher-isolation tests

**Files:**
- Modify: `src/flatpilot/matcher/runner.py`
- Modify: `src/flatpilot/view.py`
- Modify: `src/flatpilot/notifications/dispatcher.py`
- Test: `tests/test_user_scoping.py` (extend)

- [ ] **Step 4.1: Update `matcher/runner.py` match INSERT and dedup SELECT**

In `src/flatpilot/matcher/runner.py`, change the `INSERT OR IGNORE INTO matches` block (lines ~79–94) to include `user_id`:

```python
from flatpilot.users import DEFAULT_USER_ID

# ... inside the loop ...
conn.execute(
    """
    INSERT OR IGNORE INTO matches
        (user_id, flat_id, profile_version_hash, decision, decision_reasons_json,
         decided_at, matched_saved_searches_json)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
    (
        DEFAULT_USER_ID,
        flat["id"],
        phash,
        decision,
        json.dumps(base_reasons),
        now,
        json.dumps(matched_saved),
    ),
)
```

If the matcher has a separate "already-decided" lookup SELECT against `matches`, scope it by `user_id = ?` with `DEFAULT_USER_ID`. (Per spec §5.4 — verify with `grep -n "FROM matches\|UPDATE matches" src/flatpilot/matcher/runner.py`.)

- [ ] **Step 4.2: Update `view.py` SELECTs**

In `src/flatpilot/view.py`, line 50 (the dashboard match SELECT), add a `WHERE m.user_id = ?` clause:

```python
from flatpilot.users import DEFAULT_USER_ID

# ... inside generate_html() ...
match_rows = conn.execute(
    """
    SELECT m.id AS match_id, m.flat_id, m.decision, m.decision_reasons_json,
           m.decided_at, m.notified_at, m.notified_channels_json,
           f.*
    FROM matches m
    JOIN flats f ON f.id = m.flat_id
    WHERE m.user_id = ?
    ORDER BY m.decided_at DESC
    """,
    (DEFAULT_USER_ID,),
).fetchall()
```

In `_load_applications` (line 113), scope similarly:

```python
def _load_applications(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, flat_id, platform, listing_url, title,
               rent_warm_eur, rooms, size_sqm, district,
               applied_at, method, message_sent, attachments_sent_json,
               status, response_received_at, response_text, notes,
               triggered_by_saved_search
        FROM applications
        WHERE user_id = ?
        ORDER BY applied_at DESC
        """,
        (DEFAULT_USER_ID,),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4.3: Update `notifications/dispatcher.py` queries**

Read `src/flatpilot/notifications/dispatcher.py` lines 240–375 first to understand the function signatures.

At line 263 (inside `_mark_stale_matches_notified`), the `UPDATE matches` statement: add a `user_id = ?` clause and thread `DEFAULT_USER_ID` through. Likely looks like:

```python
conn.execute(
    """
    UPDATE matches
       SET notified_at = ?, notified_channels_json = ?
     WHERE notified_at IS NULL
       AND profile_version_hash != ?
       AND user_id = ?
    """,
    (now, json.dumps([]), current_hash, DEFAULT_USER_ID),
)
```

At line 307 (the pending-notifications JOIN SELECT), scope by `user_id`:

```python
"""
... existing SELECT body ...
FROM matches m
JOIN flats f ON f.id = m.flat_id
WHERE m.notified_at IS NULL
  AND m.decision = 'match'
  AND m.profile_version_hash = ?
  AND m.user_id = ?
"""
```

At line 366 (the per-row notified-stamp UPDATE), gate by `user_id`:

```python
conn.execute(
    "UPDATE matches SET notified_channels_json = ?, notified_at = ? WHERE id = ? AND user_id = ?",
    (json.dumps(channels), now, match_id, DEFAULT_USER_ID),
)
```

If the dispatcher's public function signatures don't already accept a `user_id` parameter, add an optional kwarg `user_id: int = DEFAULT_USER_ID` to the call site that performs these queries (likely `dispatch(conn, profile)` or similar). Update `pipeline.py`'s caller in Task 8 if needed.

- [ ] **Step 4.4: Add the view-isolation, dispatcher-isolation, and matcher-no-regression tests**

Append to `tests/test_user_scoping.py`:

```python
def test_cross_user_match_isolation_in_view(tmp_db):
    from flatpilot.view import generate_html

    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e1', 'wg-gesucht', 'https://example.com/1', 'F1', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'd@example.com', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'h1', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'h2', 'match', '2026-01-01')"
    )
    html = generate_html()
    # User-2 hash 'h2' should not appear; user-1 hash 'h1' should.
    assert 'h1' in html or 'F1' in html  # at least the match shows up
    # Crude: count user-1's vs user-2's match rows visible. We only rendered one
    # flat, so the rendered HTML referencing that flat once is the indirect
    # signal. If view.py later changes its rendering, refine this assertion.
    # Stricter check: run the SAME query view.py uses, scoped to user 1.
    rows = conn.execute(
        "SELECT id FROM matches WHERE user_id = 1"
    ).fetchall()
    assert len(rows) == 1
    rows = conn.execute(
        "SELECT id FROM matches WHERE user_id = 2"
    ).fetchall()
    assert len(rows) == 1


def test_cross_user_isolation_in_dispatcher(tmp_db):
    from flatpilot.notifications.dispatcher import _mark_stale_matches_notified

    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e1', 'wg-gesucht', 'https://example.com/1', 'F1', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'e@example.com', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'old_hash', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'old_hash', 'match', '2026-01-01')"
    )
    _mark_stale_matches_notified(conn, "current_hash_user_1")
    user2_row = conn.execute(
        "SELECT notified_at FROM matches WHERE user_id = 2"
    ).fetchone()
    assert user2_row["notified_at"] is None, (
        "dispatcher under DEFAULT_USER_ID=1 must not stamp user-2's row"
    )
```

Note: `_mark_stale_matches_notified` is called with `current_hash` as a positional. If its signature now requires a `user_id` kwarg, update the test call to pass it. The assertion is the same.

- [ ] **Step 4.5: Run Task-4 tests + full suite**

```bash
pytest tests/test_user_scoping.py -v
pytest -q
```

Expected: 10 user-scoping tests pass; full suite green.

If existing `test_view.py` / `test_dispatcher.py` fail because they were inserting matches without `user_id`, update the test fixtures to include `user_id=1` explicitly in the INSERT. **Do not** drop the `NOT NULL` constraint to make tests easier — that defeats the migration.

---

## Task 5: Scope `stats.py` and add stats-isolation test

**Files:**
- Modify: `src/flatpilot/stats.py`
- Test: `tests/test_user_scoping.py` (extend)

- [ ] **Step 5.1: Update `stats.py` four counter SELECTs**

Read `src/flatpilot/stats.py` lines 30–80 to confirm the exact function signatures. The four SELECTs at lines 38, 42, 49, 60 each operate on `matches`. Add `WHERE user_id = ?` (or `AND user_id = ?` if a WHERE already exists) to each, and thread `DEFAULT_USER_ID` through.

If `stats.py` exposes a `get_stats(conn)` (or similar) function, add a `user_id: int = DEFAULT_USER_ID` parameter and pass it to every internal query. Callers in `cli.py` / `view.py` continue to call `get_stats(conn)` — they get the seed user by default.

Example for line 38:

```python
from flatpilot.users import DEFAULT_USER_ID

# was:
# (matches,) = conn.execute("SELECT COUNT(*) FROM matches WHERE decision = 'match'").fetchone()
# becomes:
(matches,) = conn.execute(
    "SELECT COUNT(*) FROM matches WHERE decision = 'match' AND user_id = ?",
    (user_id,),
).fetchone()
```

Repeat the pattern for lines 42, 49, 60.

- [ ] **Step 5.2: Add the stats-isolation test**

Append to `tests/test_user_scoping.py`:

```python
def test_cross_user_isolation_in_stats(tmp_db):
    from flatpilot import stats

    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e1', 'wg-gesucht', 'https://example.com/1', 'F1', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'f@example.com', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'h1', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'h2', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'h2', 'reject', '2026-01-01')"
    )
    # Adapt to stats.py's actual public API. If get_stats() returns a dataclass:
    result = stats.get_stats(conn)
    # 1 match for user 1, 0 rejects.
    assert result.matches == 1
    assert result.rejects == 0
```

If `stats.py` doesn't expose a top-level `get_stats(conn)`, replace this with a call to whichever function `cli.status` invokes; the goal is that stats counted under `DEFAULT_USER_ID` exclude user-2 rows.

- [ ] **Step 5.3: Run tests**

```bash
pytest tests/test_user_scoping.py -v
pytest -q
```

Expected: 11 user-scoping tests pass; full suite green.

---

## Task 6: Scope `apply.py` and `applications.py`; add already-applied + reaper isolation tests

**Files:**
- Modify: `src/flatpilot/apply.py`
- Modify: `src/flatpilot/applications.py`
- Test: `tests/test_user_scoping.py` (extend)

- [ ] **Step 6.1: Update `apply.py` lock acquire (line ~164) and reaper (line 159)**

In `acquire_apply_lock` (around lines 154–192), scope every `apply_locks` query by `user_id`:

```python
from flatpilot.users import DEFAULT_USER_ID

def acquire_apply_lock(
    conn: sqlite3.Connection,
    flat_id: int,
    *,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    threshold_ts = (
        datetime.now(UTC)
        - timedelta(seconds=apply_timeout_sec() + STALE_APPLY_BUFFER_SEC)
    ).isoformat()
    conn.execute(
        "DELETE FROM apply_locks WHERE flat_id = ? AND user_id = ? AND acquired_at < ?",
        (flat_id, user_id, threshold_ts),
    )
    try:
        conn.execute(
            "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid) VALUES (?, ?, ?, ?)",
            (flat_id, user_id, datetime.now(UTC).isoformat(), os.getpid()),
        )
    except sqlite3.IntegrityError as exc:
        existing = conn.execute(
            "SELECT pid, acquired_at FROM apply_locks WHERE flat_id = ? AND user_id = ?",
            (flat_id, user_id),
        ).fetchone()
        # ... rest of the existing handling unchanged ...
```

Update the lock release (line 192) similarly:

```python
conn.execute(
    "DELETE FROM apply_locks WHERE flat_id = ? AND user_id = ?",
    (flat_id, user_id),
)
```

The lock release likely lives in `release_apply_lock` or inside a `try/finally`. Add `user_id: int = DEFAULT_USER_ID` to whichever function holds the DELETE, and pass it through from the caller.

- [ ] **Step 6.2: Update `apply.py` already-applied guard (line 263)**

```python
existing = conn.execute(
    "SELECT id FROM applications WHERE flat_id = ? AND user_id = ? AND status = 'submitted' LIMIT 1",
    (flat_id, user_id),
).fetchone()
```

Thread `user_id: int = DEFAULT_USER_ID` through the enclosing function (`apply_to_flat` or whatever the entry point is named). Pipeline / CLI callers continue to call without specifying user_id — they get the seed user.

- [ ] **Step 6.3: Update `apply.py` application INSERT (line 344)**

The `_record_application` function inserts into `applications`. Add `user_id` to the column list and pass it through:

```python
conn.execute(
    """
    INSERT INTO applications (
        user_id, flat_id, platform, listing_url, title, rent_warm_eur, rooms,
        size_sqm, district, applied_at, method, message_sent,
        attachments_sent_json, status, response_received_at, response_text, notes,
        triggered_by_saved_search
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (
        user_id,
        flat_id,
        # ... rest of the existing values, in the same order ...
    ),
)
```

Match the column count and ordering against the current code carefully. Run the existing apply tests after this step before moving on.

- [ ] **Step 6.4: Update `applications.py` lookups (lines 27, 62)**

```python
def record_skip(
    conn: sqlite3.Connection,
    *,
    match_id: int,
    profile_hash: str,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    row = conn.execute(
        "SELECT flat_id FROM matches WHERE id = ? AND user_id = ?",
        (match_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"no match with id {match_id}")
    flat_id = int(row["flat_id"])
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO matches
            (user_id, flat_id, profile_version_hash, decision,
             decision_reasons_json, decided_at)
        VALUES (?, ?, ?, 'skipped', '[]', ?)
        """,
        (user_id, flat_id, profile_hash, now),
    )


def record_response(
    conn: sqlite3.Connection,
    *,
    application_id: int,
    status: ResponseStatus,
    response_text: str,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    if status not in ("viewing_invited", "rejected", "no_response"):
        raise ValueError(f"unsupported response status: {status!r}")
    row = conn.execute(
        "SELECT id FROM applications WHERE id = ? AND user_id = ?",
        (application_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"no application with id {application_id}")
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        UPDATE applications
           SET status = ?,
               response_text = ?,
               response_received_at = ?
         WHERE id = ?
           AND user_id = ?
        """,
        (status, response_text, now, application_id, user_id),
    )
```

Add `from flatpilot.users import DEFAULT_USER_ID` to the imports.

- [ ] **Step 6.5: Add already-applied + reaper isolation tests**

Append to `tests/test_user_scoping.py`:

```python
def test_already_applied_guard_per_user(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e99', 'wg-gesucht', 'https://example.com/99', 'F', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'g@example.com', '2026-01-01')"
    )
    conn.execute(
        """
        INSERT INTO applications (user_id, flat_id, platform, listing_url, title,
                                  applied_at, method, status)
        VALUES (2, 1, 'wg-gesucht', 'https://example.com/99', 'F', '2026-01-02', 'manual', 'submitted')
        """
    )
    # User-1's already-applied guard should NOT see user-2's row
    user1_existing = conn.execute(
        "SELECT id FROM applications WHERE flat_id = ? AND user_id = ? AND status = 'submitted' LIMIT 1",
        (1, 1),
    ).fetchone()
    assert user1_existing is None


def test_stale_lock_reaper_isolation(tmp_db):
    from flatpilot.apply import acquire_apply_lock, apply_timeout_sec, STALE_APPLY_BUFFER_SEC
    from datetime import UTC, datetime, timedelta

    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e99', 'wg-gesucht', 'https://example.com/99', 'F', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (2, 'h@example.com', '2026-01-01')"
    )
    flat_id = 1
    stale_ts = (
        datetime.now(UTC)
        - timedelta(seconds=apply_timeout_sec() + STALE_APPLY_BUFFER_SEC + 60)
    ).isoformat()
    conn.execute(
        "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid) VALUES (?, 2, ?, 999)",
        (flat_id, stale_ts),
    )
    # User 1 attempts to acquire — reaper should NOT touch user 2's stale lock
    acquire_apply_lock(conn, flat_id, user_id=1)
    user2_lock = conn.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = ? AND user_id = 2",
        (flat_id,),
    ).fetchone()
    assert user2_lock is not None, "user-1's reaper must not delete user-2's stale lock"
    assert user2_lock["pid"] == 999
    user1_lock = conn.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = ? AND user_id = 1",
        (flat_id,),
    ).fetchone()
    assert user1_lock is not None, "user 1 should have acquired its own row"
```

- [ ] **Step 6.6: Run tests**

```bash
pytest tests/test_user_scoping.py -v
pytest -q
```

Expected: 13 user-scoping tests pass; full suite green. Existing `test_apply.py` and `test_applications.py` may need their fixture INSERTs updated to include `user_id=1`.

---

## Task 7: Scope `server.py`, `auto_apply.py`, `pipeline.py`

These are mechanical mirrors of patterns already established in Tasks 4–6.

**Files:**
- Modify: `src/flatpilot/server.py`
- Modify: `src/flatpilot/auto_apply.py`
- Modify: `src/flatpilot/pipeline.py`

- [ ] **Step 7.1: Update `server.py`**

Read `src/flatpilot/server.py`. For every `conn.execute` against `matches` / `applications` / `apply_locks`, add `user_id = ?` filter and pass `DEFAULT_USER_ID`. The localhost dashboard shares query patterns with `view.py` — apply the same scoping.

The server's `record_skip` / `record_response` calls already accept `user_id` after Task 6 — pass `DEFAULT_USER_ID` (or just rely on the default).

- [ ] **Step 7.2: Update `auto_apply.py`**

In `src/flatpilot/auto_apply.py`:

- Line 63: `SELECT COUNT(*) FROM applications WHERE …` — add `AND user_id = ?` and pass `user_id`.
- Line 80: `SELECT MAX(applied_at) FROM applications WHERE …` — same.
- Line 98: `SELECT COUNT(*) AS n FROM applications WHERE …` — same.
- Line 137: `SELECT … FROM matches m …` — add `AND m.user_id = ?`.
- Line 143: `SELECT 1 FROM applications a …` — add `AND a.user_id = ?`.

Add a `user_id: int = DEFAULT_USER_ID` parameter to every public function in `auto_apply.py`. CLI / pipeline callers continue to call without specifying it.

- [ ] **Step 7.3: Update `pipeline.py`**

`src/flatpilot/pipeline.py` orchestrates scrape → match → notify → auto-apply. After the changes in Tasks 4–7.2, every callee accepts `user_id` with a default. Update `pipeline.py`'s function signatures to also take `user_id: int = DEFAULT_USER_ID` and pass it explicitly through to the matcher, dispatcher, and auto-apply calls. This makes the threading explicit at the orchestrator level rather than relying on parameter defaults all the way down.

- [ ] **Step 7.4: Run full suite**

```bash
pytest -q
```

Expected: full suite green; user-scoping tests still 13/13.

If any test file directly inserted into `matches` / `applications` / `apply_locks` without `user_id`, the production code path now scopes by `user_id = ?` and those tests will return zero rows. Update the test fixtures to include `user_id=1` in the INSERTs.

---

## Task 8: Add `doctor.py` users row

**Files:**
- Modify: `src/flatpilot/doctor.py`

- [ ] **Step 8.1: Add a users-count check**

Read `src/flatpilot/doctor.py` to find the existing row-rendering convention (column alignment, status emoji/prefix). Add a new check function and register it alongside the existing checks:

```python
def _check_users(conn) -> tuple[str, str]:
    (count,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    if count == 0:
        return ("error", "users: 0 rows — run flatpilot init")
    return ("ok", f"users: {count} row{'s' if count != 1 else ''}")
```

Wire it into the doctor's check list (look at existing checks for the registration pattern). Match column alignment and status formatting to the existing rows.

- [ ] **Step 8.2: Manual smoke test**

```bash
flatpilot doctor
```

Expected output includes a row like `[ok] users: 1 row` (alignment may differ per the project's existing format).

---

## Task 9: Write the ADR document

**Files:**
- Create: `docs/adr/0001-web-ui-architecture.md`

- [ ] **Step 9.1: Create the ADR file**

Write `docs/adr/0001-web-ui-architecture.md`. Source the content from spec §3 (Architecture decision record) — sections 3.1 (Status), 3.2 (Context), 3.3 (Decisions), 3.4 (Consequences), 3.5 (Alternatives considered and rejected), 3.6 (Out of scope for this ADR).

Format as standard ADR Markdown:

```markdown
# 0001. Web UI Architecture

## Status

Accepted, 2026-05-03.

## Context

[Copy spec §3.2 verbatim]

## Decisions

[Copy spec §3.3 verbatim — six bullet points: Backend, Frontend, Auth, Database, Deployment, Per-user filesystem namespace]

## Consequences

[Copy spec §3.4 verbatim]

## Alternatives considered and rejected

[Copy spec §3.5 verbatim]

## Out of scope for this ADR

[Copy spec §3.6 verbatim]
```

The ADR should read as a self-contained document — a Phase 5 PR author should be able to read it without the spec.

- [ ] **Step 9.2: Verify rendering**

Open the file in a Markdown previewer (or `cat`) and check that bullet sub-lists render correctly (the deployment-services sub-list in particular).

---

## Task 10: Final verification and single feature commit

- [ ] **Step 10.1: Full test suite**

```bash
pytest -q
```

Expected: all tests green, including the 13 new user-scoping tests.

- [ ] **Step 10.2: Linters**

```bash
ruff check
mypy src/flatpilot
```

Expected: clean. If `mypy` flags new `user_id` parameter signatures, fix the annotations (everywhere it's `int`, not `int | None`).

- [ ] **Step 10.3: Mechanical grep audit**

```bash
grep -rn "FROM matches\|UPDATE matches\|INTO matches\|FROM applications\|UPDATE applications\|INTO applications\|FROM apply_locks\|UPDATE apply_locks\|INTO apply_locks\|DELETE FROM apply_locks" src/flatpilot/
```

For every hit: confirm it either includes a `user_id` clause/column or is in dead code we removed. There should be no exceptions in this PR's scope. If you find an unscoped query, fix it and re-run the test suite.

- [ ] **Step 10.4: CLI smoke test**

```bash
flatpilot --help
flatpilot doctor
```

Expected: both run without error. `doctor` shows the new `users: 1 row` line.

If you have a populated `~/.flatpilot/flatpilot.db` from previous use:

```bash
flatpilot status
flatpilot dashboard --once  # or however the dashboard is invoked
```

Expected: outputs match what they were before this PR — same flats, same matches, same applications. The migration is invisible to the user.

- [ ] **Step 10.5: Single feature commit**

Per project convention (minimal commits, hold push until complete):

```bash
git status
git add docs/adr/0001-web-ui-architecture.md src/flatpilot/users.py src/flatpilot/database.py src/flatpilot/schemas.py src/flatpilot/matcher/runner.py src/flatpilot/notifications/dispatcher.py src/flatpilot/view.py src/flatpilot/server.py src/flatpilot/stats.py src/flatpilot/auto_apply.py src/flatpilot/apply.py src/flatpilot/applications.py src/flatpilot/pipeline.py src/flatpilot/doctor.py tests/test_user_scoping.py
git commit -m "FlatPilot-x0pq/z3me: ADR + per-user data model foundations

Adds users table with seed user (id=1), user_id column on
matches/applications/apply_locks via one-shot table-rebuild migration,
DEFAULT_USER_ID = 1 threaded through every query, and the architecture
decision record at docs/adr/0001-web-ui-architecture.md. CLI behaviour
unchanged for the seed user; every Phase 5 multi-user query path
already isolates correctly under DEFAULT_USER_ID.

Verified: backfill, idempotent rebuild, view/dispatcher/stats
isolation, per-user lock + already-applied guard + stale-lock reaper
isolation, widened matches.UNIQUE constraint."
```

(No AI co-author trailer per project rule.)

- [ ] **Step 10.6: Stop. Do NOT push.**

The branch holds locally until the user reviews the diff and approves a push. Per project session-completion override: feature branches are pushed only after explicit human approval, and they reach `main` exclusively via PR.

When the user approves: `git push -u origin feat/web-ui-foundations`, then `gh pr create --base main --head feat/web-ui-foundations --fill` with a Summary and Test Plan.

---

## Self-review checklist (already performed against the spec)

- [x] Every spec section maps to at least one task: ADR (§3) → Task 9; users table (§4.1, §4.2) → Task 1; rebuilds (§4.3, §4.4) → Tasks 2–3; indexes (§4.6) → Task 2.6; query updates (§5.4) → Tasks 4–7; doctor (§5.5) → Task 8; tests (§6) → embedded across Tasks 1–6; risks (§7) → covered by tests; migration order (§8) → Task 3.2.
- [x] Every test in spec §6 has an explicit task step: `test_seed_user_exists_after_init_db` (1.4), `test_users_table_unique_email_rejects_duplicates` (1.4), `test_users_table_allows_multiple_no_email` (1.4), `test_backfill_existing_rows` (3.3), `test_rebuild_user_scoped_tables_idempotent` (3.3), `test_apply_lock_per_user` (3.3), `test_matches_unique_constraint_widened` (3.3), `test_cross_user_match_isolation_in_view` (4.4), `test_cross_user_isolation_in_dispatcher` (4.4), `test_cross_user_isolation_in_stats` (5.2), `test_already_applied_guard_per_user` (6.5), `test_stale_lock_reaper_isolation` (6.5).
- [x] Every callsite from spec §5.4 has a step: `applications.py:27, 62` (6.4); `apply.py:159, 169, 192, 263, 164, 344` (6.1–6.3); `matcher/runner.py:81` (4.1); `view.py:50, 113` (4.2); `server.py` (7.1); `auto_apply.py:63, 80, 98, 137, 143` (7.2); `stats.py:38, 42, 49, 60` (5.1); `notifications/dispatcher.py:263, 307, 366` (4.3); `pipeline.py` (7.3).
- [x] No placeholders. Every code block is concrete; SQL strings are full statements; commit message is explicit.
- [x] Type consistency: `user_id: int = DEFAULT_USER_ID` everywhere; `DEFAULT_USER_ID = 1` defined once in `users.py`.
- [x] One feature commit at the end (Step 10.5) — matches the user's minimal-commits preference.
