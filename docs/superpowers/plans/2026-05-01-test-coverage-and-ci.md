# Test Coverage Completion + CI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close FlatPilot-kmy and FlatPilot-dwao in one PR by adding GitHub Actions CI, filling the unit-test gaps named in `kmy`, and adding the two missing safety-rail integration tests for `dwao`.

**Architecture:** Test-only PR. No production-code edits. New tests use the existing `tmp_db` fixture, `Profile.load_example().model_copy(...)`, and `unittest.mock.patch` / `monkeypatch` patterns already in the suite. New CI workflow runs `ruff check` + `pytest` (coverage flags via `addopts`).

**Tech Stack:** Python 3.11, pytest, pytest-cov, ruff, GitHub Actions, BeautifulSoup, httpx (stubbed in distance tests).

**Spec:** `docs/superpowers/specs/2026-05-01-test-coverage-and-ci-design.md`

**Commit budget:** 2 commits on `feat/test-coverage-and-ci`:
- **Commit 1** — spec doc only (Task 1).
- **Commit 2** — every other change (Tasks 2–11).

---

## File Structure

**New:**
- `.github/workflows/ci.yml` — single-job workflow: ruff + pytest on push/PR to main.
- `tests/test_matcher_filters.py` — 9 filters × 3+ cases each; one `evaluate()` integration case.
- `tests/test_matcher_distance.py` — Haversine, cache hit/miss, TTL, `resolve_flat_coords` short-circuit.
- `tests/test_wg_gesucht_scraper.py` — URL builder, parser, ad exclusion, `UnknownCityError`.
- `tests/test_notifications_dispatcher.py` — profile-hash scoping, `_mark_stale_matches_notified`, `enabled_channels`, `send_test`.
- `tests/fixtures/wg_gesucht/search_results.html` — trimmed real-snapshot, ≥2 offer cards + ≥1 ad card.

**Modified:**
- `pyproject.toml:77` — extend `addopts` with `--cov` flags.
- `tests/test_run_pipeline_apply.py` — append two integration tests (cap-exhausted, cooldown-active) at end of file.

**Audited (no edit unless gap found):**
- `tests/test_doctor.py`, `tests/test_doctor_auto_apply.py`.

---

## Pre-flight

- [ ] **Step 0a: Verify clean working tree on `main` at `origin/main`**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot status
git -C /Users/vividadmin/Desktop/FlatPilot fetch origin
git -C /Users/vividadmin/Desktop/FlatPilot rev-parse HEAD
git -C /Users/vividadmin/Desktop/FlatPilot rev-parse origin/main
```

Expected: status clean; `HEAD` matches `origin/main`. If the spec was edited but not yet committed, that's fine — Task 1 commits it.

- [ ] **Step 0b: Confirm git author identity**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot config user.name
git -C /Users/vividadmin/Desktop/FlatPilot config user.email
```

Expected: `Mukhammad Ibrokhimov` and `ibrohimovmuhammad2020@gmail.com`. If wrong, fix locally per `CLAUDE.md`.

- [ ] **Step 0c: Claim both beads**

```bash
bd update FlatPilot-kmy --claim --status=in_progress
bd update FlatPilot-dwao --claim --status=in_progress
```

---

## Task 1: Branch + commit the spec doc

**Files:**
- Modify: working tree (branch only)
- Existing on disk, uncommitted: `docs/superpowers/specs/2026-05-01-test-coverage-and-ci-design.md`

- [ ] **Step 1.1: Create the feature branch from `origin/main`**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot checkout -b feat/test-coverage-and-ci origin/main
```

- [ ] **Step 1.2: Stage and verify the spec is the only diff**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot add docs/superpowers/specs/2026-05-01-test-coverage-and-ci-design.md
git -C /Users/vividadmin/Desktop/FlatPilot status --short
```

Expected: a single `A` line for the spec file. If anything else appears, stash or unstage before continuing.

- [ ] **Step 1.3: Create commit 1 (spec only — no AI trailer)**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot commit -m "FlatPilot-kmy/dwao: spec for test coverage and CI"
git -C /Users/vividadmin/Desktop/FlatPilot log -1 --pretty=fuller
```

Expected: author and committer both `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. No `Co-Authored-By:` line.

---

## Task 2: `pyproject.toml` — coverage in `addopts`

**Files:**
- Modify: `pyproject.toml:77`

- [ ] **Step 2.1: Replace the `addopts` line**

In `pyproject.toml`, find:
```toml
addopts = "-ra"
```

Replace with:
```toml
addopts = "-ra --cov=flatpilot --cov-report=term --cov-fail-under=60"
```

