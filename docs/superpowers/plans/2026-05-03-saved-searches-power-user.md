# Saved-Searches Power-User Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle FlatPilot-6o3 (per-saved-search notification routing) and FlatPilot-d36 (wizard menu + cap/cooldown tuning) into a single PR on `feat/saved-searches-power-user`.

**Architecture:** Pydantic adds `SavedSearchNotifications` with optional per-channel transport overrides. Dispatcher does per-match channel resolution under semantic A″ (definers replace base for the channels they define; non-definers contribute nothing; `enabled=False` actively suppresses). `notified_channels_json` evolves from bare channel names to canonicalized transport signatures (`telegram:base`, `telegram:chat=...`) with backwards-compat read parsing. Wizard replaces its single `auto-default` y/N with a menu loop (`[a]dd / [e]dit N / [d]elete N / [c]aps & cooldowns / [done]`).

**Tech Stack:** Python 3.11, pydantic v2, SQLite, typer + rich, pytest. Existing test fixture `tmp_db` (`tests/conftest.py`) handles isolation.

**Spec:** `docs/superpowers/specs/2026-05-02-saved-searches-power-user-design.md`

**Branch:** `feat/saved-searches-power-user` (already created, two spec commits).

**Commit author:** `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>` (already configured locally). NO AI co-author trailers. Reference bead IDs in every commit message.

---

## Task 1: Schema — `SavedSearchNotifications` and override models

**Files:**
- Modify: `src/flatpilot/profile.py`
- Test: `tests/test_saved_search_schema.py`

- [ ] **Step 1: Write the failing tests**

Append these tests to `tests/test_saved_search_schema.py`:

```python
from flatpilot.profile import (
    EmailNotificationOverride,
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotificationOverride,
)


def test_saved_search_notifications_default_none():
    ss = SavedSearch(name="x")
    assert ss.notifications is None


def test_saved_search_notifications_round_trip_none():
    ss = SavedSearch(name="x", notifications=None)
    payload = ss.model_dump_json()
    restored = SavedSearch.model_validate_json(payload)
    assert restored.notifications is None


def test_telegram_override_field_defaults():
    o = TelegramNotificationOverride(enabled=True)
    assert o.enabled is True
    assert o.bot_token_env is None
    assert o.chat_id is None


def test_email_override_field_defaults():
    o = EmailNotificationOverride(enabled=True)
    assert o.enabled is True
    assert o.smtp_env is None


def test_telegram_override_round_trip_preserves_none():
    o = TelegramNotificationOverride(enabled=True, bot_token_env=None, chat_id=None)
    restored = TelegramNotificationOverride.model_validate_json(o.model_dump_json())
    assert restored.bot_token_env is None
    assert restored.chat_id is None


def test_saved_search_notifications_extra_forbid():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SavedSearchNotifications(unknown_channel={"enabled": True})


def test_telegram_override_extra_forbid():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TelegramNotificationOverride(enabled=True, unknown_field="x")


def test_saved_search_with_full_notifications():
    ss = SavedSearch(
        name="kreuzberg-2br",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="k_chat"),
            email=EmailNotificationOverride(enabled=False),
        ),
    )
    assert ss.notifications.telegram.chat_id == "k_chat"
    assert ss.notifications.email.enabled is False


def test_saved_search_explicit_silence_is_valid():
    """Both channels enabled=False is structurally valid (per-search opt-out)."""
    ss = SavedSearch(
        name="x",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
            email=EmailNotificationOverride(enabled=False),
        ),
    )
    assert ss.notifications.telegram.enabled is False
    assert ss.notifications.email.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_saved_search_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'SavedSearchNotifications'`.

- [ ] **Step 3: Add the new pydantic models to `profile.py`**

Insert these classes in `src/flatpilot/profile.py` immediately before the `class SavedSearch(BaseModel):` definition (around line 104):

```python
class TelegramNotificationOverride(BaseModel):
    """Saved-search-scoped override of base profile's telegram channel.

    Any transport field left as ``None`` falls through to the base profile's
    value at dispatch time. ``enabled=False`` actively suppresses the channel
    for matches against this saved search.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    bot_token_env: str | None = None
    chat_id: str | None = None


class EmailNotificationOverride(BaseModel):
    """Saved-search-scoped override of base profile's email channel."""
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    smtp_env: str | None = None


class SavedSearchNotifications(BaseModel):
    """Per-saved-search notification routing override.

    A non-None block on a saved search marks that search as a *definer* for
    every channel it specifies. The dispatcher resolves channels per-match:
    definers replace base for the channels they define; non-defining matched
    searches contribute nothing.

    NOTE: ``extra="forbid"`` will reject any future channel addition until
    that channel field is explicitly added below. Typo protection beats
    silent acceptance.
    """
    model_config = ConfigDict(extra="forbid")

    telegram: TelegramNotificationOverride | None = None
    email: EmailNotificationOverride | None = None
```

Then add the `notifications` field to `SavedSearch` (immediately after `platforms: list[str] = Field(default_factory=list)` at line 119):

```python
    notifications: SavedSearchNotifications | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_saved_search_schema.py -v`
Expected: PASS for all new tests + existing tests still pass.

- [ ] **Step 5: Run the full test suite to confirm nothing broke**

