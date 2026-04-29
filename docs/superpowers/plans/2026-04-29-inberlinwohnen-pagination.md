# FlatPilot-etu: Paginate inberlinwohnen Wohnungsfinder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the inberlinwohnen.de Wohnungsfinder scraper to walk pages 1..N of the listing feed so a fresh install ingests ≥100 listings, while keeping steady-state polling at exactly 1 page per pass.

**Architecture:** Server-rendered `?page=N` URLs (verified empirically — `?page=99` returns 0 cards on a fully rendered page; `?page=22` returns the last 2 listings on the current dataset). The scraper fetches pages sequentially within **one** `polite_session` + **one** `session_page` context — multiple `pg.goto()` calls — and terminates on (a) empty page (0 cards parsed), (b) every `external_id` on the current page already present in the platform's `flats` table, or (c) a hard cap of 30 pages. The pipeline pre-loads the platform's known `external_id`s from the DB once per pass and threads them into `fetch_new` via a new kw-only `known_external_ids` parameter on the `Scraper` Protocol. The pipeline's protocol-change cost is contained — per-scraper signature update is simpler than runtime introspection given the 3-scraper codebase.

**Error semantics — eager-I/O caveat:** all `goto`/`parse` happen inside the `with polite_session(...)` block, results are collected into a list, the context exits cleanly, and only then does `fetch_new` `yield from` the list. This pattern means **partial-success exceptions like `ValueError` from a single bad card** never produce half-yielded output (parse_listings already swallows per-card parse errors with a warning, so this is mostly defense-in-depth). However, **`RateLimitedError` mid-walk aborts the pass entirely**: the exception propagates out of the `for` loop, out of the `with` block, and out of `fetch_new` — pages 1..N-1 already in `all_flats` are dropped and `yield from all_flats` never runs. This matches the existing single-page scraper's behavior (which also loses everything on rate-limit) and keeps the backoff state-machine wiring at `cli.py:355` (`except RateLimitedError → backoff.on_failure`) untouched. Trying to "graceful-degrade" by swallowing `RateLimitedError` mid-walk would suppress the backoff trigger and is out of scope. Fresh-install users who hit rate limits mid-walk get partial inventory across multiple passes — the `INSERT OR IGNORE` idempotency makes that safe.

**Tech Stack:** Python 3.11+, Playwright (sync API via `flatpilot.scrapers.session.polite_session`), BeautifulSoup4, pytest with `monkeypatch`-injected URL-keyed page fakes, real-HTML fixtures.

---

## Pre-Flight Verification (already complete)

The following primary-source checks were done during planning. Implementer **does not need to repeat** these — they're recorded for the reviewer:

- **`?page=N` URL pattern works server-side:** confirmed via `curl -sSL https://www.inberlinwohnen.de/wohnungsfinder/?page=2` and `?page=22`, `?page=99`. Each returns HTTP 200 with disjoint apartment-ID sets (page 1: 16334–16344, page 2: 16324–16333, page 22: 239+325, page 99: 0 cards with `keine Ergebnisse` markers).
- **`robots.txt`:** `User-agent: * / Disallow:` (fully permissive — no constraint on `?page=*`).
- **Eager-I/O contract of current scraper:** verified at `src/flatpilot/scrapers/inberlinwohnen.py:95–110` — `with polite_session(...) as context, session_page(context) as pg:` block ends, then `flats = list(parse_listings(html))`, then `yield from flats`. New code preserves this property.

---

## File Inventory

**Create:**
- `tests/fixtures/inberlinwohnen/search_page2.html` — real capture of `?page=2`, ~620KB, containing 10 apartment cards with IDs 16324–16333.

**Modify:**
- `src/flatpilot/scrapers/base.py` — extend `Scraper.fetch_new` Protocol with kw-only `known_external_ids: frozenset[str] = frozenset()` parameter.
- `src/flatpilot/scrapers/inberlinwohnen.py` — implement pagination loop, accept `known_external_ids`, add module constants for cap + delay.
- `src/flatpilot/scrapers/wg_gesucht.py` — accept (and ignore) `known_external_ids` so signature satisfies updated Protocol.
- `src/flatpilot/scrapers/kleinanzeigen.py` — same signature update.
- `src/flatpilot/cli.py` — pre-fetch `known_external_ids` from `flats` table per scraper, thread into `fetch_new` call.
- `tests/test_inberlinwohnen_scraper.py` — extend with five new pagination tests (empty-page in Task 3; URL-keyed-fake fresh-install + steady-state + safety-cap + rate-limit-aborts in Task 4) and repair the pre-existing single-fixture integration test at line 158 to be URL-aware (Step 4.5).
- `tests/test_pipeline_backoff.py` — update stub `fetch_new(self, _profile)` signature to accept `known_external_ids` kwarg.
- `tests/test_registry_city_gating.py` — same: 4 stub `fetch_new` definitions need the new kwarg.
- `tests/test_pipeline_city_gating.py` — same: stub class + 2 monkeypatch fakes (`_capture`, `_fake_fetch`) need the new kwarg.

**Asset notes:**
- `tests/fixtures/inberlinwohnen/search.html` (existing, page 1, 634KB, 2026-04-26 capture) — leave untouched. New page-2 fixture is independent; the existing parser tests at `tests/test_inberlinwohnen_scraper.py:13–145` continue using `search.html` only.
- For the empty-page test, **inline** the empty HTML stub in the test (`"<html><body></body></html>"`) — consistent with the existing `test_parse_listings_empty_html_yields_nothing` at line 121. **No new "empty" fixture file.**

**Files NOT touched:** `flatpilot/database.py` (schema unchanged), `flatpilot/matcher/*` (matcher unchanged), `flatpilot/pipeline.py` (only the `cli.py` call site changes — there is no `pipeline.py` orchestration loop today; the orchestrator lives in `cli.py`).

---

## Commit Grouping

To minimize commit count (per user instruction "less git commits"), each task in this plan **does not commit** at the end. The implementer stages files (`git add`) but defers the commit. After Task 5 verification passes and the final branch review is clean, **one** squashed commit is produced:

- C1 (already in the branch before this plan executes — written by the planner): `FlatPilot-etu: write implementation plan` — the plan doc itself.
- C2 (produced after Task 5 + final branch review): `FlatPilot-etu: paginate inberlinwohnen Wohnungsfinder beyond page 1` — squash of all Task 1–5 changes.
- C3 (produced after PR is opened): `FlatPilot-etu: close bead after PR #N opened` — pure beads JSONL update.

Total: **3 commits** on the feature branch.

The plan-doc commit (C1) is staged + committed by the human running this plan **before dispatching the first implementer subagent**, so each task's stage step accumulates onto a clean tree relative to the C1 baseline.

---

## Task 1: Extend Scraper Protocol with `known_external_ids`

Update the `Scraper` Protocol contract and propagate the new kw-only parameter to every existing `fetch_new` definition (production scrapers + test stubs + monkeypatch fakes). After this task, the test suite passes (no new behavior yet — every callee accepts the kwarg and ignores it).

