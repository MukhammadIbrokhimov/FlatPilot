"""SQLite connection helper with per-thread caching and forward migrations.

The Phase 1 CLI is single-threaded, but per-thread connections keep us safe if
the pipeline ever spawns worker threads (e.g. during notification fanout).
Other modules register their tables in :data:`SCHEMAS` and any new columns in
:data:`COLUMNS`; :func:`init_db` and :func:`ensure_columns` apply them.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from flatpilot.config import DB_PATH, ensure_dirs

# table_name -> full ``CREATE TABLE IF NOT EXISTS ...`` statement
SCHEMAS: dict[str, str] = {}

# table_name -> {column_name: column_definition} — anything listed here that's
# missing from the live table is added via ALTER TABLE on the next init.
COLUMNS: dict[str, dict[str, str]] = {}


_local = threading.local()


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    existing = getattr(_local, "conn", None)
    if existing is not None:
        return existing
    path = db_path or DB_PATH
    ensure_dirs()
    conn = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    _local.conn = conn
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


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
                        status IN (
                            'submitted', 'failed', 'viewing_invited',
                            'rejected', 'no_response'
                        )
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


def init_db() -> None:
    import flatpilot.schemas  # noqa: F401  — side-effect registration
    from flatpilot.users import ensure_default_user

    conn = get_conn()
    # First pass: tables only (skips indexes that reference user_id columns
    # not yet present on pre-migration DBs).
    for name, create_sql in SCHEMAS.items():
        if not name.startswith("idx_"):
            conn.execute(create_sql)
    ensure_default_user(conn)
    # Apply forward-migration columns BEFORE rebuilding user-scoped tables
    # (FlatPilot-a9l): the rebuild's INSERT SELECT reads
    # matches.matched_saved_searches_json and applications.triggered_by_saved_search;
    # legacy DBs predate those, so add them via ALTER TABLE first.
    ensure_columns()
    _rebuild_user_scoped_tables(conn)
    # Second pass: indexes (tables now have user_id, so index creation succeeds).
    for name, create_sql in SCHEMAS.items():
        if name.startswith("idx_"):
            conn.execute(create_sql)


def ensure_columns() -> None:
    conn = get_conn()
    for table, cols in COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col_name, col_def in cols.items():
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
