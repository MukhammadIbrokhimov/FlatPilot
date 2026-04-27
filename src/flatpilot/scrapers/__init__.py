"""Scraper framework and per-platform scrapers.

Scrapers register themselves with :func:`register` and are retrieved by
their ``platform`` string. ``flatpilot scrape`` (Phase 1) iterates the
registry — or a single ``--platform`` — to drive each scraper. Each
scraper class declares ``supported_cities`` so the orchestrator can
skip platforms that do not cover ``profile.city`` before paying the
fetch cost; see :func:`supports_city`.
"""

from __future__ import annotations

from collections.abc import Iterable

from flatpilot.scrapers.base import Flat, Scraper, session_dir

_REGISTRY: dict[str, type[Scraper]] = {}

# Sentinel for "attribute genuinely not declared on the class" — distinct
# from the legitimate ``supported_cities = None`` (= "no city restriction").
_NOT_DECLARED = object()


def register(cls: type[Scraper]) -> type[Scraper]:
    """Class decorator that indexes ``cls`` under its ``platform`` attribute.

    Enforces that the class declares both ``platform`` and
    ``supported_cities`` ClassVars. ``supported_cities`` may be a
    ``frozenset[str]`` of exact city names, an empty frozenset (= "soft
    disabled — declared but supports no city right now"), or ``None``
    (= "no city restriction"). Forgetting the declaration raises
    :class:`TypeError` at import time so misconfiguration is loud rather
    than silently producing wrong scrapes.

    Usage::

        @register
        class WGGesuchtScraper:
            platform = "wg-gesucht"
            user_agent = "..."
            supported_cities = frozenset({"Berlin", "Hamburg", ...})
            def fetch_new(self, profile): ...
    """
    platform = getattr(cls, "platform", None)
    if not platform:
        raise TypeError(
            f"{cls.__name__} must set a non-empty `platform` ClassVar before @register"
        )
    supported = getattr(cls, "supported_cities", _NOT_DECLARED)
    if supported is _NOT_DECLARED:
        raise TypeError(
            f"{cls.__name__} must declare `supported_cities` ClassVar "
            f"(frozenset[str] of supported cities, or None for any) "
            f"before @register"
        )
    if platform in _REGISTRY:
        raise ValueError(
            f"Duplicate scraper registration for platform {platform!r}: "
            f"{_REGISTRY[platform].__name__} vs {cls.__name__}"
        )
    _REGISTRY[platform] = cls
    return cls


def get_scraper(platform: str) -> type[Scraper]:
    try:
        return _REGISTRY[platform]
    except KeyError as exc:
        raise KeyError(
            f"No scraper registered for platform {platform!r} "
            f"(known: {sorted(_REGISTRY)})"
        ) from exc


def all_scrapers() -> Iterable[type[Scraper]]:
    return list(_REGISTRY.values())


def supports_city(scraper_cls: type[Scraper], city: str) -> bool:
    """Return True if ``scraper_cls`` accepts ``city``.

    Exact-match comparison (no case-fold, no whitespace strip) so the
    gate stays consistent with each scraper's internal CITY_IDS dict
    lookup — ``"berlin"`` is not the same value as ``"Berlin"`` and
    neither is ``"Frankfurt"`` vs ``"Frankfurt am Main"``. A scraper
    with ``supported_cities = None`` (or no ``supported_cities``
    attribute at all) accepts every city.
    """
    supported = getattr(scraper_cls, "supported_cities", None)
    return supported is None or city in supported


__all__ = [
    "Flat",
    "Scraper",
    "all_scrapers",
    "get_scraper",
    "register",
    "session_dir",
    "supports_city",
]
