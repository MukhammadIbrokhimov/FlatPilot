from __future__ import annotations

from flatpilot.auto_apply import overlay_profile
from flatpilot.profile import Profile, SavedSearch


def _base():
    return Profile.load_example()


def test_none_overlay_returns_base_profile():
    base = _base()
    result = overlay_profile(base, None)
    assert result.rent_max_warm == base.rent_max_warm
    assert result.district_allowlist == base.district_allowlist


def test_scalar_overrides_apply():
    base = _base()
    ss = SavedSearch(name="x", rent_max_warm=999, rooms_min=1)
    result = overlay_profile(base, ss)
    assert result.rent_max_warm == 999
    assert result.rooms_min == 1
    assert result.rent_min_warm == base.rent_min_warm


def test_district_list_none_inherits():
    base = _base().model_copy(update={"district_allowlist": ["Mitte"]})
    ss = SavedSearch(name="x")
    result = overlay_profile(base, ss)
    assert result.district_allowlist == ["Mitte"]


def test_district_list_empty_overrides_to_empty():
    base = _base().model_copy(update={"district_allowlist": ["Mitte"]})
    ss = SavedSearch(name="x", district_allowlist=[])
    result = overlay_profile(base, ss)
    assert result.district_allowlist == []


def test_overlay_does_not_mutate_inputs():
    base = _base()
    base_rent = base.rent_max_warm
    ss = SavedSearch(name="x", rent_max_warm=base_rent + 500)
    _ = overlay_profile(base, ss)
    assert base.rent_max_warm == base_rent
