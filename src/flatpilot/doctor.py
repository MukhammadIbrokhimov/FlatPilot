"""Health checks for ``flatpilot doctor``.

Each check is a function returning ``(status, detail)``. Status is one of
``"OK"``, ``"MISSING"`` (required check failed — affects exit code), or
``"optional"`` (nice to have, never fails the exit code).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.table import Table

from flatpilot import config
from flatpilot.profile import load_profile

CheckFn = Callable[[], "tuple[str, str]"]


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


def _check_telegram() -> tuple[str, str]:
    # The bot token is env-based (var name chosen in the profile, default
    # TELEGRAM_BOT_TOKEN) but the chat_id lives in profile.json — checking
    # for a TELEGRAM_CHAT_ID env var as the old implementation did would
    # always report 'missing' for correctly configured users.
    profile = load_profile()
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
    profile = load_profile()
    if profile is None:
        return "optional", "no profile — run `flatpilot init`"
    if not profile.notifications.email.enabled:
        return "optional", "disabled in profile"
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        return "optional", f"enabled but missing: {', '.join(missing)}"
    return "OK", "HOST/PORT/USER/PASSWORD/FROM set"


CHECKS: list[tuple[str, CheckFn]] = [
    ("Python >= 3.11", _check_python),
    ("App directory", _check_app_dir),
    ("Playwright Chromium", _check_playwright),
    ("Telegram creds", _check_telegram),
    ("SMTP creds", _check_smtp),
]


_STYLES = {"OK": "green", "MISSING": "red", "optional": "yellow"}


def run(console: Console | None = None) -> int:
    """Run every check, print a summary table, return a CLI exit code.

    Returns 0 if every required check passed, 1 otherwise. Optional checks
    that come back missing do not affect the exit code — they're reminders.

    ``config.load_env()`` is now called in the CLI ``_bootstrap`` callback
    so every command sees ``~/.flatpilot/.env`` — no need to repeat it here.
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
    out.print(table)
    return exit_code
