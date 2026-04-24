"""Unit tests for src/flatpilot/scrapers/ua_pool.py.

The UA pool exists to make the Kleinanzeigen scraper's fingerprint look
less bot-like over time, but it must never rotate *within* a single
cookie jar (state.json), because a cookie issued under UA X looks more
suspicious when presented from UA Y than it does when presented from
UA X at its original cadence. The tests below pin that contract:

- Pool is non-trivial and only contains realistic UAs.
- First call for a platform picks and persists.
- Subsequent calls return the pinned UA.
- A separate platform gets its own pin.
- Deleting fingerprint.json causes a fresh pick.
"""

from __future__ import annotations

import json


def test_pool_is_non_trivial_and_looks_realistic() -> None:
    from flatpilot.scrapers.ua_pool import POOL

    assert len(POOL) >= 5, "pool should offer at least 5 realistic UAs"
    for ua in POOL:
        assert ua.startswith("Mozilla/5.0 ("), ua
        assert "AppleWebKit" in ua or "Gecko" in ua, ua


def test_default_user_agent_is_the_d0_validated_firefox_121(tmp_db) -> None:
    """The shipped default remains Firefox 121 on Linux — the D0-validated UA."""
    from flatpilot.scrapers.session import DEFAULT_USER_AGENT
    from flatpilot.scrapers.ua_pool import POOL

    assert DEFAULT_USER_AGENT in POOL
    assert POOL[0] == DEFAULT_USER_AGENT, "Firefox 121 must stay as POOL[0]"


def test_pin_user_agent_first_call_picks_and_persists(tmp_db) -> None:
    from flatpilot.config import SESSIONS_DIR
    from flatpilot.scrapers.ua_pool import POOL, pin_user_agent

    ua = pin_user_agent("kleinanzeigen")
    assert ua in POOL

    fp_path = SESSIONS_DIR / "kleinanzeigen" / "fingerprint.json"
    assert fp_path.exists()
    payload = json.loads(fp_path.read_text())
    assert payload == {"user_agent": ua}


def test_pin_user_agent_reuses_persisted_value(tmp_db) -> None:
    from flatpilot.scrapers.ua_pool import pin_user_agent

    first = pin_user_agent("kleinanzeigen")
    second = pin_user_agent("kleinanzeigen")
    third = pin_user_agent("kleinanzeigen")
    assert first == second == third


def test_pin_user_agent_isolates_platforms(tmp_db) -> None:
    from flatpilot.scrapers.ua_pool import pin_user_agent

    k = pin_user_agent("kleinanzeigen")
    w = pin_user_agent("wg-gesucht")
    # They may coincide by chance, but reading each back must stay stable.
    assert pin_user_agent("kleinanzeigen") == k
    assert pin_user_agent("wg-gesucht") == w


def test_deleting_fingerprint_file_allows_repin(tmp_db) -> None:
    from flatpilot.config import SESSIONS_DIR
    from flatpilot.scrapers.ua_pool import pin_user_agent

    pin_user_agent("kleinanzeigen")
    fp_path = SESSIONS_DIR / "kleinanzeigen" / "fingerprint.json"
    fp_path.unlink()

    ua_after = pin_user_agent("kleinanzeigen")
    assert fp_path.exists()
    assert json.loads(fp_path.read_text()) == {"user_agent": ua_after}


def test_corrupt_fingerprint_file_falls_back_gracefully(tmp_db) -> None:
    """A truncated or non-JSON fingerprint file must not crash the scraper."""
    from flatpilot.config import SESSIONS_DIR
    from flatpilot.scrapers.ua_pool import POOL, pin_user_agent

    session_dir = SESSIONS_DIR / "kleinanzeigen"
    session_dir.mkdir(parents=True, exist_ok=True)
    fp_path = session_dir / "fingerprint.json"
    fp_path.write_text("{not-json")

    ua = pin_user_agent("kleinanzeigen")
    assert ua in POOL
    # Recovered file must be valid JSON now.
    assert json.loads(fp_path.read_text()) == {"user_agent": ua}
