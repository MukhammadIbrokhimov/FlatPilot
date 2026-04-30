"""Integration tests for the Kleinanzeigen scraper's anti-bot wiring.

These tests cover the thin layer between ``fetch_new`` and the
session / classifier primitives. The parsing path is already covered
separately by ``parse_listings`` usage in PR #20; here we verify:

- _handle_response with fixture HTML returns the body unchanged.
- _handle_response with an injected captcha iframe raises
  ChallengeDetectedError before calling content().
- _handle_response with a Cloudflare challenge body raises.
- _handle_response with a block keyword body raises.
- _handle_response with an "unknown" outcome returns the body
  (lets parse_listings yield 0 flats — a valid empty pass).
- KleinanzeigenScraper.user_agent still resolves from the UA pool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "kleinanzeigen" / "search.html"


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, *, html: str, iframes: tuple[str, ...] = ()) -> None:
        self._html = html
        self._iframes = iframes
        self.content_calls = 0

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(1 if selector in self._iframes else 0)

    def content(self) -> str:
        self.content_calls += 1
        return self._html


def test_handle_response_returns_body_for_real_search_page() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response

    html = FIXTURE.read_text()
    page = _FakePage(html=html)
    assert _handle_response(page, city="Berlin") == html
    assert page.content_calls == 1


def test_handle_response_raises_on_captcha_iframe_before_content() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response
    from flatpilot.scrapers.session import ChallengeDetectedError

    page = _FakePage(
        html="ignored",
        iframes=("iframe[src*='challenges.cloudflare.com']",),
    )
    with pytest.raises(ChallengeDetectedError):
        _handle_response(page, city="Berlin")
    assert page.content_calls == 0  # iframe check fires before content()


def test_handle_response_raises_on_cloudflare_interstitial() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response
    from flatpilot.scrapers.session import ChallengeDetectedError

    html = "<html><body><h1>Just a moment...</h1></body></html>"
    with pytest.raises(ChallengeDetectedError):
        _handle_response(_FakePage(html=html), city="Berlin")


def test_handle_response_raises_on_block_keyword() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response
    from flatpilot.scrapers.session import ChallengeDetectedError

    html = "<html><body>Access denied.</body></html>"
    with pytest.raises(ChallengeDetectedError):
        _handle_response(_FakePage(html=html), city="Berlin")


def test_handle_response_passes_through_unknown() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response

    html = "<html><body>empty page</body></html>"
    assert _handle_response(_FakePage(html=html), city="Berlin") == html


def test_scraper_user_agent_comes_from_pool(tmp_db) -> None:
    from flatpilot.scrapers.kleinanzeigen import KleinanzeigenScraper
    from flatpilot.scrapers.ua_pool import POOL

    scraper = KleinanzeigenScraper()
    assert scraper.resolve_user_agent() in POOL


def test_fetch_new_uses_pinned_ua_and_stealth(tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile) -> None:
    """fetch_new hands a stealth-enabled SessionConfig with the pinned UA to polite_session."""
    from conftest import make_session_fakes
    from flatpilot.scrapers import kleinanzeigen as kz
    from flatpilot.scrapers.ua_pool import pin_user_agent

    captured: dict[str, Any] = {}
    polite_fake, page_fake = make_session_fakes(
        default_html=FIXTURE.read_text(),
        captured=captured,
    )
    monkeypatch.setattr(kz, "polite_session", polite_fake)
    monkeypatch.setattr(kz, "session_page", page_fake)

    pinned = pin_user_agent("kleinanzeigen")
    scraper = kz.KleinanzeigenScraper()
    list(scraper.fetch_new(berlin_profile))  # drain generator

    cfg = captured["config"]
    assert cfg.platform == "kleinanzeigen"
    assert cfg.user_agent == pinned
    assert cfg.stealth is True
