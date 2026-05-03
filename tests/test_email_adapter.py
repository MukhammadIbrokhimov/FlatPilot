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
    assert "CUSTOM_FROM" in str(exc.value)
