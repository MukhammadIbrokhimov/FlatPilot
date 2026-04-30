"""Tests for the inberlinwohnen.de Wohnungsfinder scraper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "inberlinwohnen" / "search.html"
FIXTURE_PAGE2 = Path(__file__).parent / "fixtures" / "inberlinwohnen" / "search_page2.html"


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
    tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile
) -> None:
    """fetch_new wires SEARCH_URL into a SessionConfig and drains parse_listings."""
    from conftest import make_session_fakes
    from flatpilot.scrapers import inberlinwohnen as ib

    captured: dict[str, Any] = {}
    goto_log: list[str] = []
    polite_fake, page_fake = make_session_fakes(
        html_by_url={ib.SEARCH_URL: FIXTURE.read_text()},
        goto_log=goto_log,
        captured=captured,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)

    scraper = ib.InBerlinWohnenScraper()
    flats = list(scraper.fetch_new(berlin_profile))

    assert captured["config"].platform == "inberlinwohnen"
    assert captured["config"].warmup_url == ib.WARMUP_URL
    assert goto_log[0] == ib.SEARCH_URL
    assert len(flats) == 10


def test_fetch_new_stops_when_first_page_is_empty(
    tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile
) -> None:
    """If page 1 returns 0 apartment cards (e.g. site is empty or
    filtered to zero), fetch_new yields nothing without raising and
    performs exactly one goto."""
    from conftest import make_session_fakes
    from flatpilot.scrapers import inberlinwohnen as ib

    goto_calls: list[str] = []
    polite_fake, page_fake = make_session_fakes(goto_log=goto_calls)
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)

    flats = list(ib.InBerlinWohnenScraper().fetch_new(berlin_profile))

    assert flats == []
    assert goto_calls == [ib.SEARCH_URL]


def test_fetch_new_paginates_to_page_2_on_fresh_install(
    tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile
) -> None:
    """Fresh install (no known IDs): scraper walks page 1 → page 2 →
    ... and yields the union of cards. The fake serves page-1 fixture
    on SEARCH_URL and page-2 fixture on SEARCH_URL?page=2; any further
    page returns empty HTML, terminating the walk."""
    from conftest import make_session_fakes
    from flatpilot.scrapers import inberlinwohnen as ib

    goto_log: list[str] = []
    polite_fake, page_fake = make_session_fakes(
        html_by_url={
            ib.SEARCH_URL: FIXTURE.read_text(),
            f"{ib.SEARCH_URL}?page=2": FIXTURE_PAGE2.read_text(),
            # Any further page → empty HTML default → terminate.
        },
        goto_log=goto_log,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)  # speed up tests

    profile = berlin_profile
    flats = list(
        ib.InBerlinWohnenScraper().fetch_new(profile, known_external_ids=frozenset())
    )

    # Page 1 has 10 cards, page 2 has 10 cards (both fixtures verified
    # disjoint per the capture step). Page 3 is empty → terminate.
    assert len(flats) == 20
    # external_ids are unique across the union.
    ids = [f["external_id"] for f in flats]
    assert len(ids) == len(set(ids))
    # Walked exactly page 1, page 2, page 3 (empty stop).
    assert goto_log == [
        ib.SEARCH_URL,
        f"{ib.SEARCH_URL}?page=2",
        f"{ib.SEARCH_URL}?page=3",
    ]


def test_fetch_new_steady_state_stops_after_page_1_when_all_known(
    tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile
) -> None:
    """When every page-1 ID is in known_external_ids, scraper stops
    after one goto. This is the steady-state-polling acceptance
    criterion: ~1 page per pass."""
    from flatpilot.scrapers import inberlinwohnen as ib
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    page1_html = FIXTURE.read_text()
    page1_ids = frozenset(f["external_id"] for f in parse_listings(page1_html))
    assert len(page1_ids) == 10  # sanity-check fixture

    from conftest import make_session_fakes

    goto_log: list[str] = []
    polite_fake, page_fake = make_session_fakes(
        html_by_url={
            ib.SEARCH_URL: FIXTURE.read_text(),
            f"{ib.SEARCH_URL}?page=2": FIXTURE_PAGE2.read_text(),
        },
        goto_log=goto_log,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)

    profile = berlin_profile
    flats = list(
        ib.InBerlinWohnenScraper().fetch_new(
            profile, known_external_ids=page1_ids
        )
    )

    # Both assertions matter: goto_log catches "ignored known_ids,
    # always paginates"; len(flats) catches "broke before collecting".
    # Page 1 was fetched (always), all IDs were known → stop. No page 2.
    assert goto_log == [ib.SEARCH_URL]
    # Yielded flats = page 1 contents (the scraper does NOT filter known
    # IDs out of its yield — pipeline INSERT OR IGNORE handles dedup).
    assert len(flats) == 10


def test_fetch_new_safety_cap_at_max_pages(
    tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile
) -> None:
    """If the site never returns an empty page and known_ids never
    fully match (pathological case), the scraper stops at MAX_PAGES."""
    from conftest import make_session_fakes
    from flatpilot.scrapers import inberlinwohnen as ib

    goto_log: list[str] = []
    # Build a fake that returns page-1 fixture for EVERY url — i.e., the
    # site never paginates, never empties. The walk should still terminate
    # at MAX_PAGES rather than loop forever.
    polite_fake, page_fake = make_session_fakes(
        default_html=FIXTURE.read_text(),
        goto_log=goto_log,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)
    monkeypatch.setattr(ib, "MAX_PAGES", 3)  # reduce for fast test

    profile = berlin_profile
    flats = list(
        ib.InBerlinWohnenScraper().fetch_new(profile, known_external_ids=frozenset())
    )

    # Cap = 3 → exactly 3 gotos.
    assert len(goto_log) == 3
    assert goto_log[0] == ib.SEARCH_URL
    assert goto_log[1] == f"{ib.SEARCH_URL}?page=2"
    assert goto_log[2] == f"{ib.SEARCH_URL}?page=3"
    # Each page yielded the same 10 cards (parse_listings doesn't dedup
    # within a fetch_new call); 3 pages × 10 = 30 yielded flats.
    assert len(flats) == 30


def test_fetch_new_rate_limit_mid_walk_aborts_pass_loses_collected_flats(
    tmp_db, monkeypatch: pytest.MonkeyPatch, berlin_profile
) -> None:
    """RateLimitedError on page 2 propagates out of fetch_new; pages
    already fetched (page 1) are NOT preserved. This pins the chosen
    semantics: rate-limit aborts the pass; pipeline backoff handles the
    retry; INSERT OR IGNORE on the next pass makes recovery safe.

    A future implementation that adds try/except RateLimitedError +
    break inside the loop would PASS this test only if it then re-raised
    after the break — but the simpler implementation just lets the
    exception propagate. This test pins the propagation contract."""
    from conftest import make_session_fakes
    from flatpilot.scrapers import inberlinwohnen as ib
    from flatpilot.scrapers.session import RateLimitedError

    goto_log: list[str] = []
    polite_fake, page_fake = make_session_fakes(
        default_html=FIXTURE.read_text(),
        goto_log=goto_log,
        status_fn=lambda url: 429 if "?page=2" in url else 200,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)

    with pytest.raises(RateLimitedError):
        list(ib.InBerlinWohnenScraper().fetch_new(
            berlin_profile, known_external_ids=frozenset()
        ))

    # Walked page 1 (OK) then page 2 (429 → raise). Two gotos observed.
    assert goto_log == [ib.SEARCH_URL, f"{ib.SEARCH_URL}?page=2"]
