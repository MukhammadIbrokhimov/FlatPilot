"""Pipeline threads known_external_ids from flats table into fetch_new."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console


def test_run_scrape_pass_passes_known_ids_from_db(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipeline pre-loads (platform, external_id) pairs from `flats`
    and passes the per-platform set into each scraper's fetch_new as
    `known_external_ids` (kw-only, frozenset).

    Setup: insert two flats for inberlinwohnen, one for wg_gesucht.
    Run the pipeline pass with both scrapers monkeypatched. Assert each
    scraper received exactly the set of external_ids matching its
    platform.
    """
    from datetime import UTC, datetime

    from flatpilot import database
    from flatpilot.cli import _run_scrape_pass
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib
    from flatpilot.scrapers import wg_gesucht as wg

    # Seed the DB with three flats.
    conn = database.get_conn()
    now = datetime.now(UTC).isoformat()
    for platform, ext_id in [
        ("inberlinwohnen", "16344"),
        ("inberlinwohnen", "16343"),
        ("wg-gesucht", "9999"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO flats "
            "(platform, external_id, listing_url, title, scraped_at, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (platform, ext_id, f"https://example.test/{ext_id}", "t", now, now),
        )
    conn.commit()

    captured: dict[str, frozenset[str]] = {}

    def _capture_ib(self: Any, profile: Any, **kwargs: Any) -> Any:
        captured["inberlinwohnen"] = kwargs.get("known_external_ids")
        return iter([])

    def _capture_wg(self: Any, profile: Any, **kwargs: Any) -> Any:
        captured["wg_gesucht"] = kwargs.get("known_external_ids")
        return iter([])

    monkeypatch.setattr(ib.InBerlinWohnenScraper, "fetch_new", _capture_ib)
    monkeypatch.setattr(wg.WGGesuchtScraper, "fetch_new", _capture_wg)

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    console = Console(record=True)
    scrapers = [ib.InBerlinWohnenScraper(), wg.WGGesuchtScraper()]
    _run_scrape_pass(scrapers, profile, console)

    assert captured["inberlinwohnen"] == frozenset({"16344", "16343"})
    assert captured["wg_gesucht"] == frozenset({"9999"})
    # Type contract: it must be a frozenset, not a list/set/tuple.
    assert isinstance(captured["inberlinwohnen"], frozenset)
    assert isinstance(captured["wg_gesucht"], frozenset)
