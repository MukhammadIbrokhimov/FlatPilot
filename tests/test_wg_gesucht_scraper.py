"""Unit coverage for scrapers.wg_gesucht — URL builder + parser."""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

# UnknownCityError is defined in flatpilot.errors and re-imported by
# scrapers.wg_gesucht (wg_gesucht.py:29).
from flatpilot.errors import UnknownCityError
from flatpilot.profile import Profile
from flatpilot.scrapers.wg_gesucht import WGGesuchtScraper, _parse_card

FIXTURE = Path(__file__).parent / "fixtures" / "wg_gesucht" / "search_results.html"


def test_search_url_for_berlin():
    # Berlin's CITY_IDS entry is 8 (wg_gesucht.py:60). _search_url is a
    # pure staticmethod so we can pin the full string.
    url = WGGesuchtScraper._search_url("Berlin", 8)
    assert url == "https://www.wg-gesucht.de/wohnungen-in-Berlin.8.2.1.0.html"


def test_search_url_substitutes_spaces_with_hyphens():
    url = WGGesuchtScraper._search_url("Frankfurt am Main", 41)
    assert "wohnungen-in-Frankfurt-am-Main" in url
    assert ".41.2.1.0.html" in url


def test_parse_listings_excludes_ad_cards():
    html = FIXTURE.read_text()
    # Sanity: fixture must contain the ad row that the test claims is
    # being excluded; otherwise the test's "exclusion" claim is vacuous.
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select(".wgg_card")) >= 3, "fixture missing ad card"
    assert (
        len(soup.select(".wgg_card.offer_list_item")) == 2
    ), "fixture must have exactly 2 offer cards"

    # Flat is a TypedDict (scrapers/base.py:31), so use ["key"] access.
    flats = list(WGGesuchtScraper._parse_listings(html))
    assert len(flats) == 2
    ids = {f["external_id"] for f in flats}
    assert ids == {"111111", "222222"}


def test_parse_listings_extracts_basic_fields():
    html = FIXTURE.read_text()
    flats = list(WGGesuchtScraper._parse_listings(html))
    by_id = {f["external_id"]: f for f in flats}

    assert by_id["111111"]["title"]  # non-empty
    assert by_id["111111"]["listing_url"].endswith(".111111.html")
    assert by_id["111111"]["listing_url"].startswith("https://www.wg-gesucht.de/")
    assert by_id["111111"].get("district", "").lower() == "mitte"
    assert by_id["111111"].get("rent_warm_eur") == 900.0
    assert by_id["111111"].get("size_sqm") == 60.0
    assert by_id["111111"].get("rooms") == 2.0
    assert by_id["111111"].get("available_from") == "2026-06-01"

    assert by_id["222222"].get("rent_warm_eur") == 1450.0
    assert by_id["222222"].get("requires_wbs") is True


def test_parse_card_returns_none_on_missing_id():
    soup = BeautifulSoup(
        '<div class="wgg_card offer_list_item"></div>', "html.parser"
    )
    card = soup.find("div")
    assert _parse_card(card) is None


def test_parse_card_returns_none_when_no_anchor():
    soup = BeautifulSoup(
        '<div class="wgg_card offer_list_item" data-id="42"></div>',
        "html.parser",
    )
    card = soup.find("div")
    assert _parse_card(card) is None


def test_fetch_new_raises_for_unsupported_city():
    scraper = WGGesuchtScraper()
    profile = Profile.load_example().model_copy(update={"city": "Vladivostok"})

    with pytest.raises(UnknownCityError):
        # Generator must be drained for the body to execute.
        list(scraper.fetch_new(profile, known_external_ids=frozenset()))
