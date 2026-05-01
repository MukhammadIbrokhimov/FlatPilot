# Test Coverage Completion + CI

**Beads:** FlatPilot-kmy (pytest suite + initial coverage),
FlatPilot-dwao (N6 — integration tests for caps/cooldowns/pause).

**Date:** 2026-05-01. **Status:** Approved during brainstorming;
pending sign-off on this spec before writing-plans.

---

## 1. Goal

Close the two open testing beads in a single small PR. Concretely:

1. Add a CI workflow so every PR runs ruff + pytest with a coverage
   floor.
2. Fill the unit-test gaps that `kmy` named explicitly: `matcher/filters.py`,
   `matcher/distance.py`, the WG-Gesucht parser, and
   `notifications/dispatcher.py`.
3. Add an end-to-end test for `run_pipeline_apply` that drives every
   safety rail (pause / cap / cooldown / max-failures) through the
   real orchestrator with seeded DB rows — the integration coverage
   `dwao` exists for.

Out of scope for this PR is in section 2.

## 2. Non-goals

- New dev dependencies. No `freezegun`, no `responses`, no `httpx`
  recorder. Existing `monkeypatch` + timestamp-arithmetic patterns
  are sufficient.
- Browser install in CI (`playwright install chromium`). Existing
  scraper tests stub Playwright via `make_session_fakes`; nothing in
  this PR exercises a real browser.
- mypy gate in CI. `mypy` stays in dev extras; we don't add a
  type-check job. Same reason: scope.
- Python-version matrix. Single 3.11 job. `pyproject` claims 3.12
  support but a matrix is deferred.
- Raising the coverage floor over time. We ship at 60% and let
  follow-ups ratchet it.
- Closing other beads that mention "tests" peripherally.

## 3. Surface (the diff)

### 3.1 New files

```
.github/
└── workflows/
    └── ci.yml

tests/
├── fixtures/
│   └── wg_gesucht/
│       └── search_results.html      # one captured search page
├── test_matcher_filters.py
├── test_matcher_distance.py
├── test_notifications_dispatcher.py
└── test_wg_gesucht_scraper.py
```

### 3.1a Modified test files

- `tests/test_run_pipeline_apply.py` — add the **two** integration
  cases not already covered by the file: daily-cap-exhausted and
  cooldown-active. The existing file already covers pause
  (`test_pause_short_circuits_stage`), max-failures
  (`test_skips_after_max_failures_reached`), and the happy path
  (`test_iterates_candidate_and_calls_apply_to_flat`). No new file
  for safety rails.

### 3.2 Modified files

- `pyproject.toml` — no dependency changes. Coverage flags
  (`--cov=flatpilot --cov-report=term --cov-fail-under=60`) live in
  `addopts` so contributors get coverage locally and CI gets the
  same gate without a duplicate command.

### 3.3 No code changes outside `tests/`, `pyproject.toml`, and `.github/`

If a test exposes a bug in production code, the bug fix goes in a
separate follow-up bead and PR. This PR is testing-only.

## 4. CI workflow (`.github/workflows/ci.yml`)

Triggers: `push` to any branch, `pull_request` targeting `main`.

Single job `test`, runner `ubuntu-latest`, Python 3.11.

Steps:
1. `actions/checkout@v4`
2. `actions/setup-python@v5` with `python-version: "3.11"`,
   `cache: pip`, and `cache-dependency-path: pyproject.toml` so the
   pip cache key is keyed off the dependency manifest (without it,
   the cache silently misses on every run since there's no
   `requirements*.txt`).
3. `pip install -e '.[dev]'`
4. `ruff check .`
5. `pytest` — `addopts` already supplies `--cov-fail-under=60`

No artifact upload. No coverage badge. No matrix.

## 5. Test plan per gap

### 5.1 `matcher/filters.py` — `tests/test_matcher_filters.py`