- [ ] **Step 2.2: Run pytest to confirm the addopts parse and the suite still passes against the existing tests**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest -x --no-cov -q 2>&1 | tail -25
```

(`--no-cov` to skip coverage just for this smoke; full coverage run is in Task 9.)

Expected: tests collected and passing. If the `--cov` flag is unrecognized, `pytest-cov` is not installed in the active env — `pip install -e '.[dev]'` first.

- [ ] **Step 2.3: Do NOT commit yet** — Task 11 makes the single impl commit.

---

## Task 3: `tests/test_matcher_filters.py` — filter unit coverage

**Files:**
- Create: `tests/test_matcher_filters.py`
- Read for reference: `src/flatpilot/matcher/filters.py`

- [ ] **Step 3.1: Read the production module to confirm filter signatures and reason strings**

```bash
sed -n '36,175p' /Users/vividadmin/Desktop/FlatPilot/src/flatpilot/matcher/filters.py
```

Note exact reason strings emitted by each filter — assertions use them verbatim.

- [ ] **Step 3.2: Create the test file with one block per filter**

Skeleton (extend per spec §5.1 — pass / reject / missing-field for each filter, plus `filter_radius` no-home-coords case, plus one `evaluate()` integration case):

```python
"""Unit coverage for the deterministic hard filters."""
from __future__ import annotations

from flatpilot.matcher.filters import (
    FILTERS,
    evaluate,
    filter_contract,
    filter_district,
    filter_furnished,
    filter_move_in,
    filter_pets,
    filter_radius,
    filter_rent_band,
    filter_rooms_band,
    filter_wbs,
)
from flatpilot.profile import Profile


def _profile(**overrides):
    return Profile.load_example().model_copy(update=overrides)


# --- filter_rent_band ---------------------------------------------------

def test_rent_band_passes_within_window():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat = {"rent_warm_eur": 1000}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is True and reason is None


def test_rent_band_rejects_above_max():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat = {"rent_warm_eur": 1600}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is False
    assert reason  # non-empty


def test_rent_band_rejects_when_field_missing():
    profile = _profile(rent_min_warm=500, rent_max_warm=1500)
    flat = {}
    ok, reason = filter_rent_band(flat, profile)
    assert ok is False
    assert "rent" in reason.lower()


# --- filter_rooms_band, filter_wbs, filter_district, filter_pets,
#     filter_move_in, filter_furnished, filter_contract — same shape ---


# --- filter_radius ------------------------------------------------------

def test_radius_passes_when_no_home_coords():
    profile = _profile(home_lat=None, home_lng=None, radius_km=5)
    flat = {"latitude": 0.0, "longitude": 0.0}
    ok, reason = filter_radius(flat, profile)
    assert ok is True and reason is None


# --- evaluate() integration --------------------------------------------

def test_evaluate_returns_empty_for_passing_flat():
    profile = _profile(
        rent_min_warm=400, rent_max_warm=2000, rooms_min=1, rooms_max=4,
    )
    flat = {
        "rent_warm_eur": 1000, "rooms": 2, "requires_wbs": 0,
        # plus all other fields the filters need
    }
    assert evaluate(flat, profile) == []


def test_evaluate_returns_reasons_in_filters_order():
    profile = _profile(rent_min_warm=2000, rent_max_warm=2500, rooms_min=4)
    flat = {"rent_warm_eur": 600, "rooms": 1}
    reasons = evaluate(flat, profile)
    assert len(reasons) >= 2
    # First reason corresponds to the first failing filter in FILTERS
    assert isinstance(reasons[0], str)
```

Per-filter case checklist (TDD-against-existing-code: each test should PASS on first run because production code is already correct):

| Filter | Pass | Reject | Missing field |
| --- | --- | --- | --- |
| `filter_rent_band` | within window | above max | no `rent_warm_eur` |
| `filter_rooms_band` | within window | below min | no `rooms` |
| `filter_wbs` | flat doesn't require WBS, profile has none | flat requires WBS, profile has none | n/a (boolean field) |
| `filter_district` | flat district in allowlist | flat district not in allowlist | no `district` |
| `filter_pets` | profile no pets, flat allows | profile has pets, flat forbids | no `pets_allowed` |
| `filter_move_in` | flat date within MOVE_IN_TOLERANCE of profile.move_in | flat date too late | no `available_from` |
| `filter_furnished` | matches `furnished_pref` | conflicts | no `furnished` |
| `filter_contract` | meets `min_contract_months` | shorter than minimum | no `min_contract_months` on flat |
| `filter_radius` | within `radius_km` | outside | + extra: home_lat=None passes |

- [ ] **Step 3.3: Run the file**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest tests/test_matcher_filters.py --no-cov -v 2>&1 | tail -40
```

