"""Coverage for the saved-search notifications doctor row."""
from __future__ import annotations

from flatpilot.doctor import _check_saved_search_notifications
from flatpilot.profile import (
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotificationOverride,
)


def _profile_with_searches(searches):
    from flatpilot.profile import Profile
    base = Profile.load_example()
    return base.model_copy(update={"saved_searches": list(searches)})


def test_no_overrides_returns_ok(monkeypatch):
    monkeypatch.setattr("flatpilot.doctor._safe_load_profile", lambda: (_profile_with_searches([]), None))
    status, msg = _check_saved_search_notifications()
    assert status == "OK"
    assert "0 override" in msg.lower() or "no override" in msg.lower()


def test_override_with_present_env_var_passes(monkeypatch):
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, bot_token_env="ROOMMATE_BOT_TOKEN"),
        ),
    )
    monkeypatch.setattr("flatpilot.doctor._safe_load_profile", lambda: (_profile_with_searches([ss]), None))
    monkeypatch.setenv("ROOMMATE_BOT_TOKEN", "tok")
    status, _ = _check_saved_search_notifications()
    assert status == "OK"


def test_override_with_missing_env_var_optional(monkeypatch):
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, bot_token_env="MISSING_BOT_TOKEN"),
        ),
    )
    monkeypatch.setattr("flatpilot.doctor._safe_load_profile", lambda: (_profile_with_searches([ss]), None))
    monkeypatch.delenv("MISSING_BOT_TOKEN", raising=False)
    status, msg = _check_saved_search_notifications()
    assert status == "optional"
    assert "MISSING_BOT_TOKEN" in msg


def test_disabled_override_skipped(monkeypatch):
    """enabled=False overrides don't need env vars to resolve."""
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False, bot_token_env="WHATEVER"),
        ),
    )
    monkeypatch.setattr("flatpilot.doctor._safe_load_profile", lambda: (_profile_with_searches([ss]), None))
    monkeypatch.delenv("WHATEVER", raising=False)
    status, _ = _check_saved_search_notifications()
    assert status == "OK"
