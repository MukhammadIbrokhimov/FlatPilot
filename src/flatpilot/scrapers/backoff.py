"""Per-platform adaptive backoff for the CLI run loop.

The pipeline asks :func:`should_skip` before each scrape pass and
records the outcome via :func:`on_failure` / :func:`on_success`. State
lives in a module-level dict, keyed by platform, and exists only for
the life of the process: restarting ``flatpilot run --watch`` clears
all cool-offs. That's intentional — persisted state would fight with
the user's most common recovery action (restart).

Two ladders:

- ``rate_limit`` (HTTP 429/503 from :class:`RateLimitedError`):
  60 s → 120 s → 240 s → 480 s → 960 s → 1800 s (cap).
- ``challenge`` (captcha / block keyword from
  :class:`ChallengeDetectedError`): 600 s → 1200 s → 2400 s → 3600 s (cap).

:func:`reset` clears all state and exists for tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

FailureKind = Literal["rate_limit", "challenge"]

_RATE_LIMIT_LADDER: tuple[float, ...] = (60.0, 120.0, 240.0, 480.0, 960.0, 1800.0)
_CHALLENGE_LADDER: tuple[float, ...] = (600.0, 1200.0, 2400.0, 3600.0)


@dataclass
class BackoffState:
    consecutive_failures: int = 0
    skip_until: datetime | None = None
    last_kind: FailureKind | None = None


_state: dict[str, BackoffState] = {}


def _ladder_for(kind: FailureKind) -> tuple[float, ...]:
    if kind == "rate_limit":
        return _RATE_LIMIT_LADDER
    if kind == "challenge":
        return _CHALLENGE_LADDER
    raise ValueError(f"unknown failure kind: {kind!r}")


def _delay_for(kind: FailureKind, consecutive: int) -> float:
    # consecutive=0 would index ladder[-1] (the cap) by accident; guard
    # explicitly so a misuse surfaces as a ValueError, not a silent
    # worst-case delay.
    if consecutive < 1:
        raise ValueError(f"consecutive must be >= 1, got {consecutive}")
    ladder = _ladder_for(kind)
    return ladder[min(consecutive - 1, len(ladder) - 1)]


def should_skip(platform: str, *, now: datetime) -> tuple[bool, float]:
    """Return ``(skip, seconds_remaining)`` for the next scrape pass.

    ``skip`` is True while the cool-off window is active; the caller
    reports the remaining seconds to the user and moves on.
    """
    st = _state.get(platform)
    if st is None or st.skip_until is None:
        return (False, 0.0)
    if now >= st.skip_until:
        return (False, 0.0)
    remaining = (st.skip_until - now).total_seconds()
    return (True, remaining)


def on_failure(platform: str, kind: FailureKind, *, now: datetime) -> None:
    """Record a scrape failure and extend the cool-off window.

    Independent failures within one run stack; the ladder is not reset
    by intervening successes unless :func:`on_success` is called
    explicitly by the caller.
    """
    if kind not in ("rate_limit", "challenge"):
        raise ValueError(f"unknown failure kind: {kind!r}")

    st = _state.setdefault(platform, BackoffState())
    st.consecutive_failures += 1
    st.last_kind = kind
    delay = _delay_for(kind, st.consecutive_failures)
    st.skip_until = now + timedelta(seconds=delay)
    logger.warning(
        "%s: %s failure #%d — cooling off for %.0fs",
        platform,
        kind,
        st.consecutive_failures,
        delay,
    )


def on_success(platform: str) -> None:
    """Clear any accumulated backoff for ``platform``."""
    if platform in _state:
        del _state[platform]


def reset() -> None:
    """Clear every platform's state. Used by tests."""
    _state.clear()
