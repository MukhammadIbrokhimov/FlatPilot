"""Tests for the ``flatpilot doctor`` per-platform cookie check.

Pre-existing checks (Python version, app dir, Playwright, Telegram,
SMTP) have install-environment tails and aren't unit-tested here. The
new ``_check_platform_cookies`` helper, which only ever touches
``~/.flatpilot/sessions/<platform>/state.json`` (redirected by
``tmp_db``), is tractable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from rich.console import Console

from flatpilot import doctor
from flatpilot.scrapers.base import session_dir


def _write_state(platform: str, *, expires_unix: list[int | float]) -> None:
    """Drop a state.json with the given cookie expiry timestamps under tmp_db."""
    path = session_dir(platform) / "state.json"
    cookies = [
        {
            "name": f"c{i}",
            "value": "x",
            "expires": exp,
            "domain": ".example",
            "path": "/",
        }
        for i, exp in enumerate(expires_unix)
    ]
    path.write_text(json.dumps({"cookies": cookies, "origins": []}))


def test_check_platform_cookies_no_state_returns_optional(tmp_db):
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "no session" in detail
    assert "flatpilot login wg-gesucht" in detail


def test_check_platform_cookies_unreadable_returns_optional(tmp_db):
    path = session_dir("wg-gesucht") / "state.json"
    path.write_text("{not valid json")
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "unreadable" in detail


def test_check_platform_cookies_handles_malformed_cookies_field(tmp_db):
    # Syntactically-valid JSON but `cookies` is null / a non-list — a
    # ``dict.get(default)`` does NOT fall back on present-but-wrong
    # values, so naive iteration would crash on the file the doctor
    # was invoked to diagnose. Verify every shape returns a tuple.
    path = session_dir("wg-gesucht") / "state.json"
    for payload in ('{"cookies": null}', '{"cookies": 5}', '{"cookies": "nope"}'):
        path.write_text(payload)
        status, detail = doctor._check_platform_cookies("wg-gesucht")
        assert status == "optional"
        assert "session-only cookies" in detail


def test_check_platform_cookies_expired_returns_optional(tmp_db):
    past = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    _write_state("wg-gesucht", expires_unix=[past])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "EXPIRED" in detail


def test_check_platform_cookies_within_warn_window_returns_optional(tmp_db):
    # 1.5 days from now — inside the 3-day warning window.
    soon = (datetime.now(UTC) + timedelta(days=1, hours=12)).timestamp()
    _write_state("wg-gesucht", expires_unix=[soon])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "soon" in detail


def test_check_platform_cookies_fresh_returns_ok(tmp_db):
    future = (datetime.now(UTC) + timedelta(days=30)).timestamp()
    _write_state("wg-gesucht", expires_unix=[future])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "OK"
    assert "30d" in detail


def test_check_platform_cookies_session_only_cookies_returns_optional(tmp_db):
    # All cookies have expires == -1 (Playwright's marker for browser-
    # session cookies that don't persist across runs).
    _write_state("wg-gesucht", expires_unix=[-1, -1])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "session-only" in detail


def test_check_platform_cookies_picks_earliest_expiry(tmp_db):
    """Multi-cookie state — doctor warns on the earliest expiry."""
    now = datetime.now(UTC)
    cookies = [
        (now + timedelta(days=30)).timestamp(),
        (now + timedelta(days=2)).timestamp(),
    ]
    _write_state("wg-gesucht", expires_unix=cookies)
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "soon" in detail


def test_run_includes_a_row_per_filler_platform(tmp_db, monkeypatch):
    # Replace static checks with an empty list so the test only
    # exercises the new per-platform iteration — the static checks
    # have install-time tails (playwright executable presence,
    # telegram env, smtp env) that flake in CI.
    monkeypatch.setattr(doctor, "CHECKS", [])
    console = Console(record=True, width=200, force_terminal=False)
    exit_code = doctor.run(console=console)
    output = console.export_text()
    # Per-platform rows are optional-only, so they must never push the
    # exit code from 0 to 1.
    assert exit_code == 0
    # Both fillers registered as of this PR — the iteration order is
    # alphabetic so kleinanzeigen comes before wg-gesucht.
    assert "Session: wg-gesucht" in output
    assert "Session: kleinanzeigen" in output
    assert "no session" in output
