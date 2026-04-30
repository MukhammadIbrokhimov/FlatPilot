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


def init_db() -> None:
    import flatpilot.schemas  # noqa: F401  — side-effect registration

    conn = get_conn()
    for create_sql in SCHEMAS.values():
        conn.execute(create_sql)
    ensure_columns()


def ensure_columns() -> None:
    conn = get_conn()
    for table, cols in COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col_name, col_def in cols.items():
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
