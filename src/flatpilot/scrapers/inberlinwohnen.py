"""Inberlinwohnen.de Wohnungsfinder scraper.

Aggregates Berlin's six municipal-housing companies (degewo, Gesobau,
Howoge, Stadt und Land, WBM, Gewobag) into one Wohnungen feed at
https://inberlinwohnen.de/wohnungsfinder/. Berlin-only by design — the
registry-level city gate (see ``flatpilot.scrapers.supports_city``)
filters this scraper out for any other ``profile.city``.

Card DOM (verified against tests/fixtures/inberlinwohnen/search.html,
captured 2026-04-26):

- Each apartment is a ``<div id="apartment-{flat_id}" class="mb-3">``.
- Inside ``.list__details`` there is a ``<span class="text-xl block">``
  with the title, an ``<a wire:click="processDeeplink" href="...">``
  pointing off-site to the operator's expose, and one or two ``<dl>``
  blocks of ``<dt>label:</dt><dd>value</dd>`` pairs covering address,
  rooms, area, prices, occupation date, and the WBS flag.
- Numbers use German formatting: thousand-dot, decimal-comma
  (``1.594,52`` = 1594.52). Areas append ``m²``; prices append ``€``.

Phase 1 MVP scrapes page 1 only (~10 listings). Pagination can land
later without changing the public shape.

WBS scope note: the search-results card exposes only the binary
``WBS: erforderlich`` / ``WBS: nicht erforderlich`` flag — no
per-listing size or income tier. The bead (FlatPilot-rqks) asked
for size + income extraction "when stated"; on this aggregator the
tier values live on the operator's expose page (off-site deeplink)
and are intentionally out of scope for the search-page parser. If
the page later starts surfacing tier rows in the dl, extend
``_parse_card`` to write ``wbs_size_category`` /
``wbs_income_category`` from the matching dt/dd entries.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import urljoin

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


HOST = "https://inberlinwohnen.de"
SEARCH_URL = f"{HOST}/wohnungsfinder/"
WARMUP_URL = f"{HOST}/"

# Cookie-banner buttons observed on inberlinwohnen.de — the page uses a
# generic GDPR banner. Selectors are matched in order; the first one
# that becomes visible is clicked.
CONSENT_SELECTORS: tuple[str, ...] = (
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Einverstanden')",
    "button:has-text('Zustimmen')",
)

# Pagination constants. The Wohnungsfinder feed uses server-rendered
# `?page=N` URLs (verified empirically — page 99 returns a fully-rendered
# page with 0 cards and `keine Ergebnisse` markers; page 22 is currently
# the last page with 2 listings). On a fresh install the full inventory
# is ~22 pages × 10 listings = ~220 flats; MAX_PAGES caps that with
# headroom for organic growth without changing code.
MAX_PAGES: int = 30

# Sleep between page fetches inside one polite_session. Distinct from
# session.DEFAULT_INTERVAL_SEC (120s, inter-pass) — this is intra-pass
# politeness so 30 sequential page loads spread over ~45s rather than
# spiking the host with parallel-feeling requests. 1.5s is conservative;
# WG-Gesucht's ~2s rule of thumb informs the choice.
POLITE_PAGE_DELAY_SEC: float = 1.5


_APARTMENT_ID_RE = re.compile(r"^apartment-(\d+)$")
# German number: optional thousand-dot groups, optional decimal comma.
_NUMBER_RE = re.compile(r"\d+(?:\.\d{3})*(?:,\d+)?")
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


@register
class InBerlinWohnenScraper:
    platform: ClassVar[str] = "inberlinwohnen"
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT
    supported_cities: ClassVar[frozenset[str] | None] = frozenset({"Berlin"})

    def fetch_new(
        self,
        profile: Profile,
        *,
        known_external_ids: frozenset[str] = frozenset(),
    ) -> Iterable[Flat]:
        config = SessionConfig(
            platform=self.platform,
            user_agent=self.user_agent,
            warmup_url=WARMUP_URL,
            consent_selectors=CONSENT_SELECTORS,
        )

        all_flats: list[Flat] = []
        with polite_session(config) as context, session_page(context) as pg:
            for page_num in range(1, MAX_PAGES + 1):
                if page_num == 1:
                    url = SEARCH_URL
                else:
                    url = f"{SEARCH_URL}?page={page_num}"
                    time.sleep(POLITE_PAGE_DELAY_SEC)

                logger.info("%s: fetching %s", self.platform, url)
                response = pg.goto(url, wait_until="domcontentloaded")
                if response is None:
                    logger.warning("%s: null response from %s", self.platform, url)
                    break
                check_rate_limit(response.status, self.platform)
                if response.status >= 400:
                    logger.warning(
                        "%s: page %d returned HTTP %d",
                        self.platform,
                        page_num,
                        response.status,
                    )
                    break
                html = pg.content()

                page_flats = list(parse_listings(html))
                logger.info(
                    "%s: page %d → %d listings",
                    self.platform,
                    page_num,
                    len(page_flats),
                )
                if not page_flats:
                    # Empty page — past the end of the inventory.
                    break
                all_flats.extend(page_flats)

                page_ids = {f["external_id"] for f in page_flats}
                if page_ids and page_ids.issubset(known_external_ids):
                    # Steady state: every ID on this page is already in
                    # the DB, so older pages will be too. Stop early.
                    logger.info(
                        "%s: page %d fully known — stopping pagination",
                        self.platform,
                        page_num,
                    )
                    break

        logger.info("%s: parsed %d listings total", self.platform, len(all_flats))
        yield from all_flats


def parse_listings(html: str) -> Iterable[Flat]:
    """Yield a :class:`Flat` per ``#apartment-<id>`` block in ``html``.

    Exposed module-level so parser tests can feed fixture HTML without
    constructing a scraper instance — same convention as
    ``flatpilot.scrapers.kleinanzeigen.parse_listings``.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select('div[id^="apartment-"]'):
        try:
            flat = _parse_card(card)
        except Exception as exc:
            logger.warning(
                "inberlinwohnen: skipping unparseable card (%s: %s)",
                exc.__class__.__name__,
                exc,
            )
            continue
        if flat is not None:
            yield flat