Run: `pytest -x`
Expected: ALL PASS (the new optional `notifications` field defaults to `None` so existing profile fixtures load unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/profile.py tests/test_saved_search_schema.py
git commit -m "FlatPilot-6o3: add SavedSearchNotifications schema with optional channel overrides"
```

---

## Task 2: Wire `EmailNotification.smtp_env` prefix into the email adapter

**Why this is its own task:** `profile.py` documents `smtp_env` as a prefix used to look up `SMTP_HOST`, `SMTP_PORT`, etc., but `notifications/email.py` ignores the prefix and hardcodes `SMTP_*` names. Saved-search overrides setting `smtp_env="ROOMMATE_SMTP"` would do nothing without this fix. Wiring it up first means the override kwarg in Task 4 has somewhere to land.

**Files:**
- Modify: `src/flatpilot/notifications/email.py`
- Modify: `src/flatpilot/notifications/dispatcher.py:67-70` (the `_email_recipient` helper)
- Test: `tests/test_email_adapter.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_email_adapter.py`:

```python
"""Coverage for the SMTP env-prefix resolution in notifications.email."""
from __future__ import annotations

import pytest

from flatpilot.notifications import email as email_adapter


def test_send_uses_smtp_env_prefix(monkeypatch):
    """Default prefix 'SMTP' resolves to SMTP_HOST etc."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("SMTP_FROM", "from@x.com")

    captured: dict = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["host"] = host
            captured["port"] = port
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def starttls(self):
            pass
        def login(self, u, p):
            captured["user"] = u
        def send_message(self, msg):
            captured["msg_from"] = msg["From"]

    monkeypatch.setattr(email_adapter.smtplib, "SMTP", _FakeSMTP)

    email_adapter.send("to@x.com", "subj", "plain body")

    assert captured["host"] == "smtp.example.com"
    assert captured["port"] == 587
    assert captured["user"] == "u"
    assert captured["msg_from"] == "from@x.com"


def test_send_uses_custom_smtp_env_prefix(monkeypatch):
    """smtp_env='ROOMMATE' resolves to ROOMMATE_HOST etc., not SMTP_HOST."""
    # Set both prefixes; the call should use only ROOMMATE_*.
    monkeypatch.setenv("SMTP_HOST", "should-not-be-used")
    monkeypatch.setenv("ROOMMATE_HOST", "smtp.roommate.com")
    monkeypatch.setenv("ROOMMATE_PORT", "465")
    monkeypatch.setenv("ROOMMATE_USER", "ru")
    monkeypatch.setenv("ROOMMATE_PASSWORD", "rp")
    monkeypatch.setenv("ROOMMATE_FROM", "from@roommate.com")

    captured: dict = {}

    class _FakeSMTPSSL:
        def __init__(self, host, port, timeout):
            captured["host"] = host
            captured["port"] = port
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def login(self, u, p):
            captured["user"] = u
        def send_message(self, msg):
            captured["msg_from"] = msg["From"]

    monkeypatch.setattr(email_adapter.smtplib, "SMTP_SSL", _FakeSMTPSSL)

    email_adapter.send("to@x.com", "subj", "plain body", smtp_env="ROOMMATE")

    assert captured["host"] == "smtp.roommate.com"
    assert captured["port"] == 465
    assert captured["user"] == "ru"
    assert captured["msg_from"] == "from@roommate.com"


def test_send_missing_prefixed_env_raises(monkeypatch):
    """Missing prefixed env var produces an EmailError naming the missing names."""
    monkeypatch.delenv("CUSTOM_HOST", raising=False)
    monkeypatch.delenv("CUSTOM_PORT", raising=False)
    monkeypatch.delenv("CUSTOM_USER", raising=False)
    monkeypatch.delenv("CUSTOM_PASSWORD", raising=False)
    monkeypatch.delenv("CUSTOM_FROM", raising=False)

    with pytest.raises(email_adapter.EmailError) as exc:
        email_adapter.send("to@x.com", "subj", "body", smtp_env="CUSTOM")
    assert "CUSTOM_HOST" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_adapter.py -v`
Expected: FAIL — `send()` doesn't accept `smtp_env` kwarg yet.

- [ ] **Step 3: Replace `notifications/email.py` to honor the prefix**

Replace `src/flatpilot/notifications/email.py` with:

```python
"""SMTP email adapter.

Credentials come from the environment using a configurable prefix.
The default prefix is ``SMTP`` (so ``SMTP_HOST``, ``SMTP_PORT``,
``SMTP_USER``, ``SMTP_PASSWORD``, ``SMTP_FROM``). Saved-search-scoped
overrides may pass a different prefix (e.g. ``ROOMMATE_SMTP``) to route
notifications through a separate SMTP account.

Port 465 triggers ``SMTP_SSL``; any other port opens a plain connection
and upgrades via STARTTLS.

Note: this module lives at ``flatpilot.notifications.email`` — the stdlib
``email`` package is still imported correctly below thanks to Python 3's
absolute imports.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


_REQUIRED_SUFFIXES = ("HOST", "PORT", "USER", "PASSWORD", "FROM")
_TIMEOUT = 30.0


class EmailError(RuntimeError):
    pass


def _env_names(prefix: str) -> tuple[str, ...]:
    return tuple(f"{prefix}_{suffix}" for suffix in _REQUIRED_SUFFIXES)


def send(
    to: str,
    subject: str,
    plain: str,
    html: str | None = None,
    *,
    smtp_env: str | None = None,
) -> None:
    prefix = smtp_env if smtp_env is not None else "SMTP"
    names = _env_names(prefix)
    values = {name: os.environ.get(name) for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise EmailError(f"SMTP config missing: {', '.join(missing)}")

    host_key, port_key, user_key, password_key, from_key = names
    try:
        port = int(values[port_key])  # type: ignore[arg-type]
    except ValueError as exc:
        raise EmailError(f"{port_key} must be an integer, got {values[port_key]!r}") from exc

    msg = EmailMessage()
    msg["From"] = values[from_key]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(plain)
    if html:
        msg.add_alternative(html, subtype="html")

    host: str = values[host_key]  # type: ignore[assignment]
    user: str = values[user_key]  # type: ignore[assignment]
    password: str = values[password_key]  # type: ignore[assignment]

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Email send failed: {exc}") from exc

    logger.info("Email delivered to %s via %s:%d", to, host, port)
```

- [ ] **Step 4: Update the dispatcher's `_email_recipient` helper**

In `src/flatpilot/notifications/dispatcher.py`, look at `_email_recipient` (~line 67):

```python
def _email_recipient() -> str | None:
    return os.environ.get("EMAIL_TO") or os.environ.get("SMTP_FROM")
```

Replace with a prefix-aware version:

```python
def _email_recipient(smtp_env: str | None = None) -> str | None:
    """``EMAIL_TO`` is a global override; otherwise resolve <prefix>_FROM."""
    prefix = smtp_env if smtp_env is not None else "SMTP"
    return os.environ.get("EMAIL_TO") or os.environ.get(f"{prefix}_FROM")
```

The single existing call site (in `_send`) stays unchanged for now; Task 6 updates it to thread through the override.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_email_adapter.py tests/test_notifications_dispatcher.py -v`
Expected: PASS for the new tests; existing dispatcher tests still pass (they don't exercise the smtp_env path).

- [ ] **Step 6: Run the full test suite**

Run: `pytest -x`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/flatpilot/notifications/email.py src/flatpilot/notifications/dispatcher.py tests/test_email_adapter.py
git commit -m "FlatPilot-6o3: wire EmailNotification.smtp_env prefix into email adapter"
```

---

## Task 3: Telegram adapter — add override kwargs

**Files:**
- Modify: `src/flatpilot/notifications/telegram.py`
- Test: `tests/test_telegram_adapter.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_telegram_adapter.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_adapter.py -v`
Expected: FAIL — `send()` doesn't accept `bot_token_env` / `chat_id` kwargs.

- [ ] **Step 3: Update `telegram.py` `send()` signature**

Replace the `send()` function in `src/flatpilot/notifications/telegram.py` with:

```python
def send(
    profile: Profile,
    text: str,
    *,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = False,
    bot_token_env: str | None = None,
    chat_id: str | None = None,
) -> None:
    tg = profile.notifications.telegram
    if not tg.enabled:
        logger.debug("Telegram notifications disabled in profile; skipping send")
        return

    resolved_token_env = bot_token_env if bot_token_env is not None else tg.bot_token_env
    resolved_chat_id = chat_id if chat_id is not None else tg.chat_id

    token = os.environ.get(resolved_token_env)
    if not token:
        raise TelegramError(f"bot token not found in env var {resolved_token_env!r}")
    if not resolved_chat_id:
        raise TelegramError("chat_id not configured (override or base both empty)")

    url = f"{API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": resolved_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TelegramError(f"Telegram send failed: {exc}") from exc

    body = response.json()
    if not body.get("ok"):
        raise TelegramError(
            f"Telegram API returned ok=false: {body.get('description', 'unknown error')}"
        )

    logger.info("Telegram message delivered to chat %s", resolved_chat_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_adapter.py tests/test_notifications_dispatcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/notifications/telegram.py tests/test_telegram_adapter.py
git commit -m "FlatPilot-6o3: add bot_token_env/chat_id override kwargs to telegram.send"
```

---

## Task 4: Email adapter — already covered

Task 2 already added `smtp_env` kwarg support to `email.send()`. No new code needed; verify by re-reading the signature.

- [ ] **Step 1: Verify signature matches spec**

Run: `grep -n "^def send" src/flatpilot/notifications/email.py`
Expected: signature includes `*, smtp_env: str | None = None`.

If not, return to Task 2.

---

## Task 5: Dispatcher helper — `_resolve_channels_for_match`

**Files:**
- Modify: `src/flatpilot/notifications/dispatcher.py`
- Test: `tests/test_dispatcher_resolve_channels.py` (new file — keeps the helper tests focused)

This is a pure function that consumes the matched-search names and the profile, producing the (channel, signature, transport_kwargs) tuples for one match row. Pure function ⇒ easy to test exhaustively without DB setup.

- [ ] **Step 1: Define the data shape and write the failing tests**

Create `tests/test_dispatcher_resolve_channels.py`:

```python
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
    EmailNotificationOverride,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dispatcher_resolve_channels.py -v`
Expected: FAIL — `_resolve_channels_for_match` not defined.

- [ ] **Step 3: Implement the helper in `dispatcher.py`**

Add this section near the top of `src/flatpilot/notifications/dispatcher.py`, right after the existing imports and the `_SYNTHETIC_FLAT` block:

```python
# Per-match channel resolution under semantic A″ (per-channel replace).
# See docs/superpowers/specs/2026-05-02-saved-searches-power-user-design.md §4.
#
# Output tuple shape: (channel, signature, transport_kwargs).
# - channel: "telegram" or "email"
# - signature: canonicalized string used as the dedup key in
#   notified_channels_json. Always "<channel>:base" when the resolved
#   transport equals base profile's transport for every field.
# - transport_kwargs: dict passed to the adapter's send(); keys are
#   override fields that differ from base. Empty dict when signature is
#   "<channel>:base" — signals "no override needed."

_TELEGRAM_FIELDS = ("bot_token_env", "chat_id")
_EMAIL_FIELDS = ("smtp_env",)


def _resolve_channel(
    *,
    channel: str,
    fields: tuple[str, ...],
    base_cfg: Any,
    overrides: list[Any],
) -> list[tuple[str, str, dict[str, str]]]:
    """Resolve one channel for one match row. Pure function.

    ``base_cfg`` is the corresponding ``profile.notifications.<channel>``
    block (e.g. ``profile.notifications.telegram``). ``overrides`` is the
    ordered list of non-None per-search override blocks for this channel
    (from definers in the matched-search list).

    Returns 0+ (channel, signature, transport_kwargs) tuples, deduped by
    canonicalized signature.
    """
    if not overrides:
        # No definers for this channel → base fires if enabled.
        if base_cfg.enabled:
            return [(channel, f"{channel}:base", {})]
        return []

    # Definers replace base. Filter to enabled=True overrides.
    enabled_overrides = [o for o in overrides if o.enabled]
    if not enabled_overrides:
        # All definers actively suppressed the channel.
        return []

    seen: dict[str, tuple[str, str, dict[str, str]]] = {}
    for override in enabled_overrides:
        kwargs: dict[str, str] = {}
        differs_from_base = False
        for field in fields:
            override_value = getattr(override, field)
            base_value = getattr(base_cfg, field)
            resolved = override_value if override_value is not None else base_value
            kwargs[field] = resolved
            if resolved != base_value:
                differs_from_base = True

        if not differs_from_base:
            signature = f"{channel}:base"
            transport_kwargs: dict[str, str] = {}
        else:
            signature = f"{channel}:" + ",".join(
                f"{field.split('_')[0]}={kwargs[field]}"
                for field in fields
                if kwargs[field] != getattr(base_cfg, field)
            )
            # transport_kwargs threads through ALL resolved values so the
            # adapter doesn't have to consult the profile for the fields
            # the dispatcher already resolved.
            transport_kwargs = dict(kwargs)

        seen[signature] = (channel, signature, transport_kwargs)

    return list(seen.values())


def _resolve_channels_for_match(
    profile: Profile,
    matched_names: list[str],
) -> list[tuple[str, str, dict[str, str]]]:
    """Top-level per-match resolver. See module-level docstring."""
    saved_by_name = {ss.name: ss for ss in profile.saved_searches}

    telegram_overrides = []
    email_overrides = []
    for name in matched_names:
        ss = saved_by_name.get(name)
        if ss is None:
            logger.debug("dispatch: matched-search name %r not in profile (stale row)", name)
            continue
        if ss.notifications is None:
            continue
        if ss.notifications.telegram is not None:
            telegram_overrides.append(ss.notifications.telegram)
        if ss.notifications.email is not None:
            email_overrides.append(ss.notifications.email)

    out: list[tuple[str, str, dict[str, str]]] = []
    out.extend(_resolve_channel(
        channel="telegram",
        fields=_TELEGRAM_FIELDS,
        base_cfg=profile.notifications.telegram,
        overrides=telegram_overrides,
    ))
    out.extend(_resolve_channel(
        channel="email",
        fields=_EMAIL_FIELDS,
        base_cfg=profile.notifications.email,
        overrides=email_overrides,
    ))
    return out
```

Add `from typing import Any` to the existing imports if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dispatcher_resolve_channels.py -v`
Expected: PASS for all 11 tests.

If any fail, fix the helper. Common issues: signature ordering nondeterminism (use `sorted()` over fields), `kwargs[field]` typing (None vs str).

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/notifications/dispatcher.py tests/test_dispatcher_resolve_channels.py
git commit -m "FlatPilot-6o3: add _resolve_channels_for_match per-match resolver under semantic A″"
```

---

## Task 6: Restructure `dispatch_pending` loop body

**Files:**
- Modify: `src/flatpilot/notifications/dispatcher.py:119-197`
- Test: `tests/test_notifications_dispatcher.py` (extend)
- Test: `tests/test_dispatcher_signatures.py` (new file)

The existing loop iterates `enabled_channels(profile)` (a per-run global list of bare channel names). Under A″, the channel set is per-match-row, so the loop body must shift from "iterate global enabled channels" to "iterate this row's resolved tuples." `sent_canonicals` becomes `set[signature]`. `notified_channels_json` reads parse legacy bare names as `<ch>:base` and writes use the new signature format.

- [ ] **Step 1: Write failing tests for signature-based dedup and backwards-compat**

Create `tests/test_dispatcher_signatures.py`:

```python
"""Signature-based dedup, canonicalization, backwards-compat parse.

Higher-level than test_dispatcher_resolve_channels.py: exercises
dispatch_pending against a real DB (tmp_db fixture) so the writeback
format and read-side parse are covered end-to-end.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import flatpilot.notifications.dispatcher as disp
from flatpilot.profile import (
    EmailNotification,
    Notifications,
    Profile,
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotification,
    TelegramNotificationOverride,
    profile_hash,
)


def _profile_with(*, telegram=False, email=False, saved_searches=()) -> Profile:
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


def _seed_flat_match(conn, *, profile_hash_value, matched_names="[]", external_id="e1"):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
           VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, ?, 0)""",
        (external_id, now, now),
    )
    flat_id = cur.lastrowid
    conn.execute(
        """INSERT INTO matches
            (flat_id, profile_version_hash, decision, decision_reasons_json,
             decided_at, matched_saved_searches_json)
           VALUES (?, ?, 'match', '[]', ?, ?)""",
        (flat_id, profile_hash_value, now, matched_names),
    )
    return flat_id


def test_dispatch_no_matched_searches_writes_signature_format(tmp_db, monkeypatch):
    """Even legacy-style matches now persist new-format signatures."""
    profile = _profile_with(telegram=True, email=True)
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(tmp_db, profile_hash_value=phash)

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    row = tmp_db.execute(
        "SELECT notified_channels_json FROM matches WHERE flat_id=?",
        (flat_id,),
    ).fetchone()
    notified = sorted(json.loads(row["notified_channels_json"]))
    assert notified == ["email:base", "telegram:base"]


def test_dispatch_legacy_bare_names_dedup(tmp_db, monkeypatch):
    """An existing row with ['telegram'] is treated as already-fired for telegram:base."""
    profile = _profile_with(telegram=True, email=True)
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(tmp_db, profile_hash_value=phash)
    # Pre-set notified_channels_json to legacy format with telegram only.
    tmp_db.execute(
        "UPDATE matches SET notified_channels_json='[\"telegram\"]' WHERE flat_id=?",
        (flat_id,),
    )

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    # Only email should fire — telegram is already in legacy notified set.
    sent_channels = [s[0] for s in sends]
    assert sent_channels == ["email"]

    # Writeback upgraded the row to signature format.
    row = tmp_db.execute(
        "SELECT notified_channels_json FROM matches WHERE flat_id=?",
        (flat_id,),
    ).fetchone()
    notified = sorted(json.loads(row["notified_channels_json"]))
    assert notified == ["email:base", "telegram:base"]


def test_dispatch_definer_replaces_base_telegram(tmp_db, monkeypatch):
    """telegram override + email base = override telegram fires, base email fires."""
    ss = SavedSearch(
        name="kreuzberg-2br",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="k_chat"),
        ),
    )
    profile = _profile_with(telegram=True, email=True, saved_searches=[ss])
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='["kreuzberg-2br"]',
    )

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    by_channel = {s[0]: s[1] for s in sends}
    assert "telegram" in by_channel
    assert by_channel["telegram"].get("chat_id") == "k_chat"
    assert "email" in by_channel
    assert by_channel["email"] == {} or by_channel["email"].get("smtp_env") == "SMTP"