Expected: all PASS. If a test FAILS, that surfaces a real bug in production code. Per spec §3.3, do NOT fix the bug here — file a follow-up bead and remove (or `pytest.skip`-with-reason) just that test from this PR.

- [ ] **Step 3.4: Do NOT commit yet.**

---

## Task 4: `tests/test_matcher_distance.py` — geocoder cache + Haversine

**Files:**
- Create: `tests/test_matcher_distance.py`
- Read: `src/flatpilot/matcher/distance.py`

- [ ] **Step 4.1: Read distance.py to confirm `httpx.get` call shape, cache JSON schema, TTL constant**

```bash
sed -n '1,140p' /Users/vividadmin/Desktop/FlatPilot/src/flatpilot/matcher/distance.py
```

- [ ] **Step 4.2: Create the test file**

```python
"""Unit coverage for matcher.distance: Haversine + Nominatim cache."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import flatpilot.matcher.distance as dist


def test_haversine_km_known_pair():
    # Berlin Hbf (~52.5251, 13.3694) to Alexanderplatz (~52.5219, 13.4132)
    km = dist.haversine_km(52.5251, 13.3694, 52.5219, 13.4132)
    assert 1.0 < km < 4.0


def test_geocode_cache_hit_does_not_call_httpx(tmp_path, monkeypatch):
    cache_path = tmp_path / "geocode_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "berlin hbf": {
                    "lat": 52.5,
                    "lng": 13.4,
                    "cached_at": datetime.now(UTC).isoformat(),
                }
            }
        )
    )
    monkeypatch.setattr(dist, "GEOCODE_CACHE_PATH", cache_path)

    calls = {"n": 0}

    def fake_get(*_a, **_kw):
        calls["n"] += 1
        raise AssertionError("httpx.get should not be called on cache hit")

    monkeypatch.setattr(dist.httpx, "get", fake_get)

    coords = dist.geocode("Berlin Hbf")
    assert coords == (52.5, 13.4)
    assert calls["n"] == 0


def test_geocode_cache_miss_writes_through(tmp_path, monkeypatch):
    cache_path = tmp_path / "geocode_cache.json"
    monkeypatch.setattr(dist, "GEOCODE_CACHE_PATH", cache_path)

    class _Resp:
        status_code = 200
        def json(self):
            return [{"lat": "52.50", "lon": "13.40"}]

    calls = {"n": 0}

    def fake_get(*_a, **_kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(dist.httpx, "get", fake_get)

    coords = dist.geocode("Berlin Hbf")
    assert coords == (52.5, 13.4)
    assert calls["n"] == 1
    assert cache_path.exists()
    saved = json.loads(cache_path.read_text())
    assert "berlin hbf" in {k.lower() for k in saved}


def test_entry_fresh_ttl():
    fresh = {"cached_at": (datetime.now(UTC) - timedelta(days=30)).isoformat()}
    stale = {"cached_at": (datetime.now(UTC) - timedelta(days=200)).isoformat()}
    assert dist._entry_fresh(fresh) is True
    assert dist._entry_fresh(stale) is False


def test_resolve_flat_coords_short_circuits_when_present(monkeypatch):
    def boom(*_a, **_kw):
        raise AssertionError("geocode must not be called when coords present")

    monkeypatch.setattr(dist, "geocode", boom)

    # NOTE: keys are `lat`/`lng` (per distance.py:143-144), NOT `latitude`/
    # `longitude`. If you use the long names the short-circuit silently
    # fails and the boom() stub fires.
    flat = {"lat": 52.5, "lng": 13.4, "title": "x"}
    coords = dist.resolve_flat_coords(flat)
    assert coords == (52.5, 13.4)
```

Notes the implementer must verify against actual code:
- `dist.geocode`'s cache lookup is case-folded — line 96 of distance.py reads `address.strip().lower()`. The test uses `"berlin hbf"` (lowercased) as the cache key.
- The Nominatim response JSON shape returned by httpx must match what `geocode()` parses. If `geocode()` does `resp.json()[0]["lat"]`, the fake `_Resp.json` returns a list. Verify in code; adjust the fake if `geocode()` reads a dict instead.
- `resolve_flat_coords` reads `flat.get("lat")` and `flat.get("lng")` (`distance.py:143-144`) — already encoded in the test above. Do NOT change to `latitude`/`longitude`.

