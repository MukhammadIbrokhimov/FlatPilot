"""Tests for the inberlinwohnen.de Wohnungsfinder scraper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "inberlinwohnen" / "search.html"


def test_parse_listings_count_matches_apartment_blocks() -> None:
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    flats = list(parse_listings(html))
    # Fixture was captured 2026-04-26 from page 1 of the live feed; it
    # contains 10 apartment-* blocks. If the fixture is replaced, update
    # this expectation alongside it.
    assert len(flats) == 10


def test_parse_listings_first_card_required_fields() -> None:
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    flats = list(parse_listings(html))
    first = flats[0]

    assert first["external_id"] == "16214"
    assert first["listing_url"] == (
        "https://www.degewo.de/de/properties/W1100-42014-0033-0502.html"
    )
    assert first["title"] == "Erstbezug in Grünau - Dachterrasse inklusive!"


def test_parse_listings_first_card_numeric_and_date_fields() -> None:
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    first = list(parse_listings(html))[0]

    assert first["rooms"] == 3.0
    assert first["size_sqm"] == 94.35
    assert first["rent_cold_eur"] == 1594.52
    assert first["extra_costs_eur"] == 209.46
    assert first["rent_warm_eur"] == 1998.34  # Gesamtmiete = Kaltmiete + Nebenkosten
    assert first["available_from"] == "2026-04-26"
    assert first["online_since"] == "2026-04-26"


def test_parse_listings_first_card_address_and_district() -> None:
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    first = list(parse_listings(html))[0]

    assert first["address"] == "Am Falkenberg 11M, 12524, Treptow-Köpenick"
    assert first["district"] == "Treptow-Köpenick"


def test_parse_listings_first_card_wbs_not_required() -> None:
    """First fixture card has 'WBS: nicht erforderlich' → requires_wbs=False."""
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    first = list(parse_listings(html))[0]

    assert first["requires_wbs"] is False


def test_parse_listings_some_card_wbs_required() -> None:
    """At least one fixture card carries 'WBS: erforderlich' → requires_wbs=True.

    The fixture spans 10 cards from six municipal landlords; in practice
    multiple cards require a WBS. This test guards the parser branch
    that reads the affirmative form of the label.
    """
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    flats = list(parse_listings(html))
    assert any(flat.get("requires_wbs") is True for flat in flats), (
        "expected at least one fixture card with WBS: erforderlich"
    )


def test_parse_listings_every_card_has_required_fields() -> None:
    """Every emitted Flat carries external_id, listing_url, title — the
    three fields the orchestrator requires (see Flat TypedDict in
    ``flatpilot.scrapers.base``)."""
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    flats = list(parse_listings(html))
    assert flats, "fixture should produce at least one flat"
    for f in flats:
        assert f["external_id"]
        assert f["listing_url"].startswith(("http://", "https://"))
        assert f["title"]


def test_parse_listings_skips_unrelated_apartment_id_divs() -> None:
    """A loose ``<div id="apartment-foo">`` (non-numeric) must not become a Flat."""
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = (
        "<html><body>"
        '<div id="apartment-not-a-number" class="mb-3">'
        '  <div class="list__details">'
        "    <span class=\"text-xl block\">title</span>"
        '    <a target="_blank" href="https://example.com/x">Alle Details</a>'
        "  </div>"
        "</div>"
        "</body></html>"
    )
    assert list(parse_listings(html)) == []


def test_parse_listings_empty_html_yields_nothing() -> None:
    """A page with no apartment-* divs (e.g. a search returning zero
    hits) yields zero flats without raising — important so an empty
    legitimate result doesn't trigger anti-bot cool-off in the
    orchestrator."""
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    assert list(parse_listings("<html><body></body></html>")) == []


def test_parse_listings_district_is_a_district_name_not_plz() -> None:
    """Every emitted Flat's district (when present) is a Berlin-borough
    name, not a 5-digit postcode. The fixture spans 6 different
    operators (degewo, Gesobau, Howoge, Stadt und Land, WBM, Gewobag);
    this guards against a future card whose address shape ('Street, PLZ'
    with no trailing district) would otherwise leak the PLZ as the
    district value and break filter_district + the matcher."""
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    html = FIXTURE.read_text()
    for f in parse_listings(html):
        if "district" in f:
            assert not f["district"].isdigit(), f"district is a PLZ: {f}"
            assert len(f["district"]) > 2


def test_scraper_class_attributes(tmp_db) -> None:
    from flatpilot.scrapers import get_scraper, supports_city
    from flatpilot.scrapers.inberlinwohnen import InBerlinWohnenScraper

    assert get_scraper("inberlinwohnen") is InBerlinWohnenScraper
    assert InBerlinWohnenScraper.platform == "inberlinwohnen"
    assert InBerlinWohnenScraper.supported_cities == frozenset({"Berlin"})
    assert supports_city(InBerlinWohnenScraper, "Berlin") is True
    assert supports_city(InBerlinWohnenScraper, "Munich") is False


def test_fetch_new_uses_polite_session_with_search_url(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch_new wires SEARCH_URL into a SessionConfig and drains parse_listings."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib

    captured: dict[str, Any] = {}

    class _FakeCtxMgr:
        def __init__(self, config: Any) -> None:
            captured["config"] = config

        def __enter__(self) -> Any:
            return object()

        def __exit__(self, *_exc: Any) -> None:
            return None

    class _FakePageCtxMgr:
        def __init__(self, _ctx: Any) -> None:
            pass

        def __enter__(self) -> Any:
            class _P:
                def goto(self, url: str, **_kw: Any) -> Any:
                    captured["goto_url"] = url

                    class _R:
                        status = 200

                    return _R()

                def content(self) -> str:
                    return FIXTURE.read_text()

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(ib, "polite_session", _FakeCtxMgr)
    monkeypatch.setattr(ib, "session_page", _FakePageCtxMgr)

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})

    scraper = ib.InBerlinWohnenScraper()
    flats = list(scraper.fetch_new(profile))

    assert captured["config"].platform == "inberlinwohnen"
    assert captured["config"].warmup_url == ib.WARMUP_URL
    assert captured["goto_url"] == ib.SEARCH_URL
    assert len(flats) == 10
