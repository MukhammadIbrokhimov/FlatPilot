"""Single-source-of-truth checks for shared exceptions."""

from __future__ import annotations


def test_profile_missing_error_is_single_class():
    """apply.py and matcher/runner.py must reference the same exception class.

    Pre-fix they were two unrelated `class ProfileMissingError(RuntimeError)`
    definitions; an `except ProfileMissingError` block bound to one wouldn't
    catch instances of the other.
    """
    from flatpilot import errors
    from flatpilot.apply import ProfileMissingError as ApplyPME
    from flatpilot.matcher.runner import ProfileMissingError as MatcherPME

    assert ApplyPME is errors.ProfileMissingError
    assert MatcherPME is errors.ProfileMissingError


def test_unknown_city_error_is_single_class():
    """Both scrapers must reference the same exception class.

    Pre-fix wg_gesucht.py and kleinanzeigen.py defined separate classes,
    so a caller couldn't write one `except UnknownCityError` block to
    cover both scrapers.
    """
    from flatpilot import errors
    from flatpilot.scrapers.kleinanzeigen import UnknownCityError as KleinUCE
    from flatpilot.scrapers.wg_gesucht import UnknownCityError as WgUCE

    assert WgUCE is errors.UnknownCityError
    assert KleinUCE is errors.UnknownCityError


def test_profile_missing_error_subclasses_runtime_error():
    """Existing call sites raise/catch ``RuntimeError`` semantics; preserve."""
    from flatpilot.errors import ProfileMissingError

    assert issubclass(ProfileMissingError, RuntimeError)


def test_unknown_city_error_subclasses_value_error():
    """Existing call sites use ValueError semantics; preserve."""
    from flatpilot.errors import UnknownCityError

    assert issubclass(UnknownCityError, ValueError)
