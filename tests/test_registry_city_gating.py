"""Tests for the scraper-registry city-gating mechanism."""

from __future__ import annotations

from typing import ClassVar

import pytest


def _stub_scraper_cls(supported: frozenset[str] | None) -> type:
    class _Stub:
        platform: ClassVar[str] = "stub"
        user_agent: ClassVar[str] = "test-ua"
        supported_cities: ClassVar[frozenset[str] | None] = supported

        def fetch_new(self, profile):  # noqa: ARG002 — protocol stub
            yield from ()

    return _Stub


def test_supports_city_passes_when_supported_cities_is_none() -> None:
    """A scraper with supported_cities=None accepts every profile city."""
    from flatpilot.scrapers import supports_city

    cls = _stub_scraper_cls(None)
    assert supports_city(cls, "Berlin") is True
    assert supports_city(cls, "Munich") is True
    assert supports_city(cls, "") is True


def test_supports_city_exact_match_only() -> None:
    """Comparison is exact-match (case-sensitive) — mirrors per-scraper CITY_IDS dict lookups."""
    from flatpilot.scrapers import supports_city

    cls = _stub_scraper_cls(frozenset({"Berlin"}))
    assert supports_city(cls, "Berlin") is True
    assert supports_city(cls, "berlin") is False
    assert supports_city(cls, " Berlin") is False
    assert supports_city(cls, "Berlin ") is False
    assert supports_city(cls, "Munich") is False


def test_supports_city_handles_multi_city_set() -> None:
    from flatpilot.scrapers import supports_city

    cls = _stub_scraper_cls(frozenset({"Berlin", "Hamburg", "Munich"}))
    assert supports_city(cls, "Berlin") is True
    assert supports_city(cls, "Munich") is True
    assert supports_city(cls, "Hamburg") is True
    assert supports_city(cls, "Köln") is False


def test_supports_city_empty_frozenset_supports_nothing() -> None:
    """An empty frozenset means 'declared, but no cities' — used to soft-disable a scraper."""
    from flatpilot.scrapers import supports_city

    cls = _stub_scraper_cls(frozenset())
    assert supports_city(cls, "Berlin") is False
    assert supports_city(cls, "Munich") is False


def test_register_rejects_class_without_supported_cities(monkeypatch: pytest.MonkeyPatch) -> None:
    """A class that forgets to declare supported_cities fails at @register time."""
    from flatpilot import scrapers

    # monkeypatch.setattr swaps _REGISTRY at module scope; register() reads
    # the name through module globals so the swap takes effect. Don't
    # refactor register to bind _REGISTRY in a closure or default arg
    # without updating this test. monkeypatch's function scope guarantees
    # each test gets a fresh empty dict and the original is restored
    # afterwards.
    monkeypatch.setattr(scrapers, "_REGISTRY", {})

    with pytest.raises(TypeError, match=r"supported_cities"):

        @scrapers.register
        class _MissingSupportedCities:
            platform: ClassVar[str] = "no-cities-test"
            user_agent: ClassVar[str] = "x"

            def fetch_new(self, profile):  # noqa: ARG002
                yield from ()


def test_register_accepts_supported_cities_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """supported_cities=None is a legitimate declaration ('no restriction')."""
    from flatpilot import scrapers

    monkeypatch.setattr(scrapers, "_REGISTRY", {})

    @scrapers.register
    class _AnyCity:
        platform: ClassVar[str] = "any-city-test"
        user_agent: ClassVar[str] = "x"
        supported_cities: ClassVar[frozenset[str] | None] = None

        def fetch_new(self, profile):  # noqa: ARG002
            yield from ()

    assert "any-city-test" in scrapers._REGISTRY


def test_register_still_rejects_missing_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing platform-presence check still applies."""
    from flatpilot import scrapers

    monkeypatch.setattr(scrapers, "_REGISTRY", {})

    with pytest.raises(TypeError, match=r"platform"):

        @scrapers.register
        class _NoPlatform:
            user_agent: ClassVar[str] = "x"
            supported_cities: ClassVar[frozenset[str] | None] = None

            def fetch_new(self, profile):  # noqa: ARG002
                yield from ()