- [ ] **Step 4.3: Run the file**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest tests/test_matcher_distance.py --no-cov -v 2>&1 | tail -40
```

Expected: all PASS. If `geocode()`'s response shape doesn't match the fake `_Resp`, fix the fake (don't change production code).

---

## Task 5: `tests/test_wg_gesucht_scraper.py` + fixture HTML

**Files:**
- Create: `tests/fixtures/wg_gesucht/search_results.html`
- Create: `tests/test_wg_gesucht_scraper.py`
- Read: `src/flatpilot/scrapers/wg_gesucht.py`

- [ ] **Step 5.1: Read the scraper to know exactly which selectors `_parse_card` extracts**

```bash
sed -n '167,230p' /Users/vividadmin/Desktop/FlatPilot/src/flatpilot/scrapers/wg_gesucht.py
```

Record: which `data-*` attrs and inner-text patterns `_parse_card` reads.

- [ ] **Step 5.2: Create `tests/fixtures/wg_gesucht/search_results.html`**

Minimum viable: 2 valid `.wgg_card.offer_list_item` divs with the attributes `_parse_card` reads (`data-id`, `data-asset_id` if present, district link, price, rooms, available-from text), + 1 `.wgg_card.housinganywhere_ad` (or `airbnb_ad`) div without the `offer_list_item` class to confirm the selector excludes it.

Template — replace placeholder values with fields the implementer extracts from the production parser:

```html
<!doctype html>
<html><body>

<div class="wgg_card offer_list_item" data-id="111111">
  <a class="detailansicht" href="/wg-zimmer-in-berlin-mitte.111111.html">2-Zimmer Wohnung Mitte</a>
  <div class="col-sm-3"><b>900 €</b></div>
  <div class="col-sm-3">2 Zimmer | 60 m²</div>
  <div class="col-sm-3">ab 01.06.2026</div>
</div>

<div class="wgg_card offer_list_item" data-id="222222">
  <a class="detailansicht" href="/wohnungen-in-berlin-kreuzberg.222222.html">3 BR Kreuzberg</a>
  <div class="col-sm-3"><b>1450 €</b></div>
  <div class="col-sm-3">3 Zimmer | 85 m²</div>
  <div class="col-sm-3">ab 15.07.2026</div>
</div>

<!-- Ad row, must be excluded -->
<div class="wgg_card housinganywhere_ad" data-id="999999">
  <a href="https://housinganywhere.com">Sponsored</a>
</div>

</body></html>
```

After writing, the implementer MUST update field names / attribute names to match what `_parse_card` actually reads. If `_parse_card` reads `data-asset_id`, add it. If it reads `<span class="badge">WBS</span>`, include one card with that and one without.

- [ ] **Step 5.3: Create the test file**

```python
"""Unit coverage for scrapers.wg_gesucht — URL builder + parser."""
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

# Confirmed in errors.py:30; wg_gesucht.py:29 imports from flatpilot.errors.
from flatpilot.errors import UnknownCityError
from flatpilot.profile import Profile
from flatpilot.scrapers.wg_gesucht import WGGesuchtScraper, _parse_card

FIXTURE = Path(__file__).parent / "fixtures" / "wg_gesucht" / "search_results.html"


def test_search_url_for_berlin():
    # Berlin's CITY_IDS entry is 8 (wg_gesucht.py:60). _search_url is a
    # pure staticmethod so we can pin the full string.
    url = WGGesuchtScraper._search_url("Berlin", 8)
    assert url == "https://www.wg-gesucht.de/wohnungen-in-Berlin.8.2.1.0.html"


def test_search_url_substitutes_spaces_with_hyphens():
    url = WGGesuchtScraper._search_url("Frankfurt am Main", 41)
    assert "wohnungen-in-Frankfurt-am-Main" in url
    assert ".41.2.1.0.html" in url


def test_parse_listings_excludes_ad_cards():
    html = FIXTURE.read_text()
    # Sanity: fixture must contain the ad row that the test claims is
    # being excluded; otherwise the test's "exclusion" claim is vacuous.
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select(".wgg_card")) >= 3, "fixture missing ad card"
    assert len(soup.select(".wgg_card.offer_list_item")) == 2, "fixture must have 2 offer cards"

    # Flat is a TypedDict (scrapers/base.py:31), so use ["key"] access.
    flats = list(WGGesuchtScraper._parse_listings(html))
    assert len(flats) == 2
    ids = {f["external_id"] for f in flats}
    assert ids == {"111111", "222222"}


def test_parse_listings_extracts_basic_fields():
    html = FIXTURE.read_text()
    flats = list(WGGesuchtScraper._parse_listings(html))
    by_id = {f["external_id"]: f for f in flats}
    assert by_id["111111"]["title"]  # non-empty
    assert by_id["111111"]["listing_url"].endswith(".111111.html")


def test_parse_card_returns_none_on_missing_id():
    soup = BeautifulSoup(
        '<div class="wgg_card offer_list_item"></div>', "html.parser"
    )
    card = soup.find("div")
    assert _parse_card(card) is None