Filters under test (one block per filter):
`filter_rent_band`, `filter_rooms_band`, `filter_wbs`,
`filter_district`, `filter_pets`, `filter_move_in`,
`filter_furnished`, `filter_contract`, `filter_radius`.

For each filter, three cases minimum:
- Pass: a flat that satisfies the predicate.
- Reject: a flat that violates the predicate; assert reason string is
  non-empty and stable.
- Missing field: a flat with the relevant field absent; assert it
  rejects with an `*_unknown`-style reason (per the module docstring,
  missing fields reject rather than pass).

`filter_radius` additionally needs a "no home coords" case: when
`profile.home_lat` or `profile.home_lng` is `None`, the filter
returns `(True, None)` regardless of flat coords (`filters.py:130-149`).
This is a real-world default for users who haven't set a home
location and must be tested explicitly.

Plus one `evaluate(...)` integration case that combines a passing
flat (empty reason list) with a multi-failure flat (reasons in order
matching `FILTERS`).

Use `Profile.load_example()` and `model_copy(update=...)` for
scenarios. No DB needed.

### 5.2 `matcher/distance.py` — `tests/test_matcher_distance.py`

`distance.py` uses `httpx.get` directly to call Nominatim (not
`geopy.Nominatim`, despite what older docs say) and binds
`GEOCODE_CACHE_PATH` at import time via
`from flatpilot.config import GEOCODE_CACHE_PATH`. The `tmp_db`
fixture in `conftest.py` does **not** redirect that bound name, so
each test below MUST `monkeypatch.setattr(flatpilot.matcher.distance,
"GEOCODE_CACHE_PATH", tmp_path / "geocode_cache.json")` itself.

Cases:
- `haversine_km`: known city-pair distance within 1 km tolerance
  (e.g. Berlin Hbf → Alexanderplatz ≈ 1.4 km).
- `geocode` cache hit: pre-seed the redirected cache JSON file with
  a fresh entry; monkeypatch `flatpilot.matcher.distance.httpx.get`
  to a stub that records call count; assert it was never called.
- `geocode` cache miss → write-through: empty cache,
  `httpx.get` stub returns a fixed `(lat, lng)` JSON payload;
  assert one call, assert the cache file now contains the entry.
- `_entry_fresh` TTL: a cache entry with `cached_at` 200 days ago is
  not fresh; one with `cached_at` 30 days ago is fresh.
- `resolve_flat_coords`: a flat with `(lat, lng)` already populated
  short-circuits and never calls geocode.

### 5.3 `scrapers/wg_gesucht.py` — `tests/test_wg_gesucht_scraper.py`

Cases:
- `WGGesuchtScraper._search_url` (staticmethod): assert URL shape for
  a city in `CITY_IDS` (e.g. Berlin) and that the slug substitutes
  spaces with hyphens for multi-word cities.
- `WGGesuchtScraper._parse_listings` (staticmethod): feed in
  `tests/fixtures/wg_gesucht/search_results.html` and assert the
  generator yields N `Flat` objects with sane fields
  (`external_id`, `listing_url`, `title`, `rent_warm_eur`, `rooms`,
  `district`).
- `_parse_card` resilience: import the **module-level** function via
  `from flatpilot.scrapers.wg_gesucht import _parse_card` (it is
  not a method on the scraper class); pass a malformed card
  (missing `data-id`) and assert the function returns `None` without
  raising.
- `fetch_new` — `UnknownCityError` raised for an unsupported city.
  Use `make_session_fakes` so no real Playwright runs; assert the
  fakes were never invoked because the error fires before
  `polite_session`.

The fixture HTML is a trimmed snapshot of a real search results page
(2-3 cards is enough) AND must include at least one
`housinganywhere_ad` / `airbnb_ad` card so the test can assert the
selector (`.wgg_card.offer_list_item`) excludes ad rows. It lives
under `tests/fixtures/wg_gesucht/` and is committed alongside the
test.