**Files:**
- Modify: `src/flatpilot/scrapers/base.py:55-73`
- Modify: `src/flatpilot/scrapers/inberlinwohnen.py:86`
- Modify: `src/flatpilot/scrapers/wg_gesucht.py:106`
- Modify: `src/flatpilot/scrapers/kleinanzeigen.py:104`
- Modify: `tests/test_pipeline_backoff.py:46`
- Modify: `tests/test_registry_city_gating.py:16,80,96,115`
- Modify: `tests/test_pipeline_city_gating.py:28,~120-180`

- [ ] **Step 1.1: Capture baseline pytest count**

Run: `.venv/bin/pytest --collect-only -q | tail -3`
Expected: ends with `227 tests collected` (the agreed post-PR-29 baseline).

- [ ] **Step 1.2: Update Protocol in `base.py`**

In `src/flatpilot/scrapers/base.py`, replace the `fetch_new` declaration in the `Scraper` Protocol (currently lines 72–73):

```python
    def fetch_new(
        self,
        profile: Profile,
        *,
        known_external_ids: frozenset[str] = frozenset(),
    ) -> Iterable[Flat]:
        """Yield every listing currently visible under ``profile``.

        ``known_external_ids`` is an optional set of ``external_id`` values
        already persisted in the ``flats`` table for this scraper's
        platform. Scrapers that paginate may use it to terminate the page
        walk once they observe a page whose IDs are entirely known. Single-
        page scrapers ignore it. The pipeline always passes a frozenset
        (possibly empty); callees should treat the value as read-only.
        """
```

Update the module docstring at the top of `base.py` (lines 8–14) to mention the new parameter:

```python
"""Shared types for per-platform scrapers.

Each scraper is a class that sets a ``platform`` ClassVar, registers itself
through :func:`flatpilot.scrapers.register`, and implements :meth:`fetch_new`.
The orchestrator (``flatpilot scrape``, ``flatpilot run``) iterates the
registry and calls ``fetch_new(profile, known_external_ids=...)`` on each
scraper, where ``known_external_ids`` is the set of ``external_id`` values
already persisted for that platform — used by paginating scrapers to
terminate cheaply when they hit a fully-known page.

``Flat`` mirrors the C1 ``flats`` table schema. Only ``external_id``,
``listing_url``, and ``title`` are required on every yielded record —
every other field is optional and the matcher treats missing values as
reject-with-reason (see ``flatpilot.matcher.filters``). The orchestrator
writes flats with ``INSERT OR IGNORE`` against the ``(platform,
external_id)`` UNIQUE constraint, so repeated scrapes are idempotent and
scrapers do not need to track what they have already emitted.
"""
```

- [ ] **Step 1.3: Update production scrapers' signatures**

In `src/flatpilot/scrapers/inberlinwohnen.py:86`, replace:
```python
    def fetch_new(self, profile: Profile) -> Iterable[Flat]:
```
with:
```python
    def fetch_new(
        self,
        profile: Profile,
        *,
        known_external_ids: frozenset[str] = frozenset(),
    ) -> Iterable[Flat]:
```

Do **not** change the body yet (Task 4 will). The new parameter is accepted but unused.

In `src/flatpilot/scrapers/wg_gesucht.py:106`, apply the identical signature change. The body stays as-is — wg_gesucht does not paginate and ignores the kwarg.

In `src/flatpilot/scrapers/kleinanzeigen.py:104`, apply the identical signature change. Body unchanged.

- [ ] **Step 1.4: Update test stubs in `test_pipeline_backoff.py`**

In `tests/test_pipeline_backoff.py:46`, replace:
```python
    def fetch_new(self, _profile: Any) -> Any:
```
with:
```python
    def fetch_new(self, _profile: Any, **_kwargs: Any) -> Any:
```

Use `**_kwargs` (rather than typing the exact kwarg) to keep the stub minimal — these stubs are not exercising the new parameter, just satisfying the call shape.

- [ ] **Step 1.5: Update test stubs in `test_registry_city_gating.py`**

In `tests/test_registry_city_gating.py`, find every `def fetch_new(self, profile)` (currently lines 16, 80, 96, 115 per the grep result). Replace each with:
```python
        def fetch_new(self, profile, **_kwargs):  # noqa: ARG002 — protocol stub
```

Preserve the existing `# noqa` comment and indentation. The `**_kwargs` is added so each stub class still satisfies the updated Protocol.

- [ ] **Step 1.6: Update fakes in `test_pipeline_city_gating.py`**

In `tests/test_pipeline_city_gating.py`:

- The stub class at line 28: `def fetch_new(self, profile):` → `def fetch_new(self, profile, **_kwargs):`.
- The `_capture(...)` and `_fake_fetch(...)` functions used in the `monkeypatch.setattr(..., "fetch_new", _capture)` / `_fake_fetch` calls (around lines 112–181). Each function signature must accept `**_kwargs` after its existing positional parameters. Read the existing function bodies before editing to verify the parameter names — apply the change to whatever the parameter list is, e.g.:

```python
def _capture(self, profile, **_kwargs):
    ...
def _fake_fetch(self, profile, **_kwargs):
    ...
```

(The exact existing parameter list is `(self, profile)` — verify by reading and apply `**_kwargs` consistently.)

- [ ] **Step 1.7: Run full test suite — verify no regressions**

Run: `.venv/bin/pytest -q`
Expected: `227 passed` (no count change — Task 1 is signature-only).

If any test fails, the cause is most likely a `fetch_new` callsite or stub that was missed. Re-grep `fetch_new` across `src/` and `tests/` and ensure every definition accepts the new kwarg. **Do not commit** — proceed to Task 2.

- [ ] **Step 1.8: Stage Task 1 changes (do not commit)**

```bash
git add src/flatpilot/scrapers/base.py \
        src/flatpilot/scrapers/inberlinwohnen.py \
        src/flatpilot/scrapers/wg_gesucht.py \
        src/flatpilot/scrapers/kleinanzeigen.py \
        tests/test_pipeline_backoff.py \
        tests/test_registry_city_gating.py \
        tests/test_pipeline_city_gating.py
```

**Do not run `git commit`.** Changes accumulate for the squashed C2.

---

## Task 2: Pipeline Pre-Loads `known_external_ids` per Scraper

Modify the orchestrator at `src/flatpilot/cli.py` to query the `flats` table once per scraper (after the backoff skip-check, before the `fetch_new` call) and pass the result as `known_external_ids` into `fetch_new`. Add a regression test that asserts the pipeline passes the kwarg.

**Files:**
- Modify: `src/flatpilot/cli.py:340-380` (the `for scraper in scrapers:` loop)
- Modify: `tests/test_pipeline_city_gating.py` (add new test) **OR** create a focused new file `tests/test_pipeline_known_ids.py`

> **Decision:** create a new file `tests/test_pipeline_known_ids.py`. The pipeline_city_gating file is already long and thematically focused on city gating; a separate file keeps the new test discoverable and avoids fixture coupling.

- [ ] **Step 2.1: Write the failing test — pipeline passes known_external_ids**

Create `tests/test_pipeline_known_ids.py`:

