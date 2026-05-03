"""Unit coverage for scrapers.immoscout24_rss — RSS → Flat parser."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from flatpilot.profile import Profile
from flatpilot.scrapers.immoscout24_rss import (
    ImmoScout24RSSScraper,
    _entry_to_flat,
    parse_feed,
)

FIXTURE = Path(__file__).parent / "fixtures" / "immoscout24_rss" / "feed.xml"


def test_parse_feed_yields_one_flat_per_item():
    flats = list(parse_feed(FIXTURE.read_bytes()))
    assert len(flats) == 3
    ids = {f["external_id"] for f in flats}
    assert ids == {"123456789", "987654321", "555000111"}


def test_parse_feed_extracts_basic_fields():
    flats = list(parse_feed(FIXTURE.read_bytes()))
    by_id = {f["external_id"]: f for f in flats}

    rich = by_id["123456789"]
    assert rich["listing_url"] == "https://www.immobilienscout24.de/expose/123456789"
    assert "2-Zimmer" in rich["title"]
    # Description should pick the warm rent (1.200 €), not the cold (950 €).
    assert rich["rent_warm_eur"] == 1200.0
    assert rich["size_sqm"] == 60.0
    assert rich["rooms"] == 2.0
    assert rich["available_from"] == "2026-07-01"
    assert rich["online_since"] == "2026-05-02"


def test_parse_feed_picks_up_wbs_flag():
    flats = list(parse_feed(FIXTURE.read_bytes()))
    by_id = {f["external_id"]: f for f in flats}
    assert by_id["987654321"].get("requires_wbs") is True


def test_parse_feed_handles_minimal_description():
    flats = list(parse_feed(FIXTURE.read_bytes()))
    by_id = {f["external_id"]: f for f in flats}

    sparse = by_id["555000111"]
    assert sparse["title"] == "Studio in Kreuzberg"
    assert sparse["listing_url"].endswith("/555000111")
    # No m²/€/Zimmer/date in title or description -> those fields stay absent
    # (Flat is total=False, so the matcher will reject-with-reason).
    assert "rent_warm_eur" not in sparse
    assert "size_sqm" not in sparse
    assert "rooms" not in sparse


def test_external_id_falls_back_to_numeric_guid():
    """The third fixture item carries a numeric guid (`isPermaLink="false"`)
    instead of an /expose/ link in the guid — the parser must still
    extract the ID from the entry link.
    """
    flats = list(parse_feed(FIXTURE.read_bytes()))
    assert any(f["external_id"] == "555000111" for f in flats)


def test_entry_without_link_is_dropped():
    class _Entry(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    entry = _Entry()
    entry["title"] = "no link, no flat"
    assert _entry_to_flat(entry) is None


def test_fetch_new_skips_when_no_urls_configured():
    scraper = ImmoScout24RSSScraper()
    profile = Profile.load_example()
    # Default profile has no immoscout24_rss_urls.
    flats = list(scraper.fetch_new(profile, known_external_ids=frozenset()))
    assert flats == []


def test_fetch_new_logs_and_continues_on_http_error(monkeypatch, caplog):
    """A dead feed URL must not abort the whole pass — the scraper logs
    and moves on. Otherwise one stale saved search would silently kill
    every other feed in the same profile.
    """
    import flatpilot.scrapers.immoscout24_rss as mod

    def _boom(url: str, ua: str) -> bytes:
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(mod, "_fetch_feed", _boom)

    scraper = ImmoScout24RSSScraper()
    profile = Profile.load_example().model_copy(
        update={"immoscout24_rss_urls": ["https://example.invalid/feed.rss"]}
    )

    with caplog.at_level("WARNING"):
        flats = list(scraper.fetch_new(profile, known_external_ids=frozenset()))

    assert flats == []
    assert any("failed to fetch" in r.getMessage() for r in caplog.records)


def test_fetch_new_dedups_external_ids_across_feeds(monkeypatch):
    """Two saved-search RSS feeds will overlap when they cover adjacent
    districts — the same expose ID can appear in both. The adapter
    de-dupes by external_id within a single ``fetch_new`` pass so the
    pipeline doesn't waste an INSERT-OR-IGNORE round-trip per duplicate.
    """
    import flatpilot.scrapers.immoscout24_rss as mod

    feed_bytes = FIXTURE.read_bytes()
    monkeypatch.setattr(mod, "_fetch_feed", lambda url, ua: feed_bytes)

    scraper = ImmoScout24RSSScraper()
    profile = Profile.load_example().model_copy(
        update={
            "immoscout24_rss_urls": [
                "https://www.immobilienscout24.de/feed/saved/1.rss",
                "https://www.immobilienscout24.de/feed/saved/2.rss",
            ]
        }
    )

    flats = list(scraper.fetch_new(profile, known_external_ids=frozenset()))
    ids = [f["external_id"] for f in flats]
    assert sorted(ids) == sorted(set(ids)), f"duplicates leaked: {ids}"
    assert len(ids) == 3


def test_immoscout24_registers_with_no_city_restriction():
    """ImmoScout24 RSS is city-agnostic — the saved-search URL itself
    encodes the city, so the registry-level city gate has nothing to
    do.
    """
    # Trigger registration.
    import flatpilot.scrapers.immoscout24_rss  # noqa: F401
    from flatpilot.scrapers import get_scraper, supports_city

    cls = get_scraper("immoscout24")
    assert cls.supported_cities is None
    # supports_city() should return True for any city when supported_cities is None.
    assert supports_city(cls, "Berlin") is True
    assert supports_city(cls, "Vladivostok") is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.200 €", 1200.0),
        ("950,50 €", 950.50),
        ("60 m²", 60.0),
        ("3 Zimmer", 3.0),
    ],
)
def test_german_number_extraction(raw, expected):
    """Sanity check on the regex helpers — the description text mixes
    German thousand-dot / decimal-comma formatting and we must parse it
    back into a float identical to what the matcher expects.
    """
    from flatpilot.scrapers.immoscout24_rss import (
        _RENT_RE,
        _ROOMS_RE,
        _SIZE_RE,
        _first_match,
    )

    if "€" in raw:
        assert _first_match(_RENT_RE, raw) == expected
    elif "m²" in raw:
        assert _first_match(_SIZE_RE, raw) == expected
    elif "Zimmer" in raw:
        assert _first_match(_ROOMS_RE, raw) == expected