def _parse_card(card: Any) -> Flat | None:
    dom_id = card.get("id") or ""
    m = _APARTMENT_ID_RE.match(dom_id)
    if not m:
        return None
    external_id = m.group(1)

    deeplink = card.find("a", attrs={"wire:click": "processDeeplink"})
    if deeplink is None:
        deeplink = card.select_one('.list__details a[target="_blank"][href]')
    if deeplink is None or not deeplink.get("href"):
        return None
    listing_url = urljoin(HOST, deeplink["href"])

    title_el = card.select_one(".list__details > span.text-xl") or card.select_one(
        ".list__details span.block"
    )
    title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
    if not title:
        title = "Untitled listing"

    flat: Flat = {
        "external_id": external_id,
        "listing_url": listing_url,
        "title": title,
    }

    details = _extract_dl(card)

    address = details.get("Adresse")
    if address:
        flat["address"] = address
        district = _district_from_address(address)
        if district:
            flat["district"] = district

    rooms = _german_number(details.get("Zimmeranzahl") or "")
    if rooms is not None:
        flat["rooms"] = rooms

    size = _german_number(details.get("Wohnfläche") or "")
    if size is not None:
        flat["size_sqm"] = size

    cold = _german_number(details.get("Kaltmiete") or "")
    if cold is not None:
        flat["rent_cold_eur"] = cold

    extra = _german_number(details.get("Nebenkosten") or "")
    if extra is not None:
        flat["extra_costs_eur"] = extra

    total = _german_number(details.get("Gesamtmiete") or "")
    if total is not None:
        flat["rent_warm_eur"] = total

    available = _iso_date(details.get("Bezugsfertig ab") or "")
    if available:
        flat["available_from"] = available

    online = _iso_date(details.get("Eingestellt am") or "")
    if online:
        flat["online_since"] = online

    wbs_text = details.get("WBS")
    if wbs_text is None:
        # No WBS row on this card — default to True. Inberlinwohnen lists
        # municipal stock; cards without an explicit flag are exceptional
        # and we'd rather over-report and let the matcher reject than
        # silently miss a WBS-required listing.
        flat["requires_wbs"] = True
    else:
        flat["requires_wbs"] = "nicht erforderlich" not in wbs_text.lower()

    return flat


def _extract_dl(card: Any) -> dict[str, str]:
    """Return a label→value map from every ``<dl>`` inside ``.list__details``.

    Each ``<dl>`` holds parallel ``<dt>`` / ``<dd>`` lists (no nesting).
    The first ``<dt>`` in document order maps to the first ``<dd>``,
    and so on. Earlier labels win on duplicates — the page never
    repeats a label across columns in practice but we guard for it.
    """
    out: dict[str, str] = {}
    for dl in card.select(".list__details dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds, strict=False):
            label = dt.get_text(" ", strip=True).rstrip(":").strip()
            value = " ".join(dd.get_text(" ", strip=True).split())
            if label and label not in out:
                out[label] = value
    return out


def _district_from_address(address: str) -> str | None:
    """``"Am Falkenberg 11M, 12524, Treptow-Köpenick"`` → ``"Treptow-Köpenick"``."""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return None
    tail = parts[-1]
    return tail or None


def _german_number(text: str) -> float | None:
    """Parse a German-formatted number out of ``text`` and return it as float.

    ``"3,0"``        → 3.0
    ``"94,35 m²"``   → 94.35
    ``"1.594,52 €"`` → 1594.52
    Returns ``None`` if no number can be located.
    """
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    raw = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _iso_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, month, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{month}-{day}"