def test_fetch_new_raises_for_unsupported_city():
    scraper = WGGesuchtScraper()
    profile = Profile.load_example().model_copy(update={"city": "Vladivostok"})

    with pytest.raises(UnknownCityError):
        # Generator must be drained for the body to execute.
        list(scraper.fetch_new(profile, known_external_ids=frozenset()))
```

- [ ] **Step 5.4: Run the file**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest tests/test_wg_gesucht_scraper.py --no-cov -v 2>&1 | tail -40
```

Expected: all PASS. If `_parse_listings` extracts fewer fields than the test asserts, narrow the assertions to what the parser actually returns.

---

## Task 6: `tests/test_notifications_dispatcher.py`

**Files:**
- Create: `tests/test_notifications_dispatcher.py`
- Read: `src/flatpilot/notifications/dispatcher.py`

- [ ] **Step 6.1: Read dispatcher.py — confirm column names and `_send` signature**

```bash
sed -n '1,200p' /Users/vividadmin/Desktop/FlatPilot/src/flatpilot/notifications/dispatcher.py
```

- [ ] **Step 6.2: Create the test file**

```python
"""Unit coverage for notifications.dispatcher."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import flatpilot.notifications.dispatcher as disp
from flatpilot.profile import Profile, profile_hash


def _seed_flat(conn, *, external_id="e1"):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
        VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, ?, 0)
        """,
        (external_id, now, now),
    )
    return cur.lastrowid


def _seed_match(conn, *, flat_id, profile_hash, decision="match"):
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO matches
            (flat_id, profile_version_hash, decision, decision_reasons_json,
             decided_at, matched_saved_searches_json)
        VALUES (?, ?, ?, '[]', ?, '[]')
        """,
        (flat_id, profile_hash, decision, now),
    )


def test_dispatch_pending_skips_stale_hash_rows(tmp_db, monkeypatch):
    profile = Profile.load_example()
    current = profile_hash(profile)
    stale = "deadbeef" * 4  # any string != current

    flat_a = _seed_flat(tmp_db, external_id="a")
    flat_b = _seed_flat(tmp_db, external_id="b")
    _seed_match(tmp_db, flat_id=flat_a, profile_hash=current)
    _seed_match(tmp_db, flat_id=flat_b, profile_hash=stale)

    sends: list[tuple[str, int]] = []

    def fake_send(channel, flat, profile):
        sends.append((channel, flat["id"]))

    monkeypatch.setattr(disp, "_send", fake_send)
    monkeypatch.setattr(
        disp, "enabled_channels", lambda _p: ["telegram"]
    )

    disp.dispatch_pending(profile)
    assert sends == [("telegram", flat_a)]


def test_mark_stale_flips_notified_at_without_send(tmp_db, monkeypatch):
    profile = Profile.load_example()
    current = profile_hash(profile)
    stale = "00" * 16

    flat_a = _seed_flat(tmp_db, external_id="a")
    flat_b = _seed_flat(tmp_db, external_id="b")
    _seed_match(tmp_db, flat_id=flat_a, profile_hash=current)
    _seed_match(tmp_db, flat_id=flat_b, profile_hash=stale)

    monkeypatch.setattr(disp, "_send", lambda *a, **k: None)
    monkeypatch.setattr(disp, "enabled_channels", lambda _p: [])

    # Calling dispatch_pending exercises both branches; the helper is
    # also private, so we drive it through the public entry point.
    disp.dispatch_pending(profile)

    rows = {
        r["flat_id"]: r["notified_at"]
        for r in tmp_db.execute(
            "SELECT flat_id, notified_at FROM matches"
        ).fetchall()
    }
    # Stale row was marked, current-hash row is still unsent (no enabled
    # channels) but should not have a notified_at either.
    assert rows[flat_b]
    assert not rows[flat_a]


def _notifications(*, telegram: bool, email: bool):
    """Build a Notifications model with the given enabled flags.

    Real schema (profile.py:39-60): Notifications has nested
    TelegramNotification(enabled=...) and EmailNotification(enabled=...)
    children. Passing a flat {"telegram": bool} dict will fail pydantic
    validation — use the proper child models.
    """
    from flatpilot.profile import (
        EmailNotification,
        Notifications,
        TelegramNotification,
    )
    return Notifications(
        telegram=TelegramNotification(enabled=telegram, chat_id="x"),
        email=EmailNotification(enabled=email),
    )


def test_enabled_channels_empty_when_neither_configured():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=False, email=False)}
    )
    assert disp.enabled_channels(profile) == []


def test_enabled_channels_positional_order_both_on():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=True, email=True)}
    )
    # Order is positional (telegram first, email second per dispatcher.py:49-55),
    # not alphabetically sorted.
    assert disp.enabled_channels(profile) == ["telegram", "email"]


def test_enabled_channels_telegram_only():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=True, email=False)}
    )
    assert disp.enabled_channels(profile) == ["telegram"]


def test_enabled_channels_email_only():
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=False, email=True)}
    )
    assert disp.enabled_channels(profile) == ["email"]


def test_send_test_invokes_each_enabled_channel_once(monkeypatch):
    profile = Profile.load_example().model_copy(
        update={"notifications": _notifications(telegram=True, email=True)}
    )

    calls: list[str] = []

    def fake_send(channel, flat, profile):
        calls.append(channel)

    monkeypatch.setattr(disp, "_send", fake_send)

    result = disp.send_test(profile)
    assert sorted(calls) == ["email", "telegram"]
    assert isinstance(result, dict)
    assert set(result.keys()) >= {"telegram", "email"}
```

