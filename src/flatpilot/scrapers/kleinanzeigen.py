"""Kleinanzeigen Mietwohnung scraper.

Parses the search-results page for ``profile.city`` (+ optional
``radius_km``). Each listing is rendered as
``<article class="aditem" data-adid="…" data-href="…">``; the scraper
extracts external id, title, district, size, rooms, and price from the
card without fetching the detail page.

Rent normalisation caveat
-------------------------
Kleinanzeigen search cards show a single price with no Warm/Kalt label —
in practice it is almost always the Kaltmiete. The matcher's
:func:`flatpilot.matcher.filters.filter_rent_band` only consults
``rent_warm_eur``, so leaving that field unset would silently reject
every Kleinanzeigen listing. For Phase 1 we therefore populate
``rent_warm_eur`` from the card price (consistent with the WG-Gesucht
scraper's convention) and leave ``rent_cold_eur`` unset — a future
detail-page enrichment pass can produce a true Kalt/Warm split. The
practical effect: the rent cap over-admits a few listings whose
real Warmmiete would exceed the cap; the user sees the honest figure
on the detail page and rejects them manually.

Anti-bot behaviour was validated in FlatPilot-3hu2 (D0 probe) — 158/158
polls at 90 s cadence, 100 % success — so this scraper reuses
:mod:`flatpilot.scrapers.session` with the same Firefox 121 fingerprint
and cookie persistence path.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import urljoin

from flatpilot.errors import UnknownCityError
from flatpilot.profile import Profile
from flatpilot.scrapers import register
from flatpilot.scrapers.base import Flat
from flatpilot.scrapers.block_detect import (
    ChallengeDetectedError,
    classify_content,
    has_captcha_iframe,
)
from flatpilot.scrapers.session import (
    DEFAULT_USER_AGENT,
    SessionConfig,
    check_rate_limit,
    polite_session,
)
from flatpilot.scrapers.session import (
    page as session_page,
)
from flatpilot.scrapers.ua_pool import pin_user_agent

logger = logging.getLogger(__name__)


HOST = "https://www.kleinanzeigen.de"
WARMUP_URL = f"{HOST}/"

# Kleinanzeigen search path:
#   /s-wohnung-mieten/<city-slug>/c203l<loc_id>[r<radius_km>]
# c203 is the Mietwohnung category; l<id> is the internal location ID.
# r<km> appends a radius filter (verified live: 20 km returns Berlin +
# surrounding suburbs). Values are added only after empirical verification —
# profile.city lookups that miss this table raise UnknownCityError so we
# never silently hit a 404 page.
CITY_IDS: dict[str, int] = {
    "Berlin": 3331,
}

CONSENT_SELECTORS: tuple[str, ...] = (
    "#gdpr-banner-accept",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Einverstanden')",
    "button:has-text('Zustimmen')",
)


_RENT_RE = re.compile(r"(\d+(?:\.\d{3})*(?:,\d+)?)\s*€")
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²")
# Kleinanzeigen cards use "2 Zi.", "3 Zi.", or occasionally "3-Zimmer".
_ROOMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:Zi\.?|Zimmer)", re.IGNORECASE)
_PLZ_DISTRICT_RE = re.compile(r"^(\d{5})\s+(.+)$")


@register
class KleinanzeigenScraper:
    platform: ClassVar[str] = "kleinanzeigen"
    # Kept for protocol compatibility. The actual UA used per call is
    # picked from the pool via resolve_user_agent() so repeated fresh
    # sessions don't all share one fingerprint.
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT
    # Berlin-only today — extending requires both an entry in CITY_IDS
    # above AND adding the city here so the orchestrator stops gating.
    supported_cities: ClassVar[frozenset[str] | None] = frozenset(CITY_IDS.keys())

    def resolve_user_agent(self) -> str:
        return pin_user_agent(self.platform)

    def fetch_new(
        self,
        profile: Profile,
        *,
        known_external_ids: frozenset[str] = frozenset(),
    ) -> Iterable[Flat]:
        loc_id = CITY_IDS.get(profile.city)
        if loc_id is None:
            raise UnknownCityError(
                f"{self.platform}: no location_id for {profile.city!r}; "
                f"extend CITY_IDS in {__name__}"
            )

        url = self._search_url(profile.city, loc_id, profile.radius_km)
        config = SessionConfig(
            platform=self.platform,
            user_agent=self.resolve_user_agent(),
            warmup_url=WARMUP_URL,
            consent_selectors=CONSENT_SELECTORS,
            stealth=True,
        )

        logger.info("%s: fetching %s", self.platform, url)
        with polite_session(config) as context, session_page(context) as pg:
            response = pg.goto(url, wait_until="domcontentloaded")
            if response is None:
                logger.warning("%s: null response from %s", self.platform, url)
                return
            check_rate_limit(response.status, self.platform)
            if response.status >= 400:
                logger.warning(
                    "%s: search returned HTTP %d", self.platform, response.status
                )
                return
            html = _handle_response(pg, city=profile.city)

        flats = list(parse_listings(html))
        logger.info(
            "%s: parsed %d listings from %s", self.platform, len(flats), profile.city
        )
        yield from flats

    @staticmethod
    def _search_url(city: str, loc_id: int, radius_km: int | None) -> str:
        slug = city.strip().lower().replace(" ", "-")
        suffix = f"r{radius_km}" if radius_km and radius_km > 0 else ""
        return f"{HOST}/s-wohnung-mieten/{slug}/c203l{loc_id}{suffix}"


def _handle_response(page: Any, *, city: str) -> str:
    """Classify the current ``page`` and return its HTML on ok/unknown.

    Raises :class:`ChallengeDetectedError` on a captcha iframe,
    Cloudflare soft challenge, or hard block keyword. The ``unknown``
    classifier outcome — thin body or city not mentioned — is
    deliberately passed through: a legitimate empty search is thin by
    definition and must not trigger a cool-off.
    """
    if has_captcha_iframe(page):
        raise ChallengeDetectedError(f"kleinanzeigen: captcha iframe present for {city}")

    html = page.content()
    outcome = classify_content(html, city=city)
    if outcome in ("challenge_cloudflare", "block_keyword"):
        raise ChallengeDetectedError(f"kleinanzeigen: {outcome} detected for {city}")
    # ok and unknown both return the HTML; parse_listings yields zero
    # on an empty page without error.
    return html


def parse_listings(html: str) -> Iterable[Flat]:
    """Yield a :class:`Flat` per ``article.aditem`` in ``html``.

    Exposed module-level so parser tests can feed fixture HTML without
    constructing a scraper instance.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("article.aditem"):
        try:
            flat = _parse_card(card)
        except Exception as exc:
            logger.warning(
                "kleinanzeigen: skipping unparseable card (%s: %s)",
                exc.__class__.__name__,
                exc,
            )
            continue
        if flat is not None:
            yield flat


