from __future__ import annotations

import pytest
from pydantic import ValidationError

from flatpilot.profile import Profile, SavedSearch


def test_minimal_saved_search_loads():
    ss = SavedSearch(name="kreuzberg-2br")
    assert ss.name == "kreuzberg-2br"
    assert ss.auto_apply is False
    assert ss.platforms == []
    assert ss.rent_max_warm is None


def test_name_regex_rejects_uppercase():
    with pytest.raises(ValidationError):
        SavedSearch(name="Kreuzberg")


def test_name_regex_rejects_spaces():
    with pytest.raises(ValidationError):
        SavedSearch(name="kreuzberg 2br")


def test_name_regex_accepts_underscore_hyphen_digits():
    SavedSearch(name="my_search-1")


def test_rent_range_validator():
    with pytest.raises(ValidationError):
        SavedSearch(name="x", rent_min_warm=1500, rent_max_warm=1000)


def test_rooms_range_validator():
    with pytest.raises(ValidationError):
        SavedSearch(name="x", rooms_min=3, rooms_max=2)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        SavedSearch(name="x", unknown_field=42)


def test_overlay_fields_default_to_none():
    ss = SavedSearch(name="x")
    for field in (
        "rent_min_warm", "rent_max_warm", "rooms_min", "rooms_max",
        "district_allowlist", "radius_km", "furnished_pref",
        "min_contract_months",
    ):
        assert getattr(ss, field) is None, field


def test_platforms_defaults_to_empty_list_not_none():
    ss = SavedSearch(name="x")
    assert ss.platforms == []


def test_profile_loads_with_default_auto_apply_block():
    p = Profile.load_example()
    assert p.auto_apply.daily_cap_per_platform["wg-gesucht"] == 20
    assert p.saved_searches == []


def test_profile_accepts_saved_searches(tmp_path):
    profile_dict = Profile.load_example().model_dump(mode="json")
    profile_dict["saved_searches"] = [
        {"name": "ss1", "auto_apply": True, "rent_max_warm": 1200},
        {"name": "ss2", "auto_apply": False},
    ]
    p = Profile.model_validate(profile_dict)
    assert len(p.saved_searches) == 2
    assert p.saved_searches[0].auto_apply is True
    assert p.saved_searches[1].rent_max_warm is None


def test_profile_rejects_duplicate_saved_search_names():
    profile_dict = Profile.load_example().model_dump(mode="json")
    profile_dict["saved_searches"] = [
        {"name": "dup", "auto_apply": True},
        {"name": "dup", "auto_apply": False},
    ]
    with pytest.raises(ValidationError, match="duplicate"):
        Profile.model_validate(profile_dict)


def test_profile_hash_changes_when_saved_searches_added():
    from flatpilot.profile import profile_hash

    base = Profile.load_example()
    h_before = profile_hash(base)

    with_ss = base.model_copy(
        update={"saved_searches": [SavedSearch(name="x", auto_apply=True)]}
    )
    h_after = profile_hash(with_ss)
    assert h_before != h_after


def test_example_profile_demonstrates_auto_apply_shape():
    p = Profile.load_example()
    assert "wg-gesucht" in p.auto_apply.daily_cap_per_platform
    assert isinstance(p.saved_searches, list)