`_send`'s signature is `(channel: str, flat: dict[str, Any], profile: Profile) -> None` per `dispatcher.py:76`. If the function evolves later, update the fake's signature to match.

- [ ] **Step 6.3: Run the file**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest tests/test_notifications_dispatcher.py --no-cov -v 2>&1 | tail -40
```

Expected: all PASS.

---

## Task 7: Doctor audit (no edit unless gap found)

**Files:**
- Read: `tests/test_doctor.py`, `tests/test_doctor_auto_apply.py`, `src/flatpilot/doctor.py`

- [ ] **Step 7.1: Enumerate `_check_telegram` and `_check_smtp` branches**

```bash
grep -n 'def _check_telegram\|def _check_smtp\|return ' /Users/vividadmin/Desktop/FlatPilot/src/flatpilot/doctor.py
```

List branches (e.g. "telegram disabled", "telegram enabled but token missing", "telegram enabled and token present", "smtp disabled", "smtp enabled but server missing", "smtp enabled and configured", "malformed profile / load_profile raises").

- [ ] **Step 7.2: Cross-reference against the existing tests**

```bash
grep -n 'def test_' /Users/vividadmin/Desktop/FlatPilot/tests/test_doctor.py /Users/vividadmin/Desktop/FlatPilot/tests/test_doctor_auto_apply.py
```

For each branch from 7.1, find a matching test. Record gaps.

- [ ] **Step 7.3: If — and only if — a branch named in `kmy` is missing, append the gap-fillers to `tests/test_doctor.py`**

Confirmed real entrypoint: `flatpilot.doctor.run(console: Console | None = None) -> int` (`doctor.py:243`). It accepts an optional `Console` and returns an exit-code int. If the malformed-profile branch is uncovered, add:

```python
def test_doctor_handles_malformed_profile(tmp_db):
    from io import StringIO

    from rich.console import Console

    from flatpilot import doctor
    from flatpilot.config import PROFILE_PATH

    PROFILE_PATH.write_text("{ not valid json")  # malformed

    buf = StringIO()
    rc = doctor.run(Console(file=buf, force_terminal=False))

    assert rc != 0
    assert "profile" in buf.getvalue().lower()
```

If `doctor.run` raises on a malformed profile instead of returning a non-zero rc, that's a real bug — file a follow-up bead per spec §3.3 and skip this test rather than fixing the bug here.

- [ ] **Step 7.4: Run only the doctor tests**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest tests/test_doctor.py tests/test_doctor_auto_apply.py --no-cov -v 2>&1 | tail -25
```

Expected: PASS. Document in the PR description which `kmy` branches are now covered (existing) vs. added (new gap fillers).

---

## Task 8: Add 2 safety-rail integration tests to `tests/test_run_pipeline_apply.py`

**Files:**
- Modify: `tests/test_run_pipeline_apply.py` (append at end)
- Read: `src/flatpilot/auto_apply.py:172-256`

- [ ] **Step 8.1: Read `_try_flat` to confirm gate ordering**

```bash
sed -n '172,256p' /Users/vividadmin/Desktop/FlatPilot/src/flatpilot/auto_apply.py
```

Confirmed gate order: cap → cooldown → max-failures → completeness_ok → apply_to_flat. Cap and cooldown reject *before* `completeness_ok`, so the new tests do NOT need to patch `completeness_ok`.

- [ ] **Step 8.2: Append the two tests to `tests/test_run_pipeline_apply.py`**