def _parse_card(card: Any) -> Flat | None:
    external_id = card.get("data-adid")
    if not external_id:
        return None

    href = card.get("data-href") or ""
    if not href:
        anchor = card.select_one("a[href]")
        href = anchor.get("href", "") if anchor else ""
    if not href:
        return None
    listing_url = urljoin(HOST, href)

    title_el = card.select_one(".aditem-main--middle h2 a") or card.select_one("h2 a")
    title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
    if not title:
        title = "Untitled listing"

    flat: Flat = {
        "external_id": str(external_id),
        "listing_url": listing_url,
        "title": title,
    }

    top_left = card.select_one(".aditem-main--top--left")
    if top_left:
        location = _clean(top_left.get_text(" ", strip=True))
        m = _PLZ_DISTRICT_RE.match(location)
        if m:
            flat["address"] = location
            flat["district"] = m.group(2).strip()
        elif location:
            flat["address"] = location

    # The tags block holds size + rooms separated by middle-dots and lots
    # of whitespace. Normalise to a single-spaced string before regexing.
    tags_el = card.select_one(".aditem-main--middle--tags")
    tag_text = _clean(tags_el.get_text(" ", strip=True)) if tags_el else ""

    size = _first_float(_SIZE_RE, tag_text)
    if size is not None:
        flat["size_sqm"] = size

    rooms = _first_float(_ROOMS_RE, tag_text)
    if rooms is None:
        # Fallback: some titles carry "2-Zi.-Wohnung" but not the tags.
        rooms = _first_float(_ROOMS_RE, title)
    if rooms is not None:
        flat["rooms"] = rooms

    # Pick the primary price element explicitly — its sibling `.old-price`
    # holds the crossed-out original and would otherwise leak into a
    # container-level get_text().
    price_el = card.select_one(".aditem-main--middle--price-shipping--price")
    if price_el:
        price = _first_float(_RENT_RE, _clean(price_el.get_text(" ", strip=True)))
        if price is not None:
            flat["rent_warm_eur"] = price

    card_text = _clean(card.get_text(" ", strip=True)).lower()
    if "wbs" in card_text:
        flat["requires_wbs"] = True

    return flat


def _clean(text: str) -> str:
    return " ".join(text.split())


def _first_float(pattern: re.Pattern[str], text: str) -> float | None:
    m = pattern.search(text)
    if not m:
        return None
    raw = m.group(1)
    # German number format: "1.730" = 1730; "86,01" = 86.01. Strip
    # thousand-dots then swap the decimal comma.
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None
