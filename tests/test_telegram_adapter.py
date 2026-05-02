"""Override-kwarg coverage for notifications.telegram."""
from __future__ import annotations

import pytest

from flatpilot.notifications import telegram as telegram_adapter
from flatpilot.profile import Profile


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


def _enable_telegram(profile: Profile, *, bot_token_env="TELEGRAM_BOT_TOKEN", chat_id="111") -> Profile:
    return profile.model_copy(
        update={
            "notifications": profile.notifications.model_copy(
                update={
                    "telegram": profile.notifications.telegram.model_copy(
                        update={"enabled": True, "bot_token_env": bot_token_env, "chat_id": chat_id}
                    )
                }
            )
        }
    )


def test_send_uses_profile_token_and_chat_when_no_overrides(monkeypatch):
    profile = _enable_telegram(Profile.load_example())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t-base")

    captured: dict = {}
    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["chat_id"] = json["chat_id"]
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(telegram_adapter.httpx, "post", _fake_post)
    telegram_adapter.send(profile, "hello")
    assert "/bott-base/" in captured["url"]
    assert captured["chat_id"] == "111"


def test_send_chat_id_override(monkeypatch):
    profile = _enable_telegram(Profile.load_example())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t-base")

    captured: dict = {}
    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["chat_id"] = json["chat_id"]
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(telegram_adapter.httpx, "post", _fake_post)
    telegram_adapter.send(profile, "hello", chat_id="999")
    assert "/bott-base/" in captured["url"]
    assert captured["chat_id"] == "999"


def test_send_bot_token_env_override(monkeypatch):
    profile = _enable_telegram(Profile.load_example())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t-base")
    monkeypatch.setenv("ROOMMATE_BOT_TOKEN", "t-room")

    captured: dict = {}
    def _fake_post(url, json, timeout):
        captured["url"] = url
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(telegram_adapter.httpx, "post", _fake_post)
    telegram_adapter.send(profile, "hello", bot_token_env="ROOMMATE_BOT_TOKEN")
    assert "/bott-room/" in captured["url"]


def test_send_missing_override_token_raises(monkeypatch):
    profile = _enable_telegram(Profile.load_example())
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    with pytest.raises(telegram_adapter.TelegramError) as exc:
        telegram_adapter.send(profile, "hello", bot_token_env="MISSING_TOKEN")
    assert "MISSING_TOKEN" in str(exc.value)


def test_send_skips_when_disabled_at_profile(monkeypatch):
    """Override kwargs do NOT bypass the profile.enabled gate."""
    profile = Profile.load_example()  # telegram disabled by default
    sent = False

    def _fake_post(*a, **kw):
        nonlocal sent
        sent = True
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(telegram_adapter.httpx, "post", _fake_post)
    telegram_adapter.send(profile, "hello", chat_id="999")
    assert sent is False
