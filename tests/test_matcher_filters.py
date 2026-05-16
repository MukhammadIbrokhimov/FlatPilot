"""Unit coverage for the deterministic hard filters."""
from __future__ import annotations

from datetime import date, timedelta

from flatpilot.matcher.filters import (
    MOVE_IN_TOLERANCE,
    evaluate,
    filter_contract,
    filter_district,
    filter_furnished,
    filter_move_in,
    filter_pets,
    filter_radius,
    filter_rent_band,
    filter_rooms_band,
    filter_short_term,
    filter_wbs,
)
from flatpilot.profile import WBS, Profile


def _profile(**overrides):
    return Profile.load_example().model_copy(update=overrides)


# --- filter_rent_band ---------------------------------------------------

def test_rent_band_passes_within_window():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat = {"rent_warm_eur": 1000}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is True and reason is None


def test_rent_band_rejects_above_max():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat = {"rent_warm_eur": 1600}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is False
    assert reason == "rent_too_high"


def test_rent_band_rejects_below_min():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat = {"rent_warm_eur": 100}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is False
    assert reason == "rent_too_low"


def test_rent_band_rejects_when_field_missing():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat: dict = {}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is False
    assert reason == "rent_unknown"


# --- filter_rooms_band --------------------------------------------------

def test_rooms_band_passes_within_window():
    profile = _profile(rooms_min=2, rooms_max=3)
    flat = {"rooms": 2}
    ok, reason = filter_rooms_band(flat, profile)
    assert ok is True and reason is None


def test_rooms_band_rejects_below_min():
    profile = _profile(rooms_min=2, rooms_max=3)
    flat = {"rooms": 1}
    ok, reason = filter_rooms_band(flat, profile)
    assert ok is False
    assert reason == "too_few_rooms"


def test_rooms_band_rejects_above_max():
    profile = _profile(rooms_min=2, rooms_max=3)
    flat = {"rooms": 5}
    ok, reason = filter_rooms_band(flat, profile)
    assert ok is False
    assert reason == "too_many_rooms"


def test_rooms_band_rejects_when_field_missing():
    profile = _profile(rooms_min=2, rooms_max=3)
    flat: dict = {}
    ok, reason = filter_rooms_band(flat, profile)
    assert ok is False
    assert reason == "rooms_unknown"


# --- filter_wbs ---------------------------------------------------------

def test_wbs_passes_when_flat_does_not_require_wbs():
    profile = _profile()  # default WBS status = "none"
    flat = {"requires_wbs": False}
    ok, reason = filter_wbs(flat, profile)
    assert ok is True and reason is None


def test_wbs_rejects_when_flat_requires_wbs_and_user_has_none():
    profile = _profile()  # default: wbs.status = "none"
    flat = {"requires_wbs": True}
    ok, reason = filter_wbs(flat, profile)
    assert ok is False
    assert reason == "wbs_required_but_user_has_none"


def test_wbs_rejects_on_size_mismatch():
    profile = _profile(
        wbs=WBS(status="yes", size_category=2, income_category=140),
    )
    flat = {"requires_wbs": True, "wbs_size_category": 4}
    ok, reason = filter_wbs(flat, profile)
    assert ok is False
    assert reason == "wbs_size_mismatch"


def test_wbs_rejects_on_income_mismatch():
    profile = _profile(
        wbs=WBS(status="yes", size_category=2, income_category=140),
    )
    flat = {
        "requires_wbs": True,
        "wbs_size_category": 2,
        "wbs_income_category": 100,
    }
    ok, reason = filter_wbs(flat, profile)
    assert ok is False
    assert reason == "wbs_income_mismatch"


# --- filter_district ----------------------------------------------------

def test_district_passes_when_allowlist_empty():
    profile = _profile(district_allowlist=[])
    flat = {"district": "Mitte"}
    ok, reason = filter_district(flat, profile)
    assert ok is True and reason is None


def test_district_passes_when_in_allowlist():
    profile = _profile(district_allowlist=["Mitte", "Kreuzberg"])
    flat = {"district": "Mitte"}
    ok, reason = filter_district(flat, profile)
    assert ok is True and reason is None


def test_district_rejects_when_not_in_allowlist():
    profile = _profile(district_allowlist=["Mitte", "Kreuzberg"])
    flat = {"district": "Spandau"}
    ok, reason = filter_district(flat, profile)
    assert ok is False
    assert reason == "wrong_district"


def test_district_rejects_when_field_missing_with_allowlist_set():
    profile = _profile(district_allowlist=["Mitte"])
    flat: dict = {}
    ok, reason = filter_district(flat, profile)
    assert ok is False
    assert reason == "district_unknown"


# --- filter_pets --------------------------------------------------------

def test_pets_passes_when_profile_has_no_pets():
    profile = _profile(pets=[])
    flat = {"pets_allowed": False}
    ok, reason = filter_pets(flat, profile)
    assert ok is True and reason is None


