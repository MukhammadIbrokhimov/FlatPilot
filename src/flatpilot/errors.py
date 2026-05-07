"""Cross-module exception classes.

Centralised here so different layers raising/catching the same conceptual
error all reference one class object — an ``except ProfileMissingError``
block bound to ``flatpilot.errors`` catches whether the raise came from
``apply_to_flat`` or ``run_match``.

Add new shared exceptions here when more than one module needs to
raise/catch the same conceptual error. Don't dump every project
exception in this module — module-local ones (e.g. ``FillError`` inside
fillers/) stay where they are used.
"""

from __future__ import annotations


class FlatPilotError(Exception):
    """Base class for all project-level FlatPilot exceptions."""


class ProfileMissingError(FlatPilotError, RuntimeError):
    """Raised when an entry point runs before ``flatpilot init``.

    Both ``apply_to_flat`` and ``run_match`` short-circuit when
    ``load_profile()`` returns ``None`` — they need a profile to do
    anything useful, and a missing profile is user-correctable.
    """


class UnknownCityError(FlatPilotError, ValueError):
    """Raised by a scraper when ``profile.city`` has no platform city ID mapped.

    Each scraper keeps its own ``CITY_IDS`` table (the ID format is
    platform-specific) but they all raise this when the lookup misses,
    so the orchestrator can render one consistent error message.
    """


class UnsupportedPlatformError(FlatPilotError, LookupError):
    """Raised when ``flatpilot apply`` runs against a platform with no filler.

    inberlinwohnen.de and ImmoScout24 are intentionally scrape+notify only:
    the former deeplinks each listing to a different landlord's site (one
    filler per operator would be required), the latter is RSS-only by
    design to avoid the fragile HTML path FlatPilot-l9hm / FlatPilot-h7q
    removed. Subclasses ``LookupError`` so the existing ``except LookupError``
    branch in :func:`flatpilot.cli.apply` exits 2 with the friendly message.
    """
