"""Tests for the configurable apply subprocess timeout (FlatPilot-c3e).

The default is 180s. ``FLATPILOT_APPLY_TIMEOUT_SEC`` overrides it.
Invalid values (non-int, non-positive) log a warning and fall back to
the default rather than raising — the env var is an ergonomics knob, a
typo shouldn't break apply.
"""

from __future__ import annotations

import logging

import pytest


def test_apply_timeout_default_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    from flatpilot.apply import DEFAULT_APPLY_TIMEOUT_SEC, apply_timeout_sec

    monkeypatch.delenv("FLATPILOT_APPLY_TIMEOUT_SEC", raising=False)
    assert apply_timeout_sec() == DEFAULT_APPLY_TIMEOUT_SEC == 180


def test_apply_timeout_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch):
    from flatpilot.apply import apply_timeout_sec

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "42")
    assert apply_timeout_sec() == 42


def test_apply_timeout_invalid_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    from flatpilot.apply import DEFAULT_APPLY_TIMEOUT_SEC, apply_timeout_sec

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "not-a-number")
    with caplog.at_level(logging.WARNING, logger="flatpilot.apply"):
        assert apply_timeout_sec() == DEFAULT_APPLY_TIMEOUT_SEC
    assert "FLATPILOT_APPLY_TIMEOUT_SEC" in caplog.text


def test_apply_timeout_non_positive_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    from flatpilot.apply import DEFAULT_APPLY_TIMEOUT_SEC, apply_timeout_sec

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "0")
    with caplog.at_level(logging.WARNING, logger="flatpilot.apply"):
        assert apply_timeout_sec() == DEFAULT_APPLY_TIMEOUT_SEC
    assert "FLATPILOT_APPLY_TIMEOUT_SEC" in caplog.text


def test_spawn_apply_passes_configured_timeout_to_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end: setting the env var changes the timeout subprocess.run sees."""
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    captured_kwargs: dict = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)

        class _Done:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Done()

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "5")
    with patch("flatpilot.server.subprocess.run", side_effect=fake_run):
        result = _spawn_apply(42)

    assert result["ok"] is True
    assert captured_kwargs.get("timeout") == 5


def test_spawn_apply_timeout_error_string_reports_configured_value(
    monkeypatch: pytest.MonkeyPatch,
):
    """The configured timeout must appear in the timeout-error message.

    Otherwise the user sees 'timed out after 180s' regardless of what
    they actually set, and we have no proof the env var is honored.
    """
    import subprocess
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else kwargs.get("args", []),
            timeout=kwargs.get("timeout", 180),
            output="",
            stderr="",
        )

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "7")
    with patch("flatpilot.server.subprocess.run", side_effect=raise_timeout):
        result = _spawn_apply(42)

    assert result["ok"] is False
    assert "timed out after 7s" in result["stdout_tail"]