The existing `_seed_flat` helper at line 25 hard-codes `external_id='e1'`, so calling it more than once per test will fail the UNIQUE constraint on `flats.external_id`. The new tests need 2-3 distinct flats each. Add a local helper at the top of the new test block (don't touch the existing `_seed_flat` — that would risk breaking the existing tests):

```python
def _seed_flat_with(conn, *, external_id, platform="wg-gesucht"):
    """Like _seed_flat but with caller-supplied external_id (UNIQUE)."""
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at, requires_wbs) "
        "VALUES (?, ?, 'https://x', 'T', ?, ?, 0)",
        (external_id, platform, now, now),
    )
    return cur.lastrowid
```

Then the two tests. The file already imports `json`, `datetime`, `UTC`, `unittest.mock.patch`, `Console`, `Profile`, `SavedSearch`, `save_profile`, `_seed_match`, `_profile_with_one_auto_search`. Reuse them.

```python
def test_daily_cap_exhausted_skips_pending_match(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import AutoApplySettings, profile_hash

    base = _profile_with_one_auto_search()
    profile = base.model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 2},
                cooldown_seconds_per_platform={"wg-gesucht": 0},
                pacing_seconds_per_platform={"wg-gesucht": 0},
            )
        }
    )
    save_profile(profile)
    today = datetime.now(UTC).isoformat()

    # Seed 2 submitted rows = cap reached. Each prior flat gets a
    # unique external_id so the UNIQUE constraint holds.
    for i in range(2):
        prior_flat = _seed_flat_with(tmp_db, external_id=f"prior-{i}")
        tmp_db.execute(
            "INSERT INTO applications "
            "(flat_id, platform, listing_url, title, applied_at, method, "
            " attachments_sent_json, status) "
            "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'submitted')",
            (prior_flat, today),
        )

    pending_flat = _seed_flat_with(tmp_db, external_id="pending")
    _seed_match(
        tmp_db,
        flat_id=pending_flat,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())

    mocked.assert_not_called()
    new_apps = tmp_db.execute(
        "SELECT COUNT(*) FROM applications WHERE flat_id = ?", (pending_flat,)
    ).fetchone()[0]
    assert new_apps == 0


def test_active_cooldown_skips_pending_match(tmp_db):
    from datetime import timedelta

    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import AutoApplySettings, profile_hash

    base = _profile_with_one_auto_search()
    profile = base.model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 100},
                cooldown_seconds_per_platform={"wg-gesucht": 120},
                pacing_seconds_per_platform={"wg-gesucht": 0},
            )
        }
    )
    save_profile(profile)

    # One submitted row 30s ago → 90s cooldown remaining > 0.
    prior_flat = _seed_flat_with(tmp_db, external_id="prior-cool")
    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    tmp_db.execute(
        "INSERT INTO applications "
        "(flat_id, platform, listing_url, title, applied_at, method, "
        " attachments_sent_json, status) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'submitted')",
        (prior_flat, recent),
    )

    pending_flat = _seed_flat_with(tmp_db, external_id="pending-cool")
    _seed_match(
        tmp_db,
        flat_id=pending_flat,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())

    mocked.assert_not_called()
```

- [ ] **Step 8.3: Run the file**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest tests/test_run_pipeline_apply.py --no-cov -v 2>&1 | tail -40
```

Expected: all PASS, including the two new tests AND the three existing pause/max-failures/happy-path tests (no regressions).

---

## Task 9: `.github/workflows/ci.yml`

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 9.1: Verify the directory does not exist**

```bash
ls /Users/vividadmin/Desktop/FlatPilot/.github 2>/dev/null
```

Expected: not found.

- [ ] **Step 9.2: Create the workflow file**

```yaml
name: ci

on:
  push:
    branches-ignore:
      - "gh-readonly-queue/**"
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: pyproject.toml

      - name: Install
        run: pip install -e '.[dev]'

      - name: Lint
        run: ruff check .

      - name: Test
        run: pytest
```

- [ ] **Step 9.3: Sanity-check the YAML parses (no install of `act` needed — just ensure it's valid YAML)**

```bash
python -c "import yaml; print(yaml.safe_load(open('/Users/vividadmin/Desktop/FlatPilot/.github/workflows/ci.yml')))" 2>&1 | head -5
```

Expected: a dict — no `yaml.YAMLError`.

---

## Task 10: Full local verification (the gate before commit)

- [ ] **Step 10.1: Lint the entire repo (CI step 4)**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && ruff check . 2>&1 | tail -20
```

Expected: `All checks passed!`. If new test files have lint issues, fix in place and re-run.

- [ ] **Step 10.2: Run pytest with coverage (CI step 5)**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && python -m pytest 2>&1 | tail -25
```

Expected: all tests pass; coverage line prints `Required test coverage of 60% reached. Total coverage: NN.NN%`. If `--cov-fail-under` triggers under 60:
- **Do NOT amend the spec doc** (commit 1 is already final and spec-only).
- In `pyproject.toml`'s `addopts`, lower `--cov-fail-under=60` to whatever the actual rounded-down floor is. Keep the change in commit 2 (this PR).
- Add a brief note in the PR description explaining the actual % and naming a follow-up bead to ratchet it.
Identifying a quick-win module to bring coverage up is preferable to lowering the bar — exhaust that option first.

- [ ] **Step 10.3: Confirm git diff is what we expect (and only what we expect)**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot status --short
```

Expected:
```
 M pyproject.toml
 M tests/test_run_pipeline_apply.py
?? .github/
?? tests/fixtures/wg_gesucht/
?? tests/test_matcher_distance.py
?? tests/test_matcher_filters.py
?? tests/test_notifications_dispatcher.py
?? tests/test_wg_gesucht_scraper.py
```

(Plus possibly `tests/test_doctor.py` if a gap was filled in Task 7.)

If anything else (e.g. `__pycache__`, edits to production files), investigate before continuing.

---

## Task 11: User approval gate + commit 2 + push + PR

- [ ] **Step 11.1: Pause and ask user for explicit approval to commit and push**

Show the user: branch name, list of new/modified files (from `git status --short`), and the coverage % from Step 10.2. Wait for "yes" / "go ahead" before proceeding.

- [ ] **Step 11.2: Stage every file in scope, no globbing**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && \
  git add pyproject.toml tests/test_run_pipeline_apply.py \
          .github/workflows/ci.yml \
          tests/fixtures/wg_gesucht/search_results.html \
          tests/test_matcher_distance.py tests/test_matcher_filters.py \
          tests/test_notifications_dispatcher.py tests/test_wg_gesucht_scraper.py
# Add tests/test_doctor.py only if Task 7 modified it.
git status --short
```

Expected: every line now starts with `A` or `M`. No untracked files.

- [ ] **Step 11.3: Create commit 2 with a HEREDOC body, no AI trailer**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && git commit -m "$(cat <<'EOF'
FlatPilot-kmy/dwao: add CI workflow and complete test coverage

Closes FlatPilot-kmy and FlatPilot-dwao.

- New GitHub Actions workflow runs ruff + pytest on push/PR to main.
- New unit tests for matcher.filters, matcher.distance, scrapers.wg_gesucht
  parser, and notifications.dispatcher.
- Two new integration tests in test_run_pipeline_apply.py for the
  daily-cap and cooldown safety rails (pause / max-failures / happy
  path were already covered).
- pyproject addopts now sets --cov-fail-under=60.
EOF
)"
git log -2 --pretty=fuller
```

Expected: two commits, both authored by `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`, no `Co-Authored-By:` trailer.

- [ ] **Step 11.4: Push the branch with upstream tracking**

```bash
git -C /Users/vividadmin/Desktop/FlatPilot push -u origin feat/test-coverage-and-ci
```

Expected: branch created on `origin`. The newly pushed branch should also trigger the CI workflow we just added — note in the PR if the workflow appears as `pending`.

- [ ] **Step 11.5: Open the PR**

```bash
cd /Users/vividadmin/Desktop/FlatPilot && gh pr create --base main --head feat/test-coverage-and-ci --title "feat: complete test coverage + add CI (kmy, dwao)" --body "$(cat <<'EOF'
## Summary

Closes FlatPilot-kmy (pytest suite + initial coverage) and FlatPilot-dwao (N6 — integration tests for caps/cooldowns/pause).

- Adds GitHub Actions CI: ruff + pytest with --cov-fail-under=60 on push and PRs to main.
- Adds unit tests for matcher.filters, matcher.distance, scrapers.wg_gesucht parser, and notifications.dispatcher.
- Adds the two safety-rail integration tests (daily cap, cooldown active) that the existing test_run_pipeline_apply.py was missing — pause, max-failures, and happy path were already covered.
- pyproject addopts gains coverage flags so local pytest mirrors CI.

Spec: docs/superpowers/specs/2026-05-01-test-coverage-and-ci-design.md

## Test plan

- [x] ruff check . is green locally
- [x] pytest is green locally with --cov-fail-under=60
- [ ] CI workflow goes green on the PR
EOF
)"
```

Capture the PR URL and report it back.

- [ ] **Step 11.6: Close the beads (only after the PR is open and green-ish in CI)**

```bash
bd close FlatPilot-kmy FlatPilot-dwao
# Skip the next line if no dolt remote is configured (per CLAUDE.md
# Git & PR Rules — `bd dolt push` is orthogonal to GitHub):
# bd dolt push
```

---

## Out-of-scope follow-ups (file as new beads if encountered)

- Any production-code bug surfaced by a new test (per spec §3.3, fix in a separate PR).
- Raising `--cov-fail-under` above 60 — leave for a follow-up bead.
- Python 3.12 in CI matrix — leave for a follow-up bead.
- `mypy --strict` CI gate — leave for a follow-up bead.
- `playwright install chromium` step — leave for whichever PR first needs a real browser in CI.
