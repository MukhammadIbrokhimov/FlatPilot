"""Unit tests for src/flatpilot/scrapers/backoff.py.

The pipeline needs three questions answered for each platform on each
pass: 'can I scrape now?', 'scrape failed, how long do I cool off?',
'scrape succeeded, reset my state'. Those map to should_skip,
on_failure, on_success. Failures come in two flavours — rate_limit
(HTTP 429/503) and challenge (Cloudflare / block keyword) — with
separate ladders because a challenge is a stronger signal than a 429.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def _reset_backoff() -> None:
    from flatpilot.scrapers.backoff import reset

    reset()
    yield
    reset()


def _t(seconds: int) -> datetime:
    return datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def test_no_history_does_not_skip() -> None:
    from flatpilot.scrapers.backoff import should_skip

    skip, remaining = should_skip("kleinanzeigen", now=_t(0))
    assert skip is False
    assert remaining == 0.0


def test_single_rate_limit_failure_schedules_short_cool_off() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    skip, remaining = should_skip("kleinanzeigen", now=_t(10))
    assert skip is True
    assert 40.0 < remaining <= 60.0  # first rung = 60s, 10s have passed


def test_rate_limit_ladder_doubles() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    _, first = should_skip("kleinanzeigen", now=_t(0))
    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    _, second = should_skip("kleinanzeigen", now=_t(0))
    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    _, third = should_skip("kleinanzeigen", now=_t(0))

    assert first == 60.0
    assert second == 120.0
    assert third == 240.0


def test_rate_limit_ladder_caps_at_30_min() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    for _ in range(20):
        on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    _, remaining = should_skip("kleinanzeigen", now=_t(0))
    assert remaining == 1800.0


def test_challenge_uses_longer_ladder_than_rate_limit() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    on_failure("kleinanzeigen", "challenge", now=_t(0))
    _, remaining = should_skip("kleinanzeigen", now=_t(0))
    assert remaining == 600.0


def test_challenge_ladder_caps_at_60_min() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    for _ in range(20):
        on_failure("kleinanzeigen", "challenge", now=_t(0))
    _, remaining = should_skip("kleinanzeigen", now=_t(0))
    assert remaining == 3600.0


def test_success_resets_state() -> None:
    from flatpilot.scrapers.backoff import on_failure, on_success, should_skip

    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    on_success("kleinanzeigen")
    skip, remaining = should_skip("kleinanzeigen", now=_t(0))
    assert skip is False
    assert remaining == 0.0


def test_skip_clears_when_window_expires() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    on_failure("kleinanzeigen", "rate_limit", now=_t(0))
    # First rung is 60s.
    skip_mid, _ = should_skip("kleinanzeigen", now=_t(30))
    skip_past, remaining_past = should_skip("kleinanzeigen", now=_t(90))
    assert skip_mid is True
    assert skip_past is False
    assert remaining_past == 0.0


def test_platforms_are_isolated() -> None:
    from flatpilot.scrapers.backoff import on_failure, should_skip

    on_failure("kleinanzeigen", "challenge", now=_t(0))
    skip_k, _ = should_skip("kleinanzeigen", now=_t(0))
    skip_w, _ = should_skip("wg-gesucht", now=_t(0))
    assert skip_k is True
    assert skip_w is False


def test_unknown_failure_kind_raises() -> None:
    from flatpilot.scrapers.backoff import on_failure

    with pytest.raises(ValueError):
        on_failure("kleinanzeigen", "mystery", now=_t(0))