def test_dispatch_explicit_suppress_via_enabled_false(tmp_db, monkeypatch):
    ss = SavedSearch(
        name="quiet",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=False),
        ),
    )
    profile = _profile_with(telegram=True, email=True, saved_searches=[ss])
    phash = profile_hash(profile)
    _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='["quiet"]',
    )

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    sent_channels = [s[0] for s in sends]
    assert "telegram" not in sent_channels
    assert "email" in sent_channels


def test_canonical_dedup_uses_signature(tmp_db, monkeypatch):
    """Two flats in same canonical cluster, one with override, one without:
    each fires once because their signatures differ."""
    ss = SavedSearch(
        name="s1",
        notifications=SavedSearchNotifications(
            telegram=TelegramNotificationOverride(enabled=True, chat_id="A"),
        ),
    )
    profile = _profile_with(telegram=True, email=False, saved_searches=[ss])
    phash = profile_hash(profile)

    flat1 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='["s1"]', external_id="e1",
    )
    flat2 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names='[]', external_id="e2",
    )
    # Both flats share canonical_id = flat1 (flat2 points at flat1 as canonical)
    tmp_db.execute("UPDATE flats SET canonical_flat_id=? WHERE id=?", (flat1, flat2))

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    # Two distinct telegram signatures → both fire.
    telegram_sends = [s for s in sends if s[0] == "telegram"]
    assert len(telegram_sends) == 2
    chat_ids = sorted(s[1].get("chat_id") for s in telegram_sends)
    assert chat_ids == ["A", "base_chat"]


def test_canonical_dedup_collapses_identical_signatures(tmp_db, monkeypatch):
    """Two flats in same canonical cluster, both no override → telegram:base fires once."""
    profile = _profile_with(telegram=True, email=False)
    phash = profile_hash(profile)

    flat1 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names="[]", external_id="e1",
    )
    flat2 = _seed_flat_match(
        tmp_db, profile_hash_value=phash, matched_names="[]", external_id="e2",
    )
    tmp_db.execute("UPDATE flats SET canonical_flat_id=? WHERE id=?", (flat1, flat2))

    sends: list[tuple[str, dict]] = []
    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, kwargs))

    monkeypatch.setattr(disp, "_send", fake_send)
    disp.dispatch_pending(profile)

    telegram_sends = [s for s in sends if s[0] == "telegram"]
    assert len(telegram_sends) == 1


def test_empty_array_notified_channels_parses_clean(tmp_db, monkeypatch):
    """Schema default '[]' parses to empty set; pending dispatch fires normally."""
    profile = _profile_with(telegram=True)
    phash = profile_hash(profile)
    flat_id = _seed_flat_match(tmp_db, profile_hash_value=phash)
    # Confirm starting state.
    row = tmp_db.execute(
        "SELECT notified_channels_json FROM matches WHERE flat_id=?", (flat_id,),
    ).fetchone()
    assert row["notified_channels_json"] == "[]"

    sends: list = []
    monkeypatch.setattr(disp, "_send", lambda *a, **kw: sends.append(a[0]))
    disp.dispatch_pending(profile)
    assert sends == ["telegram"]
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_dispatcher_signatures.py -v`
Expected: FAIL — current dispatcher writes bare channel names; doesn't call `_resolve_channels_for_match`.

- [ ] **Step 3: Restructure `dispatch_pending`**

Replace the body of `dispatch_pending` in `src/flatpilot/notifications/dispatcher.py` (the existing function from approximately line 119 to line 197). Keep the early-channels-empty short-circuit, the `_mark_stale_matches_notified` call, and the SQL SELECT. Replace the per-row loop. Also extend the SELECT to pull `m.matched_saved_searches_json`.

```python
def _parse_signatures(raw: str | None) -> set[str]:
    """Parse notified_channels_json with legacy bare-name compat.

    Bare channel names (legacy format pre-2026-05) are upgraded to
    '<channel>:base' signatures so dedup against new pending dispatches
    works correctly.
    """
    if not raw:
        return set()
    try:
        items = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return set()
    out: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        if ":" in item:
            out.add(item)  # already in signature format
        else:
            out.add(f"{item}:base")  # upgrade bare name
    return out