### 5.4 `doctor.py` — audit existing tests

Bead `kmy` lists "`doctor.py` — all branches of `_check_telegram` /
`_check_smtp` (including malformed profile)" as initial-coverage
scope. `tests/test_doctor.py` and `tests/test_doctor_auto_apply.py`
already exist. Implementer step: read both files; if every branch
named in the bead is exercised, no new tests required — record the
coverage in this PR's description. If any branch is missing
(notably the malformed-profile path), add the gap fillers to
`test_doctor.py`. **No new doctor test file** unless a meaningful
new theme is uncovered.

### 5.5 `notifications/dispatcher.py` — `tests/test_notifications_dispatcher.py`

The bead language is "profile-hash scoping logic introduced in
FlatPilot-usm". The relevant code is `dispatch_pending` +
`_mark_stale_matches_notified`. Cases:

- `dispatch_pending` only sends for matches whose
  `profile_version_hash` equals the current `profile_hash(profile)`.
  Seed two pending match rows: one with the current hash, one with
  a stale hash. To avoid the canonical-flat-id dedup branch
  (`dispatcher.py` ~line 159), seed each match against a distinct
  flat row with `canonical_flat_id IS NULL` so `COALESCE` falls
  back to the flat's own `id`. Stub `_send` via
  `monkeypatch.setattr(flatpilot.notifications.dispatcher, "_send", fake)`
  (or both `telegram.send` and `email.send` at their module
  boundaries). Assert `_send` is called exactly once and only for
  the current-hash row.
- `_mark_stale_matches_notified` flips stale rows so
  `notified_at IS NOT NULL` without invoking `_send`. The actual
  schema field is `notified_at` (timestamp), not a `notified`
  boolean. Assert: stale row's `notified_at` is non-empty,
  current-hash row's `notified_at` stays empty until dispatch
  runs. `notified_channels_json` is not touched by this helper.
- `enabled_channels`: profile with neither Telegram nor SMTP →
  empty list; profile with both → `["telegram", "email"]`. Order
  is positional (telegram first, email second) per
  `dispatcher.py` lines 49–55 — assert against the literal list,
  not against `sorted(...)`.
- `send_test`: returns the per-channel status dict; `_send` is
  invoked once per enabled channel.

### 5.6 Integration: `run_pipeline_apply` — extend `tests/test_run_pipeline_apply.py`

The existing file already covers pause
(`test_pause_short_circuits_stage`), max-failures
(`test_skips_after_max_failures_reached`), and the happy path
(`test_iterates_candidate_and_calls_apply_to_flat`). dwao is
satisfied by adding the **two** missing rails — daily-cap-exhausted
and cooldown-active — to the same file, reusing its existing helper
fixtures. Do **not** create a parallel file.

New tests to add:

