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


# --- _check_telegram / _check_smtp branch coverage --------------------
# bead kmy names "all branches of _check_telegram / _check_smtp
# (including malformed profile)". Existing suite covered _check_pause,
# _check_saved_searches, _check_platform_burn, _check_platform_cookies
# but not the credential checks themselves; gap-fillers below.


def test_check_telegram_no_profile_returns_optional(tmp_db):
    # No profile.json on disk → _safe_load_profile() returns (None, None).
    status, detail = doctor._check_telegram()
    assert status == "optional"
    assert "no profile" in detail


def test_check_telegram_disabled_returns_optional(tmp_db):
    from flatpilot.profile import Profile, save_profile

    # Default example profile has telegram.enabled=False.
    save_profile(Profile.load_example())
    status, detail = doctor._check_telegram()
    assert status == "optional"
    assert "disabled" in detail


def test_check_telegram_enabled_missing_token_returns_optional(tmp_db, monkeypatch):
    from flatpilot.profile import (
        Notifications,
        Profile,
        TelegramNotification,
        save_profile,
    )

    # Enable telegram but ensure the env token is missing.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    profile = Profile.load_example().model_copy(
        update={
            "notifications": Notifications(
                telegram=TelegramNotification(
                    enabled=True,
                    bot_token_env="TELEGRAM_BOT_TOKEN",
                    chat_id="",
                ),
            ),
        }
    )
    save_profile(profile)
    status, detail = doctor._check_telegram()
    assert status == "optional"
    assert "enabled but" in detail
    assert "TELEGRAM_BOT_TOKEN" in detail
    assert "chat_id" in detail


def test_check_telegram_enabled_configured_returns_ok(tmp_db, monkeypatch):
    from flatpilot.profile import (
        Notifications,
        Profile,
        TelegramNotification,
        save_profile,
    )

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abcdef:12345")
    profile = Profile.load_example().model_copy(
        update={
            "notifications": Notifications(
                telegram=TelegramNotification(
                    enabled=True,
                    bot_token_env="TELEGRAM_BOT_TOKEN",
                    chat_id="42",
                ),
            ),
        }
    )
    save_profile(profile)
    status, detail = doctor._check_telegram()
    assert status == "OK"
    assert "TELEGRAM_BOT_TOKEN" in detail


def test_check_smtp_no_profile_returns_optional(tmp_db):
    status, detail = doctor._check_smtp()
    assert status == "optional"
    assert "no profile" in detail


def test_check_smtp_disabled_returns_optional(tmp_db):
    from flatpilot.profile import Profile, save_profile

    save_profile(Profile.load_example())
    status, detail = doctor._check_smtp()
    assert status == "optional"
    assert "disabled" in detail


def test_check_smtp_enabled_missing_env_returns_optional(tmp_db, monkeypatch):
    from flatpilot.profile import (
        EmailNotification,
        Notifications,
        Profile,
        save_profile,
    )

    for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)
    profile = Profile.load_example().model_copy(
        update={
            "notifications": Notifications(
                email=EmailNotification(enabled=True, smtp_env="SMTP"),
            ),
        }
    )
    save_profile(profile)
    status, detail = doctor._check_smtp()
    assert status == "optional"
    assert "enabled but missing" in detail
    # All five env vars are missing — detail should list every one of them
    # rather than short-circuiting on the first.
    for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        assert var in detail


def test_check_smtp_enabled_configured_returns_ok(tmp_db, monkeypatch):
    from flatpilot.profile import (
        EmailNotification,
        Notifications,
        Profile,
        save_profile,
    )

    for var, val in (
        ("SMTP_HOST", "smtp.example.com"),
        ("SMTP_PORT", "587"),
        ("SMTP_USER", "user"),
        ("SMTP_PASSWORD", "pw"),
        ("SMTP_FROM", "me@example.com"),
    ):
        monkeypatch.setenv(var, val)
    profile = Profile.load_example().model_copy(
        update={
            "notifications": Notifications(
                email=EmailNotification(enabled=True, smtp_env="SMTP"),
            ),
        }
    )
    save_profile(profile)
    status, detail = doctor._check_smtp()
    assert status == "OK"
    assert "set" in detail


def test_doctor_handles_malformed_profile(tmp_db):
    from io import StringIO

    from flatpilot.config import PROFILE_PATH

    PROFILE_PATH.write_text("{ not valid json")  # malformed

    buf = StringIO()
    rc = doctor.run(Console(file=buf, force_terminal=False, width=200))

    # Doctor must surface "profile unreadable" without crashing. Per
    # _safe_load_profile (doctor.py:79-82), the error path returns
    # ("optional", "profile unreadable: ..."), so the status badge for
    # the credential rows ends up "optional" — exit code stays 0
    # (optional never fails). Either rc == 0 with the explanation in
    # the table, or rc != 0 if a downstream check trips. What we
    # really care about is that run() returned without raising AND
    # that the table mentions the malformed profile.
    output = buf.getvalue().lower()
    assert "profile" in output
    assert isinstance(rc, int)
