"""Shared pytest fixtures for FlatPilot tests.

The project stores user state under ``~/.flatpilot`` in production; tests
must never touch that directory. ``tmp_db`` redirects every path
computed off ``APP_DIR`` so ``ensure_dirs()`` creates directories under
``tmp_path`` instead, and clears the thread-local connection cache so
each test starts from a clean, isolated database.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

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

    # auto_apply.PAUSE_PATH is computed from APP_DIR at import time, so
    # patch the bound name explicitly. Lazy import keeps this fixture
    # importable on a tree that hasn't introduced auto_apply.py yet.
    from flatpilot import auto_apply as _auto_apply

    monkeypatch.setattr(_auto_apply, "PAUSE_PATH", app_dir / "PAUSE")

    database.close_conn()
    database.init_db()
    conn = database.get_conn()
    try:
        yield conn
    finally:
        database.close_conn()


@pytest.fixture
def berlin_profile():
    from flatpilot.profile import Profile

    return Profile.load_example().model_copy(update={"city": "Berlin"})


def make_session_fakes(
    html_by_url: dict[str, str] | None = None,
    *,
    default_html: str = "<html><body></body></html>",
    goto_log: list[str] | None = None,
    captured: dict[str, Any] | None = None,
    locator_count: int = 0,
    status_fn: Callable[[str], int] | None = None,
) -> tuple[type, type]:
    """Return a (polite_session_cls, session_page_cls) stub pair.

    html_by_url:   URL → HTML; unmatched URLs return default_html.
    default_html:  HTML for URLs absent from html_by_url (default: empty page).
    goto_log:      list to append each goto() URL to.
    captured:      dict that receives the SessionConfig as captured["config"].
    locator_count: return value for page.locator(...).count().
    status_fn:     URL → HTTP status code; defaults to always 200.
    """
    _html: dict[str, str] = html_by_url or {}
    _log = goto_log
    _cap = captured
    _status: Callable[[str], int] = status_fn or (lambda _u: 200)

    class _FakeCtxMgr:
        def __init__(self, config: Any) -> None:
            if _cap is not None:
                _cap["config"] = config

        def __enter__(self) -> Any:
            return object()

        def __exit__(self, *_exc: Any) -> None:
            return None

    class _FakePageCtxMgr:
        def __init__(self, _ctx: Any) -> None:
            pass

        def __enter__(self) -> Any:
            class _L:
                def count(self) -> int:
                    return locator_count

            class _P:
                def __init__(self) -> None:
                    self._url: str | None = None

                def goto(self, url: str, **_kw: Any) -> Any:
                    if _log is not None:
                        _log.append(url)
                    self._url = url

                    class _R:
                        pass

                    _R.status = _status(url)
                    return _R()

                def locator(self, _sel: str) -> Any:
                    return _L()

                def content(self) -> str:
                    return _html.get(self._url or "", default_html)

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    return _FakeCtxMgr, _FakePageCtxMgr