def _send(
    channel: str,
    flat: dict[str, Any],
    profile: Profile,
    **transport_kwargs: str,
) -> None:
    if channel == "telegram":
        telegram_adapter.send(
            profile,
            template.render_html(flat),
            parse_mode="HTML",
            **{k: v for k, v in transport_kwargs.items() if k in ("bot_token_env", "chat_id")},
        )
    elif channel == "email":
        smtp_env = transport_kwargs.get("smtp_env")
        recipient = _email_recipient(smtp_env=smtp_env)
        if not recipient:
            raise email_adapter.EmailError(
                f"no recipient — set EMAIL_TO or {(smtp_env or 'SMTP')}_FROM"
            )
        email_adapter.send(
            recipient,
            _subject_for(flat),
            template.render_plain(flat),
            template.render_html(flat),
            **({"smtp_env": smtp_env} if smtp_env is not None else {}),
        )
    else:
        raise ValueError(f"unknown channel: {channel!r}")


def dispatch_pending(profile: Profile) -> DispatchSummary:
    # Early-out only if NEITHER base nor any saved search could ever fire.
    # Cheap test: base channels enabled, OR any saved search has notifications.
    base_has_channel = (
        profile.notifications.telegram.enabled or profile.notifications.email.enabled
    )
    any_search_has_notifications = any(
        ss.notifications is not None for ss in profile.saved_searches
    )
    if not base_has_channel and not any_search_has_notifications:
        logger.info("no channels enabled in profile; nothing to send")
        return {"processed": 0, "sent": {}, "failed": {}}

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)
    _mark_stale_matches_notified(conn, phash)

    rows = conn.execute(
        """
        SELECT m.id AS match_id,
               m.notified_channels_json,
               m.matched_saved_searches_json,
               COALESCE(f.canonical_flat_id, f.id) AS canonical_id,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        WHERE m.decision = 'match' AND m.profile_version_hash = ?
        ORDER BY canonical_id, f.id
        """,
        (phash,),
    ).fetchall()

    sent_canonicals: dict[int, set[str]] = {}
    sent: dict[str, int] = {}
    failed: dict[str, int] = {}
    processed = 0

    for row in rows:
        flat = dict(row)
        match_id = flat.pop("match_id")
        canonical_id = flat.pop("canonical_id")
        matched_names_raw = flat.pop("matched_saved_searches_json", "[]") or "[]"
        try:
            matched_names = json.loads(matched_names_raw)
        except (TypeError, json.JSONDecodeError):
            matched_names = []
        notified = _parse_signatures(flat.pop("notified_channels_json", None))
        already_for_canonical = sent_canonicals.setdefault(canonical_id, set())

        resolved = _resolve_channels_for_match(profile, matched_names)
        effective = notified | already_for_canonical
        pending = [t for t in resolved if t[1] not in effective]
        if not pending:
            continue

        processed += 1
        for channel, signature, transport_kwargs in pending:
            try:
                _send(channel, flat, profile, **transport_kwargs)
            except (telegram_adapter.TelegramError, email_adapter.EmailError) as exc:
                logger.warning(
                    "match %d channel %s (%s) failed: %s",
                    match_id, channel, signature, exc,
                )
                failed[channel] = failed.get(channel, 0) + 1
                continue
            notified.add(signature)
            already_for_canonical.add(signature)
            sent[channel] = sent.get(channel, 0) + 1

        if notified:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE matches SET notified_channels_json = ?, notified_at = ? WHERE id = ?",
                (json.dumps(sorted(notified)), now, match_id),
            )

    logger.info(
        "dispatch: processed=%d sent=%s failed=%s", processed, sent, failed
    )
    return {"processed": processed, "sent": sent, "failed": failed}
```

The old `_parse_channels` and `enabled_channels` helpers can stay (the latter is still used by `send_test`). The old `_send` is replaced (now accepts `**transport_kwargs`).

- [ ] **Step 4: Update `send_test` to call `_send` without override kwargs**

Look at `send_test()` in `dispatcher.py` (~line 200). The existing call site is:

```python
            _send(channel, dict(_SYNTHETIC_FLAT), profile)
```

This still works because `_send`'s new signature has `**transport_kwargs` defaulting to empty. No change needed; just verify the function signature wasn't broken by the rewrite.

- [ ] **Step 5: Update the two existing dispatcher tests**

The new dispatcher's early-out checks `profile.notifications.telegram.enabled` directly instead of calling `enabled_channels()`. Existing tests in `tests/test_notifications_dispatcher.py` rely on stubbing `enabled_channels` to fake a telegram-enabled state — that stub becomes a no-op under the new code. They must be rewritten to enable telegram on the profile itself. Apply these exact edits:

In `tests/test_notifications_dispatcher.py`, replace the body of `test_dispatch_pending_skips_stale_hash_rows`:

```python
def test_dispatch_pending_skips_stale_hash_rows(tmp_db, monkeypatch):
    base = Profile.load_example()
    profile = base.model_copy(update={
        "notifications": base.notifications.model_copy(update={
            "telegram": base.notifications.telegram.model_copy(update={
                "enabled": True, "chat_id": "test_chat",
            })
        })
    })
    current = profile_hash(profile)
    stale = "deadbeef" * 4

    flat_a = _seed_flat(tmp_db, external_id="a")
    flat_b = _seed_flat(tmp_db, external_id="b")
    _seed_match(tmp_db, flat_id=flat_a, profile_hash=current)
    _seed_match(tmp_db, flat_id=flat_b, profile_hash=stale)

    sends: list[tuple[str, int]] = []

    def fake_send(channel, flat, profile, **kwargs):
        sends.append((channel, flat["id"]))

    monkeypatch.setattr(disp, "_send", fake_send)

    disp.dispatch_pending(profile)
    assert sends == [("telegram", flat_a)]
```

In the same file, replace the body of `test_mark_stale_flips_notified_at_without_send` similarly:

```python
def test_mark_stale_flips_notified_at_without_send(tmp_db, monkeypatch):
    base = Profile.load_example()
    profile = base.model_copy(update={
        "notifications": base.notifications.model_copy(update={
            "telegram": base.notifications.telegram.model_copy(update={
                "enabled": True, "chat_id": "test_chat",
            })
        })
    })
    current = profile_hash(profile)
    stale = "00" * 8

    flat_a = _seed_flat(tmp_db, external_id="a")
    flat_b = _seed_flat(tmp_db, external_id="b")
    _seed_match(tmp_db, flat_id=flat_a, profile_hash=current)
    _seed_match(tmp_db, flat_id=flat_b, profile_hash=stale)

    sent: list[str] = []

    def fake_send(channel, flat, profile, **kwargs):
        sent.append(channel)

    monkeypatch.setattr(disp, "_send", fake_send)

    disp.dispatch_pending(profile)
    # No real send invocation, but the stale row's notified_at should now be set.
    row_b_notified = tmp_db.execute(
        "SELECT notified_at FROM matches WHERE flat_id=?", (flat_b,),
    ).fetchone()["notified_at"]
    assert row_b_notified is not None
    # Current-hash row's notified_at remains None because the no-op fake_send
    # didn't add anything to ``notified``.
    row_a_notified = tmp_db.execute(
        "SELECT notified_at FROM matches WHERE flat_id=?", (flat_a,),
    ).fetchone()["notified_at"]
    assert row_a_notified is None
```

If the existing `test_mark_stale_flips_notified_at_without_send` body extends past what's shown above (it does in the current file — read it before editing), preserve any additional assertions but apply the same two changes: enable telegram on the profile, change `fake_send` to accept `**kwargs`, and remove any `monkeypatch.setattr(disp, "enabled_channels", ...)` line.

Then run all dispatcher tests:

Run: `pytest tests/test_notifications_dispatcher.py tests/test_dispatcher_signatures.py tests/test_dispatcher_resolve_channels.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Run the full test suite**

Run: `pytest -x`
Expected: ALL PASS.

If tests in `test_view.py` or `test_view_auto_apply.py` reference `notified_channels_json` rows, verify they don't break. (They shouldn't — `view.py` only reads from the `applications` table.)

- [ ] **Step 7: Commit**

```bash
git add src/flatpilot/notifications/dispatcher.py tests/test_dispatcher_signatures.py tests/test_notifications_dispatcher.py
git commit -m "FlatPilot-6o3: dispatcher uses per-match channel resolution and signature dedup"
```

---

## Task 7: Doctor row — saved-search notification env vars

