from __future__ import annotations

import sqlite3

import pytest

from flatpilot.database import close_conn, get_conn, init_db


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: {"type": r[2], "notnull": r[3], "dflt_value": r[4], "pk": r[5]} for r in rows}


@pytest.fixture
def _upgrade_db(tmp_path, monkeypatch):
    """Yields a db_path with DB_PATH monkeypatched; does NOT call init_db."""
    db_path = tmp_path / "flatpilot.db"
    monkeypatch.setattr("flatpilot.config.DB_PATH", db_path)
    monkeypatch.setattr("flatpilot.database.DB_PATH", db_path)
    close_conn()
    yield db_path
    close_conn()


def test_fresh_install_has_email_normalized_column(tmp_db) -> None:
    init_db()
    conn = get_conn()
    cols = _columns(conn, "users")
    assert "email_normalized" in cols
    assert cols["email_normalized"]["type"] == "TEXT"
    assert cols["email_normalized"]["notnull"] == 0


def test_email_normalized_partial_unique_index(tmp_db) -> None:
    init_db()
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'users'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "idx_users_email_normalized" in names
    sql = next(r[1] for r in rows if r[0] == "idx_users_email_normalized")
    assert "UNIQUE" in sql.upper()
    assert "WHERE EMAIL_NORMALIZED IS NOT NULL" in sql.upper().replace("\n", " ")


def test_email_normalized_allows_multiple_nulls(tmp_db) -> None:
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (email, email_normalized, created_at) VALUES (?, NULL, ?)",
        ("a@x.com", "2026-05-04T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO users (email, email_normalized, created_at) VALUES (?, NULL, ?)",
        ("b@x.com", "2026-05-04T00:00:00Z"),
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM users WHERE email_normalized IS NULL").fetchone()[0]
    assert count == 3


def test_email_normalized_rejects_duplicate_non_null(tmp_db) -> None:
    init_db()
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (email, email_normalized, created_at) VALUES (?, ?, ?)",
        ("Foo@x.com", "foo@x.com", "2026-05-04T00:00:00Z"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (email, email_normalized, created_at) VALUES (?, ?, ?)",
            ("foo@x.com", "foo@x.com", "2026-05-04T00:00:00Z"),
        )
        conn.commit()


def test_fresh_install_has_magic_link_tokens_table(tmp_db) -> None:
    init_db()
    conn = get_conn()
    cols = _columns(conn, "magic_link_tokens")
    assert set(cols) == {"jti", "email", "issued_at", "expires_at", "used_at"}
    assert cols["jti"]["pk"] == 1
    assert cols["jti"]["notnull"] == 1
    assert cols["email"]["notnull"] == 1
    assert cols["issued_at"]["notnull"] == 1
    assert cols["expires_at"]["notnull"] == 1
    assert cols["used_at"]["notnull"] == 0

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'magic_link_tokens'"
    ).fetchall()
    assert "idx_magic_link_tokens_expires" in {r[0] for r in rows}


def test_upgrade_adds_email_normalized_column_to_existing_install(_upgrade_db) -> None:
    """Simulate a foundations-shipped DB (no email_normalized) and run init_db."""
    raw = sqlite3.connect(str(_upgrade_db))
    try:
        raw.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                created_at TEXT NOT NULL
            );
            INSERT INTO users (id, email, created_at)
                VALUES (1, NULL, '2026-04-01T00:00:00Z');
            INSERT INTO users (id, email, created_at)
                VALUES (2, 'CLI-User@Example.com', '2026-04-02T00:00:00Z');
        """)
        raw.commit()
    finally:
        raw.close()

    init_db()

    conn = get_conn()
    cols = _columns(conn, "users")
    assert "email_normalized" in cols

    row1 = conn.execute(
        "SELECT email, email_normalized FROM users WHERE id = 1"
    ).fetchone()
    assert (row1[0], row1[1]) == (None, None)

    row2 = conn.execute(
        "SELECT email, email_normalized FROM users WHERE id = 2"
    ).fetchone()
    assert (row2[0], row2[1]) == ("CLI-User@Example.com", "cli-user@example.com")


def test_upgrade_is_idempotent(_upgrade_db) -> None:
    """Running init_db twice on an already-upgraded DB must not raise."""
    init_db()
    init_db()

    conn = get_conn()
    cols = _columns(conn, "users")
    assert "email_normalized" in cols


def test_init_db_purges_old_magic_link_tokens(tmp_db) -> None:
    """Tokens whose expires_at is more than 1 day in the past are deleted on init_db."""
    init_db()
    conn = get_conn()
    conn.executescript(
        "INSERT INTO magic_link_tokens (jti, email, issued_at, expires_at, used_at) "
        "    VALUES ('fresh', 'a@x.com', datetime('now'), datetime('now', '+15 minutes'), NULL);"
        "INSERT INTO magic_link_tokens (jti, email, issued_at, expires_at, used_at) "
        "    VALUES ('ancient', 'b@x.com', datetime('now', '-30 days'),"
        "            datetime('now', '-30 days', '+15 minutes'), NULL);"
    )
    conn.commit()

    init_db()  # Re-run; it must purge the ancient row.

    conn = get_conn()
    rows = conn.execute("SELECT jti FROM magic_link_tokens").fetchall()
    assert {r[0] for r in rows} == {"fresh"}
