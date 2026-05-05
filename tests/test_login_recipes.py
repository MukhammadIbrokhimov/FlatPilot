"""Tests for the platform → login-recipe registry in :mod:`flatpilot.login`.

These tests are pure-Python and don't open a browser; they assert the
registry shape so downstream callers (the ``flatpilot login`` CLI and
the Web UI's ``Connect`` button per FlatPilot-8jx) can rely on every
supported platform being resolvable.
"""

from __future__ import annotations

import pytest

from flatpilot.login import _LOGIN_SITES, UnknownPlatformError, _resolve_platform


def test_immoscout24_login_recipe_registered():
    site = _resolve_platform("immoscout24")
    assert site.login_url == "https://www.immobilienscout24.de/anmelden.html"
    assert site.warmup_url == "https://www.immobilienscout24.de/"
    assert site.consent_selectors  # non-empty


def test_all_known_platforms_resolve():
    # Snapshot guard: catches accidental removals or typos in keys.
    expected = {"wg-gesucht", "kleinanzeigen", "immoscout24"}
    assert set(_LOGIN_SITES) == expected


def test_unknown_platform_lists_immoscout24():
    # The error message lists known platforms so the user can correct
    # a typo. Make sure immoscout24 shows up there now that it's supported.
    with pytest.raises(UnknownPlatformError, match="immoscout24"):
        _resolve_platform("immoscout-24")
