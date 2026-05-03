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


def _seed_flat(conn, *, flat_id=1, title="F", platform="wg-gesucht"):
    conn.execute(
        "INSERT INTO flats (id, external_id, platform, listing_url,"
        " title, scraped_at, first_seen_at)"
        " VALUES (?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (flat_id, f"e{flat_id}", platform, f"https://example.com/{flat_id}", title),
    )


def _seed_user(conn, *, user_id, email):
    conn.execute(
        "INSERT INTO users (id, email, created_at) VALUES (?, ?, '2026-01-01')",
        (user_id, email),
    )


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
    insert_user = "INSERT INTO users (email, created_at) VALUES (?, ?)"
    conn.execute(insert_user, ("a@example.com", "2026-01-01T00:00:00+00:00"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert_user, ("a@example.com", "2026-01-02T00:00:00+00:00"))


def test_users_table_allows_multiple_no_email(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute("INSERT INTO users (email, created_at) VALUES (NULL, '2026-01-01T00:00:00+00:00')")
    conn.execute("INSERT INTO users (email, created_at) VALUES (NULL, '2026-01-02T00:00:00+00:00')")
    (count,) = conn.execute("SELECT COUNT(*) FROM users WHERE email IS NULL").fetchone()
    assert count == 3  # seed + 2 inserted


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
        "INSERT INTO flats (external_id, platform, listing_url, title,"
        " scraped_at, first_seen_at)"
        " VALUES ('ext1', 'wg-gesucht', 'https://example.com/1', 'Test flat',"
        " '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 'hashA', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title,"
        " applied_at, method, status)"
        " VALUES (1, 'wg-gesucht', 'https://example.com/1', 'Test flat',"
        " '2026-01-02', 'manual', 'submitted')"
    )
    conn.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid)"
        " VALUES (1, '2026-01-02', 1234)"
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
    (lock_uid,) = conn.execute(
        "SELECT user_id FROM apply_locks WHERE flat_id = 1"
    ).fetchone()
    assert lock_uid == 1
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
    _seed_flat(conn, flat_id=1, title="F")
    _seed_user(conn, user_id=2, email="b@example.com")
    flat_id = 1
    insert_lock = (
        "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid)"
        " VALUES (?, ?, '2026-01-02', ?)"
    )
    conn.execute(insert_lock, (flat_id, 1, 100))
    conn.execute(insert_lock, (flat_id, 2, 200))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert_lock, (flat_id, 1, 101))


def test_matches_unique_constraint_widened(tmp_db):
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, scraped_at, first_seen_at)"
        " VALUES ('e7', 'wg-gesucht', 'https://example.com/7', 'F', '2026-01-01', '2026-01-01')"
    )
    _seed_user(conn, user_id=2, email="c@example.com")
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'hashX', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'hashX', 'match', '2026-01-01')"
    )
    cur = conn.execute(
        "INSERT OR IGNORE INTO matches"
        " (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'hashX', 'match', '2026-01-02')"
    )
    assert cur.rowcount == 0


def test_cross_user_match_isolation_in_view(tmp_db):
    from flatpilot.view import generate_html

    init_db()
    conn = get_conn()
    _seed_flat(conn, flat_id=1, title="USER1_FLAT")
    _seed_flat(conn, flat_id=2, title="USER2_FLAT")
    _seed_user(conn, user_id=2, email="d@example.com")
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'h1', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 2, 'h2', 'match', '2026-01-01')"
    )
    html = generate_html()
    assert "USER1_FLAT" in html, "user-1's flat must appear in dashboard"
    assert "USER2_FLAT" not in html, "user-2's flat must not leak into user-1's dashboard"


def test_cross_user_isolation_in_dispatcher(tmp_db):
    from flatpilot.notifications.dispatcher import _mark_stale_matches_notified

    init_db()
    conn = get_conn()
    _seed_flat(conn, flat_id=1, title="F1")
    _seed_user(conn, user_id=2, email="e@example.com")
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'old_hash', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'old_hash', 'match', '2026-01-01')"
    )
    _mark_stale_matches_notified(conn, "current_hash_for_user_1")
    user1_row = conn.execute(
        "SELECT notified_at FROM matches WHERE user_id = 1"
    ).fetchone()
    user2_row = conn.execute(
        "SELECT notified_at FROM matches WHERE user_id = 2"
    ).fetchone()
    assert user1_row["notified_at"] is not None, "user-1's stale row should be stamped"
    assert user2_row["notified_at"] is None, (
        "dispatcher under DEFAULT_USER_ID=1 must not stamp user-2's row"
    )


def test_cross_user_isolation_in_stats(tmp_db):
    from flatpilot import stats

    init_db()
    conn = get_conn()
    _seed_flat(conn, flat_id=1, title="F1")
    _seed_user(conn, user_id=2, email="f@example.com")
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (1, 1, 'h1', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at)"
        " VALUES (2, 1, 'h2', 'match', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, decided_at,"
        " decision_reasons_json) VALUES (2, 1, 'h2', 'reject', '2026-01-01', '[\"rent_too_high\"]')"
    )
    result = stats.get_stats()
    assert result["matched"] == 1, "stats should count only user-1 match rows"
    assert result["rejected_by_reason"] == {}, "stats should not count user-2 reject reasons"


def test_already_applied_guard_per_user(tmp_db):
    init_db()
    conn = get_conn()
    _seed_flat(conn, flat_id=1, title="F")
    _seed_user(conn, user_id=2, email="g@example.com")
    conn.execute(
        """
        INSERT INTO applications (user_id, flat_id, platform, listing_url, title,
                                  applied_at, method, status)
        VALUES (2, 1, 'wg-gesucht', 'https://example.com/99', 'F',
                '2026-01-02', 'manual', 'submitted')
        """
    )
    user1_existing = conn.execute(
        "SELECT id FROM applications "
        "WHERE flat_id = ? AND user_id = ? AND status = 'submitted' LIMIT 1",
        (1, 1),
    ).fetchone()
    assert user1_existing is None, (
        "user-1's already-applied guard must not see user-2's submitted row"
    )


def test_stale_lock_reaper_isolation(tmp_db):
    from datetime import UTC, datetime, timedelta

    from flatpilot.apply import (
        STALE_APPLY_BUFFER_SEC,
        acquire_apply_lock,
        apply_timeout_sec,
    )

    init_db()
    conn = get_conn()
    _seed_flat(conn, flat_id=1, title="F")
    _seed_user(conn, user_id=2, email="h@example.com")
    flat_id = 1
    stale_ts = (
        datetime.now(UTC)
        - timedelta(seconds=apply_timeout_sec() + STALE_APPLY_BUFFER_SEC + 60)
    ).isoformat()
    conn.execute(
        "INSERT INTO apply_locks (flat_id, user_id, acquired_at, pid) VALUES (?, 2, ?, 999)",
        (flat_id, stale_ts),
    )
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