```python
"""Pipeline threads known_external_ids from flats table into fetch_new."""

from __future__ import annotations

from typing import Any

import pytest


def test_run_scrape_pass_passes_known_ids_from_db(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pipeline pre-loads (platform, external_id) pairs from `flats`
    and passes the per-platform set into each scraper's fetch_new as
    `known_external_ids` (kw-only, frozenset).

    Setup: insert two flats for inberlinwohnen, one for wg_gesucht.
    Run the pipeline pass with both scrapers monkeypatched. Assert each
    scraper received exactly the set of external_ids matching its
    platform.
    """
    from datetime import UTC, datetime

    from flatpilot import database
    from flatpilot.cli import _run_scrape_pass
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib
    from flatpilot.scrapers import wg_gesucht as wg

    # Seed the DB with three flats.
    conn = database.connect()
    now = datetime.now(UTC).isoformat()
    for platform, ext_id in [
        ("inberlinwohnen", "16344"),
        ("inberlinwohnen", "16343"),
        ("wg_gesucht", "9999"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO flats "
            "(platform, external_id, listing_url, title, scraped_at, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (platform, ext_id, f"https://example.test/{ext_id}", "t", now, now),
        )
    conn.commit()

    captured: dict[str, frozenset[str]] = {}

    def _capture_ib(self: Any, profile: Any, **kwargs: Any) -> Any:
        captured["inberlinwohnen"] = kwargs.get("known_external_ids")
        return iter([])

    def _capture_wg(self: Any, profile: Any, **kwargs: Any) -> Any:
        captured["wg_gesucht"] = kwargs.get("known_external_ids")
        return iter([])

    monkeypatch.setattr(ib.InBerlinWohnenScraper, "fetch_new", _capture_ib)
    monkeypatch.setattr(wg.WGGesuchtScraper, "fetch_new", _capture_wg)

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    _run_scrape_pass(profile)

    assert captured["inberlinwohnen"] == frozenset({"16344", "16343"})
    assert captured["wg_gesucht"] == frozenset({"9999"})
    # Type contract: it must be a frozenset, not a list/set/tuple.
    assert isinstance(captured["inberlinwohnen"], frozenset)
    assert isinstance(captured["wg_gesucht"], frozenset)
```

