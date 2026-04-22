"""WG-Gesucht Wohnungen scraper.

Parses the search results page for ``profile.city``. Listings are rendered
as ``<div class="wgg_card offer_list_item">`` cards; ads from third-party
partners (``housinganywhere_ad``, ``airbnb_ad``) do not carry the
``offer_list_item`` class and are filtered out by the CSS selector.

Phase 1 MVP only reads the search page — detail-page enrichment for
fields like ``online_since`` / full description can land later without
changing this module's public shape. Each card yields a :class:`Flat`
TypedDict with at least ``external_id`` / ``listing_url`` / ``title`` and
whatever numeric fields the card exposes (rent, rooms, size, district,
earliest-available date).

Anti-bot behaviour was validated in FlatPilot-h8ug (D0) — 20 consecutive
polls at 90s cadence, 100% success — so this scraper relies on
:mod:`flatpilot.scrapers.session` for the same Playwright fingerprint
and cookie-persistence path that the probe used.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import quote, urljoin

from flatpilot.profile import Profile
from flatpilot.scrapers import register
from flatpilot.scrapers.base import Flat
from flatpilot.scrapers.session import (
    DEFAULT_USER_AGENT,
    SessionConfig,
    check_rate_limit,
    polite_session,
)
from flatpilot.scrapers.session import (
    page as session_page,
)

logger = logging.getLogger(__name__)


HOST = "https://www.wg-gesucht.de"
WARMUP_URL = f"{HOST}/"
# `/mein-wg-gesucht.html` is the authenticated dashboard; unauthenticated
# visits are redirected to the login form, so the same URL works for both
# states and `flatpilot login` lands the user exactly where they need to
# start typing credentials.
LOGIN_URL = f"{HOST}/mein-wg-gesucht.html"

# WG-Gesucht search URLs require the internal numeric city ID:
#   /wohnungen-in-<CitySlug>.<city_id>.2.1.0.html
# (2 = Wohnung / full flat · 1 = zu vermieten / for rent · 0 = first page)
# A name-only URL 404s. Duplicated with scripts/wg_probe.py on purpose —
# scripts are shipped standalone.
CITY_IDS: dict[str, int] = {
    "Berlin": 8,
    "Hamburg": 55,
    "München": 90,
    "Munich": 90,
    "Köln": 73,
    "Cologne": 73,
    "Frankfurt am Main": 41,
    "Frankfurt": 41,
    "Stuttgart": 124,
    "Düsseldorf": 30,
    "Leipzig": 77,
    "Dortmund": 26,
    "Essen": 34,
    "Bremen": 17,
    "Dresden": 27,
    "Hannover": 58,
    "Nürnberg": 96,
    "Nuremberg": 96,
}

# One of these matches the WG-Gesucht / ConsentManager cookie banner. The
# probe observed #cmpwelcomebtnyes firing on a fresh container.
CONSENT_SELECTORS: tuple[str, ...] = (
    "#cmpwelcomebtnyes",
    "#cmpbntyestxt",
    "button:has-text('Einverstanden')",
    "button:has-text('Zustimmen')",
    "button:has-text('Alle akzeptieren')",
)


_RENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*€")
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²")
_ROOMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*-?\s*Zimmer", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


class UnknownCityError(ValueError):
    """Profile city has no WG-Gesucht city_id mapped."""


@register
class WGGesuchtScraper:
    platform: ClassVar[str] = "wg-gesucht"
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT

    def fetch_new(self, profile: Profile) -> Iterable[Flat]:
        city_id = CITY_IDS.get(profile.city)
        if city_id is None:
            raise UnknownCityError(
                f"{self.platform}: no city_id for {profile.city!r}; "
                f"extend CITY_IDS in {__name__}"
            )

        url = self._search_url(profile.city, city_id)
        config = SessionConfig(
            platform=self.platform,
            user_agent=self.user_agent,
            warmup_url=WARMUP_URL,
            consent_selectors=CONSENT_SELECTORS,
        )

        logger.info("%s: fetching %s", self.platform, url)
        with polite_session(config) as context, session_page(context) as pg:
            response = pg.goto(url, wait_until="domcontentloaded")
            if response is None:
                logger.warning("%s: null response from %s", self.platform, url)
                return
            check_rate_limit(response.status, self.platform)
            if response.status >= 400:
                logger.warning("%s: search returned HTTP %d", self.platform, response.status)
                return
            html = pg.content()

        flats = list(self._parse_listings(html))
        logger.info("%s: parsed %d listings from %s", self.platform, len(flats), profile.city)
        yield from flats

    @staticmethod
    def _search_url(city: str, city_id: int) -> str:
        slug = quote(city.replace(" ", "-"))
        return f"{HOST}/wohnungen-in-{slug}.{city_id}.2.1.0.html"

    @staticmethod
    def _parse_listings(html: str) -> Iterable[Flat]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select(".wgg_card.offer_list_item"):
            try:
                flat = _parse_card(card)
            except Exception as exc:
                logger.warning(
                    "wg-gesucht: skipping unparseable card (%s: %s)",
                    exc.__class__.__name__,
                    exc,
                )
                continue
            if flat is not None:
                yield flat


def _parse_card(card: Any) -> Flat | None:
    external_id = card.get("data-id") or card.get("data-asset_id")
    if not external_id:
        dom_id = card.get("id") or ""
        m = re.search(r"ad-(\d+)", dom_id)
        if not m:
            return None
        external_id = m.group(1)

    anchor = card.select_one("a[href]")
    if not anchor:
        return None
    href = anchor.get("href") or ""
    listing_url = urljoin(HOST, href)

    title_el = card.select_one("h2") or card.select_one("h3")
    title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
    if not title:
        title = anchor.get("title") or anchor.get_text(" ", strip=True) or "Untitled listing"

    flat: Flat = {
        "external_id": str(external_id),
        "listing_url": listing_url,
        "title": title,
    }

    # District: the URL path is /wohnungen-in-<City>-<District>.<id>.html.
    district = _district_from_url(href)
    if district:
        flat["district"] = district

    # Rent warm and size come from the two <b> elements in the row below
    # the title. Fall back to the whole card text if the structure shifts.
    card_text = card.get_text(" ", strip=True)
    bolds = [b.get_text(" ", strip=True) for b in card.find_all("b")]
    rent = _first_match(_RENT_RE, bolds) or _first_match(_RENT_RE, [card_text])
    if rent is not None:
        flat["rent_warm_eur"] = rent
    size = _first_match(_SIZE_RE, bolds) or _first_match(_SIZE_RE, [card_text])
    if size is not None:
        flat["size_sqm"] = size
    rooms = _first_match(_ROOMS_RE, [card_text])
    if rooms is not None:
        flat["rooms"] = rooms

    # Earliest available date — first dd.mm.yyyy in the card text.
    available_from = _first_date(card_text)
    if available_from:
        flat["available_from"] = available_from

    if "wbs" in card_text.lower():
        flat["requires_wbs"] = True

    return flat


def _district_from_url(href: str) -> str | None:
    # href looks like "/wohnungen-in-Berlin-Neukoelln.12345.html"
    m = re.search(r"/wohnungen-in-[^-/.]+-([^./]+)\.\d+\.", href)
    if not m:
        return None
    district = m.group(1).replace("-", " ").strip()
    return district or None


def _first_match(pattern: re.Pattern[str], texts: list[str]) -> float | None:
    for text in texts:
        m = pattern.search(text)
        if m:
            raw = m.group(1).replace(".", "").replace(",", ".")
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def _first_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, month, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{month}-{day}"
