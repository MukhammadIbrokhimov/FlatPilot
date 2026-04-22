"""Cookie-seeding login command.

Opens a headed Playwright window pointed at a rental platform's login
page, waits for the user to log in by hand (including 2FA / captcha),
and on Enter persists the resulting cookies to
``~/.flatpilot/sessions/<platform>/state.json`` via the same
:func:`polite_session` helper that every headless runtime path uses. No
credentials — password, username — are ever observed or stored by
FlatPilot; only the cookies the platform sets on the user's own browser.

This command is the only path in FlatPilot that requires a visible
browser, so it is the only one that must run on the host rather than
inside Docker. Everything else — ``scrape``, ``run``, ``notify`` — keeps
using the shared on-disk state written here.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from flatpilot.scrapers import wg_gesucht as _wg
from flatpilot.scrapers.base import session_dir
from flatpilot.scrapers.session import (
    DEFAULT_USER_AGENT,
    SessionConfig,
    polite_session,
)
from flatpilot.scrapers.session import page as session_page


class UnknownPlatformError(ValueError):
    """No login recipe registered for the requested platform."""


class ContainerDetectedError(RuntimeError):
    """`flatpilot login` needs a visible browser and cannot run in Docker."""


@dataclass(frozen=True)
class _LoginSite:
    login_url: str
    warmup_url: str
    consent_selectors: tuple[str, ...]


_LOGIN_SITES: dict[str, _LoginSite] = {
    "wg-gesucht": _LoginSite(
        login_url=_wg.LOGIN_URL,
        warmup_url=_wg.WARMUP_URL,
        consent_selectors=_wg.CONSENT_SELECTORS,
    ),
}


def run_login(platform: str, console: Console) -> Path:
    """Drive a headed login for ``platform`` and persist cookies."""

    site = _resolve_platform(platform)
    _guard_container()

    state_path = session_dir(platform) / "state.json"

    config = SessionConfig(
        platform=platform,
        user_agent=DEFAULT_USER_AGENT,
        headless=False,
        # Headed interactive flow: let Chromium pick a window size that
        # fits the user's screen rather than pinning 1280×900, so the
        # login / register page is fully scrollable.
        no_viewport=True,
        warmup_url=site.warmup_url,
        consent_selectors=site.consent_selectors,
    )

    console.print(
        f"[bold]Opening browser for {platform}…[/bold]  "
        f"Cookies will save to [dim]{state_path}[/dim]"
    )

    with polite_session(config) as context, session_page(context) as pg:
        pg.goto(site.login_url, wait_until="domcontentloaded")
        console.print(
            "\n[bold]Log in by hand[/bold] in the browser window "
            "(2FA / captcha included).\n"
            "Press [bold]Enter[/bold] here when you can see your dashboard, "
            "or [bold]Ctrl-C[/bold] to abort (partial cookies may still be "
            "saved — that is safe, just re-run to finish).\n"
        )
        # Piped stdin closed without a newline — treat as immediate
        # 'done' rather than crashing.
        with contextlib.suppress(EOFError):
            input()

    expiry = _earliest_expiry(state_path)
    _print_success(console, platform, state_path, expiry)
    return state_path


def _resolve_platform(platform: str) -> _LoginSite:
    try:
        return _LOGIN_SITES[platform]
    except KeyError as exc:
        known = ", ".join(sorted(_LOGIN_SITES)) or "(none registered)"
        raise UnknownPlatformError(
            f"no login recipe for platform {platform!r}; known: {known}"
        ) from exc


def _guard_container() -> None:
    if not Path("/.dockerenv").exists():
        return
    raise ContainerDetectedError(
        "`flatpilot login` needs a visible browser and cannot run in "
        "Docker on macOS/Windows (no display forwarding). Run it on the "
        "host instead:\n\n"
        "  python3.11 -m venv .venv && source .venv/bin/activate\n"
        "  pip install -e '.[dev]'\n"
        "  playwright install chromium\n"
        "  flatpilot login <platform>\n\n"
        "The cookies written under ~/.flatpilot/sessions/ are read by the "
        "Docker runtime automatically via the bind mount."
    )


def _earliest_expiry(state_path: Path) -> datetime | None:
    """Return the earliest finite cookie expiry from ``state.json``, or None.

    storage_state encodes session cookies as ``expires: -1`` (Playwright)
    or ``0`` / absent (other tooling); all three mean 'no expiry to
    report'.
    """

    try:
        raw = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    earliest: float | None = None
    for cookie in raw.get("cookies", []):
        value = cookie.get("expires")
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        if earliest is None or value < earliest:
            earliest = value

    if earliest is None:
        return None
    try:
        return datetime.fromtimestamp(earliest, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _print_success(
    console: Console,
    platform: str,
    state_path: Path,
    expiry: datetime | None,
) -> None:
    if expiry is None:
        expiry_note = "[dim]session cookies only — no expiry reported[/dim]"
    else:
        delta_days = (expiry - datetime.now(UTC)).days
        expiry_note = (
            f"earliest cookie expires [bold]{expiry.date().isoformat()}[/bold] "
            f"(~{delta_days} days)"
        )
    console.print(
        f"[green]Saved session for {platform}[/green] · {expiry_note}"
    )
    console.print(f"[dim]state file: {state_path}[/dim]")
