"""Coverage for the saved-search notifications doctor row."""
from __future__ import annotations

from flatpilot.doctor import _check_saved_search_notifications
from flatpilot.profile import (
    EmailNotificationOverride,
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotificationOverride,
)


def _profile_with_searches(searches):
    from flatpilot.profile import Profile
    base = Profile.load_example()
    return base.model_copy(update={"saved_searches": list(searches)})


def _patch_profile(monkeypatch, searches):
    monkeypatch.setattr(
        "flatpilot.doctor._safe_load_profile",
        lambda: (_profile_with_searches(searches), None),
    )


def test_no_overrides_returns_ok(monkeypatch):
    _patch_profile(monkeypatch, [])
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
    _patch_profile(monkeypatch, [ss])
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
    _patch_profile(monkeypatch, [ss])
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
    _patch_profile(monkeypatch, [ss])
    monkeypatch.delenv("WHATEVER", raising=False)
    status, _ = _check_saved_search_notifications()
    assert status == "OK"


def test_email_override_with_missing_smtp_env_optional(monkeypatch):
    """smtp_env prefix set but the resolved <prefix>_HOST is unset → optional."""
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            email=EmailNotificationOverride(enabled=True, smtp_env="ROOMMATE_SMTP"),
        ),
    )
    _patch_profile(monkeypatch, [ss])
    monkeypatch.delenv("ROOMMATE_SMTP_HOST", raising=False)
    status, msg = _check_saved_search_notifications()
    assert status == "optional"
    assert "ROOMMATE_SMTP_HOST" in msg


def test_email_override_with_present_smtp_env_passes(monkeypatch):
    """smtp_env prefix set and <prefix>_HOST present → OK."""
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            email=EmailNotificationOverride(enabled=True, smtp_env="ROOMMATE_SMTP"),
        ),
    )
    _patch_profile(monkeypatch, [ss])
    monkeypatch.setenv("ROOMMATE_SMTP_HOST", "smtp.roommate.com")
    status, _ = _check_saved_search_notifications()
    assert status == "OK"


def test_both_channels_count_as_one_override(monkeypatch):
    """A single saved search with both telegram + email enabled counts as 1 override, not 2."""
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, bot_token_env="BOTH_TOKEN"),
            email=EmailNotificationOverride(enabled=True, smtp_env="BOTH_SMTP"),
        ),
    )
    _patch_profile(monkeypatch, [ss])
    monkeypatch.setenv("BOTH_TOKEN", "tok")
    monkeypatch.setenv("BOTH_SMTP_HOST", "smtp.both.com")
    status, msg = _check_saved_search_notifications()
    assert status == "OK"
    assert "1 override" in msg  # not "2 override"
