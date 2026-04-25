"""Shared pytest fixtures for FlatPilot tests.

The project stores user state under ``~/.flatpilot`` in production; tests
must never touch that directory. ``tmp_db`` redirects every path
computed off ``APP_DIR`` so ``ensure_dirs()`` creates directories under
``tmp_path`` instead, and clears the thread-local connection cache so
each test starts from a clean, isolated database.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from flatpilot import config, database

    app_dir = tmp_path / ".flatpilot"
    db_path = app_dir / "flatpilot.db"

    # ensure_dirs() references these module-level names directly, so we
    # have to patch each one the function touches. DB_PATH is also held
    # inside flatpilot.database (imported by name), so patch it there too.
    monkeypatch.setattr(config, "APP_DIR", app_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SESSIONS_DIR", app_dir / "sessions")
    monkeypatch.setattr(config, "LOG_DIR", app_dir / "logs")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", app_dir / "attachments")
    monkeypatch.setattr(config, "TEMPLATES_DIR", app_dir / "templates")
    monkeypatch.setattr(database, "DB_PATH", db_path)

    # compose.py and attachments.py bind their path references at import time
    # via ``from flatpilot.config import …``. Patch those sub-module names too
    # so tests that write templates/attachments under tmp_path are found.
    from flatpilot import attachments as _attachments
    from flatpilot import compose as _compose

    monkeypatch.setattr(_attachments, "ATTACHMENTS_DIR", app_dir / "attachments")
    monkeypatch.setattr(_compose, "TEMPLATES_DIR", app_dir / "templates")

    # profile.py also binds PROFILE_PATH at import time.
    from flatpilot import profile as _profile

    monkeypatch.setattr(_profile, "PROFILE_PATH", app_dir / "profile.json")
    monkeypatch.setattr(config, "PROFILE_PATH", app_dir / "profile.json")

    database.close_conn()
    database.init_db()
    conn = database.get_conn()
    try:
        yield conn
    finally:
        database.close_conn()