def test_pets_rejects_when_profile_has_pets_and_flat_forbids():
    profile = _profile(pets=["dog"])
    flat = {"pets_allowed": False}
    ok, reason = filter_pets(flat, profile)
    assert ok is False
    assert reason == "no_pets_allowed"


def test_pets_passes_when_profile_has_pets_and_field_missing():
    # None (unknown) passes so we don't drop listings where the landlord
    # didn't fill the field (per filters.py:86-89 comment).
    profile = _profile(pets=["dog"])
    flat: dict = {}
    ok, reason = filter_pets(flat, profile)
    assert ok is True and reason is None


# --- filter_move_in -----------------------------------------------------

def test_move_in_passes_within_tolerance():
    move_in = date(2026, 6, 1)
    profile = _profile(move_in_date=move_in)
    flat = {"available_from": (move_in + timedelta(days=10)).isoformat()}
    ok, reason = filter_move_in(flat, profile)
    assert ok is True and reason is None


def test_move_in_rejects_when_too_late():
    move_in = date(2026, 6, 1)
    profile = _profile(move_in_date=move_in)
    flat = {
        "available_from": (move_in + MOVE_IN_TOLERANCE + timedelta(days=5)).isoformat(),
    }
    ok, reason = filter_move_in(flat, profile)
    assert ok is False
    assert reason == "move_in_too_late"


def test_move_in_passes_when_field_missing():
    # An unknown available-from date is tolerated (filters.py:95-96).
    profile = _profile(move_in_date=date(2026, 6, 1))
    flat: dict = {}
    ok, reason = filter_move_in(flat, profile)
    assert ok is True and reason is None


# --- filter_furnished ---------------------------------------------------

def test_furnished_passes_when_pref_is_any():
    profile = _profile(furnished_pref="any")
    flat = {"furnished": False}
    ok, reason = filter_furnished(flat, profile)
    assert ok is True and reason is None


def test_furnished_rejects_when_pref_is_furnished_but_flat_is_not():
    profile = _profile(furnished_pref="furnished")
    flat = {"furnished": False}
    ok, reason = filter_furnished(flat, profile)
    assert ok is False
    assert reason == "not_furnished"


def test_furnished_rejects_when_pref_is_unfurnished_but_flat_is():
    profile = _profile(furnished_pref="unfurnished")
    flat = {"furnished": True}
    ok, reason = filter_furnished(flat, profile)
    assert ok is False
    assert reason == "furnished_but_want_unfurnished"


def test_furnished_passes_when_field_missing():
    profile = _profile(furnished_pref="furnished")
    flat: dict = {}
    ok, reason = filter_furnished(flat, profile)
    assert ok is True and reason is None


# --- filter_contract ----------------------------------------------------

def test_contract_passes_when_profile_has_no_minimum():
    profile = _profile(min_contract_months=None)
    flat = {"min_contract_months": 1}
    ok, reason = filter_contract(flat, profile)
    assert ok is True and reason is None


def test_contract_passes_when_at_least_minimum():
    profile = _profile(min_contract_months=12)
    flat = {"min_contract_months": 24}
    ok, reason = filter_contract(flat, profile)
    assert ok is True and reason is None


def test_contract_rejects_when_shorter_than_minimum():
    profile = _profile(min_contract_months=12)
    flat = {"min_contract_months": 6}
    ok, reason = filter_contract(flat, profile)
    assert ok is False
    assert reason == "contract_too_short"


def test_contract_passes_when_flat_field_missing():
    # filters.py:122-124: missing flat min_contract_months means we don't
    # know — pass rather than reject.
    profile = _profile(min_contract_months=12)
    flat: dict = {}
    ok, reason = filter_contract(flat, profile)
    assert ok is True and reason is None


# --- filter_short_term --------------------------------------------------

def test_short_term_passes_when_disabled():
    profile = _profile(exclude_short_term=False)
    flat = {"title": "Zwischenmiete für 2 Wochen", "description": ""}
    ok, reason = filter_short_term(flat, profile)
    assert ok is True and reason is None


def test_short_term_rejects_zwischenmiete_in_title():
    profile = _profile(exclude_short_term=True)
    flat = {"title": "Zwischenmiete in Mitte", "description": ""}
    ok, reason = filter_short_term(flat, profile)
    assert ok is False
    assert reason == "short_term_listing"


def test_short_term_rejects_befristet_in_description():
    profile = _profile(exclude_short_term=True)
    flat = {"title": "Schöne 2-Zimmer-Wohnung", "description": "Befristet auf ein Jahr"}
    ok, reason = filter_short_term(flat, profile)
    assert ok is False
    assert reason == "short_term_listing"


def test_short_term_rejects_short_term_with_hyphen():
    profile = _profile(exclude_short_term=True)
    flat = {"title": "Lovely Berlin short-term flat", "description": ""}
    ok, reason = filter_short_term(flat, profile)
    assert ok is False
    assert reason == "short_term_listing"


