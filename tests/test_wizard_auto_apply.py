from __future__ import annotations

from flatpilot.profile import Profile, SavedSearch
from flatpilot.wizard.init import _maybe_add_auto_apply


def test_no_existing_yes_appends_starter():
    base = Profile.load_example()
    out = _maybe_add_auto_apply(base, answer=True)
    assert len(out.saved_searches) == 1
    assert out.saved_searches[0].name == "auto-default"
    assert out.saved_searches[0].auto_apply is True


def test_no_existing_no_returns_unchanged():
    base = Profile.load_example()
    out = _maybe_add_auto_apply(base, answer=False)
    assert out.saved_searches == []


def test_existing_auto_default_short_circuits():
    base = Profile.load_example().model_copy(
        update={
            "saved_searches": [
                SavedSearch(name="auto-default", auto_apply=True, rent_max_warm=999)
            ]
        }
    )
    out = _maybe_add_auto_apply(base, answer=True)
    assert len(out.saved_searches) == 1
    assert out.saved_searches[0].rent_max_warm == 999
