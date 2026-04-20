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
    bot = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if bot and chat:
        return "OK", "BOT_TOKEN + CHAT_ID set"
    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", bot), ("TELEGRAM_CHAT_ID", chat))
        if not value
    ]
    return "optional", f"missing: {', '.join(missing)}"


def _check_smtp() -> tuple[str, str]:
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"]
    missing = [k for k in keys if not os.environ.get(k)]
    if not missing:
        return "OK", "HOST/PORT/USER/PASSWORD/FROM set"
    return "optional", f"missing: {', '.join(missing)}"


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
    """
    config.load_env()
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
