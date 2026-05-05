"""ImmoScout24 saved-search RSS adapter (Phase 4 / K1).

ImmoScout24 fronts its search pages with aggressive anti-bot protection,
so HTML scraping is fragile. RSS is the supported integration: every
saved search in an ImmoScout24 account exposes an RSS feed URL that the
user copy-pastes into their profile under ``immoscout24_rss_urls``. This
adapter consumes those feeds via :mod:`feedparser` and yields :class:`Flat`
records the same shape as every other scraper, so the matcher and
notifier paths are unchanged.

RSS items expose a limited subset of fields. ``external_id`` is parsed
out of the expose URL (``/expose/<digits>``); ``listing_url`` and
``title`` are taken verbatim. ``rent_warm_eur`` / ``size_sqm`` /
``rooms`` are best-effort regex extractions from the title plus
description text — RSS does not carry structured fields for them, and
the user's saved search has already pre-filtered the feed against their
own ImmoScout24 criteria, so missing values here are rejected by the
matcher rather than guessed. Detail-page enrichment is intentionally
out of scope for this adapter; it would re-introduce the very HTML
fetch path the RSS route exists to avoid.

``supported_cities`` is ``None`` because the saved-search URL itself
encodes the city — the registry-level city gate has nothing useful to
do here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any, ClassVar

import httpx

from flatpilot.profile import Profile
from flatpilot.scrapers import register
from flatpilot.scrapers.base import Flat

logger = logging.getLogger(__name__)


HTTP_TIMEOUT_SEC = 15.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; FlatPilot/0.1; "
    "+https://github.com/MukhammadIbrokhimov/FlatPilot)"
)

# Headed-browser login constants. The RSS scraper above never opens a
# browser, but `flatpilot login immoscout24` does — and the `Connect`
# button on the Web UI's Connected-Accounts page (FlatPilot-8jx) calls
# the same engine. Cookies seeded here have no current consumer (the
# RSS adapter authenticates only via the URLs the user pastes from
# their account); the recipe exists so a future filler or
# authenticated-fetch path can adopt the same storage layout that
# WG-Gesucht and Kleinanzeigen use today.
HOST = "https://www.immobilienscout24.de"
WARMUP_URL = f"{HOST}/"
LOGIN_URL = f"{HOST}/anmelden.html"

# ImmoScout24 fronts cookies via Sourcepoint's TCF banner. The labels
# below cover the German variants Sourcepoint ships across A/B test
# arms; they should be revisited the first time `flatpilot login
# immoscout24` is run live and the actual button is captured.
CONSENT_SELECTORS: tuple[str, ...] = (
    "button[title='Alle akzeptieren']",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Einverstanden')",
    "button:has-text('Zustimmen')",
)

_EXPOSE_ID_RE = re.compile(r"/expose/(\d+)")
_RENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*€")
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²")
_ROOMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*-?\s*Zimmer", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@register
class ImmoScout24RSSScraper:
    platform: ClassVar[str] = "immoscout24"
    user_agent: ClassVar[str] = USER_AGENT
    supported_cities: ClassVar[frozenset[str] | None] = None

    def fetch_new(
        self,
        profile: Profile,
        *,
        known_external_ids: frozenset[str] = frozenset(),
    ) -> Iterable[Flat]:
        urls = list(profile.immoscout24_rss_urls)
        if not urls:
            logger.info(
                "%s: no RSS feed URLs configured in profile — skipping",
                self.platform,
            )
            return

        seen: set[str] = set()
        for url in urls:
            try:
                feed_bytes = _fetch_feed(url, self.user_agent)
            except httpx.HTTPError as exc:
                logger.warning(
                    "%s: failed to fetch %s (%s: %s) — skipping this feed",
                    self.platform,
                    url,
                    exc.__class__.__name__,
                    exc,
                )
                continue

            for flat in parse_feed(feed_bytes):
                ext = flat["external_id"]
                if ext in seen:
                    continue
                seen.add(ext)
                yield flat


def _fetch_feed(url: str, user_agent: str) -> bytes:
    with httpx.Client(
        timeout=HTTP_TIMEOUT_SEC,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
    ) as client:
        resp = client.get(url)
    resp.raise_for_status()
    return resp.content


def parse_feed(content: bytes | str) -> Iterable[Flat]:
    """Yield a :class:`Flat` per RSS ``<item>`` in ``content``.

    Module-level so parser tests can feed fixture XML without touching
    the network — same convention as
    :func:`flatpilot.scrapers.inberlinwohnen.parse_listings`.
    """
    import feedparser

    parsed = feedparser.parse(content)
    for entry in parsed.entries:
        try:
            flat = _entry_to_flat(entry)
        except Exception as exc:
            logger.warning(
                "immoscout24: skipping unparseable entry (%s: %s)",
                exc.__class__.__name__,
                exc,
            )
            continue
        if flat is not None:
            yield flat


def _entry_to_flat(entry: Any) -> Flat | None:
    link = (entry.get("link") or "").strip()
    external_id = _external_id(link, entry.get("id") or entry.get("guid") or "")
    if not external_id:
        return None
    if not link:
        return None

    title = (entry.get("title") or "").strip() or "Untitled listing"
    description_html = entry.get("summary") or entry.get("description") or ""
    description = _strip_html(description_html)

    flat: Flat = {
        "external_id": external_id,
        "listing_url": link,
        "title": title,
    }
    if description:
        flat["description"] = description

    haystack = f"{title}\n{description}"

    rent = _highest_match(_RENT_RE, haystack)
    if rent is not None:
        flat["rent_warm_eur"] = rent

    size = _first_match(_SIZE_RE, haystack)
    if size is not None:
        flat["size_sqm"] = size

    rooms = _first_match(_ROOMS_RE, haystack)
    if rooms is not None:
        flat["rooms"] = rooms

    available = _first_date(description) or _first_date(title)
    if available:
        flat["available_from"] = available

    if "wbs" in haystack.lower():
        flat["requires_wbs"] = True

    online = _published_iso(entry)
    if online:
        flat["online_since"] = online

    return flat


def _external_id(link: str, guid: str) -> str | None:
    for source in (link, guid):
        if not source:
            continue
        m = _EXPOSE_ID_RE.search(source)
        if m:
            return m.group(1)
    if guid and guid.isdigit():
        return guid
    return None


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = _HTML_TAG_RE.sub(" ", html)
    return " ".join(text.split())


def _parse_german_number(raw: str) -> float | None:
    cleaned = raw.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _first_match(pattern: re.Pattern[str], text: str) -> float | None:
    m = pattern.search(text)
    if not m:
        return None
    return _parse_german_number(m.group(1))


def _highest_match(pattern: re.Pattern[str], text: str) -> float | None:
    """Return the largest numeric match — RSS descriptions list cold/extra/warm
    side-by-side and the matcher needs the warm rent (the max of the three).
    """
    values = [_parse_german_number(m.group(1)) for m in pattern.finditer(text)]
    cleaned = [v for v in values if v is not None]
    return max(cleaned) if cleaned else None


def _first_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    day, month, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{month}-{day}"


def _published_iso(entry: Any) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        year, month, day = parsed.tm_year, parsed.tm_mon, parsed.tm_mday
    except AttributeError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"
