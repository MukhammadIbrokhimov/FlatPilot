"""Health checks for ``flatpilot doctor``.

Each check is a function returning ``(status, detail)``. Status is one of
``"OK"``, ``"MISSING"`` (required check failed — affects exit code), or
``"optional"`` (nice to have, never fails the exit code).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

# Import every filler module so the registry is populated when doctor
# walks ``all_fillers()`` for per-platform cookie checks. Mirrors the
# pattern in apply.py. Do not move this into fillers/__init__.py — that
# creates a circular-import risk.
import flatpilot.fillers.kleinanzeigen  # noqa: F401
import flatpilot.fillers.wg_gesucht  # noqa: F401
from flatpilot import config
from flatpilot.fillers import all_fillers
from flatpilot.profile import Profile, load_profile
from flatpilot.scrapers.base import session_dir

CheckFn = Callable[[], "tuple[str, str]"]

# Minimum days remaining before doctor flags an upcoming session expiry.
COOKIE_EXPIRY_WARN_DAYS: int = 3


def _check_python() -> tuple[str, str]:
    v = sys.version_info
    current = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 11):
        return "OK", f"Python {current}"
    return "MISSING", f"Python {current} < 3.11"


def _check_app_dir() -> tuple[str, str]:
    try:
        config.ensure_dirs()
    except OSError as exc:
        return "MISSING", f"{config.APP_DIR}: {exc}"
    probe = config.APP_DIR / ".doctor-write-test"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return "MISSING", f"{config.APP_DIR} not writable: {exc}"
    return "OK", str(config.APP_DIR)


def _check_playwright() -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "MISSING", "playwright package not installed"
    try:
        with sync_playwright() as p:
            exec_path = Path(p.chromium.executable_path)
    except Exception as exc:
        return "MISSING", f"{exc.__class__.__name__}: {exc}"
    if not exec_path.exists():
        return "MISSING", "Chromium not installed — run `playwright install chromium`"
    return "OK", f"Chromium at {exec_path}"


def _safe_load_profile() -> tuple[Profile | None, str | None]:
    # doctor must not crash when profile.json is malformed — that's
    # exactly the scenario a user runs it to diagnose. ValidationError
    # (invalid schema / invalid JSON) inherits from ValueError; OSError
    # covers unreadable files.
    try:
        return load_profile(), None
    except (ValueError, OSError) as exc:
        return None, f"profile unreadable: {type(exc).__name__}"


def _check_telegram() -> tuple[str, str]:
    # The bot token is env-based (var name chosen in the profile, default
    # TELEGRAM_BOT_TOKEN) but the chat_id lives in profile.json — checking
    # for a TELEGRAM_CHAT_ID env var as the old implementation did would
    # always report 'missing' for correctly configured users.
    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile — run `flatpilot init`"
    tg = profile.notifications.telegram
    if not tg.enabled:
        return "optional", "disabled in profile"
    missing: list[str] = []
    if not os.environ.get(tg.bot_token_env):
        missing.append(f"${tg.bot_token_env}")
    if not tg.chat_id:
        missing.append("profile.notifications.telegram.chat_id")
    if missing:
        return "optional", f"enabled but {', '.join(missing)}"
    return "OK", f"token from ${tg.bot_token_env}, chat_id in profile"


def _check_smtp() -> tuple[str, str]:
    # Same profile-aware shape as Telegram: only complain about SMTP_*
    # env vars when the user has enabled email notifications in profile.
    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile — run `flatpilot init`"
    if not profile.notifications.email.enabled:
        return "optional", "disabled in profile"
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        return "optional", f"enabled but missing: {', '.join(missing)}"
    return "OK", "HOST/PORT/USER/PASSWORD/FROM set"


def _check_pause() -> tuple[str, str]:
    from flatpilot.auto_apply import PAUSE_PATH

    if PAUSE_PATH.exists():
        return "optional", "PAUSED — auto-apply halted (run `flatpilot resume` to re-enable)"
    return "OK", "not paused"


def _check_saved_searches() -> tuple[str, str]:
    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile"
    active = [ss.name for ss in profile.saved_searches if ss.auto_apply]
    if not active:
        return "OK", "0 active"
    return "OK", f"{len(active)} active ({', '.join(active)})"


def _check_saved_search_notifications() -> tuple[str, str]:
    """Verify per-saved-search notification overrides reference resolvable env vars."""
    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile"

    missing_env: list[str] = []
    override_count = 0
    for ss in profile.saved_searches:
        if ss.notifications is None:
            continue
        has_override = False
        if ss.notifications.telegram is not None and ss.notifications.telegram.enabled:
            has_override = True
            env_name = ss.notifications.telegram.bot_token_env
            if env_name and not os.environ.get(env_name):
                missing_env.append(f"{ss.name}.telegram.bot_token_env={env_name}")
        if ss.notifications.email is not None and ss.notifications.email.enabled:
            has_override = True
            prefix = ss.notifications.email.smtp_env
            if prefix:
                # Only flag if the entire prefix's HOST is missing — caller
                # has the option to fall through to base for individual fields.
                host_var = f"{prefix}_HOST"
                if not os.environ.get(host_var):
                    missing_env.append(f"{ss.name}.email.smtp_env={prefix} ({host_var} unset)")
        if has_override:
            override_count += 1

    if not override_count:
        return "OK", "no overrides"
    if missing_env:
        return "optional", f"missing env vars: {', '.join(missing_env)}"
    return "OK", f"{override_count} override(s) resolve"


def _check_platform_burn(platform: str) -> tuple[str, str]:
    from flatpilot.auto_apply import cooldown_remaining_sec, daily_cap_remaining
    from flatpilot.database import get_conn, init_db

    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile"
    cap = profile.auto_apply.daily_cap_per_platform.get(platform, 0)
    if cap == 0:
        return "optional", "no cap configured (auto-apply disabled for platform)"
    init_db()
    conn = get_conn()
    remaining = daily_cap_remaining(conn, profile, platform)
    used = cap - remaining
    wait = cooldown_remaining_sec(conn, profile, platform)
    return "OK", f"{used}/{cap} used today, ready in {wait:.0f}s"


def _check_platform_cookies(platform: str) -> tuple[str, str]:
    """Probe ``~/.flatpilot/sessions/<platform>/state.json`` for cookie freshness.

    JSON-only — does not launch a browser. Reports:

    - ``"OK"`` with "expires in Nd" when the earliest persistent cookie's
      expiry is more than :data:`COOKIE_EXPIRY_WARN_DAYS` days away.
    - ``"optional"`` (yellow) with "expires in Nd — re-run … soon" when
      within the warning window.
    - ``"optional"`` with "EXPIRED — run …" when the earliest expiry is
      already past.
    - ``"optional"`` with "no session — run … if you plan to apply" when
      the file does not exist.
    - ``"optional"`` with "session state unreadable" when the file
      exists but is not parseable JSON.
    - ``"optional"`` with "session-only cookies" when every cookie has
      ``expires == -1`` (Playwright's marker for browser-session cookies
      that don't persist across runs).

    Doctor must not crash on the thing it's diagnosing — every error
    path returns a tuple, never raises.
    """
    state_path = session_dir(platform) / "state.json"
    if not state_path.exists():
        return (
            "optional",
            f"no session — run `flatpilot login {platform}` if you plan to apply",
        )
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return "optional", "session state unreadable"

    raw_cookies = state.get("cookies", []) if isinstance(state, dict) else []
    # ``state["cookies"]`` may be ``null`` or a non-list type in a corrupted
    # file; ``dict.get(default)`` only fires on missing keys, not on
    # present-but-wrong values. Iterating ``None`` would otherwise crash
    # the doctor on the exact pathology it's supposed to diagnose.
    cookies = raw_cookies if isinstance(raw_cookies, list) else []
    expiries = [
        c["expires"]
        for c in cookies
        if isinstance(c, dict)
        and isinstance(c.get("expires"), (int, float))
        and c["expires"] > 0
    ]
    if not expiries:
        return (
            "optional",
            f"session-only cookies — re-run `flatpilot login {platform}` if expired",
        )
    earliest = min(expiries)
    now = datetime.now(UTC).timestamp()
    days_remaining = (earliest - now) / 86_400.0
    if days_remaining <= 0:
        return "optional", f"EXPIRED — run `flatpilot login {platform}`"
    if days_remaining < COOKIE_EXPIRY_WARN_DAYS:
        return (
            "optional",
            f"expires in {days_remaining:.0f}d — re-run `flatpilot login {platform}` soon",
        )
    return "OK", f"expires in {days_remaining:.0f}d"


CHECKS: list[tuple[str, CheckFn]] = [
    ("Python >= 3.11", _check_python),
    ("App directory", _check_app_dir),
    ("Playwright Chromium", _check_playwright),
    ("Telegram creds", _check_telegram),
    ("SMTP creds", _check_smtp),
    ("Auto-apply: PAUSE switch", _check_pause),
    ("Auto-apply: saved searches", _check_saved_searches),
    ("Auto-apply: saved-search notif overrides", _check_saved_search_notifications),
]


_STYLES = {"OK": "green", "MISSING": "red", "optional": "yellow"}


def run(console: Console | None = None) -> int:
    """Run every check, print a summary table, return a CLI exit code.

    Returns 0 if every required check passed, 1 otherwise. Optional
    checks that come back missing do not affect the exit code — they're
    reminders. Per-platform cookie rows are always ``"optional"`` or
    ``"OK"``, so they never affect the exit code either; a user who
    hasn't logged into a platform yet is not a doctor failure.

    ``config.load_env()`` is called in the CLI ``_bootstrap`` callback
    so every command sees ``~/.flatpilot/.env`` — no need to repeat it
    here.
    """
    out = console or Console()
    table = Table(title="FlatPilot doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    exit_code = 0
    for name, check_fn in CHECKS:
        status, detail = check_fn()
        style = _STYLES[status]
        table.add_row(name, f"[{style}]{status}[/{style}]", detail)
        if status == "MISSING":
            exit_code = 1
    # Per-platform cookie rows. Sorted by platform string for stable
    # output. Status is always "optional" or "OK" — never fails the
    # exit code.
    for filler_cls in sorted(all_fillers(), key=lambda c: c.platform):
        platform = filler_cls.platform
        status, detail = _check_platform_cookies(platform)
        style = _STYLES[status]
        table.add_row(
            f"Session: {platform}",
            f"[{style}]{status}[/{style}]",
            detail,
        )
    for filler_cls in sorted(all_fillers(), key=lambda c: c.platform):
        platform = filler_cls.platform
        status, detail = _check_platform_burn(platform)
        style = _STYLES[status]
        table.add_row(
            f"Auto-apply: {platform}",
            f"[{style}]{status}[/{style}]",
            detail,
        )
    out.print(table)
    return exit_code
