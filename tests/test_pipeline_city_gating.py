"""Tests for city-gating in the scrape pipeline."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from rich.console import Console


def _profile_for_city(city: str):
    """Profile.load_example() with city overridden — example ships Frankfurt."""
    from flatpilot.profile import Profile

    return Profile.load_example().model_copy(update={"city": city})


class _GenericScraper:
    """Stub scraper used to populate the registry for pipeline tests."""

    platform: ClassVar[str]
    user_agent: ClassVar[str] = "test-ua"
    supported_cities: ClassVar[frozenset[str] | None]

    def __init__(self) -> None:
        self.fetch_called_with: Any = None

    def fetch_new(self, profile):
        self.fetch_called_with = profile.city
        yield from ()


def _make_stub(platform: str, supported_cities: frozenset[str] | None) -> type[_GenericScraper]:
    """Build a fresh subclass of ``_GenericScraper`` with the given
    platform name and supported_cities — used by the gate tests so
    each stub has a distinct identity in the registry / logs without
    duplicating the boilerplate of the base class."""
    cls = type(
        f"_Stub_{platform.replace('-', '_')}",
        (_GenericScraper,),
        {
            "platform": platform,
            "supported_cities": supported_cities,
        },
    )
    return cls


def test_run_scrape_pass_skips_scrapers_whose_cities_dont_match(tmp_db) -> None:
    """Scrapers declaring cities not matching profile.city are skipped at the gate."""
    from flatpilot.cli import _run_scrape_pass

    profile = _profile_for_city("Munich")  # not in any stub's supported set
    console = Console(record=True)

    berlin_only = _make_stub("berlin-only", frozenset({"Berlin"}))()
    any_city = _make_stub("any-city", None)()
    multi_city = _make_stub("multi", frozenset({"Berlin", "Hamburg", "Munich"}))()

    _run_scrape_pass([berlin_only, any_city, multi_city], profile, console)

    # berlin-only stub must NOT be called; the multi-city stub IS Munich-supported;
    # the any-city stub is None-cities → always called.
    assert berlin_only.fetch_called_with is None, "Berlin-only should be skipped for Munich"
    assert any_city.fetch_called_with == "Munich"
    assert multi_city.fetch_called_with == "Munich"

    output = console.export_text()
    assert "berlin-only: skipping — city 'Munich' not supported" in output


def test_run_scrape_pass_runs_all_when_all_support_city(tmp_db) -> None:
    """When every scraper supports profile.city the gate is a no-op."""
    from flatpilot.cli import _run_scrape_pass

    profile = _profile_for_city("Berlin")
    console = Console()

    a = _make_stub("plat-a", frozenset({"Berlin"}))()
    b = _make_stub("plat-b", None)()

    _run_scrape_pass([a, b], profile, console)

    assert a.fetch_called_with == "Berlin"
    assert b.fetch_called_with == "Berlin"


def test_scrape_command_rejects_explicit_platform_for_unsupported_city(tmp_db) -> None:
    """`flatpilot scrape --platform kleinanzeigen` exits 1 when profile.city is non-Berlin."""
    from typer.testing import CliRunner

    from flatpilot.cli import app
    from flatpilot.profile import Profile, save_profile

    profile = Profile.load_example().model_copy(update={"city": "Munich"})
    save_profile(profile)

    runner = CliRunner()
    result = runner.invoke(app, ["scrape", "--platform", "kleinanzeigen"])

    assert result.exit_code == 1, result.output
    assert "kleinanzeigen" in result.output
    assert "Munich" in result.output
    assert "not supported" in result.output


def test_scrape_command_runs_when_explicit_platform_supports_city(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`flatpilot scrape --platform kleinanzeigen` proceeds when profile.city is Berlin.

    We patch the scraper's fetch_new so the test does not actually hit the
    network; the assertion is that the city gate did not block invocation.
    """
    from typer.testing import CliRunner

    from flatpilot.cli import app
    from flatpilot.profile import Profile, save_profile
    from flatpilot.scrapers import kleinanzeigen as kz

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    save_profile(profile)

    called: dict[str, Any] = {}

    def _fake_fetch(self, profile):  # noqa: ARG001
        called["city"] = profile.city
        yield from ()

    monkeypatch.setattr(kz.KleinanzeigenScraper, "fetch_new", _fake_fetch)

    runner = CliRunner()
    result = runner.invoke(app, ["scrape", "--platform", "kleinanzeigen"])

    assert result.exit_code == 0, result.output
    assert called.get("city") == "Berlin"
