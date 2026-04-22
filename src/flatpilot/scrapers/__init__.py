"""Scraper framework and per-platform scrapers.

Scrapers register themselves with :func:`register` and are retrieved by
their ``platform`` string. ``flatpilot scrape`` (Phase 1) iterates the
registry — or a single ``--platform`` — to drive each scraper.
"""

from __future__ import annotations

from collections.abc import Iterable

from flatpilot.scrapers.base import Flat, Scraper, session_dir


_REGISTRY: dict[str, type[Scraper]] = {}


def register(cls: type[Scraper]) -> type[Scraper]:
    """Class decorator that indexes ``cls`` under its ``platform`` attribute.

    Usage::

        @register
        class WGGesuchtScraper:
            platform = "wg-gesucht"
            user_agent = "..."
            def fetch_new(self, profile): ...
    """
    platform = getattr(cls, "platform", None)
    if not platform:
        raise TypeError(
            f"{cls.__name__} must set a non-empty `platform` ClassVar before @register"
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


__all__ = [
    "Flat",
    "Scraper",
    "all_scrapers",
    "get_scraper",
    "register",
    "session_dir",
]
