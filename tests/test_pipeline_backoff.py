"""Integration tests for CLI pipeline + backoff wiring.

_run_scrape_pass is a thin orchestrator: it iterates registered
scrapers, consults backoff, catches the two expected exception types,
and writes flats. These tests drive it with fake scrapers and assert on
the sequence of backoff state transitions.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_backoff() -> None:
    from flatpilot.scrapers.backoff import reset

    reset()
    yield
    reset()


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, msg: str) -> None:
        self.lines.append(msg)


class _FakeScraper:
    platform = "fake"
    # Ad-hoc stub — bypasses @register, so the strict declaration check
    # there does not apply. supports_city() reads the attribute directly,
    # so we still need to declare it; None = "no city restriction" lets
    # any test profile drive this fake through the gate.
    supported_cities: frozenset[str] | None = None

    def __init__(self, behaviours: list[Any]) -> None:
        # Each element is either a list[Flat] (success) or an Exception
        # instance to raise.
        self._behaviours = list(behaviours)

    def fetch_new(self, _profile: Any) -> Any:
        step = self._behaviours.pop(0)
        if isinstance(step, Exception):
            raise step
        return iter(step)


def _make_profile() -> Any:
    # Same approach as test_kleinanzeigen_scraper.py: pull the shipped
    # example (known-valid under the latest schema) and flip to Berlin.
    from flatpilot.profile import Profile

    base = Profile.load_example()
    return base.model_copy(update={"city": "Berlin"})


def test_rate_limit_triggers_backoff_and_next_pass_skips(tmp_db) -> None:
    from flatpilot.cli import _run_scrape_pass
    from flatpilot.scrapers.session import RateLimitedError

    console = _Recorder()
    scraper = _FakeScraper([RateLimitedError("fake: HTTP 429"), []])

    _run_scrape_pass([scraper], _make_profile(), console)
    assert any("429" in line or "rate" in line.lower() for line in console.lines)

    console.lines.clear()
    _run_scrape_pass([scraper], _make_profile(), console)
    # Second pass must NOT have called fetch_new — pop would have
    # returned []; the scraper list still has one behaviour left.
    assert len(scraper._behaviours) == 1
    assert any("skip" in line.lower() or "cool" in line.lower() for line in console.lines)


def test_challenge_triggers_backoff_and_longer_cool_off(tmp_db) -> None:
    from datetime import UTC, datetime

    from flatpilot.cli import _run_scrape_pass
    from flatpilot.scrapers.backoff import _state
    from flatpilot.scrapers.session import ChallengeDetectedError

    scraper = _FakeScraper([ChallengeDetectedError("fake: captcha")])
    _run_scrape_pass([scraper], _make_profile(), _Recorder())

    st = _state["fake"]
    assert st.last_kind == "challenge"
    assert st.consecutive_failures == 1
    # Challenge ladder starts at 600s, rate_limit at 60s — ensures the
    # two paths are wired to different kinds.
    remaining = (st.skip_until - datetime.now(UTC)).total_seconds()
    assert remaining > 300


def test_successful_pass_clears_backoff(tmp_db) -> None:
    from flatpilot.cli import _run_scrape_pass
    from flatpilot.scrapers.backoff import _state
    from flatpilot.scrapers.session import RateLimitedError

    scraper = _FakeScraper([RateLimitedError("fake: HTTP 503"), []])
    _run_scrape_pass([scraper], _make_profile(), _Recorder())
    assert "fake" in _state

    # Skip the cool-off artificially by clearing skip_until so the next
    # pass actually calls fetch_new.
    _state["fake"].skip_until = None
    _run_scrape_pass([scraper], _make_profile(), _Recorder())

    # After a success, state for this platform must be gone entirely.
    assert "fake" not in _state


def test_generic_exception_does_not_feed_backoff(tmp_db) -> None:
    """Bugs aren't site signals. Random exceptions must not extend the cool-off."""
    from flatpilot.cli import _run_scrape_pass
    from flatpilot.scrapers.backoff import _state

    scraper = _FakeScraper([RuntimeError("bad parser")])
    _run_scrape_pass([scraper], _make_profile(), _Recorder())
    assert "fake" not in _state