> **Implementer note:** the existing `_run_scrape_pass` callable in `cli.py` may take different parameters than `(profile)`. **Read `cli.py:300–340` first** to find the exact entry point used by `flatpilot scrape` for a single pass. If `_run_scrape_pass` is private/named differently, use the actual function (e.g., it may be called via `app.run(["scrape", "--once"])` from typer's test runner, or there may be a public helper). Adjust the test's invocation to match — but keep the assertion semantics identical (each scraper receives a frozenset of its platform's known external_ids). The test_pipeline_city_gating.py file at line 112 has a working pattern for invoking the pipeline; mirror it.

- [ ] **Step 2.2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline_known_ids.py -v`
Expected: FAIL — `captured["inberlinwohnen"]` is `None` (current pipeline passes only `profile` positionally, so `kwargs.get("known_external_ids")` is missing). The fail message will be `assert None == frozenset({...})`.

If the test fails for a different reason (import error, fixture error, can't find `_run_scrape_pass`), STOP and fix the test fixture wiring before proceeding — a "wrong-failure" red test does not validate the green case.

- [ ] **Step 2.3: Implement the pipeline DB query + kwarg pass**

In `src/flatpilot/cli.py`, find the `for scraper in scrapers:` loop (currently around line 340). Modify it to query the `flats` table once per scraper before calling `fetch_new`. The exact hunk:

Current (lines 340–354):
```python
    for scraper in scrapers:
        plat = scraper.platform
        if not supports_city(type(scraper), profile.city):
            console.print(
                f"[dim]{plat}: skipping — city {profile.city!r} not supported[/dim]"
            )
            continue
        skip, remaining = backoff.should_skip(plat, now=now_dt)
        if skip:
            console.print(
                f"[dim]{plat}: cooling off for {remaining:.0f}s more — skipping[/dim]"
            )
            continue
        try:
            flats = list(scraper.fetch_new(profile))
```

Replace with:
```python
    for scraper in scrapers:
        plat = scraper.platform
        if not supports_city(type(scraper), profile.city):
            console.print(
                f"[dim]{plat}: skipping — city {profile.city!r} not supported[/dim]"
            )
            continue
        skip, remaining = backoff.should_skip(plat, now=now_dt)
        if skip:
            console.print(
                f"[dim]{plat}: cooling off for {remaining:.0f}s more — skipping[/dim]"
            )
            continue
        known_external_ids = frozenset(
            row[0]
            for row in conn.execute(
                "SELECT external_id FROM flats WHERE platform = ?",
                (plat,),
            )
        )
        try:
            flats = list(
                scraper.fetch_new(profile, known_external_ids=known_external_ids)
            )
```

> **Implementer notes:**
> - `conn` must be in scope at this point. If the existing function obtains the DB connection earlier, reuse that variable. If not, the implementer should add `conn = database.connect()` (or the existing equivalent) once before the `for` loop — but **do not** open a per-scraper connection.
> - The query is intentionally simple and has no `LIMIT`. The `flats` table is small enough (single user, monthly cleanup is a separate concern); fetching all `external_id`s is O(rows) once per pass. If the table grows pathologically (>100k rows), that's tracked in a separate bead, not this PR.
> - The query runs **after** the city-gate and backoff-skip checks so a skipped scraper costs nothing.

- [ ] **Step 2.4: Run test — verify pass**

Run: `.venv/bin/pytest tests/test_pipeline_known_ids.py -v`
Expected: PASS.

Run: `.venv/bin/pytest -q`
Expected: `228 passed` (227 baseline + 1 new test).

- [ ] **Step 2.5: Stage Task 2 changes (do not commit)**

```bash
git add src/flatpilot/cli.py tests/test_pipeline_known_ids.py
```

---

## Task 3: Inberlinwohnen — Module Constants + Empty-Page Termination

Add the pagination constants (`MAX_PAGES`, `POLITE_PAGE_DELAY_SEC`) and a unit test that proves a page yielding zero apartment cards stops the walk without raising. The test exercises the smallest pagination shape — the loop runs once, sees an empty page, returns.

**Files:**
- Modify: `src/flatpilot/scrapers/inberlinwohnen.py` (add constants near top)
- Modify: `tests/test_inberlinwohnen_scraper.py` (add empty-page-stops test)

- [ ] **Step 3.1: Write the empty-page regression test (green-from-arrival)**

> **Why this test lives in Task 3, not Task 4.** The empty-page test is the only test in the suite that pins the "0 cards → break" termination branch under Task 4's loop. It is green today (single-goto scraper trivially asserts `goto_calls == [SEARCH_URL]`), but a hypothetical Task-4 implementation that ignored the empty-page signal would walk to MAX_PAGES instead and the assertion `goto_calls == [SEARCH_URL]` would fail. Adding it in Task 3 means: (a) the suite is already pinned when Task 4's subagent begins, and (b) the cap test in Task 4 is independent (uses a different fake) so the two regression guards don't shadow each other.

Append to `tests/test_inberlinwohnen_scraper.py`:

```python
def test_fetch_new_stops_when_first_page_is_empty(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If page 1 returns 0 apartment cards (e.g. site is empty or
    filtered to zero), fetch_new yields nothing without raising and
    performs exactly one goto."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib

    goto_calls: list[str] = []

    class _FakeCtxMgr:
        def __init__(self, _config: Any) -> None:
            pass

        def __enter__(self) -> Any:
            return object()

        def __exit__(self, *_exc: Any) -> None:
            return None

    class _FakePageCtxMgr:
        def __init__(self, _ctx: Any) -> None:
            pass

        def __enter__(self) -> Any:
            class _P:
                def goto(self, url: str, **_kw: Any) -> Any:
                    goto_calls.append(url)

                    class _R:
                        status = 200

                    return _R()

                def content(self) -> str:
                    return "<html><body></body></html>"

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(ib, "polite_session", _FakeCtxMgr)
    monkeypatch.setattr(ib, "session_page", _FakePageCtxMgr)

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    flats = list(ib.InBerlinWohnenScraper().fetch_new(profile))

    assert flats == []
    assert goto_calls == [ib.SEARCH_URL]
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_stops_when_first_page_is_empty -v`
Expected: PASS, currently — because the existing scraper does exactly one goto. **This test is intentionally a green-from-the-start regression guard** for the empty-page early-exit invariant. It will continue passing after Task 4 because the loop terminates on `parse_listings(html)` returning an empty iterable.

> **TDD nuance:** Most tests in this plan are red-then-green. This one is green-from-day-zero because the current `fetch_new` happens to satisfy the property by being a single-page scraper. We add it now (in Task 3) so that Task 4's pagination loop, when introduced, must also satisfy this regression. **Do not soften the test if it stays green** — that is the desired behavior. The reviewer should verify the test would fail under a hypothetical "always loop until cap" implementation that ignored the 0-card signal.

- [ ] **Step 3.3: Add module constants in `inberlinwohnen.py`**

In `src/flatpilot/scrapers/inberlinwohnen.py`, immediately after the existing `CONSENT_SELECTORS` tuple (currently ending at line 71), insert:

```python

# Pagination constants. The Wohnungsfinder feed uses server-rendered
# `?page=N` URLs (verified empirically — page 99 returns a fully-rendered
# page with 0 cards and `keine Ergebnisse` markers; page 22 is currently
# the last page with 2 listings). On a fresh install the full inventory
# is ~22 pages × 10 listings = ~220 flats; MAX_PAGES caps that with
# headroom for organic growth without changing code.
MAX_PAGES: int = 30

# Sleep between page fetches inside one polite_session. Distinct from
# session.DEFAULT_INTERVAL_SEC (120s, inter-pass) — this is intra-pass
# politeness so 30 sequential page loads spread over ~45s rather than
# spiking the host with parallel-feeling requests. 1.5s is conservative;
# WG-Gesucht's ~2s rule of thumb informs the choice.
POLITE_PAGE_DELAY_SEC: float = 1.5
```

Also add `import time` near the top of the file (currently the imports stop at `urllib.parse`):
```python
import time
```

(Place it alphabetically: `re`, `time`, then the `from collections.abc` line.)

- [ ] **Step 3.4: Run the new test — verify it still passes**

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_stops_when_first_page_is_empty -v`
Expected: PASS.

Run: `.venv/bin/pytest -q`
Expected: `229 passed` (228 + 1 new test).

- [ ] **Step 3.5: Stage Task 3 changes (do not commit)**

```bash
git add src/flatpilot/scrapers/inberlinwohnen.py tests/test_inberlinwohnen_scraper.py
```

---

## Task 4: Inberlinwohnen — Pagination Loop with All-Three Termination Branches

Rewrite `InBerlinWohnenScraper.fetch_new` to walk pages 1..N, terminating on (a) empty page, (b) all IDs known, or (c) `MAX_PAGES`. Use a URL-keyed page fake in the integration tests so each `goto(url)` returns the right fixture.

**Files:**
- Modify: `src/flatpilot/scrapers/inberlinwohnen.py:86-110` (the `fetch_new` method body)
- Modify: `tests/test_inberlinwohnen_scraper.py` (add fresh-install + steady-state + cap tests)
- Create: `tests/fixtures/inberlinwohnen/search_page2.html` (real capture)

- [ ] **Step 4.1: Capture the page-2 fixture**

The fixture is real-world HTML from the live Wohnungsfinder. Capture it with curl:

```bash
curl -sSL \
  -A "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0" \
  -o tests/fixtures/inberlinwohnen/search_page2.html \
  "https://www.inberlinwohnen.de/wohnungsfinder/?page=2"
```

Verify size (~620KB) and that it contains 10 apartment cards:
```bash
wc -c tests/fixtures/inberlinwohnen/search_page2.html
grep -oE 'apartment-finder\.apartment-[0-9]+' tests/fixtures/inberlinwohnen/search_page2.html | sort -u | wc -l
```
Expected: bytes ≈ 620k (±200k acceptable — the page size varies with active cards), distinct apartment-IDs = 10.

> **Implementer note:** the live page may have rotated; if the captured page-2 has fewer than 10 cards (e.g., 7), accept the captured count and adjust the test expectation accordingly (Step 4.4). The contract is "page 2 has >0 disjoint cards from page 1," not exactly 10.

> **Stale-data caveat:** if the captured fixture's IDs overlap with the existing `search.html` page-1 IDs (16334–16344), the inventory has rotated and *new* page-1 IDs have appeared. In that case, the pipeline-test invariant ("page 1 ∩ page 2 = ∅") breaks.
>
> **Verify zero overlap.** After capturing `search_page2.html`, run:
>
> ```bash
> comm -12 \
>   <(grep -oE 'apartment-finder\.apartment-[0-9]+' tests/fixtures/inberlinwohnen/search.html | sort -u) \
>   <(grep -oE 'apartment-finder\.apartment-[0-9]+' tests/fixtures/inberlinwohnen/search_page2.html | sort -u)
> ```
>
> Expected: empty output (no shared IDs). If output is non-empty, both fixtures are stale relative to each other.
>
> **If recapture is needed**, follow this procedure:
>
> 1. Recapture page 1 over the existing fixture:
>    ```bash
>    curl -sSL -A "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0" \
>      -o tests/fixtures/inberlinwohnen/search.html \
>      "https://www.inberlinwohnen.de/wohnungsfinder/"
>    ```
> 2. Recapture page 2 (same command as Step 4.1).
> 3. Re-run the `comm -12` check above. Confirm zero overlap.
> 4. Read the first card's actual values from the new page-1 fixture so the existing parser tests can be updated. Use a one-shot Python invocation:
>    ```bash
>    .venv/bin/python -c "
>    from pathlib import Path
>    from flatpilot.scrapers.inberlinwohnen import parse_listings
>    flats = list(parse_listings(Path('tests/fixtures/inberlinwohnen/search.html').read_text()))
>    print(flats[0])
>    "
>    ```
> 5. Update the existing parser-test assertions at `tests/test_inberlinwohnen_scraper.py:31–50` (the `test_parse_listings_first_card_required_fields` and `test_parse_listings_first_card_numeric_and_date_fields` and `test_parse_listings_first_card_address_and_district` and `test_parse_listings_first_card_wbs_not_required` tests) to match the printed values. The fields to update: `external_id`, `listing_url`, `title`, `rooms`, `size_sqm`, `rent_cold_eur`, `extra_costs_eur`, `rent_warm_eur`, `available_from`, `online_since`, `address`, `district`, `requires_wbs`. Also update the comment at line 18 noting the fixture capture date to today's date.
> 6. Run the parse-only suite to verify: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py -k 'parse' -v`. Expect every test to pass with the new fixture data.
> 7. **If `test_parse_listings_first_card_wbs_not_required` no longer applies** (because the new first card is WBS-required), invert the test assertion *or* re-pick a card from the fixture that satisfies the original "WBS: nicht erforderlich" property and update the test to index that card explicitly. Do NOT delete the WBS-not-required test — it's pinning a parser branch.
>
> The implementer should treat recapture as a last resort (it expands the diff into the existing parser tests). If the original 2026-04-26 fixture's IDs (16214 etc.) are still disjoint from a fresh page-2 capture, leave the existing fixture untouched.

- [ ] **Step 4.2: Write the failing test — fresh install paginates**

In `tests/test_inberlinwohnen_scraper.py`, add a fresh-install pagination test using a URL-keyed page fake:

```python
FIXTURE_PAGE2 = Path(__file__).parent / "fixtures" / "inberlinwohnen" / "search_page2.html"


def _make_url_keyed_session_fakes(
    *,
    fixture_for_url: dict[str, str],
    goto_log: list[str],
) -> tuple[type, type]:
    """Build (polite_session_fake, session_page_fake) where goto(url)
    looks the URL up in `fixture_for_url` and content() returns the
    matching HTML. URLs not in the dict are treated as empty pages
    ('<html><body></body></html>') — useful for "ran past the cap"
    tests. `goto_log` records every URL handed to goto.
    """

    class _FakeCtxMgr:
        def __init__(self, _config: Any) -> None:
            pass

        def __enter__(self) -> Any:
            return object()

        def __exit__(self, *_exc: Any) -> None:
            return None

    class _FakePageCtxMgr:
        def __init__(self, _ctx: Any) -> None:
            pass

        def __enter__(self) -> Any:
            log = goto_log
            mapping = fixture_for_url

            class _P:
                def __init__(self) -> None:
                    self._current_url: str | None = None

                def goto(self, url: str, **_kw: Any) -> Any:
                    log.append(url)
                    self._current_url = url

                    class _R:
                        status = 200

                    return _R()

                def content(self) -> str:
                    if self._current_url is None:
                        return "<html><body></body></html>"
                    path = mapping.get(self._current_url)
                    if path is None:
                        return "<html><body></body></html>"
                    return Path(path).read_text()

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    return _FakeCtxMgr, _FakePageCtxMgr


def test_fetch_new_paginates_to_page_2_on_fresh_install(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh install (no known IDs): scraper walks page 1 → page 2 →
    ... and yields the union of cards. The fake serves page-1 fixture
    on SEARCH_URL and page-2 fixture on SEARCH_URL?page=2; any further
    page returns empty HTML, terminating the walk."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib

    goto_log: list[str] = []
    fix_p1 = str(FIXTURE)
    fix_p2 = str(FIXTURE_PAGE2)
    polite_fake, page_fake = _make_url_keyed_session_fakes(
        fixture_for_url={
            ib.SEARCH_URL: fix_p1,
            f"{ib.SEARCH_URL}?page=2": fix_p2,
            # Any further page → empty HTML default → terminate.
        },
        goto_log=goto_log,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)  # speed up tests

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    flats = list(
        ib.InBerlinWohnenScraper().fetch_new(profile, known_external_ids=frozenset())
    )

    # Page 1 has 10 cards, page 2 has 10 cards (both fixtures verified
    # disjoint per the capture step). Page 3 is empty → terminate.
    assert len(flats) == 20
    # external_ids are unique across the union.
    ids = [f["external_id"] for f in flats]
    assert len(ids) == len(set(ids))
    # Walked exactly page 1, page 2, page 3 (empty stop).
    assert goto_log == [
        ib.SEARCH_URL,
        f"{ib.SEARCH_URL}?page=2",
        f"{ib.SEARCH_URL}?page=3",
    ]


def test_fetch_new_steady_state_stops_after_page_1_when_all_known(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When every page-1 ID is in known_external_ids, scraper stops
    after one goto. This is the steady-state-polling acceptance
    criterion: ~1 page per pass."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib
    from flatpilot.scrapers.inberlinwohnen import parse_listings

    page1_html = FIXTURE.read_text()
    page1_ids = frozenset(f["external_id"] for f in parse_listings(page1_html))
    assert len(page1_ids) == 10  # sanity-check fixture

    goto_log: list[str] = []
    polite_fake, page_fake = _make_url_keyed_session_fakes(
        fixture_for_url={
            ib.SEARCH_URL: str(FIXTURE),
            f"{ib.SEARCH_URL}?page=2": str(FIXTURE_PAGE2),
        },
        goto_log=goto_log,
    )
    monkeypatch.setattr(ib, "polite_session", polite_fake)
    monkeypatch.setattr(ib, "session_page", page_fake)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    flats = list(
        ib.InBerlinWohnenScraper().fetch_new(
            profile, known_external_ids=page1_ids
        )
    )

    # Both assertions matter: goto_log catches "ignored known_ids,
    # always paginates"; len(flats) catches "broke before collecting".
    # Page 1 was fetched (always), all IDs were known → stop. No page 2.
    assert goto_log == [ib.SEARCH_URL]
    # Yielded flats = page 1 contents (the scraper does NOT filter known
    # IDs out of its yield — pipeline INSERT OR IGNORE handles dedup).
    assert len(flats) == 10


def test_fetch_new_safety_cap_at_max_pages(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the site never returns an empty page and known_ids never
    fully match (pathological case), the scraper stops at MAX_PAGES."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib

    goto_log: list[str] = []

    # Build a fake that returns page-1 fixture for EVERY url — i.e., the
    # site never paginates, never empties. The walk should still terminate
    # at MAX_PAGES rather than loop forever.
    class _FakeCtxMgr:
        def __init__(self, _config: Any) -> None:
            pass

        def __enter__(self) -> Any:
            return object()

        def __exit__(self, *_exc: Any) -> None:
            return None

    fixture_html = FIXTURE.read_text()

    class _FakePageCtxMgr:
        def __init__(self, _ctx: Any) -> None:
            pass

        def __enter__(self) -> Any:
            log = goto_log

            class _P:
                def goto(self, url: str, **_kw: Any) -> Any:
                    log.append(url)

                    class _R:
                        status = 200

                    return _R()

                def content(self) -> str:
                    return fixture_html

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(ib, "polite_session", _FakeCtxMgr)
    monkeypatch.setattr(ib, "session_page", _FakePageCtxMgr)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)
    monkeypatch.setattr(ib, "MAX_PAGES", 3)  # reduce for fast test

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    flats = list(
        ib.InBerlinWohnenScraper().fetch_new(profile, known_external_ids=frozenset())
    )

    # Cap = 3 → exactly 3 gotos.
    assert len(goto_log) == 3
    assert goto_log[0] == ib.SEARCH_URL
    assert goto_log[1] == f"{ib.SEARCH_URL}?page=2"
    assert goto_log[2] == f"{ib.SEARCH_URL}?page=3"
    # Each page yielded the same 10 cards (parse_listings doesn't dedup
    # within a fetch_new call); 3 pages × 10 = 30 yielded flats.
    assert len(flats) == 30


def test_fetch_new_rate_limit_mid_walk_aborts_pass_loses_collected_flats(
    tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RateLimitedError on page 2 propagates out of fetch_new; pages
    already fetched (page 1) are NOT preserved. This pins the chosen
    semantics: rate-limit aborts the pass; pipeline backoff handles the
    retry; INSERT OR IGNORE on the next pass makes recovery safe.

    A future implementation that adds try/except RateLimitedError +
    break inside the loop would PASS this test only if it then re-raised
    after the break — but the simpler implementation just lets the
    exception propagate. This test pins the propagation contract."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import inberlinwohnen as ib
    from flatpilot.scrapers.session import RateLimitedError

    goto_log: list[str] = []

    class _FakeCtxMgr:
        def __init__(self, _config: Any) -> None:
            pass

        def __enter__(self) -> Any:
            return object()

        def __exit__(self, *_exc: Any) -> None:
            return None

    fixture_html = FIXTURE.read_text()

    class _FakePageCtxMgr:
        def __init__(self, _ctx: Any) -> None:
            pass

        def __enter__(self) -> Any:
            log = goto_log

            class _P:
                def goto(self, url: str, **_kw: Any) -> Any:
                    log.append(url)
                    # Page 1 = OK; page 2 = 429.
                    if "?page=2" in url:

                        class _R429:
                            status = 429

                        return _R429()

                    class _R200:
                        status = 200

                    return _R200()

                def content(self) -> str:
                    return fixture_html

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(ib, "polite_session", _FakeCtxMgr)
    monkeypatch.setattr(ib, "session_page", _FakePageCtxMgr)
    monkeypatch.setattr(ib, "POLITE_PAGE_DELAY_SEC", 0.0)

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    with pytest.raises(RateLimitedError):
        list(ib.InBerlinWohnenScraper().fetch_new(
            profile, known_external_ids=frozenset()
        ))

    # Walked page 1 (OK) then page 2 (429 → raise). Two gotos observed.
    assert goto_log == [ib.SEARCH_URL, f"{ib.SEARCH_URL}?page=2"]
```

> **Implementer notes on these tests:**
> - The `_make_url_keyed_session_fakes` helper is defined at module scope **once** and reused across the three new pagination tests. Place it after the existing `_FakeCtxMgr` / `_FakePageCtxMgr` definitions (or before — both work; the existing single-fixture integration test at line 158 is updated separately in Step 4.4.5).
> - The cap-test patches `ib.MAX_PAGES` to 3 to keep the test fast. Don't reduce it lower than 2 (else page 1 alone would hit the cap and we'd never exercise the loop).
> - The steady-state test's two assertions are **both load-bearing**. `goto_log == [SEARCH_URL]` catches a hypothetical "ignore known_ids, always paginate" bug. `len(flats) == 10` catches a hypothetical "broke before collecting page-1 flats into `all_flats`" bug. Do not drop either as redundant during a refactor.

- [ ] **Step 4.3: Run the four new tests to verify expected pre-implementation states**

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_paginates_to_page_2_on_fresh_install -v`
Expected: FAIL — current `fetch_new` does one goto, returns 10 flats; `goto_log` will be `[SEARCH_URL]` not the expected 3-element list.

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_steady_state_stops_after_page_1_when_all_known -v`
Expected: PASS *currently* (because the current scraper already stops after page 1). This is the steady-state property — the test is green-pre-implementation but **must remain green post-Task-4**. Confirm it does.

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_safety_cap_at_max_pages -v`
Expected: FAIL — same reason as the fresh-install test.

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_rate_limit_mid_walk_aborts_pass_loses_collected_flats -v`
Expected: FAIL — current scraper does one goto on `SEARCH_URL`, gets 200, returns. The test's `goto_log` assertion expects `[SEARCH_URL, "?page=2"]` (two gotos) and `pytest.raises(RateLimitedError)` — neither holds before Task 4. The failure may surface as either "DID NOT RAISE" or as a goto-log mismatch.

If any test fails for a non-design reason (import error, type error, fixture missing), STOP and fix the test wiring.

- [ ] **Step 4.4: Implement the pagination loop**

Replace `InBerlinWohnenScraper.fetch_new` (currently `inberlinwohnen.py:86-110`) with:

```python
    def fetch_new(
        self,
        profile: Profile,
        *,
        known_external_ids: frozenset[str] = frozenset(),
    ) -> Iterable[Flat]:
        config = SessionConfig(
            platform=self.platform,
            user_agent=self.user_agent,
            warmup_url=WARMUP_URL,
            consent_selectors=CONSENT_SELECTORS,
        )

        all_flats: list[Flat] = []
        with polite_session(config) as context, session_page(context) as pg:
            for page_num in range(1, MAX_PAGES + 1):
                if page_num == 1:
                    url = SEARCH_URL
                else:
                    url = f"{SEARCH_URL}?page={page_num}"
                    time.sleep(POLITE_PAGE_DELAY_SEC)

                logger.info("%s: fetching %s", self.platform, url)
                response = pg.goto(url, wait_until="domcontentloaded")
                if response is None:
                    logger.warning("%s: null response from %s", self.platform, url)
                    break
                check_rate_limit(response.status, self.platform)
                if response.status >= 400:
                    logger.warning(
                        "%s: page %d returned HTTP %d",
                        self.platform,
                        page_num,
                        response.status,
                    )
                    break
                html = pg.content()

                page_flats = list(parse_listings(html))
                logger.info(
                    "%s: page %d → %d listings",
                    self.platform,
                    page_num,
                    len(page_flats),
                )
                if not page_flats:
                    # Empty page — past the end of the inventory.
                    break
                all_flats.extend(page_flats)

                page_ids = {f["external_id"] for f in page_flats}
                if page_ids and page_ids.issubset(known_external_ids):
                    # Steady state: every ID on this page is already in
                    # the DB, so older pages will be too. Stop early.
                    logger.info(
                        "%s: page %d fully known — stopping pagination",
                        self.platform,
                        page_num,
                    )
                    break

        logger.info("%s: parsed %d listings total", self.platform, len(all_flats))
        yield from all_flats
```

> **Critical implementation details:**
> - The `for page_num in range(1, MAX_PAGES + 1):` loop is upper-bounded — there is no `while True`. The cap is enforced even if every page returns flats and none are known.
> - **Reference `MAX_PAGES` directly inside the loop** (`for page_num in range(1, MAX_PAGES + 1)`). Do **not** bind to a local variable before the loop, do **not** use as a default-arg value (`def fetch_new(..., _max=MAX_PAGES)`). The cap test at Step 4.2 relies on `monkeypatch.setattr(ib, 'MAX_PAGES', 3)` taking effect at call-time; binding it earlier would silently defeat the monkeypatch.
> - Page 1 URL is `SEARCH_URL` (no query string), pages 2+ use `f"{SEARCH_URL}?page={page_num}"`. The site happens to also accept `?page=1` but we keep the bare URL for backward-compatibility with the existing single-fixture integration test at line 158.
> - `time.sleep(POLITE_PAGE_DELAY_SEC)` runs **before** each page>1 goto, not after — so a single-page steady-state pass costs 0 sleep. **All tests that exercise the loop MUST `monkeypatch.setattr(ib, 'POLITE_PAGE_DELAY_SEC', 0.0)`** — otherwise CI runs pay 1.5s × pages of real wall-clock time. Future tests added later must respect this convention.
> - Termination order matters and is verified by the tests above:
>   1. Empty `page_flats` → break (handles "past-end-of-inventory" + "site is empty"). Order matters: this check happens **before** the all-known check so an empty page doesn't fall through to the subset test (which would be vacuously True for `set() ⊂ frozenset()`).
>   2. `page_ids ⊆ known_external_ids` → break (steady-state shortcut).
>   3. Loop end at `MAX_PAGES + 1` exclusive bound → natural exit (cap).
> - The `if page_ids and page_ids.issubset(...)` guard is defensive; `page_flats` non-empty already implies `page_ids` non-empty.
> - All flats from the current page are appended to `all_flats` **before** the all-known check breaks out, so the steady-state pass yields the page-1 listings (the scraper does *not* try to be clever and skip its own yield — pipeline `INSERT OR IGNORE` is the dedup).
> - **`RateLimitedError` mid-walk aborts the pass.** `check_rate_limit(response.status, ...)` raises out of the loop, out of the `with` block, and out of `fetch_new`. Pages 1..N-1 already in `all_flats` are discarded; `yield from all_flats` never runs. This matches the existing single-page scraper's semantics (the pipeline at `cli.py:355` catches `RateLimitedError`, schedules backoff, and the next pass starts fresh — `INSERT OR IGNORE` makes the redo safe). **Do not** wrap the `check_rate_limit` call in `try/except`. The plan's Task 4 includes a test (Step 4.2) that pins this behavior, so an attempt to "graceful-degrade" would fail the suite.
> - The eager-collect-then-yield pattern still has value for **other** exceptions (e.g., a `ValueError` raised by `parse_listings` on a malformed card). `parse_listings` already swallows per-card errors with a warning, so this is defense-in-depth — but the pattern preserves "all-or-nothing" yield semantics regardless.

- [ ] **Step 4.5: Repair pre-existing single-fixture integration test**

The existing test `test_fetch_new_uses_polite_session_with_search_url` at `tests/test_inberlinwohnen_scraper.py:158` will break under the new pagination loop because its ad-hoc page fake's `content()` returns the page-1 fixture **unconditionally on every goto**. With `known_external_ids=frozenset()` (the test's effective default — the test calls `scraper.fetch_new(profile)` without the kwarg, so the Protocol default applies), the scraper would loop forever (until MAX_PAGES) seeing the same 10 IDs and never marking them "all-known".

The fix has two parts:

**Part A — make the fake's `content()` URL-aware.** Find the existing `_FakePageCtxMgr` inner class (around lines 177–197 in the original file). Modify the `_P` class so:
- `goto(url)` records both the **first** URL visited (`captured.setdefault("first_goto_url", url)`) and the **most recent** URL (`captured["goto_url"] = url`).
- `content()` returns the fixture only on `SEARCH_URL`; on anything else it returns `"<html><body></body></html>"`.

Replace the existing `_P` class definition (lines ~182–194) with:

```python
            class _P:
                def goto(self, url: str, **_kw: Any) -> Any:
                    captured.setdefault("first_goto_url", url)
                    captured["goto_url"] = url

                    class _R:
                        status = 200

                    return _R()

                def content(self) -> str:
                    # After FlatPilot-etu added pagination, this fake must
                    # distinguish page 1 from later pages so the loop hits
                    # an empty page on goto #2 and terminates.
                    if captured.get("goto_url") == ib.SEARCH_URL:
                        return FIXTURE.read_text()
                    return "<html><body></body></html>"
```

**Part B — update the existing assertion.** At line 209 the existing test asserts:
```python
assert captured["goto_url"] == ib.SEARCH_URL
```

Under the new fake, `goto_url` reflects the **last** URL visited (which is `?page=2` after the empty stop). Change the assertion to:

```python
assert captured["first_goto_url"] == ib.SEARCH_URL
```

This tightens the assertion to "the first goto is page 1," which is the actual property the test was protecting (the original wording was correct only by accident-of-single-page).

Run only the existing single-fixture test to confirm it now passes under the pagination loop:

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_uses_polite_session_with_search_url -v`
Expected: PASS — `len(flats) == 10` (page 1 only, page 2 returns empty HTML, walk stops).

If the test still fails after this repair, **stop**. The most likely cause is that Part A's `captured` dict isn't accessible from inside the inner `_P` class — verify the closure capture is correct (the `captured` variable is defined in the enclosing `test_fetch_new_uses_polite_session_with_search_url` body and should be reachable via closure).

- [ ] **Step 4.6: Run the four new tests + full inberlinwohnen suite — verify all pass**

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_paginates_to_page_2_on_fresh_install -v`
Expected: PASS.

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_steady_state_stops_after_page_1_when_all_known -v`
Expected: PASS.

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_safety_cap_at_max_pages -v`
Expected: PASS.

Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py::test_fetch_new_rate_limit_mid_walk_aborts_pass_loses_collected_flats -v`
Expected: PASS.

Run all inberlinwohnen tests:
Run: `.venv/bin/pytest tests/test_inberlinwohnen_scraper.py -v`
Expected: every test passes — including the repaired pre-existing integration test from Step 4.5.

- [ ] **Step 4.7: Run full test suite — verify no regressions**

Run: `.venv/bin/pytest -q`
Expected: `233 passed` (228 baseline-after-task-2 + 1 from Task 3 + 4 new in Task 4 = 233).

> **Test count cross-check:**
> - Post-PR-29 baseline: 227.
> - +1 from Task 2: 228.
> - +1 from Task 3: 229.
> - +4 from Task 4 (fresh-install, steady-state, cap, rate-limit): 233.
>
> If `pytest -q` shows `234+`: a duplicate test or a stale fixture leaked an extra collected test. Diff against `git diff --stat` and confirm only the planned tests were added.
>
> If `pytest -q` shows `232 or fewer`: a previously-green test got broken silently. Run `.venv/bin/pytest -q --tb=line` and identify the failure before continuing.

- [ ] **Step 4.8: Run ruff — verify no new lint errors**

Run: `.venv/bin/ruff check src/flatpilot/scrapers/inberlinwohnen.py src/flatpilot/scrapers/base.py src/flatpilot/scrapers/wg_gesucht.py src/flatpilot/scrapers/kleinanzeigen.py src/flatpilot/cli.py tests/test_inberlinwohnen_scraper.py tests/test_pipeline_known_ids.py tests/test_pipeline_backoff.py tests/test_registry_city_gating.py tests/test_pipeline_city_gating.py`
Expected: zero errors on these files. (The 14 pre-existing repo-wide ruff errors tracked under FlatPilot-4wk are in other modules and are out-of-scope.)

If a new ruff error appears, fix it before proceeding.

- [ ] **Step 4.9: Stage Task 4 changes (do not commit)**

```bash
git add src/flatpilot/scrapers/inberlinwohnen.py \
        tests/test_inberlinwohnen_scraper.py \
        tests/fixtures/inberlinwohnen/search_page2.html
```

---

## Task 5: Final Branch Review + Squashed Commit

After Tasks 1–4 are staged (no commits yet beyond the C1 plan-doc commit which the human made before dispatching the first subagent), run a final code review over the full branch diff, address any Important findings, then produce the single squashed C2 commit.

- [ ] **Step 5.1: Verify staging is complete**

Run: `git status`
Expected: every file from the File Inventory above is shown as `staged`. No untracked files relevant to the task should remain.

Run: `git diff --cached --stat`
Expected: 9 modified files + 1 created (`search_page2.html`) + 1 created (`test_pipeline_known_ids.py`) = 11 paths.

- [ ] **Step 5.2: Run full quality gates one final time**

Run: `.venv/bin/pytest -q`
Expected: `233 passed`.

Run: `.venv/bin/ruff check src/ tests/`
Expected: 14 pre-existing errors (the FlatPilot-4wk legacy set) — **no new errors introduced by this branch**. Verify this by comparing the error file list to the pre-PR-29 main branch baseline.

- [ ] **Step 5.3: Dispatch superpowers:code-reviewer (opus) over the full branch diff**

This branch touches 4 production files + 1 cli + 5 test files + 1 fixture — well above the "≤2 files use sonnet" threshold. Use **opus** for the final review.

The review prompt should ask the reviewer to verify:
- Eager-I/O contract preserved for non-`RateLimitedError` exceptions (collect inside `with`, yield after); `RateLimitedError` mid-walk DOES propagate and DOES discard collected flats (this is the chosen contract — pinned by the rate-limit test).
- Termination hierarchy (empty → all-known → cap) and that order is correct.
- Steady-state property (1 goto when all page-1 IDs are known) is exercised by a test where BOTH `goto_log == [SEARCH_URL]` and `len(flats) == 10` are load-bearing.
- The page-1 + page-2 fixtures have disjoint apartment-IDs (no rotation drift) — confirm via the `comm -12` check.
- The Protocol contract change is additive (kw-only + default), and every existing call site / stub / monkeypatch fake accepts the new kwarg.
- The `time.sleep(POLITE_PAGE_DELAY_SEC)` is gated by `page_num > 1` so steady-state passes pay 0 sleep.
- No leaks of `time.sleep` into tests (all FOUR new pagination tests in Task 4 must monkeypatch `POLITE_PAGE_DELAY_SEC` to 0.0).
- `MAX_PAGES` is referenced from module scope at call-time (not bound to a default-arg or local before the loop) — verify the cap test's monkeypatch actually takes effect by reading the implementation's loop header.
- The repaired pre-existing test at `tests/test_inberlinwohnen_scraper.py:158` correctly uses `first_goto_url` (not `goto_url`) for the page-1 assertion.
- No accidental `--no-verify` / signature-only stubs in production code.

Apply any **Important** findings as additional staged changes (no separate commits). Re-run pytest + ruff after fixes. Skip **Nit** findings.

- [ ] **Step 5.4: Produce the single squashed C2 commit**

```bash
git commit -m "FlatPilot-etu: paginate inberlinwohnen Wohnungsfinder beyond page 1"
```

> **Critical:**
> - **No `Co-Authored-By` trailer.** No `🤖 Generated with...`. No AI co-author at all.
> - Single bead ID prefix (`FlatPilot-etu:`) per commit-message convention.
> - The pre-commit hook will auto-stage `.beads/issues.jsonl` (the bead-claim from earlier). Let it ride. **Do not** `--no-verify`.

After commit:
Run: `git log --oneline origin/main..HEAD`
Expected output (in order, oldest at bottom):
```
<sha2> FlatPilot-etu: paginate inberlinwohnen Wohnungsfinder beyond page 1
<sha1> FlatPilot-etu: write implementation plan
```
Two commits on the branch. The C3 close-bead commit comes after PR creation.

- [ ] **Step 5.5: Push and open PR**

```bash
git push -u origin feat/inberlinwohnen-pagination
```

Then `gh pr create --base main --head feat/inberlinwohnen-pagination` with a hand-written body containing:
- **Summary:** the bead, the user-visible behavior change.
- **What changed:** Protocol kwarg, pipeline DB query, inberlinwohnen pagination loop, new fixture.
- **Behavior changes:** fresh install ingests ≥100 listings (was 10); steady state still costs 1 page per pass.
- **Test plan:** running pytest, observing the 233-test count, manual `flatpilot scrape --platform inberlinwohnen` against the live site (optional — guarded behind a manual smoke).
- **What's NOT in this PR:** no other-scraper pagination (those scrapers ignore the kwarg by design); no schema changes; no dashboard/notifier changes.
- **Closes:** `FlatPilot-etu`.

Return the PR URL and stop. Do **not** auto-close the bead — that happens in C3 after the PR opens.

- [ ] **Step 5.6: Close the bead and produce C3 (after PR is open)**

```bash
bd close FlatPilot-etu --reason="Merged in PR #N"
git add .beads/issues.jsonl
git commit -m "FlatPilot-etu: close bead after PR #N opened"
git push
```

(Replace `N` with the actual PR number.)

Final branch state: 3 commits (plan, feature, bead-close).

---

## Self-Review

**1. Spec coverage** (the bead's acceptance criteria):

| Acceptance criterion | Covered by |
|---|---|
| "fresh install ingests >100 listings" | Task 4 fresh-install test asserts ≥20 from the 2-page fake; live behavior on 22-page real site reaches ~220. The test does not assert exactly ≥100, but the live-site coverage follows from MAX_PAGES=30 + fixture coherence (10 cards/page × 22 pages = 220). Reviewer should confirm the implementation does not artificially cap below 22. |
| "steady-state polling still costs ~1 page per pass" | Task 4 steady-state test asserts exactly `goto_log == [SEARCH_URL]` when known_ids cover all page-1 IDs. |
| "extend fetch_new to walk pages until either (a) all hits already exist in the flats table or (b) a configurable cap" | Task 4 implements both, with the empty-page break as a third (more aggressive than the bead asked) termination. |
| "Respect a per-page polite delay" | `POLITE_PAGE_DELAY_SEC = 1.5` introduced in Task 3, applied in Task 4 before each page>1 goto. |

**2. Placeholder scan:** zero placeholders — every code block is a complete implementation.

**3. Type consistency:** `frozenset[str]` is used consistently across the Protocol (Task 1.2), the production scrapers (Task 1.3), the pipeline call site (Task 2.3), all four pagination tests (Task 4.2), and the test invocations (Task 2.1's `frozenset({...})` literals). No accidental `set` / `Iterable` / `list` drift.

The `MAX_PAGES` (int) and `POLITE_PAGE_DELAY_SEC` (float) constants have consistent types across declaration (Task 3.3) and monkeypatched values (Task 4.2: `MAX_PAGES → 3`, `POLITE_PAGE_DELAY_SEC → 0.0`).

The `_make_url_keyed_session_fakes` helper returns a `tuple[type, type]` — consistent across its definition and the two call sites that use it (fresh-install + steady-state tests in Task 4.2). The cap test and the rate-limit test use ad-hoc fakes (not the helper) because they need different `goto`/`content` semantics.

---

## Final notes for the human running this plan

- The plan-doc commit (C1) must be made on the feature branch **before** dispatching the first subagent. Run `git add docs/superpowers/plans/2026-04-29-inberlinwohnen-pagination.md && git commit -m "FlatPilot-etu: write implementation plan"` from the feature branch.
- All Task 1–5 work proceeds **without committing** until the final squash in Step 5.4. Subagents must not run `git commit` in any task other than 5.4 + 5.6.
- The `superpowers:code-reviewer` agent over the full branch (Step 5.3) is the spec/quality gate; bake any Important findings into Task 5 before the C2 squash.