**Files:**
- Modify: `src/flatpilot/doctor.py` (add a helper + register row)
- Test: `tests/test_doctor_saved_search_notifications.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doctor_saved_search_notifications.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_doctor_saved_search_notifications.py -v`
Expected: FAIL — `_check_saved_search_notifications` not defined.

- [ ] **Step 3: Add the helper to `doctor.py`**

Insert this function in `src/flatpilot/doctor.py` immediately after `_check_saved_searches` (~line 142):

```python
def _check_saved_search_notifications() -> tuple[str, str]:
    """Verify per-saved-search notification overrides reference resolvable env vars."""
    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile"

    missing_env: list[str] = []
    override_count = 0
    for ss in profile.saved_searches:
        if ss.notifications is None:
            continue
        if ss.notifications.telegram is not None and ss.notifications.telegram.enabled:
            override_count += 1
            env_name = ss.notifications.telegram.bot_token_env
            if env_name and not os.environ.get(env_name):
                missing_env.append(f"{ss.name}.telegram.bot_token_env={env_name}")
        if ss.notifications.email is not None and ss.notifications.email.enabled:
            override_count += 1
            prefix = ss.notifications.email.smtp_env
            if prefix:
                # Only flag if the entire prefix's HOST is missing — caller
                # has the option to fall through to base for individual fields.
                host_var = f"{prefix}_HOST"
                if not os.environ.get(host_var):
                    missing_env.append(f"{ss.name}.email.smtp_env={prefix} ({host_var} unset)")

    if not override_count:
        return "OK", "no overrides"
    if missing_env:
        return "optional", f"missing env vars: {', '.join(missing_env)}"
    return "OK", f"{override_count} override(s) resolve"
```

Then register the new check in the `CHECKS` list at `doctor.py:229-237`. Append a new tuple immediately after the `("Auto-apply: saved searches", _check_saved_searches),` line so the list reads:

```python
CHECKS: list[tuple[str, CheckFn]] = [
    ("Python >= 3.11", _check_python),
    ("App directory", _check_app_dir),
    ("Playwright Chromium", _check_playwright),
    ("Telegram creds", _check_telegram),
    ("SMTP creds", _check_smtp),
    ("Auto-apply: PAUSE switch", _check_pause),
    ("Auto-apply: saved searches", _check_saved_searches),
    ("Auto-apply: saved-search notif overrides", _check_saved_search_notifications),
]
```

- [ ] **Step 4: Add `import os` if not already at the top of `doctor.py`**

```bash
grep -n "^import os" src/flatpilot/doctor.py
```

If missing, add `import os` to the top imports.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_doctor_saved_search_notifications.py tests/test_doctor.py tests/test_doctor_auto_apply.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/doctor.py tests/test_doctor_saved_search_notifications.py
git commit -m "FlatPilot-6o3: add doctor row for saved-search notification override env vars"
```

---

## Task 8: Wizard — top-level menu loop scaffolding

**Files:**
- Modify: `src/flatpilot/wizard/init.py`
- Test: `tests/test_wizard_menu.py` (new file)

This task lays the menu's surface area but doesn't wire it into `run()` yet (Task 12 does that). Build it as a free function `_saved_searches_menu(out, profile) -> Profile` that takes a profile and returns a (possibly modified) profile. Pure-ish, easy to test by patching `Prompt.ask`/`Confirm.ask`.

- [ ] **Step 1: Write failing tests for menu rendering and "done" exit**

Create `tests/test_wizard_menu.py`:

```python
"""Coverage for the saved-searches wizard menu loop."""
from __future__ import annotations

from io import StringIO

from rich.console import Console

from flatpilot.profile import Profile, SavedSearch
from flatpilot.wizard.init import _saved_searches_menu


def _capture_console():
    return Console(file=StringIO(), force_terminal=False, width=120)


