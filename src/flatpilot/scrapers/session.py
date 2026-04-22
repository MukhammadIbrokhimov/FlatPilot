"""Playwright session helper for polite scraping.

Wraps browser / context lifecycle for Phase-1 Playwright scrapers and
persists cookies + localStorage between runs at
``~/.flatpilot/sessions/<platform>/state.json`` so the consent banner and
anti-bot fingerprints only have to be established once.

Scope:

- Load prior storage state at session open; save on every ``page()`` exit
  and once more on session close so an abrupt kill doesn't lose cookies.
- One-shot warm-up: visit a configured ``warmup_url`` on session open and
  click the first matching consent-banner selector. Proven out by the D0
  probe's 30-minute clean run — ``#cmpwelcomebtnyes`` handles WG-Gesucht.
- Realistic defaults: Firefox-style UA, de-DE locale, Europe/Berlin
  timezone, 1280×900 viewport. The D0 probe survived with exactly this
  fingerprint so the scraper inherits it.
- :func:`check_rate_limit`: scrapers call this after every ``page.goto``
  response; a 429 / 503 raises :class:`RateLimitedError` and the orchestrator's
  ``--watch`` loop treats it as "skip this pass, try again after the
  configured interval" — the natural backoff.

The ``--watch`` loop interval itself lives in the CLI (D4); this module
only knows about per-request behaviour.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from flatpilot.scrapers.base import session_dir

logger = logging.getLogger(__name__)


# D4's `flatpilot scrape --watch --interval SECS` defaults to this. The D0
# probe validated 90 s over 30 min with zero captchas; 120 s is the
# conservative production default — same policy as the beads spec.
DEFAULT_INTERVAL_SEC: float = 120.0

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
)
DEFAULT_VIEWPORT: dict[str, int] = {"width": 1280, "height": 900}
DEFAULT_NAV_TIMEOUT_MS: int = 30_000
CONSENT_CLICK_TIMEOUT_MS: int = 2_000
WARM_UP_SETTLE_SEC: float = 2.0


class RateLimitedError(RuntimeError):
    """HTTP 429 / 503 from the target platform; abort the current pass."""


@dataclass
class SessionConfig:
    """Config for :func:`polite_session`.

    ``platform`` drives the on-disk session path; ``warmup_url`` +
    ``consent_selectors`` drive the one-shot banner dismissal. Everything
    else has sensible defaults the D0 probe already validated.
    """

    platform: str
    user_agent: str = DEFAULT_USER_AGENT
    locale: str = "de-DE"
    timezone_id: str = "Europe/Berlin"
    headless: bool = True
    viewport: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_VIEWPORT))
    # Set for headed interactive flows (e.g. `flatpilot login`) so the
    # window is free to match the physical screen and the user can
    # resize / scroll. Scrape paths keep the pinned viewport because the
    # D0 probe validated that fingerprint.
    no_viewport: bool = False
    warmup_url: str | None = None
    consent_selectors: tuple[str, ...] = ()
    nav_timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS


def check_rate_limit(status: int, platform: str) -> None:
    """Raise :class:`RateLimitedError` on a 429 / 503 HTTP status."""
    if status in (429, 503):
        raise RateLimitedError(f"{platform}: HTTP {status}")


@contextmanager
def polite_session(config: SessionConfig) -> Iterator[Any]:
    """Yield a Playwright ``BrowserContext`` with persisted state + warm-up.

    The yielded value is the Playwright ``BrowserContext`` — callers
    typically use :func:`page` (below) instead of driving it directly, but
    exposing the context lets scrapers open multiple pages in parallel
    within a single pass if they want to.

    The session saves storage state on exit and closes the browser
    cleanly even if the caller raises.
    """
    from playwright.sync_api import sync_playwright

    state_path = session_dir(config.platform) / "state.json"
    storage = str(state_path) if state_path.exists() else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.headless)
        try:
            context_kwargs: dict[str, Any] = {
                "user_agent": config.user_agent,
                "locale": config.locale,
                "timezone_id": config.timezone_id,
                "storage_state": storage,
            }
            if config.no_viewport:
                context_kwargs["no_viewport"] = True
            else:
                context_kwargs["viewport"] = config.viewport
            context = browser.new_context(**context_kwargs)
            try:
                if config.warmup_url:
                    _warm_up(context, config)
                    _save_state(context, state_path)
                yield context
            finally:
                try:
                    _save_state(context, state_path)
                except Exception as exc:
                    logger.warning("%s: final state save failed: %s", config.platform, exc)
                context.close()
        finally:
            browser.close()


@contextmanager
def page(context: Any, *, nav_timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS) -> Iterator[Any]:
    """Yield a fresh Playwright ``Page`` and close it on exit.

    Scrapers should use this rather than ``context.new_page()`` directly
    so the page is always closed, even on exception.
    """
    new_page = context.new_page()
    new_page.set_default_navigation_timeout(nav_timeout_ms)
    try:
        yield new_page
    finally:
        new_page.close()


def _warm_up(context: Any, config: SessionConfig) -> None:
    assert config.warmup_url is not None
    warm_page = context.new_page()
    try:
        warm_page.set_default_navigation_timeout(config.nav_timeout_ms)
        warm_page.goto(config.warmup_url, wait_until="domcontentloaded")
        for selector in config.consent_selectors:
            try:
                btn = warm_page.locator(selector).first
                if btn.is_visible(timeout=CONSENT_CLICK_TIMEOUT_MS):
                    btn.click()
                    logger.info(
                        "%s: accepted consent banner via %s", config.platform, selector
                    )
                    break
            except Exception:
                continue
        time.sleep(WARM_UP_SETTLE_SEC)
    finally:
        warm_page.close()


def _save_state(context: Any, path: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