def test_short_term_rejects_numeric_weeks():
    profile = _profile(exclude_short_term=True)
    flat = {"title": "Studio frei für 2 Wochen", "description": ""}
    ok, reason = filter_short_term(flat, profile)
    assert ok is False
    assert reason == "short_term_listing"


def test_short_term_rejects_numeric_short_months():
    profile = _profile(exclude_short_term=True)
    flat = {"title": "Wohnung", "description": "frei für 3 Monate"}
    ok, reason = filter_short_term(flat, profile)
    assert ok is False
    assert reason == "short_term_listing"


def test_short_term_passes_when_month_count_above_short_range():
    # "12 Monate" must not match \b[1-5]\s*monate?\b — only 1-5 should fire.
    profile = _profile(exclude_short_term=True)
    flat = {"title": "Wohnung ab Juni", "description": "Mindestlaufzeit 12 Monate"}
    ok, reason = filter_short_term(flat, profile)
    assert ok is True and reason is None


def test_short_term_passes_for_typical_long_term_listing():
    profile = _profile(exclude_short_term=True)
    flat = {
        "title": "Helle 2-Zimmer-Wohnung in Kreuzberg",
        "description": "Unbefristeter Mietvertrag, Erstbezug nach Sanierung.",
    }
    ok, reason = filter_short_term(flat, profile)
    assert ok is True and reason is None


def test_short_term_date_range_short_span_rejects():
    # 60-day window vs profile min of 6 months (180 days) → reject.
    profile = _profile(exclude_short_term=True, min_contract_months=6)
    flat = {
        "title": "Wohnung verfügbar",
        "description": "Verfügbar 01.06.2026 bis 31.07.2026.",
    }
    ok, reason = filter_short_term(flat, profile)
    assert ok is False
    assert reason == "short_term_listing"


def test_short_term_date_range_long_span_passes():
    profile = _profile(exclude_short_term=True, min_contract_months=6)
    flat = {
        "title": "Wohnung verfügbar",
        "description": "Verfügbar 01.06.2026 bis 30.06.2027.",
    }
    ok, reason = filter_short_term(flat, profile)
    assert ok is True and reason is None


def test_short_term_date_range_skipped_when_no_min_contract():
    # Without a profile minimum we don't second-guess a date range that
    # didn't match any keyword pattern.
    profile = _profile(exclude_short_term=True, min_contract_months=None)
    flat = {
        "title": "Wohnung",
        "description": "Vermietung 01.06.2026 bis 30.06.2026.",
    }
    ok, reason = filter_short_term(flat, profile)
    assert ok is True and reason is None


def test_short_term_passes_when_title_and_description_empty():
    profile = _profile(exclude_short_term=True)
    flat = {"title": "", "description": None}
    ok, reason = filter_short_term(flat, profile)
    assert ok is True and reason is None


# --- filter_radius ------------------------------------------------------

def test_radius_passes_when_no_home_coords():
    profile = _profile(home_lat=None, home_lng=None, radius_km=5)
    flat = {"lat": 0.0, "lng": 0.0}
    ok, reason = filter_radius(flat, profile)
    assert ok is True and reason is None


def test_radius_passes_when_within_band():
    # Berlin Hbf (~52.5251, 13.3694) to Alexanderplatz (~52.5219, 13.4132)
    # is ~3 km. Profile radius_km=10 should pass.
    profile = _profile(home_lat=52.5251, home_lng=13.3694, radius_km=10)
    flat = {"lat": 52.5219, "lng": 13.4132}
    ok, reason = filter_radius(flat, profile)
    assert ok is True and reason is None


def test_radius_rejects_outside_band():
    # Berlin to Hamburg (~280 km) — far outside any reasonable radius.
    profile = _profile(home_lat=52.5200, home_lng=13.4050, radius_km=20)
    flat = {"lat": 53.5511, "lng": 9.9937}
    ok, reason = filter_radius(flat, profile)
    assert ok is False
    assert reason == "outside_radius"


# --- evaluate() integration --------------------------------------------

def test_evaluate_returns_empty_for_passing_flat():
    profile = _profile(
        rent_min_warm=400, rent_max_warm=2000, rooms_min=1, rooms_max=4,
        district_allowlist=[], home_lat=None, home_lng=None,
        pets=[], furnished_pref="any", min_contract_months=None,
    )
    flat = {
        "rent_warm_eur": 1000,
        "rooms": 2,
        "requires_wbs": False,
    }
    assert evaluate(flat, profile) == []


def test_evaluate_returns_reasons_in_filters_order():
    # Both rent and rooms outside band → both filters fire; rent comes
    # first in FILTERS so its reason is index 0.
    profile = _profile(
        rent_min_warm=2000, rent_max_warm=2500,
        rooms_min=4, rooms_max=5,
    )
    flat = {"rent_warm_eur": 600, "rooms": 1, "requires_wbs": False}
    reasons = evaluate(flat, profile)
    assert len(reasons) >= 2
    # filter_rent_band is index 0 in FILTERS, filter_rooms_band is index 1.
    assert reasons[0] == "rent_too_low"
    assert reasons[1] == "too_few_rooms"
