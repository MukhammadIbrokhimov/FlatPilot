"""Per-match channel resolution under semantic A″.

Tests the pure helper ``_resolve_channels_for_match`` in isolation —
no DB, no env vars, no network. The helper consumes the matched-search
names + profile and returns ordered (channel, signature, transport_kwargs)
tuples for a single match row.
"""
from __future__ import annotations

from flatpilot.notifications.dispatcher import _resolve_channels_for_match
from flatpilot.profile import (
    EmailNotification,
    Notifications,
    Profile,
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotification,
    TelegramNotificationOverride,
)


def _profile(*, telegram=False, email=False, saved_searches=()) -> Profile:
    """Helper that builds a Profile with controllable notification state."""
    base = Profile.load_example()
    return base.model_copy(
        update={
            "notifications": Notifications(
                telegram=TelegramNotification(
                    enabled=telegram, bot_token_env="TELEGRAM_BOT_TOKEN", chat_id="base_chat",
                ),
                email=EmailNotification(enabled=email, smtp_env="SMTP"),
            ),
            "saved_searches": list(saved_searches),
        }
    )


def test_no_matched_searches_uses_base_only():
    p = _profile(telegram=True, email=True)
    out = _resolve_channels_for_match(p, matched_names=[])
    sigs = sorted(sig for _, sig, _ in out)
    assert sigs == ["email:base", "telegram:base"]


def test_matched_search_with_no_notifications_is_silent():
    """Non-defining matched search contributes nothing — base fires unchanged."""
    ss = SavedSearch(name="silent", notifications=None)
    p = _profile(telegram=True, email=True, saved_searches=[ss])
    out = _resolve_channels_for_match(p, matched_names=["silent"])
    sigs = sorted(sig for _, sig, _ in out)
    assert sigs == ["email:base", "telegram:base"]


def test_definer_with_telegram_only_replaces_telegram_email_inherits():
    """A″: definer covers telegram only → telegram replaced, email inherits base."""
    ss = SavedSearch(
        name="kreuzberg-2br",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="k_chat"),
        ),
    )
    p = _profile(telegram=True, email=True, saved_searches=[ss])
    out = _resolve_channels_for_match(p, matched_names=["kreuzberg-2br"])
    by_sig = {sig: kwargs for _, sig, kwargs in out}
    assert "telegram:chat=k_chat" in by_sig
    assert "email:base" in by_sig
    assert "telegram:base" not in by_sig  # base telegram was replaced


def test_definer_telegram_disabled_actively_suppresses():
    """enabled=False on a definer suppresses the channel (no fire, base does not fall through)."""
    ss = SavedSearch(
        name="quiet",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
        ),
    )
    p = _profile(telegram=True, email=True, saved_searches=[ss])
    out = _resolve_channels_for_match(p, matched_names=["quiet"])
    sigs = sorted(sig for _, sig, _ in out)
    assert sigs == ["email:base"]


def test_two_definers_distinct_chat_ids_both_fire():
    s1 = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="A"),
        ),
    )
    s2 = SavedSearch(
        name="s2",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="B"),
        ),
    )
    p = _profile(telegram=True, email=True, saved_searches=[s1, s2])
    out = _resolve_channels_for_match(p, matched_names=["s1", "s2"])
    sigs = sorted(sig for _, sig, _ in out)
    assert "telegram:chat=A" in sigs
    assert "telegram:chat=B" in sigs
    assert "email:base" in sigs


def test_two_definers_identical_overrides_collapse():
    """Identical resolved transports dedup to one signature."""
    s1 = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="SAME"),
        ),
    )
    s2 = SavedSearch(
        name="s2",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="SAME"),
        ),
    )
    p = _profile(telegram=True, email=True, saved_searches=[s1, s2])
    out = _resolve_channels_for_match(p, matched_names=["s1", "s2"])
    telegram_sigs = [sig for _, sig, _ in out if sig.startswith("telegram")]
    assert telegram_sigs == ["telegram:chat=SAME"]


def test_canonicalization_override_resolves_to_base():
    """Override that matches base values produces 'channel:base' signature."""
    s1 = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(
                enabled=True,
                bot_token_env="TELEGRAM_BOT_TOKEN",  # equals base default
                chat_id="base_chat",                  # equals base value
            ),
        ),
    )
    p = _profile(telegram=True, email=False, saved_searches=[s1])
    out = _resolve_channels_for_match(p, matched_names=["s1"])
    sigs = [sig for _, sig, _ in out]
    assert sigs == ["telegram:base"]


def test_mixed_enabled_true_and_false_on_same_channel():
    """One enabled=True wins; enabled=False contributes nothing, doesn't suppress sibling."""
    s1 = SavedSearch(
        name="on",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="K"),
        ),
    )
    s2 = SavedSearch(
        name="off",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
        ),
    )
    p = _profile(telegram=True, email=False, saved_searches=[s1, s2])
    out = _resolve_channels_for_match(p, matched_names=["on", "off"])
    sigs = [sig for _, sig, _ in out]
    assert sigs == ["telegram:chat=K"]


def test_all_enabled_false_suppresses_channel():
    s1 = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
        ),
    )
    s2 = SavedSearch(
        name="s2",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
        ),
    )
    p = _profile(telegram=True, email=False, saved_searches=[s1, s2])
    out = _resolve_channels_for_match(p, matched_names=["s1", "s2"])
    sigs = [sig for _, sig, _ in out]
    assert sigs == []  # telegram suppressed, email not enabled


def test_stale_name_in_matched_list_is_silent():
    """A name no longer in the profile is treated as a non-definer."""
    p = _profile(telegram=True, email=True)  # no saved searches
    out = _resolve_channels_for_match(p, matched_names=["deleted-search"])
    sigs = sorted(sig for _, sig, _ in out)
    assert sigs == ["email:base", "telegram:base"]


def test_partial_override_inherits_base_for_unset_fields():
    """chat_id=None falls through to base; bot_token_env override applies.

    Signature canonicalization (Section 4.3) excludes base-equal fields,
    so the signature mentions only bot_token_env. transport_kwargs still
    threads the resolved chat_id through to the adapter.
    """
    s1 = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(
                enabled=True, bot_token_env="ALT_TOKEN", chat_id=None,
            ),
        ),
    )
    p = _profile(telegram=True, email=False, saved_searches=[s1])
    out = _resolve_channels_for_match(p, matched_names=["s1"])
    assert len(out) == 1
    channel, signature, kwargs = out[0]
    assert channel == "telegram"
    assert signature == "telegram:bot=ALT_TOKEN"
    # Resolved transport carries both fields so the adapter doesn't
    # need to consult the profile for chat_id.
    assert kwargs["bot_token_env"] == "ALT_TOKEN"
    assert kwargs["chat_id"] == "base_chat"


def test_all_none_override_plus_chat_override_both_fire():
    """Spec §4.1 worked example B: definer X all-None enabled=True + definer Y chat=B
    → both signatures fire (telegram:base AND telegram:chat=B)."""
    x = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True),  # all transport None
        ),
    )
    y = SavedSearch(
        name="y",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="B"),
        ),
    )
    p = _profile(telegram=True, email=False, saved_searches=[x, y])
    out = _resolve_channels_for_match(p, matched_names=["x", "y"])
    sigs = sorted(sig for _, sig, _ in out)
    assert sigs == ["telegram:base", "telegram:chat=B"]
