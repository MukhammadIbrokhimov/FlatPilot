"""Deterministic hard filters for flat → profile matching.

Each filter is a pure function ``(flat, profile) -> (passes, reason)`` — no
LLM calls, no scoring, no fuzzy weights. A flat passes the matcher only if
every filter passes; any failure adds its reason to ``decision_reasons_json``
so the dashboard and status command can explain rejections.

``flat`` is any ``Mapping`` (``sqlite3.Row``, ``dict``, pydantic dump). The
filter list itself is exposed as :data:`FILTERS` so callers can iterate or
override; :func:`evaluate` is the convenience wrapper.

Fields a flat might be missing (``rent_warm_eur``, ``rooms``) are treated as
reject-with-reason rather than pass, so incomplete scrapes don't silently
slip through — better a ``rooms_unknown`` line in the rejection log than a
false-positive notification.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

from flatpilot.profile import Profile

FilterResult = tuple[bool, str | None]
Filter = Callable[[Mapping[str, Any], Profile], FilterResult]


# Flats can become available up to this many days after the profile's target
# move-in — landlords commonly post a few weeks ahead, and a small tolerance
# avoids missing otherwise-perfect matches.
MOVE_IN_TOLERANCE = timedelta(days=30)


def filter_rent_band(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    rent = flat.get("rent_warm_eur")
    if rent is None:
        return False, "rent_unknown"
    if rent < profile.rent_min_warm:
        return False, "rent_too_low"
    if rent > profile.rent_max_warm:
        return False, "rent_too_high"
    return True, None


def filter_rooms_band(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    rooms = flat.get("rooms")
    if rooms is None:
        return False, "rooms_unknown"
    if rooms < profile.rooms_min:
        return False, "too_few_rooms"
    if rooms > profile.rooms_max:
        return False, "too_many_rooms"
    return True, None


def filter_wbs(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    if not flat.get("requires_wbs"):
        return True, None
    if profile.wbs.status != "yes":
        return False, "wbs_required_but_user_has_none"
    flat_size = flat.get("wbs_size_category")
    flat_income = flat.get("wbs_income_category")
    if flat_size is not None and flat_size != profile.wbs.size_category:
        return False, "wbs_size_mismatch"
    if flat_income is not None and flat_income != profile.wbs.income_category:
        return False, "wbs_income_mismatch"
    return True, None


def filter_district(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    if not profile.district_allowlist:
        return True, None
    district = flat.get("district")
    if district is None:
        return False, "district_unknown"
    if district not in profile.district_allowlist:
        return False, "wrong_district"
    return True, None


def filter_pets(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    if not profile.pets:
        return True, None
    # Explicit False from scraper = reject. None (unknown) passes so we don't
    # miss listings where the landlord didn't fill the field in.
    if flat.get("pets_allowed") is False:
        return False, "no_pets_allowed"
    return True, None


def filter_move_in(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    available_from = flat.get("available_from")
    if not available_from:
        return True, None
    try:
        flat_date = date.fromisoformat(str(available_from))
    except ValueError:
        return True, None
    if flat_date > profile.move_in_date + MOVE_IN_TOLERANCE:
        return False, "move_in_too_late"
    return True, None


def filter_furnished(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    if profile.furnished_pref == "any":
        return True, None
    furnished = flat.get("furnished")
    if furnished is None:
        return True, None
    if profile.furnished_pref == "furnished" and not furnished:
        return False, "not_furnished"
    if profile.furnished_pref == "unfurnished" and furnished:
        return False, "furnished_but_want_unfurnished"
    return True, None


def filter_contract(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    if profile.min_contract_months is None:
        return True, None
    flat_min = flat.get("min_contract_months")
    if flat_min is None:
        return True, None
    if flat_min < profile.min_contract_months:
        return False, "contract_too_short"
    return True, None


def filter_radius(flat: Mapping[str, Any], profile: Profile) -> FilterResult:
    # Radius check is skipped when the profile has no home coordinates yet
    # (e.g. before the setup wizard geocodes the user's address) — rather
    # than rejecting every flat, let the other filters speak.
    if profile.home_lat is None or profile.home_lng is None:
        return True, None

    # Local import: distance.py pulls in httpx and only runs the Nominatim
    # path when coords are missing, so the lazy import keeps test runs that
    # stub out the matcher layer cheap.
    from flatpilot.matcher.distance import haversine_km, resolve_flat_coords

    coords = resolve_flat_coords(flat, profile)
    if coords is None:
        return False, "location_unknown"
    flat_lat, flat_lng = coords
    distance_km = haversine_km(profile.home_lat, profile.home_lng, flat_lat, flat_lng)
    if distance_km > profile.radius_km:
        return False, "outside_radius"
    return True, None


FILTERS: list[Filter] = [
    filter_rent_band,
    filter_rooms_band,
    filter_wbs,
    filter_district,
    filter_radius,
    filter_pets,
    filter_move_in,
    filter_furnished,
    filter_contract,
]


def evaluate(flat: Mapping[str, Any], profile: Profile) -> list[str]:
    """Run every filter against a flat; return the list of failure reasons.

    An empty list means every filter passed — the flat is a match.
    """
    reasons: list[str] = []
    for f in FILTERS:
        passes, reason = f(flat, profile)
        if not passes and reason:
            reasons.append(reason)
    return reasons