def test_menu_done_immediately_returns_unchanged(monkeypatch):
    """Picking 'done' on the first prompt returns the profile unchanged."""
    profile = Profile.load_example()
    out = _capture_console()

    answers = iter(["done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(answers),
    )

    result = _saved_searches_menu(out, profile)
    assert result.saved_searches == profile.saved_searches


def test_menu_renders_existing_searches(monkeypatch):
    """Existing saved searches appear as numbered rows in the menu output."""
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [
            SavedSearch(name="auto-default", auto_apply=True),
            SavedSearch(name="kreuzberg-2br", auto_apply=True, platforms=["wg-gesucht"]),
        ]
    })
    out = _capture_console()

    answers = iter(["done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(answers),
    )

    _saved_searches_menu(out, profile)
    output_text = out.file.getvalue()
    assert "1." in output_text
    assert "auto-default" in output_text
    assert "2." in output_text
    assert "kreuzberg-2br" in output_text


def test_menu_empty_state_omits_edit_delete_choices(monkeypatch):
    """When list is empty, [e]dit/[d]elete should not be valid choices."""
    profile = Profile.load_example()  # no saved searches
    out = _capture_console()

    captured_choices: list = []
    def fake_ask(*a, choices=None, **kw):
        if choices is not None:
            captured_choices.append(list(choices))
        return "done"
    monkeypatch.setattr("flatpilot.wizard.init.Prompt.ask", fake_ask)

    _saved_searches_menu(out, profile)
    assert captured_choices, "menu should have prompted with explicit choices"
    first_choices = captured_choices[0]
    assert "edit" not in first_choices
    assert "delete" not in first_choices
    assert "add" in first_choices
    assert "caps" in first_choices
    assert "done" in first_choices
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wizard_menu.py -v`
Expected: FAIL — `_saved_searches_menu` not defined.

- [ ] **Step 3: Implement the menu scaffolding**

Add this code to `src/flatpilot/wizard/init.py`, before the `run()` function. Add `Table` import: `from rich.table import Table`.

```python
def _saved_searches_menu(out: Console, profile: Profile) -> Profile:
    """Drive add/edit/delete/caps interactively until user picks done.

    Returns a possibly-modified Profile. Pure of side effects beyond
    Prompt.ask / Confirm.ask interaction, so easy to test by patching
    those.
    """
    while True:
        _render_saved_searches_table(out, profile)
        has_searches = bool(profile.saved_searches)
        choices = ["add"]
        if has_searches:
            choices += ["edit", "delete"]
        choices += ["caps", "done"]
        action = Prompt.ask(
            "Action", choices=choices, default="done",
        )
        if action == "done":
            return profile
        if action == "add":
            profile = _add_saved_search(out, profile)
        elif action == "edit":
            profile = _edit_saved_search(out, profile)
        elif action == "delete":
            profile = _delete_saved_search(out, profile)
        elif action == "caps":
            profile = _edit_caps_and_cooldowns(out, profile)


def _render_saved_searches_table(out: Console, profile: Profile) -> None:
    out.rule("Saved searches & auto-apply")
    if not profile.saved_searches:
        out.print("[dim](no saved searches yet)[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Auto-apply")
    table.add_column("Platforms")
    table.add_column("Notifications")
    for i, ss in enumerate(profile.saved_searches, start=1):
        table.add_row(
            str(i),
            ss.name,
            "✓" if ss.auto_apply else "✗",
            ", ".join(ss.platforms) if ss.platforms else "any",
            _summarize_notifications(ss),
        )
    out.print(table)


def _summarize_notifications(ss: SavedSearch) -> str:
    if ss.notifications is None:
        return "base"
    parts: list[str] = []
    if ss.notifications.telegram is not None:
        if ss.notifications.telegram.enabled:
            label = "telegram"
            if (
                ss.notifications.telegram.bot_token_env is not None
                or ss.notifications.telegram.chat_id is not None
            ):
                label += " (override)"
            parts.append(label)
        else:
            parts.append("telegram (off)")
    if ss.notifications.email is not None:
        if ss.notifications.email.enabled:
            label = "email"
            if ss.notifications.email.smtp_env is not None:
                label += " (override)"
            parts.append(label)
        else:
            parts.append("email (off)")
    return " + ".join(parts) if parts else "none"


def _add_saved_search(out: Console, profile: Profile) -> Profile:
    raise NotImplementedError("Task 9 implements this")


def _edit_saved_search(out: Console, profile: Profile) -> Profile:
    raise NotImplementedError("Task 9 implements this")


def _delete_saved_search(out: Console, profile: Profile) -> Profile:
    raise NotImplementedError("Task 10 implements this")


def _edit_caps_and_cooldowns(out: Console, profile: Profile) -> Profile:
    raise NotImplementedError("Task 11 implements this")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wizard_menu.py -v`
Expected: PASS for the three new tests.

- [ ] **Step 5: Run full suite**

Run: `pytest -x`
Expected: ALL PASS (existing wizard tests still pass — `_maybe_add_auto_apply` is unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/wizard/init.py tests/test_wizard_menu.py
git commit -m "FlatPilot-d36: add wizard saved-searches menu scaffolding (top-level loop, table, summary)"
```

---

## Task 9: Wizard — add/edit sub-flow

**Files:**
- Modify: `src/flatpilot/wizard/init.py`
- Test: `tests/test_wizard_menu.py` (extend)

The add/edit sub-flow is the largest single piece. It walks 4 minimal prompts (name, auto-apply, platforms, notifications) and an optional 8-prompt filter-overrides branch. Build edit by reusing add and seeding defaults from the existing search.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wizard_menu.py`:

```python
def test_add_minimal_saved_search(monkeypatch):
    """Add flow with no overrides: 4 prompts (name, auto-apply, platforms, override-notif=no), then customize-filters=no."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "kreuzberg-2br", "wg-gesucht", "done"])
    confirm_answers = iter([True, False, False])  # auto-apply=True, override notif=No, customize filters=No

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    assert len(result.saved_searches) == 1
    ss = result.saved_searches[0]
    assert ss.name == "kreuzberg-2br"
    assert ss.auto_apply is True
    assert ss.platforms == ["wg-gesucht"]
    assert ss.notifications is None
    assert ss.rent_min_warm is None  # customize-filters=No left this unset


def test_add_with_telegram_override(monkeypatch):
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter([
        "add", "k-2br",
        "",  # platforms blank → all
        "K_BOT_TOKEN", "k_chat_id",  # telegram override values
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply
        True,   # override notifications?
        True,   # telegram have an opinion?
        True,   # telegram enabled?
        False,  # email have an opinion?
        False,  # customize filters?
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.notifications is not None
    assert ss.notifications.telegram is not None
    assert ss.notifications.telegram.enabled is True
    assert ss.notifications.telegram.bot_token_env == "K_BOT_TOKEN"
    assert ss.notifications.telegram.chat_id == "k_chat_id"
    assert ss.notifications.email is None


def test_add_with_explicit_email_suppress(monkeypatch):
    """User says 'have an opinion=yes, enabled=no' → enabled=False stored."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "x", "", "done"])
    confirm_answers = iter([
        True,    # auto-apply
        True,    # override notifications?
        False,   # telegram opinion
        True,    # email opinion
        False,   # email enabled?
        False,   # customize filters?
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.notifications is not None
    assert ss.notifications.telegram is None
    assert ss.notifications.email is not None
    assert ss.notifications.email.enabled is False


def test_add_no_opinion_collapses_to_none(monkeypatch):
    """All channels 'no opinion' → notifications stored as None (not empty block)."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "x", "", "done"])
    confirm_answers = iter([
        True,    # auto-apply
        True,    # override notifications? = yes
        False,   # telegram opinion = no
        False,   # email opinion = no
        False,   # customize filters?
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.notifications is None


def test_add_with_filter_overrides(monkeypatch):
    """customize-filters=yes walks all 8 overlay prompts."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter([
        "add", "x", "",
        # 6 int prompts (rent_min, rent_max, rooms_min, rooms_max, radius_km, min_contract_months)
        "800", "1500", "1", "3", "10", "",
        # district list (override=yes path)
        "kreuzberg, mitte",
        # furnished_pref (override=yes path)
        "any",
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply
        False,  # override notifications? = no
        True,   # customize filters? = yes
        True,   # override district allowlist? = yes
        True,   # override furnished pref? = yes
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.rent_min_warm == 800
    assert ss.rent_max_warm == 1500
    assert ss.rooms_min == 1
    assert ss.rooms_max == 3
    assert ss.radius_km == 10
    assert ss.min_contract_months is None
    assert ss.district_allowlist == ["kreuzberg", "mitte"]
    assert ss.furnished_pref == "any"


def test_add_district_override_blank_means_empty_list(monkeypatch):
    """override=yes + blank list input → [] (override-to-empty)."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter([
        "add", "x", "",
        "", "", "", "", "", "",  # 6 ints, all blank
        "",  # district list blank → []
        # furnished_pref override=no, so no prompt for value
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply
        False,  # override notifications? = no
        True,   # customize filters? = yes
        True,   # override district allowlist? = yes
        False,  # override furnished pref? = no
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.district_allowlist == []
    assert ss.furnished_pref is None


def test_add_invalid_name_reprompts(monkeypatch):
    """Invalid name pattern triggers a re-prompt loop."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "Bad Name!", "valid-name", "", "done"])
    confirm_answers = iter([False, False, False])  # auto-apply, override-notif, customize-filters

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    assert len(result.saved_searches) == 1
    assert result.saved_searches[0].name == "valid-name"


def test_edit_existing(monkeypatch):
    """Edit branch loads defaults from the existing search."""
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [SavedSearch(name="kreuzberg-2br", auto_apply=False, platforms=["wg-gesucht"])]
    })
    out = _capture_console()

    prompt_answers = iter([
        "edit", "1",  # picks first search
        "kreuzberg-2br",  # name (kept)
        "wg-gesucht, kleinanzeigen",  # platforms updated
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply now True
        False,  # override notifications? = no
        False,  # customize filters? = no
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.name == "kreuzberg-2br"
    assert ss.auto_apply is True
    assert ss.platforms == ["wg-gesucht", "kleinanzeigen"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wizard_menu.py -v`
Expected: FAIL — `_add_saved_search`, `_edit_saved_search` raise `NotImplementedError`.

- [ ] **Step 3: Implement add/edit**

Replace the `_add_saved_search` and `_edit_saved_search` stubs in `wizard/init.py` with:

```python
import re
from flatpilot.profile import (
    EmailNotificationOverride,
    SavedSearchNotifications,
    TelegramNotificationOverride,
)

_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_PLATFORM_VALUES = {"wg-gesucht", "kleinanzeigen", "inberlinwohnen"}


def _add_saved_search(out: Console, profile: Profile) -> Profile:
    new_ss = _build_saved_search(out, current=None, existing_names={ss.name for ss in profile.saved_searches})
    return profile.model_copy(update={"saved_searches": [*profile.saved_searches, new_ss]})


def _edit_saved_search(out: Console, profile: Profile) -> Profile:
    n = len(profile.saved_searches)
    raw = Prompt.ask(f"Which one to edit? [1-{n}]")
    try:
        idx = int(raw) - 1
        if not 0 <= idx < n:
            raise ValueError
    except ValueError:
        out.print("[red]Invalid selection.[/red]")
        return profile
    current = profile.saved_searches[idx]
    other_names = {ss.name for i, ss in enumerate(profile.saved_searches) if i != idx}
    updated = _build_saved_search(out, current=current, existing_names=other_names)
    new_list = list(profile.saved_searches)
    new_list[idx] = updated
    return profile.model_copy(update={"saved_searches": new_list})


def _build_saved_search(
    out: Console,
    *,
    current: SavedSearch | None,
    existing_names: set[str],
) -> SavedSearch:
    name = _prompt_name(out, current=current, existing_names=existing_names)
    auto_apply_default = current.auto_apply if current else False
    auto_apply = Confirm.ask("Auto-apply for matches against this search?", default=auto_apply_default)
    platforms = _prompt_platforms(out, current=current)
    notifications = _prompt_notifications_override(out, current=current)
    overlay = _prompt_filter_overrides(out, current=current)

    return SavedSearch(
        name=name,
        auto_apply=auto_apply,
        platforms=platforms,
        notifications=notifications,
        **overlay,
    )


def _prompt_name(out: Console, *, current: SavedSearch | None, existing_names: set[str]) -> str:
    default = current.name if current else None
    while True:
        raw = Prompt.ask(
            "Saved search name (lowercase, digits, _, -)",
            default=default,
        )
        if not _NAME_PATTERN.match(raw):
            out.print("[red]Name must match ^[a-z0-9_-]+$[/red]")
            continue
        if raw in existing_names:
            out.print(f"[red]A saved search named {raw!r} already exists.[/red]")
            continue
        return raw


def _prompt_platforms(out: Console, *, current: SavedSearch | None) -> list[str]:
    default = ", ".join(current.platforms) if current and current.platforms else ""
    while True:
        raw = Prompt.ask(
            "Platforms (comma-separated; empty = all platforms)",
            default=default,
        )
        if not raw.strip():
            return []
        items = [p.strip() for p in raw.split(",") if p.strip()]
        unknown = [p for p in items if p not in _PLATFORM_VALUES]
        if unknown:
            out.print(f"[red]Unknown platform(s): {', '.join(unknown)}. "
                      f"Valid: {sorted(_PLATFORM_VALUES)}[/red]")
            continue
        return items


def _prompt_notifications_override(
    out: Console, *, current: SavedSearch | None,
) -> SavedSearchNotifications | None:
    default_override = current is not None and current.notifications is not None
    if not Confirm.ask(
        "Override notifications for matches against this search?",
        default=default_override,
    ):
        return None

    out.print(
        "[dim]For each channel you set here, this search REPLACES base profile's "
        "setting for matches against this search alone. Set 'enabled=no' to suppress "
        "the channel for this search.[/dim]"
    )

    telegram = _prompt_channel_override(
        out,
        channel="telegram",
        current=current.notifications.telegram if current and current.notifications else None,
        fields=(("bot_token_env", "Bot token env var"), ("chat_id", "Chat ID")),
        builder=TelegramNotificationOverride,
    )
    email = _prompt_channel_override(
        out,
        channel="email",
        current=current.notifications.email if current and current.notifications else None,
        fields=(("smtp_env", "SMTP env-var prefix"),),
        builder=EmailNotificationOverride,
    )

    if telegram is None and email is None:
        return None
    return SavedSearchNotifications(telegram=telegram, email=email)


def _prompt_channel_override(
    out: Console,
    *,
    channel: str,
    current,
    fields: tuple[tuple[str, str], ...],
    builder,
):
    have_opinion_default = current is not None
    if not Confirm.ask(
        f"{channel}: have an opinion for this search?",
        default=have_opinion_default,
    ):
        return None
    enabled_default = current.enabled if current else True
    enabled = Confirm.ask(f"{channel} enabled for this search?", default=enabled_default)
    kwargs = {"enabled": enabled}
    if enabled:
        for field_name, prompt_label in fields:
            current_value = getattr(current, field_name, None) if current else None
            default = current_value or ""
            raw = Prompt.ask(f"{prompt_label} (blank = inherit base)", default=default)
            kwargs[field_name] = raw.strip() or None
    return builder(**kwargs)


def _prompt_filter_overrides(out: Console, *, current: SavedSearch | None) -> dict:
    has_overrides = current is not None and any(
        getattr(current, f) is not None for f in (
            "rent_min_warm", "rent_max_warm", "rooms_min", "rooms_max",
            "district_allowlist", "radius_km", "furnished_pref", "min_contract_months",
        )
    )
    if not Confirm.ask(
        "Customize filter overrides for this search?",
        default=has_overrides,
    ):
        return {
            "rent_min_warm": getattr(current, "rent_min_warm", None) if current else None,
            "rent_max_warm": getattr(current, "rent_max_warm", None) if current else None,
            "rooms_min": getattr(current, "rooms_min", None) if current else None,
            "rooms_max": getattr(current, "rooms_max", None) if current else None,
            "radius_km": getattr(current, "radius_km", None) if current else None,
            "min_contract_months": getattr(current, "min_contract_months", None) if current else None,
            "district_allowlist": getattr(current, "district_allowlist", None) if current else None,
            "furnished_pref": getattr(current, "furnished_pref", None) if current else None,
        }

    overlay: dict = {}
    for field, label in (
        ("rent_min_warm", "Min warm rent (€/month, blank=inherit)"),
        ("rent_max_warm", "Max warm rent (€/month, blank=inherit)"),
        ("rooms_min", "Min rooms (blank=inherit)"),
        ("rooms_max", "Max rooms (blank=inherit)"),
        ("radius_km", "Radius km (blank=inherit)"),
        ("min_contract_months", "Min contract months (blank=inherit)"),
    ):
        current_val = getattr(current, field, None) if current else None
        default = str(current_val) if current_val is not None else ""
        overlay[field] = _prompt_optional_int(out, label, default=default, min_value=0)

    # district_allowlist: two-step override
    current_districts = getattr(current, "district_allowlist", None) if current else None
    if Confirm.ask(
        "Override district allowlist for this search?",
        default=current_districts is not None,
    ):
        default = ", ".join(current_districts) if current_districts else ""
        raw = Prompt.ask(
            "Districts (comma-separated; blank = any district)",
            default=default,
        )
        overlay["district_allowlist"] = [d.strip() for d in raw.split(",") if d.strip()]
    else:
        overlay["district_allowlist"] = None

    # furnished_pref: two-step override
    current_furnished = getattr(current, "furnished_pref", None) if current else None
    if Confirm.ask(
        "Override furnished preference for this search?",
        default=current_furnished is not None,
    ):
        overlay["furnished_pref"] = Prompt.ask(
            "Furnished preference",
            choices=["any", "furnished", "unfurnished"],
            default=current_furnished or "any",
        )
    else:
        overlay["furnished_pref"] = None

    return overlay
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wizard_menu.py -v`
Expected: ALL PASS.

If a test fails, the most likely cause is the `monkeypatch` answer iterators being exhausted in the wrong order — verify by adding a print statement temporarily and tracing the actual prompt sequence.

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/wizard/init.py tests/test_wizard_menu.py
git commit -m "FlatPilot-d36: wizard add/edit sub-flow with tiered overlay prompts"
```

---

## Task 10: Wizard — delete sub-flow

**Files:**
- Modify: `src/flatpilot/wizard/init.py`
- Test: `tests/test_wizard_menu.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wizard_menu.py`:

```python
def test_delete_confirms_by_name(monkeypatch):
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [
            SavedSearch(name="auto-default"),
            SavedSearch(name="kreuzberg-2br"),
        ],
    })
    out = _capture_console()

    prompt_answers = iter(["delete", "1", "auto-default", "done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: True,
    )

    result = _saved_searches_menu(out, profile)
    names = [ss.name for ss in result.saved_searches]
    assert names == ["kreuzberg-2br"]


def test_delete_aborts_on_wrong_name(monkeypatch):
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [SavedSearch(name="auto-default")],
    })
    out = _capture_console()

    prompt_answers = iter(["delete", "1", "wrong", "done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: True,
    )

    result = _saved_searches_menu(out, profile)
    assert [ss.name for ss in result.saved_searches] == ["auto-default"]


def test_delete_reprints_with_renumbered_indices(monkeypatch):
    """After deleting row 1 of 3, the remaining searches reprint as 1, 2 (not 2, 3)."""
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [
            SavedSearch(name="alpha"),
            SavedSearch(name="beta"),
            SavedSearch(name="gamma"),
        ],
    })
    out = _capture_console()

    prompt_answers = iter(["delete", "1", "alpha", "done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: True,
    )

    _saved_searches_menu(out, profile)
    output = out.file.getvalue()

    # The post-delete table should appear after the pre-delete table.
    # Both contain "alpha"/"beta"/"gamma" rows in the pre-delete table; the
    # post-delete table contains only "beta"/"gamma" with renumbered indices.
    # We assert by counting: alpha must appear exactly once (pre-delete only).
    assert output.count("alpha") == 1
    # beta and gamma each appear in both tables (pre + post) → twice.
    assert output.count("beta") == 2
    assert output.count("gamma") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wizard_menu.py::test_delete_confirms_by_name tests/test_wizard_menu.py::test_delete_aborts_on_wrong_name -v`
Expected: FAIL — `_delete_saved_search` raises `NotImplementedError`.

- [ ] **Step 3: Implement delete**

Replace the `_delete_saved_search` stub with:

```python
def _delete_saved_search(out: Console, profile: Profile) -> Profile:
    n = len(profile.saved_searches)
    raw = Prompt.ask(f"Which one to delete? [1-{n}]")
    try:
        idx = int(raw) - 1
        if not 0 <= idx < n:
            raise ValueError
    except ValueError:
        out.print("[red]Invalid selection.[/red]")
        return profile
    target = profile.saved_searches[idx]
    confirm = Prompt.ask(
        f"Delete '{target.name}'? Type the name to confirm",
    )
    if confirm != target.name:
        out.print("[yellow]Aborted; nothing deleted.[/yellow]")
        return profile
    new_list = [ss for i, ss in enumerate(profile.saved_searches) if i != idx]
    return profile.model_copy(update={"saved_searches": new_list})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wizard_menu.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/wizard/init.py tests/test_wizard_menu.py
git commit -m "FlatPilot-d36: wizard delete sub-flow with name-confirm guard"
```

---

## Task 11: Wizard — caps & cooldowns sub-flow

**Files:**
- Modify: `src/flatpilot/wizard/init.py`
- Test: `tests/test_wizard_menu.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wizard_menu.py`:

```python
def test_caps_walks_all_platforms(monkeypatch):
    """Platforms iterate in alphabetical order: inberlinwohnen, kleinanzeigen, wg-gesucht."""
    profile = Profile.load_example()
    out = _capture_console()

    # Existing defaults from AutoApplySettings: 3 platforms, cap 20, cooldown 120.
    # We change wg-gesucht's cap to 50 and cooldown to 90; keep others as default.
    prompt_answers = iter([
        "caps",
        "", "",       # inberlinwohnen blank → keep current (alphabetically first)
        "", "",       # kleinanzeigen blank → keep current
        "50", "90",   # wg-gesucht cap=50, cooldown=90
        "done",
    ])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: False,
    )

    result = _saved_searches_menu(out, profile)
    assert result.auto_apply.daily_cap_per_platform["wg-gesucht"] == 50
    assert result.auto_apply.cooldown_seconds_per_platform["wg-gesucht"] == 90
    assert result.auto_apply.daily_cap_per_platform["kleinanzeigen"] == 20
    assert result.auto_apply.cooldown_seconds_per_platform["kleinanzeigen"] == 120
    assert result.auto_apply.daily_cap_per_platform["inberlinwohnen"] == 20
    assert result.auto_apply.cooldown_seconds_per_platform["inberlinwohnen"] == 120
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_wizard_menu.py::test_caps_walks_all_platforms -v`
Expected: FAIL — `_edit_caps_and_cooldowns` raises `NotImplementedError`.

- [ ] **Step 3: Implement caps & cooldowns**

Replace the `_edit_caps_and_cooldowns` stub with:

```python
def _edit_caps_and_cooldowns(out: Console, profile: Profile) -> Profile:
    out.rule("Caps & cooldowns")
    new_caps = dict(profile.auto_apply.daily_cap_per_platform)
    new_cooldowns = dict(profile.auto_apply.cooldown_seconds_per_platform)
    for platform in sorted(profile.auto_apply.daily_cap_per_platform.keys()):
        out.print(f"[bold]Platform: {platform}[/bold]")
        cap_current = new_caps.get(platform, 20)
        cap = _prompt_optional_int(
            out, f"  Daily cap (current {cap_current})",
            default=str(cap_current), min_value=0,
        )
        if cap is not None:
            new_caps[platform] = cap

        cooldown_current = new_cooldowns.get(platform, 120)
        cooldown = _prompt_optional_int(
            out, f"  Cooldown seconds (current {cooldown_current})",
            default=str(cooldown_current), min_value=0,
        )
        if cooldown is not None:
            new_cooldowns[platform] = cooldown

    return profile.model_copy(update={
        "auto_apply": profile.auto_apply.model_copy(update={
            "daily_cap_per_platform": new_caps,
            "cooldown_seconds_per_platform": new_cooldowns,
        })
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_wizard_menu.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/wizard/init.py tests/test_wizard_menu.py
git commit -m "FlatPilot-d36: wizard caps & cooldowns sub-flow walks all configured platforms"
```

---

## Task 12: Wire menu into `run()`, remove legacy auto-apply prompt

**Files:**
- Modify: `src/flatpilot/wizard/init.py:179-186` (the auto-apply Y/N section)
- Modify: `src/flatpilot/wizard/init.py:44-51` (remove `_maybe_add_auto_apply`)
- Test: `tests/test_wizard_auto_apply.py` (delete or rewrite)
- Test: `tests/test_wizard_menu.py` (extend)

- [ ] **Step 1: Replace the auto-apply prompt with the menu loop**

In `src/flatpilot/wizard/init.py`, find this block (~line 179):

```python
    out.rule("Auto-apply (Phase 4)")
    if not any(ss.name == "auto-default" for ss in profile.saved_searches):
        enable = Confirm.ask(
            "Enable auto-apply with a starter saved search? "
            "(Use `flatpilot pause` to disable temporarily.)",
            default=False,
        )
        profile = _maybe_add_auto_apply(profile, answer=enable)
```

Replace with:

```python
    profile = _saved_searches_menu(out, profile)
```

- [ ] **Step 2: Delete the dead `_maybe_add_auto_apply` helper**

Remove the entire function from `src/flatpilot/wizard/init.py` (lines 44-51 in the original).

- [ ] **Step 3: Delete the legacy test file**

```bash
rm tests/test_wizard_auto_apply.py
```

The test cases there cover the now-removed `_maybe_add_auto_apply`. Equivalent coverage is in `test_wizard_menu.py`.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -x`
Expected: ALL PASS.

If any tests fail because they imported `_maybe_add_auto_apply`, find and update them — that import is gone now.

- [ ] **Step 5: Manual smoke test (optional but recommended)**

Run: `python -m flatpilot init` in a clean tmp dir (use `FLATPILOT_HOME=/tmp/fp-smoke`).
Walk through to the saved-searches menu and verify: empty state shows `[a]dd / [c]aps / [done]`, adding shows `[a]dd / [e]dit / [d]elete / [c]aps / [done]`, table renders correctly.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/wizard/init.py
git rm tests/test_wizard_auto_apply.py
git commit -m "FlatPilot-d36: replace legacy auto-apply Y/N with saved-searches menu loop"
```

---

## Task 13: README note — `flatpilot run` after profile edits

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the anchor**

Run: `grep -n "flatpilot init\|flatpilot run\|flatpilot notify" README.md`

Pick the line that mentions both `flatpilot init` and `flatpilot run` (typically a Quick Start or Usage section). If no such line exists, append the note as a new subsection at the end of the Usage section, immediately before the next `##` heading.

- [ ] **Step 2: Add the note as a new paragraph**

Insert this paragraph right after the chosen anchor line, with a blank line before and after for markdown spacing:

```markdown
**After editing saved searches:** run `flatpilot run` (which re-matches before notifying), not `flatpilot notify` standalone. Profile edits rotate the internal hash that scopes pending matches; running `flatpilot notify` directly after an edit will silently drop queued notifications. `flatpilot run` re-creates the match rows under the new hash automatically.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "FlatPilot-6o3/d36: document flatpilot run vs flatpilot notify after profile edits"
```

---

## Task 14: Final verification + close beads + push

- [ ] **Step 1: Run the full test suite with coverage**

Run: `pytest --cov=flatpilot --cov-report=term-missing -v`
Expected: ALL PASS. Coverage on changed modules (`profile.py`, `notifications/dispatcher.py`, `notifications/email.py`, `notifications/telegram.py`, `wizard/init.py`, `doctor.py`) ≥ 95%.

- [ ] **Step 2: Run linters**

Run: `ruff check src/ tests/`
Expected: clean. Fix any warnings.

Run: `mypy src/flatpilot/`
Expected: clean (or pre-existing-only warnings).

- [ ] **Step 3: Close the beads**

```bash
bd close FlatPilot-6o3 FlatPilot-d36
```

- [ ] **Step 4: Stage and commit beads update**

```bash
git add .beads/issues.jsonl
git commit -m "FlatPilot-6o3/d36: close beads after implementation complete"
```

- [ ] **Step 5: Push the branch**

```bash
git push -u origin feat/saved-searches-power-user
```

- [ ] **Step 6: Open a PR**

```bash
gh pr create --base main --head feat/saved-searches-power-user --title "feat: per-saved-search notification routing + wizard menu (FlatPilot-6o3/d36)" --body "$(cat <<'EOF'
## Summary

Bundles two beads into a single PR:

- **FlatPilot-6o3** — Per-saved-search notification routing under semantic A″ (definers replace base for the channels they define; non-definers contribute nothing; `enabled=False` actively suppresses).
- **FlatPilot-d36** — Wizard support for multiple saved searches, edit/delete on re-run, and interactive cap/cooldown tuning via a menu loop.

Spec: `docs/superpowers/specs/2026-05-02-saved-searches-power-user-design.md`
Plan: `docs/superpowers/plans/2026-05-03-saved-searches-power-user.md`

## Test plan

- [ ] `pytest -v` — all tests pass including new schema, dispatcher, wizard, doctor coverage
- [ ] `pytest --cov=flatpilot --cov-report=term-missing` — ≥95% line coverage on changed modules
- [ ] `ruff check src/ tests/` — clean
- [ ] `mypy src/flatpilot/` — clean
- [ ] Manual smoke: `flatpilot init` in a tmp `FLATPILOT_HOME` walks through the new menu loop without errors
- [ ] Manual smoke: `flatpilot doctor` shows the new saved-search-notification-overrides row
- [ ] Manual smoke: editing a saved search and running `flatpilot notify` standalone produces a debug log mentioning the dropped queued notifications (per Risk 2 documentation in the spec)
EOF
)"
```

- [ ] **Step 7: Return the PR URL**

The `gh pr create` command prints the PR URL. Capture it and return it as the final output of this plan.

---

## Self-Review

**Spec coverage check:**
- §3 Schema → Task 1 ✓
- §4.1 A″ resolution → Task 5 ✓
- §4.2 transport resolution → Task 5 ✓ (inside `_resolve_channel`)
- §4.3 signature canonicalization → Task 5 (helper) + Task 6 (writeback) ✓
- §4.4 adapter signatures → Task 3 (telegram) + Task 2/4 (email) ✓
- §4.5 misconfig + stale name → Task 5 (stale name in helper) + Task 6 (misconfig path via existing TelegramError/EmailError catch) ✓
- §4.6 send_test unchanged → Task 6 step 4 verifies ✓
- §5.1 menu top level → Task 8 ✓
- §5.2 add/edit sub-flow → Task 9 ✓
- §5.3 delete sub-flow → Task 10 ✓
- §5.4 caps & cooldowns → Task 11 ✓
- §5.5 re-run handling (auto-default no special status) → Task 12 (replaces the special-case prompt) ✓
- §5.6 validation → covered by pydantic at save-time + name re-prompt loop in Task 9
- §6 Risks 1-6 → Risk 1/2 covered by wizard explainer (Task 9) + README note (Task 13). Risks 3-6 are correctness invariants pinned by tests in Tasks 5/6.
- §7 tests → distributed across Tasks 1, 2, 3, 5, 6, 7, 8-11, with the schema tests covered by Task 1, dispatcher tests by Tasks 5+6, wizard tests by Tasks 8-11, doctor by Task 7.
- §8 doctor row → Task 7 ✓

**Placeholder scan:** No "TODO", "TBD", "implement later", "fill in details", or undefined references. Stub functions in Task 8 raise `NotImplementedError` and are explicitly replaced in Tasks 9-11.

**Type consistency:**
- `_resolve_channels_for_match` returns `list[tuple[str, str, dict[str, str]]]` in Task 5; consumed in Task 6 with the same shape.
- `_send` signature in Task 6 uses `**transport_kwargs`; called by adapters via `**kwargs`.
- `SavedSearchNotifications`, `TelegramNotificationOverride`, `EmailNotificationOverride` are imported consistently across Tasks 1, 5, 8, 9.
- `_maybe_add_auto_apply` is referenced in Task 12 step 2 for deletion only — earlier tasks don't depend on it surviving.

No issues found. Plan is ready to execute.