1. **Daily cap exhausted**: seed `cap` submitted rows for today on
   the same platform (`profile.auto_apply.daily_cap_per_platform["wg-gesucht"] = cap`).
   Assert `apply_to_flat` (patched via
   `unittest.mock.patch("flatpilot.auto_apply.apply_to_flat")` to
   match the existing file's idiom, line 66) was not called and no
   new `applications` row was written for the pending match's
   `flat_id`.
2. **Cooldown active**: seed exactly one **submitted** row (not a
   failed row — `cooldown_remaining_sec` counts both, but using
   `submitted` keeps the test's intent unambiguous) with `applied_at`
   set to `(datetime.now(UTC) - timedelta(seconds=30)).isoformat()`.
   Set `profile.auto_apply.cooldown_seconds_per_platform["wg-gesucht"] = 120`
   AND set `profile.auto_apply.pacing_seconds_per_platform["wg-gesucht"] = 0`
   explicitly so `max(cooldown, pacing)` is deterministic. Assert
   the pending match is skipped, no new submission.

Both tests use `unittest.mock.patch` to match the existing
`test_run_pipeline_apply.py` style. Saved-search overlay is built
with `SavedSearch(name=..., auto_apply=True, platforms=["wg-gesucht"])`
attached to the profile via `model_copy(update=...)`. Use the file's
existing seed helper(s); only add new helpers if a needed shape is
missing.

For the happy-path assertion shape (referenced by the existing
test, included here for clarity if it changes):
`apply_to_flat(flat_id, method="auto", saved_search=name)` —
`flat_id` is positional, `method` and `saved_search` are kwargs
(`auto_apply.py:245`).

## 6. Test idioms (so the new tests match the repo)

- Use the existing `tmp_db` fixture for any DB-touching test.
- Use `Profile.load_example().model_copy(update={...})` for scenario
  profiles. Don't construct `Profile(...)` from scratch.
- Stub external boundaries (`apply_to_flat`, `_send`,
  `flatpilot.matcher.distance.httpx.get`) via `monkeypatch` or
  `unittest.mock.patch` — both stdlib idioms are already in the
  test suite; don't introduce a third mocking library.
- For cooldown windows, insert applications with timestamps offset
  via `(datetime.now(UTC) - timedelta(seconds=N)).isoformat()`.
- Console output assertions, if any, use a `rich.console.Console`
  with `record=True` rather than capturing stdout.
- Adding `--cov=flatpilot --cov-report=term --cov-fail-under=60` to
  `addopts` makes every local `pytest` produce a coverage report.
  The runtime cost is on the order of a few seconds for the current
  suite; flagged here so the slowdown isn't a surprise.

## 7. Coverage threshold rationale

`--cov-fail-under=60` is the floor `kmy` named ("60% to start, raise
over time"). It's intentionally low for first introduction: the
existing test suite plus the new tests in this PR should clear it
comfortably, and we don't want CI flapping over a 0.5% drift. A
follow-up bead can ratchet it.

## 8. Commit / PR plan (minimal commits)

Branch: `feat/test-coverage-and-ci` from `origin/main`.

**Two commits total:**

1. `FlatPilot-kmy/dwao: spec for test coverage and CI` — this design
   doc only.
2. `FlatPilot-kmy/dwao: add CI workflow and complete test coverage`
   — every other change in section 3 in one squashed commit.

PR title: `feat: complete test coverage + add CI (kmy, dwao)`.
PR body: Summary + Test Plan referencing this spec. Base `main`,
head `feat/test-coverage-and-ci`. No AI co-author trailer
(per project rules).

## 9. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Coverage measurement reveals < 60% even after this PR | Run `pytest --cov` locally before pushing; if below, either lower the floor in this PR (with a note) or extend coverage scope. |
| WG-Gesucht fixture HTML drifts vs. live site | Document in a comment that the fixture is a 2026-05-01 snapshot; selector breakage is what `FlatPilot-92j`-style verification beads are for, not what this PR guards. |
| Distance test with real Nominatim is flaky | We never hit Nominatim — `flatpilot.matcher.distance.httpx.get` is monkeypatched in every cache-related test. |
| Integration test races with the real `~/.flatpilot/PAUSE` file | `tmp_db` redirects `PAUSE_PATH` via `auto_apply.PAUSE_PATH` monkeypatch (already in `conftest.py`). |
| New test files add a noticeable runtime to `pytest` | All new tests are pure-Python with stubbed boundaries; expected delta is single-digit seconds. |

## 10. Acceptance criteria

- `pytest` runs green locally and in CI.
- `ruff check .` runs green locally and in CI.
- `pytest --cov` prints ≥ 60% line coverage on `flatpilot/` and CI
  enforces the same gate.
- Both `kmy` and `dwao` are closeable: every numbered item in their
  bead descriptions has either a corresponding test in this PR or
  is explicitly named in section 2 (Non-goals).
- The PR contains exactly two commits authored by Mukhammad
  Ibrokhimov with no AI co-author trailers.
