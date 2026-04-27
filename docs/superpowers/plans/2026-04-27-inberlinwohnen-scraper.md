# Inberlinwohnen.de Scraper + Registry City Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Epic J — add an inberlinwohnen.de scraper for Berlin's municipal-housing portal (WBS-heavy), plus a registry-level city-gating mechanism so the orchestrator skips scrapers whose declared cities don't include `profile.city`.

**Architecture:**
- Add `supported_cities: ClassVar[frozenset[str] | None]` to the `Scraper` Protocol; `None` = no restriction. The `@register` decorator enforces declaration (loud failure on omission). A new `supports_city(scraper_cls, city)` helper does exact-match comparison and is used by the orchestrator before each fetch. Apply the declaration to `wg-gesucht` (`frozenset(CITY_IDS.keys())`), `kleinanzeigen` (`frozenset({"Berlin"})`), and the new `inberlinwohnen` scraper.
- The new scraper parses `https://inberlinwohnen.de/wohnungsfinder/` — listings are server-rendered as `<div id="apartment-<flat_id>">` blocks holding a collapsible `.list__details` section with `<dl>` definition lists for every field. Phase 1 MVP scrapes page 1 (~10 listings). The card always carries an explicit `WBS: erforderlich` / `WBS: nicht erforderlich` label, so we extract `requires_wbs` from text rather than defaulting blindly; missing label falls back to `True` (the platform's defining trait).

**Tech Stack:** Python 3.11+, pydantic, BeautifulSoup, Playwright (via existing `polite_session` helper), pytest, ruff.

---

## Pre-flight (verify before any code change)

- [ ] **PF.1: Verify branch and clean state**

  Run:
  ```bash
  git branch --show-current
  git status --short
  ```
  Expected: branch is `feat/inberlinwohnen-scraper`. Status shows only the in-flight `.beads/issues.jsonl` (auto-drift) and `.claude/settings.json` (untouched user file) modifications. The fixture `tests/fixtures/inberlinwohnen/search.html` is already on disk (saved during planning).

- [ ] **PF.2: Verify venv has dev deps**

  Run:
  ```bash
  .venv/bin/pytest --version
  .venv/bin/ruff --version
  ```
  Expected: both print versions (no `command not found`). If they fail, run `.venv/bin/pip install -e '.[dev]'` and `.venv/bin/playwright install chromium` first.

- [ ] **PF.3: Verify schema constants we depend on are still in place**

  Run:
  ```bash
  grep -n "supported_cities\|@register\|class Scraper" src/flatpilot/scrapers/__init__.py src/flatpilot/scrapers/base.py
  grep -n "CITY_IDS" src/flatpilot/scrapers/wg_gesucht.py src/flatpilot/scrapers/kleinanzeigen.py
  ```
  Expected:
  - `src/flatpilot/scrapers/__init__.py` has `def register(cls)` and `_REGISTRY: dict[...]`.
  - `src/flatpilot/scrapers/base.py` has `class Scraper(Protocol)` with `platform` and `user_agent` ClassVars only — no `supported_cities` yet.
  - `wg_gesucht.py` has `CITY_IDS: dict[str, int]` at module scope (lines ~58-77).
  - `kleinanzeigen.py` has `CITY_IDS: dict[str, int]` (just `{"Berlin": 3331}`).

- [ ] **PF.4: Verify fixture HTML is in place and has 10 listings**

  Run:
  ```bash
  test -f tests/fixtures/inberlinwohnen/search.html && \
    grep -c 'id="apartment-' tests/fixtures/inberlinwohnen/search.html
  ```
  Expected: prints `10`.

---

## File Structure

| Path | Action | Purpose |
|---|---|---|
| `src/flatpilot/scrapers/base.py` | modify | Add `supported_cities` ClassVar to `Scraper` Protocol. |
| `src/flatpilot/scrapers/__init__.py` | modify | `register` enforces `supported_cities` presence; add `supports_city(cls, city)` helper. |
| `src/flatpilot/scrapers/wg_gesucht.py` | modify | Declare `supported_cities = frozenset(CITY_IDS.keys())`. |
| `src/flatpilot/scrapers/kleinanzeigen.py` | modify | Declare `supported_cities = frozenset(CITY_IDS.keys())` (= `{"Berlin"}`). |
| `src/flatpilot/scrapers/inberlinwohnen.py` | create | New scraper module: `InBerlinWohnenScraper` + `parse_listings` + helpers. |
| `src/flatpilot/cli.py` | modify | `_run_scrape_pass` filters by city + prints "skipping — city not supported"; `scrape --platform` exits 1 on unsupported city; pipeline-bootstrap import sites add `inberlinwohnen`. |
| `tests/test_registry_city_gating.py` | create | Tests for `supports_city` helper and strict `register`. |
| `tests/test_inberlinwohnen_scraper.py` | create | Parser tests against fixture HTML; scraper-class wiring tests. |
| `tests/test_pipeline_city_gating.py` | create | Tests for `_run_scrape_pass` city filtering and `scrape --platform` validation. |
| `tests/fixtures/inberlinwohnen/search.html` | (already created) | Verbatim copy of `https://inberlinwohnen.de/wohnungsfinder/` from 2026-04-26. |

---

## Task 1 — Registry-level city gating (FlatPilot-4fir)

**Files:**
- Modify: `src/flatpilot/scrapers/base.py` (lines 55–66 — `Scraper` Protocol)
- Modify: `src/flatpilot/scrapers/__init__.py` (lines 18–54 — `register` + new helper)
- Modify: `src/flatpilot/scrapers/wg_gesucht.py` (line ~100 — declaration on the class)
- Modify: `src/flatpilot/scrapers/kleinanzeigen.py` (line ~93 — declaration on the class)
- Modify: `src/flatpilot/cli.py` (lines 213–222 `_run_pipeline_scrape`, 290–301 in `scrape` command, 314–359 `_run_scrape_pass`)
- Create: `tests/test_registry_city_gating.py`
- Create: `tests/test_pipeline_city_gating.py`

### 1.1 — Failing test for `supports_city` helper

- [ ] **Step 1.1.1: Write the failing test**

  Create `tests/test_registry_city_gating.py`:

  ```python
  """Tests for the scraper-registry city-gating mechanism."""

  from __future__ import annotations

  from typing import ClassVar

  import pytest


  def _stub_scraper_cls(supported: frozenset[str] | None) -> type:
      class _Stub:
          platform: ClassVar[str] = "stub"
          user_agent: ClassVar[str] = "test-ua"
          supported_cities: ClassVar[frozenset[str] | None] = supported

          def fetch_new(self, profile):  # noqa: ARG002 — protocol stub
              yield from ()

      return _Stub


  def test_supports_city_passes_when_supported_cities_is_none() -> None:
      """A scraper with supported_cities=None accepts every profile city."""
      from flatpilot.scrapers import supports_city

      cls = _stub_scraper_cls(None)
      assert supports_city(cls, "Berlin") is True
      assert supports_city(cls, "Munich") is True
      assert supports_city(cls, "") is True


  def test_supports_city_exact_match_only() -> None:
      """Comparison is exact-match (case-sensitive) — mirrors per-scraper CITY_IDS dict lookups."""
      from flatpilot.scrapers import supports_city

      cls = _stub_scraper_cls(frozenset({"Berlin"}))
      assert supports_city(cls, "Berlin") is True
      assert supports_city(cls, "berlin") is False
      assert supports_city(cls, " Berlin") is False
      assert supports_city(cls, "Berlin ") is False
      assert supports_city(cls, "Munich") is False


  def test_supports_city_handles_multi_city_set() -> None:
      from flatpilot.scrapers import supports_city

      cls = _stub_scraper_cls(frozenset({"Berlin", "Hamburg", "Munich"}))
      assert supports_city(cls, "Berlin") is True
      assert supports_city(cls, "Munich") is True
      assert supports_city(cls, "Hamburg") is True
      assert supports_city(cls, "Köln") is False


  def test_supports_city_empty_frozenset_supports_nothing() -> None:
      """An empty frozenset means 'declared, but no cities' — used to soft-disable a scraper."""
      from flatpilot.scrapers import supports_city

      cls = _stub_scraper_cls(frozenset())
      assert supports_city(cls, "Berlin") is False
      assert supports_city(cls, "Munich") is False
  ```

- [ ] **Step 1.1.2: Run the test to verify it fails**

  Run:
  ```bash
  .venv/bin/pytest tests/test_registry_city_gating.py -v
  ```
  Expected: 4 failures of type `ImportError: cannot import name 'supports_city' from 'flatpilot.scrapers'`.

- [ ] **Step 1.1.3: Add `supported_cities` to the `Scraper` Protocol**

  Edit `src/flatpilot/scrapers/base.py`. Replace the existing `Scraper` Protocol (lines 55–66):

  ```python
  class Scraper(Protocol):
      """Per-platform scraper contract.

      Implementations are expected to be lightweight classes — construction
      is cheap, ``fetch_new`` is where the network work happens.
      """

      platform: ClassVar[str]
      user_agent: ClassVar[str]
      # Frozenset of exact-match city names this scraper accepts (compared
      # verbatim to ``profile.city``). ``None`` means "no city restriction —
      # supports any city". The @register decorator enforces declaration so
      # a new scraper that forgets the field fails loudly at import time
      # rather than silently running against the wrong cities. See
      # ``flatpilot.scrapers.supports_city`` for the comparison helper.
      supported_cities: ClassVar[frozenset[str] | None]

      def fetch_new(self, profile: Profile) -> Iterable[Flat]:
          """Yield every listing currently visible under ``profile``."""
  ```

- [ ] **Step 1.1.4: Implement `supports_city` and tighten `register`**

  Edit `src/flatpilot/scrapers/__init__.py`. Replace the entire file content with:

  ```python
  """Scraper framework and per-platform scrapers.

  Scrapers register themselves with :func:`register` and are retrieved by
  their ``platform`` string. ``flatpilot scrape`` (Phase 1) iterates the
  registry — or a single ``--platform`` — to drive each scraper. Each
  scraper class declares ``supported_cities`` so the orchestrator can
  skip platforms that do not cover ``profile.city`` before paying the
  fetch cost; see :func:`supports_city`.
  """

  from __future__ import annotations

  from collections.abc import Iterable

  from flatpilot.scrapers.base import Flat, Scraper, session_dir


  _REGISTRY: dict[str, type[Scraper]] = {}

  # Sentinel for "attribute genuinely not declared on the class" — distinct
  # from the legitimate ``supported_cities = None`` (= "no city restriction").
  _NOT_DECLARED = object()


  def register(cls: type[Scraper]) -> type[Scraper]:
      """Class decorator that indexes ``cls`` under its ``platform`` attribute.

      Enforces that the class declares both ``platform`` and
      ``supported_cities`` ClassVars. ``supported_cities`` may be a
      ``frozenset[str]`` of exact city names, an empty frozenset (= "soft
      disabled — declared but supports no city right now"), or ``None``
      (= "no city restriction"). Forgetting the declaration raises
      :class:`TypeError` at import time so misconfiguration is loud rather
      than silently producing wrong scrapes.

      Usage::

          @register
          class WGGesuchtScraper:
              platform = "wg-gesucht"
              user_agent = "..."
              supported_cities = frozenset({"Berlin", "Hamburg", ...})
              def fetch_new(self, profile): ...
      """
      platform = getattr(cls, "platform", None)
      if not platform:
          raise TypeError(
              f"{cls.__name__} must set a non-empty `platform` ClassVar before @register"
          )
      supported = getattr(cls, "supported_cities", _NOT_DECLARED)
      if supported is _NOT_DECLARED:
          raise TypeError(
              f"{cls.__name__} must declare `supported_cities` ClassVar "
              f"(frozenset[str] of supported cities, or None for any) "
              f"before @register"
          )
      if platform in _REGISTRY:
          raise ValueError(
              f"Duplicate scraper registration for platform {platform!r}: "
              f"{_REGISTRY[platform].__name__} vs {cls.__name__}"
          )
      _REGISTRY[platform] = cls
      return cls


  def get_scraper(platform: str) -> type[Scraper]:
      try:
          return _REGISTRY[platform]
      except KeyError as exc:
          raise KeyError(
              f"No scraper registered for platform {platform!r} "
              f"(known: {sorted(_REGISTRY)})"
          ) from exc


  def all_scrapers() -> Iterable[type[Scraper]]:
      return list(_REGISTRY.values())


  def supports_city(scraper_cls: type[Scraper], city: str) -> bool:
      """Return True if ``scraper_cls`` accepts ``city``.

      Exact-match comparison (no case-fold, no whitespace strip) so the
      gate stays consistent with each scraper's internal CITY_IDS dict
      lookup — ``"berlin"`` is not the same value as ``"Berlin"`` and
      neither is ``"Frankfurt"`` vs ``"Frankfurt am Main"``. A scraper
      with ``supported_cities = None`` accepts every city.
      """
      supported = scraper_cls.supported_cities
      return supported is None or city in supported


  __all__ = [
      "Flat",
      "Scraper",
      "all_scrapers",
      "get_scraper",
      "register",
      "session_dir",
      "supports_city",
  ]
  ```

- [ ] **Step 1.1.5: Run the helper tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_registry_city_gating.py -v
  ```
  Expected: 4 passes.

### 1.2 — Failing test that `register` rejects classes missing `supported_cities`

- [ ] **Step 1.2.1: Append the failing test to `tests/test_registry_city_gating.py`**

  Append:

  ```python
  def test_register_rejects_class_without_supported_cities(monkeypatch: pytest.MonkeyPatch) -> None:
      """A class that forgets to declare supported_cities fails at @register time."""
      from flatpilot import scrapers

      monkeypatch.setattr(scrapers, "_REGISTRY", {})

      with pytest.raises(TypeError, match=r"supported_cities"):

          @scrapers.register
          class _MissingSupportedCities:
              platform: ClassVar[str] = "no-cities-test"
              user_agent: ClassVar[str] = "x"

              def fetch_new(self, profile):  # noqa: ARG002
                  yield from ()


  def test_register_accepts_supported_cities_none(monkeypatch: pytest.MonkeyPatch) -> None:
      """supported_cities=None is a legitimate declaration ('no restriction')."""
      from flatpilot import scrapers

      monkeypatch.setattr(scrapers, "_REGISTRY", {})

      @scrapers.register
      class _AnyCity:
          platform: ClassVar[str] = "any-city-test"
          user_agent: ClassVar[str] = "x"
          supported_cities: ClassVar[frozenset[str] | None] = None

          def fetch_new(self, profile):  # noqa: ARG002
              yield from ()

      assert "any-city-test" in scrapers._REGISTRY


  def test_register_still_rejects_missing_platform(monkeypatch: pytest.MonkeyPatch) -> None:
      """Existing platform-presence check still applies."""
      from flatpilot import scrapers

      monkeypatch.setattr(scrapers, "_REGISTRY", {})

      with pytest.raises(TypeError, match=r"platform"):

          @scrapers.register
          class _NoPlatform:
              user_agent: ClassVar[str] = "x"
              supported_cities: ClassVar[frozenset[str] | None] = None

              def fetch_new(self, profile):  # noqa: ARG002
                  yield from ()
  ```

- [ ] **Step 1.2.2: Run the new tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_registry_city_gating.py -v
  ```
  Expected: 7 passes (3 new + 4 existing).

### 1.3 — Declare `supported_cities` on the existing scrapers

- [ ] **Step 1.3.1: Run the full test suite to confirm we have a red baseline first**

  Run:
  ```bash
  .venv/bin/pytest tests/ -x --ignore=tests/test_registry_city_gating.py 2>&1 | tail -30
  ```
  Expected: every existing test passes EXCEPT any that import `flatpilot.scrapers.wg_gesucht` or `flatpilot.scrapers.kleinanzeigen` may now fail with `TypeError: ... supported_cities ...` because their classes no longer satisfy the strict `register`. (If the suite is fully green, that's fine too — it just means no test imports them at module top-level. Either way, the next two steps fix it.)

- [ ] **Step 1.3.2: Declare `supported_cities` on `WGGesuchtScraper`**

  Edit `src/flatpilot/scrapers/wg_gesucht.py`. Find the class block (line ~100) and replace:

  ```python
  @register
  class WGGesuchtScraper:
      platform: ClassVar[str] = "wg-gesucht"
      user_agent: ClassVar[str] = DEFAULT_USER_AGENT
  ```

  with:

  ```python
  @register
  class WGGesuchtScraper:
      platform: ClassVar[str] = "wg-gesucht"
      user_agent: ClassVar[str] = DEFAULT_USER_AGENT
      # Cities WG-Gesucht has a numeric search ID for. The class body is
      # evaluated AFTER the module-level CITY_IDS dict, so the keys are
      # already available at class-creation time.
      supported_cities: ClassVar[frozenset[str] | None] = frozenset(CITY_IDS.keys())
  ```

- [ ] **Step 1.3.3: Declare `supported_cities` on `KleinanzeigenScraper`**

  Edit `src/flatpilot/scrapers/kleinanzeigen.py`. Find the class block (line ~93) and replace:

  ```python
  @register
  class KleinanzeigenScraper:
      platform: ClassVar[str] = "kleinanzeigen"
      # Kept for protocol compatibility. The actual UA used per call is
      # picked from the pool via resolve_user_agent() so repeated fresh
      # sessions don't all share one fingerprint.
      user_agent: ClassVar[str] = DEFAULT_USER_AGENT
  ```

  with:

  ```python
  @register
  class KleinanzeigenScraper:
      platform: ClassVar[str] = "kleinanzeigen"
      # Kept for protocol compatibility. The actual UA used per call is
      # picked from the pool via resolve_user_agent() so repeated fresh
      # sessions don't all share one fingerprint.
      user_agent: ClassVar[str] = DEFAULT_USER_AGENT
      # Berlin-only today — extending requires both an entry in CITY_IDS
      # above AND adding the city here so the orchestrator stops gating.
      supported_cities: ClassVar[frozenset[str] | None] = frozenset(CITY_IDS.keys())
  ```

- [ ] **Step 1.3.4: Run the full test suite to confirm green**

  Run:
  ```bash
  .venv/bin/pytest tests/ -x 2>&1 | tail -30
  ```
  Expected: every test passes.

### 1.4 — Pipeline filters scrapers by city

- [ ] **Step 1.4.1: Write the failing test**

  Create `tests/test_pipeline_city_gating.py`:

  ```python
  """Tests for city-gating in the scrape pipeline."""

  from __future__ import annotations

  import logging
  from typing import Any, ClassVar

  import pytest
  from rich.console import Console


  def _profile_for_city(city: str):
      """Profile.load_example() with city overridden — example ships Frankfurt."""
      from flatpilot.profile import Profile

      return Profile.load_example().model_copy(update={"city": city})


  class _GenericScraper:
      """Stub scraper used to populate the registry for pipeline tests."""

      platform: ClassVar[str]
      user_agent: ClassVar[str] = "test-ua"
      supported_cities: ClassVar[frozenset[str] | None]

      def __init__(self) -> None:
          self.fetch_called_with: Any = None

      def fetch_new(self, profile):
          self.fetch_called_with = profile.city
          yield from ()


  def _make_stub(platform: str, supported_cities: frozenset[str] | None) -> type[_GenericScraper]:
      cls = type(
          f"_Stub_{platform.replace('-', '_')}",
          (_GenericScraper,),
          {
              "platform": platform,
              "supported_cities": supported_cities,
          },
      )
      return cls


  def test_run_scrape_pass_skips_scrapers_whose_cities_dont_match(
      tmp_db, caplog: pytest.LogCaptureFixture
  ) -> None:
      from flatpilot.cli import _run_scrape_pass

      profile = _profile_for_city("Munich")  # not in any stub's supported set
      console = Console(record=True)

      berlin_only = _make_stub("berlin-only", frozenset({"Berlin"}))()
      any_city = _make_stub("any-city", None)()
      multi_city = _make_stub("multi", frozenset({"Berlin", "Hamburg", "Munich"}))()

      with caplog.at_level(logging.INFO):
          _run_scrape_pass([berlin_only, any_city, multi_city], profile, console)

      # berlin-only stub must NOT be called; the multi-city stub IS Munich-supported;
      # the any-city stub is None-cities → always called.
      assert berlin_only.fetch_called_with is None, "Berlin-only should be skipped for Munich"
      assert any_city.fetch_called_with == "Munich"
      assert multi_city.fetch_called_with == "Munich"

      output = console.export_text()
      assert "berlin-only: skipping — city 'Munich' not supported" in output


  def test_run_scrape_pass_runs_all_when_all_support_city(tmp_db) -> None:
      from flatpilot.cli import _run_scrape_pass

      profile = _profile_for_city("Berlin")
      console = Console()

      a = _make_stub("plat-a", frozenset({"Berlin"}))()
      b = _make_stub("plat-b", None)()

      _run_scrape_pass([a, b], profile, console)

      assert a.fetch_called_with == "Berlin"
      assert b.fetch_called_with == "Berlin"
  ```

- [ ] **Step 1.4.2: Run the test to verify it fails**

  Run:
  ```bash
  .venv/bin/pytest tests/test_pipeline_city_gating.py -v
  ```
  Expected: `test_run_scrape_pass_skips_scrapers_whose_cities_dont_match` fails — the Berlin-only stub IS being called for Munich (no gate) AND the "skipping — city 'Munich' not supported" line is missing from console output.

- [ ] **Step 1.4.3: Add the city gate to `_run_scrape_pass`**

  Edit `src/flatpilot/cli.py`. Find `_run_scrape_pass` (lines 314–359). Replace the inner loop. The current loop starts with `for scraper in scrapers:` (line 324). Replace **only** the body up to the `try:` line. The full edited function:

  ```python
  def _run_scrape_pass(scrapers: list, profile, console) -> None:
      from datetime import datetime

      from flatpilot.database import get_conn
      from flatpilot.scrapers import backoff, supports_city
      from flatpilot.scrapers.session import ChallengeDetectedError, RateLimitedError

      conn = get_conn()
      now_dt = datetime.now(UTC)
      now = now_dt.isoformat()
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
          except RateLimitedError as exc:
              backoff.on_failure(plat, "rate_limit", now=datetime.now(UTC))
              console.print(f"[yellow]{plat}: {exc} — skipping this pass[/yellow]")
              continue
          except ChallengeDetectedError as exc:
              backoff.on_failure(plat, "challenge", now=datetime.now(UTC))
              console.print(
                  f"[red]{plat}: anti-bot challenge detected ({exc}) — "
                  f"extended cool-off[/red]"
              )
              continue
          except Exception as exc:
              console.print(
                  f"[red]{plat}: fetch failed ({exc.__class__.__name__}: {exc})[/red]"
              )
              continue

          new_count = 0
          for flat in flats:
              if _insert_flat(conn, flat, plat, now):
                  new_count += 1
          console.print(
              f"{plat}: [bold]{len(flats)}[/bold] listings, "
              f"[green]{new_count}[/green] new"
          )
          backoff.on_success(plat)
  ```

- [ ] **Step 1.4.4: Run the pipeline tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_pipeline_city_gating.py -v
  ```
  Expected: 2 passes.

### 1.5 — `scrape --platform` rejects unsupported city

- [ ] **Step 1.5.1: Append the failing test to `tests/test_pipeline_city_gating.py`**

  Append:

  ```python
  def test_scrape_command_rejects_explicit_platform_for_unsupported_city(
      tmp_db, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      """`flatpilot scrape --platform kleinanzeigen` exits 1 when profile.city is non-Berlin."""
      from typer.testing import CliRunner

      from flatpilot.cli import app
      from flatpilot.profile import Profile, save_profile

      profile = Profile.load_example().model_copy(update={"city": "Munich"})
      save_profile(profile)

      runner = CliRunner()
      result = runner.invoke(app, ["scrape", "--platform", "kleinanzeigen"])

      assert result.exit_code == 1, result.output
      assert "kleinanzeigen" in result.output
      assert "Munich" in result.output
      assert "not supported" in result.output


  def test_scrape_command_runs_when_explicit_platform_supports_city(
      tmp_db, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      """`flatpilot scrape --platform kleinanzeigen` proceeds when profile.city is Berlin.

      We patch the scraper's fetch_new so the test does not actually hit the
      network; the assertion is that the city gate did not block invocation.
      """
      from typer.testing import CliRunner

      from flatpilot.cli import app
      from flatpilot.profile import Profile, save_profile
      from flatpilot.scrapers import kleinanzeigen as kz

      profile = Profile.load_example().model_copy(update={"city": "Berlin"})
      save_profile(profile)

      called: dict[str, Any] = {}

      def _fake_fetch(self, profile):  # noqa: ARG001
          called["city"] = profile.city
          yield from ()

      monkeypatch.setattr(kz.KleinanzeigenScraper, "fetch_new", _fake_fetch)

      runner = CliRunner()
      result = runner.invoke(app, ["scrape", "--platform", "kleinanzeigen"])

      assert result.exit_code == 0, result.output
      assert called.get("city") == "Berlin"
  ```

- [ ] **Step 1.5.2: Run the new tests to verify they fail**

  Run:
  ```bash
  .venv/bin/pytest tests/test_pipeline_city_gating.py::test_scrape_command_rejects_explicit_platform_for_unsupported_city -v
  ```
  Expected: fails — exit_code is currently 0 (pipeline filter prints the skip line but `scrape` command does not exit non-zero on explicit-platform city mismatch).

- [ ] **Step 1.5.3: Add `--platform` validation to the `scrape` command**

  Edit `src/flatpilot/cli.py`. In the `scrape` function (lines 258–311) find the explicit-platform branch (lines 290–295):

  ```python
      if platform:
          try:
              scrapers = [get_scraper(platform)()]
          except KeyError as exc:
              console.print(f"[red]{exc}[/red]")
              raise typer.Exit(1) from exc
      else:
          scrapers = [cls() for cls in all_scrapers()]
  ```

  Replace with:

  ```python
      if platform:
          try:
              scraper_cls = get_scraper(platform)
          except KeyError as exc:
              console.print(f"[red]{exc}[/red]")
              raise typer.Exit(1) from exc
          if not supports_city(scraper_cls, profile.city):
              supported = scraper_cls.supported_cities
              cities_label = (
                  "any city" if supported is None else ", ".join(sorted(supported)) or "no cities"
              )
              console.print(
                  f"[red]{platform} does not support city "
                  f"{profile.city!r} (supports: {cities_label})[/red]"
              )
              raise typer.Exit(1)
          scrapers = [scraper_cls()]
      else:
          scrapers = [cls() for cls in all_scrapers()]
  ```

  Also update the import line (currently line 277):
  ```python
      from flatpilot.scrapers import all_scrapers, get_scraper
  ```
  to:
  ```python
      from flatpilot.scrapers import all_scrapers, get_scraper, supports_city
  ```

- [ ] **Step 1.5.4: Run the new tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_pipeline_city_gating.py -v
  ```
  Expected: 4 passes.

### 1.6 — Wrap up Task 1

- [ ] **Step 1.6.1: Run the full test suite + lint**

  Run:
  ```bash
  .venv/bin/pytest tests/ 2>&1 | tail -20
  .venv/bin/ruff check src/ tests/
  ```
  Expected: all tests pass; ruff reports zero issues for the new/modified files.

- [ ] **Step 1.6.2: Commit Task 1**

  ```bash
  git add src/flatpilot/scrapers/base.py \
          src/flatpilot/scrapers/__init__.py \
          src/flatpilot/scrapers/wg_gesucht.py \
          src/flatpilot/scrapers/kleinanzeigen.py \
          src/flatpilot/cli.py \
          tests/test_registry_city_gating.py \
          tests/test_pipeline_city_gating.py
  git commit -m "FlatPilot-4fir: gate scrapers on profile.city via supported_cities ClassVar

  Scrapers now declare which cities they cover via a supported_cities
  frozenset (or None for any city); the @register decorator enforces
  declaration. The scrape pipeline skips scrapers whose declared cities
  don't include profile.city before paying the fetch cost, and the
  scrape --platform CLI exits 1 when the explicit platform does not
  cover the user's city.

  Behavior change: kleinanzeigen previously raised UnknownCityError at
  fetch time for non-Berlin profiles (printed as 'fetch failed'); it
  now skips with a clear 'kleinanzeigen: skipping — city ... not
  supported' line at the gate."
  ```

---

## Task 2 — Inberlinwohnen.de scraper module (FlatPilot-rqks)

**Files:**
- Create: `src/flatpilot/scrapers/inberlinwohnen.py`
- Create: `tests/test_inberlinwohnen_scraper.py`

### 2.1 — Failing test for `parse_listings` count + first card fields

- [ ] **Step 2.1.1: Write the failing test**

  Create `tests/test_inberlinwohnen_scraper.py`:

  ```python
  """Tests for the inberlinwohnen.de Wohnungsfinder scraper."""

  from __future__ import annotations

  from pathlib import Path
  from typing import Any

  import pytest


  FIXTURE = Path(__file__).parent / "fixtures" / "inberlinwohnen" / "search.html"


  def test_parse_listings_count_matches_apartment_blocks() -> None:
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      flats = list(parse_listings(html))
      # Fixture was captured 2026-04-26 from page 1 of the live feed; it
      # contains 10 apartment-* blocks. If the fixture is replaced, update
      # this expectation alongside it.
      assert len(flats) == 10


  def test_parse_listings_first_card_required_fields() -> None:
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      flats = list(parse_listings(html))
      first = flats[0]

      assert first["external_id"] == "16214"
      assert first["listing_url"] == (
          "https://www.degewo.de/de/properties/W1100-42014-0033-0502.html"
      )
      assert first["title"] == "Erstbezug in Grünau - Dachterrasse inklusive!"
  ```

- [ ] **Step 2.1.2: Run the test to verify it fails**

  Run:
  ```bash
  .venv/bin/pytest tests/test_inberlinwohnen_scraper.py -v
  ```
  Expected: 2 failures of type `ModuleNotFoundError: No module named 'flatpilot.scrapers.inberlinwohnen'`.

- [ ] **Step 2.1.3: Create the scraper module skeleton with `parse_listings`**

  Create `src/flatpilot/scrapers/inberlinwohnen.py`:

  ```python
  """Inberlinwohnen.de Wohnungsfinder scraper.

  Aggregates Berlin's six municipal-housing companies (degewo, Gesobau,
  Howoge, Stadt und Land, WBM, Gewobag) into one Wohnungen feed at
  https://inberlinwohnen.de/wohnungsfinder/. Berlin-only by design — the
  registry-level city gate (see ``flatpilot.scrapers.supports_city``)
  filters this scraper out for any other ``profile.city``.

  Card DOM (verified against tests/fixtures/inberlinwohnen/search.html,
  captured 2026-04-26):

  - Each apartment is a ``<div id="apartment-{flat_id}" class="mb-3">``.
  - Inside ``.list__details`` there is a ``<span class="text-xl block">``
    with the title, an ``<a wire:click="processDeeplink" href="...">``
    pointing off-site to the operator's expose, and one or two ``<dl>``
    blocks of ``<dt>label:</dt><dd>value</dd>`` pairs covering address,
    rooms, area, prices, occupation date, and the WBS flag.
  - Numbers use German formatting: thousand-dot, decimal-comma
    (``1.594,52`` = 1594.52). Areas append ``m²``; prices append ``€``.

  Phase 1 MVP scrapes page 1 only (~10 listings). Pagination can land
  later without changing the public shape.
  """

  from __future__ import annotations

  import logging
  import re
  from collections.abc import Iterable
  from typing import Any, ClassVar
  from urllib.parse import urljoin

  from flatpilot.profile import Profile
  from flatpilot.scrapers import register
  from flatpilot.scrapers.base import Flat
  from flatpilot.scrapers.session import (
      DEFAULT_USER_AGENT,
      SessionConfig,
      check_rate_limit,
      polite_session,
  )
  from flatpilot.scrapers.session import (
      page as session_page,
  )

  logger = logging.getLogger(__name__)


  HOST = "https://inberlinwohnen.de"
  SEARCH_URL = f"{HOST}/wohnungsfinder/"
  WARMUP_URL = f"{HOST}/"

  # Cookie-banner buttons observed on inberlinwohnen.de — the page uses a
  # generic GDPR banner. Selectors are matched in order; the first one
  # that becomes visible is clicked.
  CONSENT_SELECTORS: tuple[str, ...] = (
      "button:has-text('Alle akzeptieren')",
      "button:has-text('Akzeptieren')",
      "button:has-text('Einverstanden')",
      "button:has-text('Zustimmen')",
  )


  _APARTMENT_ID_RE = re.compile(r"^apartment-(\d+)$")
  # German number: optional thousand-dot groups, optional decimal comma.
  _NUMBER_RE = re.compile(r"\d+(?:\.\d{3})*(?:,\d+)?")
  _DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


  @register
  class InBerlinWohnenScraper:
      platform: ClassVar[str] = "inberlinwohnen"
      user_agent: ClassVar[str] = DEFAULT_USER_AGENT
      supported_cities: ClassVar[frozenset[str] | None] = frozenset({"Berlin"})

      def fetch_new(self, profile: Profile) -> Iterable[Flat]:
          config = SessionConfig(
              platform=self.platform,
              user_agent=self.user_agent,
              warmup_url=WARMUP_URL,
              consent_selectors=CONSENT_SELECTORS,
          )

          logger.info("%s: fetching %s", self.platform, SEARCH_URL)
          with polite_session(config) as context, session_page(context) as pg:
              response = pg.goto(SEARCH_URL, wait_until="domcontentloaded")
              if response is None:
                  logger.warning("%s: null response from %s", self.platform, SEARCH_URL)
                  return
              check_rate_limit(response.status, self.platform)
              if response.status >= 400:
                  logger.warning(
                      "%s: search returned HTTP %d", self.platform, response.status
                  )
                  return
              html = pg.content()

          flats = list(parse_listings(html))
          logger.info("%s: parsed %d listings", self.platform, len(flats))
          yield from flats


  def parse_listings(html: str) -> Iterable[Flat]:
      """Yield a :class:`Flat` per ``#apartment-<id>`` block in ``html``.

      Exposed module-level so parser tests can feed fixture HTML without
      constructing a scraper instance — same convention as
      ``flatpilot.scrapers.kleinanzeigen.parse_listings``.
      """
      from bs4 import BeautifulSoup

      soup = BeautifulSoup(html, "html.parser")
      for card in soup.select('div[id^="apartment-"]'):
          try:
              flat = _parse_card(card)
          except Exception as exc:
              logger.warning(
                  "inberlinwohnen: skipping unparseable card (%s: %s)",
                  exc.__class__.__name__,
                  exc,
              )
              continue
          if flat is not None:
              yield flat


  def _parse_card(card: Any) -> Flat | None:
      dom_id = card.get("id") or ""
      m = _APARTMENT_ID_RE.match(dom_id)
      if not m:
          return None
      external_id = m.group(1)

      deeplink = card.find("a", attrs={"wire:click": "processDeeplink"})
      if deeplink is None:
          deeplink = card.select_one('.list__details a[target="_blank"][href]')
      if deeplink is None or not deeplink.get("href"):
          return None
      listing_url = urljoin(HOST, deeplink["href"])

      title_el = card.select_one(".list__details > span.text-xl") or card.select_one(
          ".list__details span.block"
      )
      title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
      if not title:
          title = "Untitled listing"

      flat: Flat = {
          "external_id": external_id,
          "listing_url": listing_url,
          "title": title,
      }

      details = _extract_dl(card)

      address = details.get("Adresse")
      if address:
          flat["address"] = address
          district = _district_from_address(address)
          if district:
              flat["district"] = district

      rooms = _german_number(details.get("Zimmeranzahl") or "")
      if rooms is not None:
          flat["rooms"] = rooms

      size = _german_number(details.get("Wohnfläche") or "")
      if size is not None:
          flat["size_sqm"] = size

      cold = _german_number(details.get("Kaltmiete") or "")
      if cold is not None:
          flat["rent_cold_eur"] = cold

      extra = _german_number(details.get("Nebenkosten") or "")
      if extra is not None:
          flat["extra_costs_eur"] = extra

      total = _german_number(details.get("Gesamtmiete") or "")
      if total is not None:
          flat["rent_warm_eur"] = total

      available = _iso_date(details.get("Bezugsfertig ab") or "")
      if available:
          flat["available_from"] = available

      online = _iso_date(details.get("Eingestellt am") or "")
      if online:
          flat["online_since"] = online

      wbs_text = details.get("WBS")
      if wbs_text is None:
          # No WBS row on this card — default to True. Inberlinwohnen lists
          # municipal stock; cards without an explicit flag are exceptional
          # and we'd rather over-report and let the matcher reject than
          # silently miss a WBS-required listing.
          flat["requires_wbs"] = True
      else:
          flat["requires_wbs"] = "nicht erforderlich" not in wbs_text.lower()

      return flat


  def _extract_dl(card: Any) -> dict[str, str]:
      """Return a label→value map from every ``<dl>`` inside ``.list__details``.

      Each ``<dl>`` holds parallel ``<dt>`` / ``<dd>`` lists (no nesting).
      The first ``<dt>`` in document order maps to the first ``<dd>``,
      and so on. Earlier labels win on duplicates — the page never
      repeats a label across columns in practice but we guard for it.
      """
      out: dict[str, str] = {}
      for dl in card.select(".list__details dl"):
          dts = dl.find_all("dt")
          dds = dl.find_all("dd")
          for dt, dd in zip(dts, dds):
              label = dt.get_text(" ", strip=True).rstrip(":").strip()
              value = " ".join(dd.get_text(" ", strip=True).split())
              if label and label not in out:
                  out[label] = value
      return out


  def _district_from_address(address: str) -> str | None:
      """``"Am Falkenberg 11M, 12524, Treptow-Köpenick"`` → ``"Treptow-Köpenick"``."""
      parts = [p.strip() for p in address.split(",") if p.strip()]
      if not parts:
          return None
      tail = parts[-1]
      return tail or None


  def _german_number(text: str) -> float | None:
      """Parse a German-formatted number out of ``text`` and return it as float.

      ``"3,0"``        → 3.0
      ``"94,35 m²"``   → 94.35
      ``"1.594,52 €"`` → 1594.52
      Returns ``None`` if no number can be located.
      """
      m = _NUMBER_RE.search(text)
      if not m:
          return None
      raw = m.group(0).replace(".", "").replace(",", ".")
      try:
          return float(raw)
      except ValueError:
          return None


  def _iso_date(text: str) -> str | None:
      m = _DATE_RE.search(text)
      if not m:
          return None
      day, month, year = m.group(1), m.group(2), m.group(3)
      return f"{year}-{month}-{day}"
  ```

- [ ] **Step 2.1.4: Run the parser tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_inberlinwohnen_scraper.py -v
  ```
  Expected: 2 passes.

### 2.2 — Failing test for numeric / date / WBS extraction on first card

- [ ] **Step 2.2.1: Append the failing test**

  Append to `tests/test_inberlinwohnen_scraper.py`:

  ```python
  def test_parse_listings_first_card_numeric_and_date_fields() -> None:
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      first = list(parse_listings(html))[0]

      assert first["rooms"] == 3.0
      assert first["size_sqm"] == 94.35
      assert first["rent_cold_eur"] == 1594.52
      assert first["extra_costs_eur"] == 209.46
      assert first["rent_warm_eur"] == 1998.34  # Gesamtmiete = Kaltmiete + Nebenkosten
      assert first["available_from"] == "2026-04-26"
      assert first["online_since"] == "2026-04-26"


  def test_parse_listings_first_card_address_and_district() -> None:
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      first = list(parse_listings(html))[0]

      assert first["address"] == "Am Falkenberg 11M, 12524, Treptow-Köpenick"
      assert first["district"] == "Treptow-Köpenick"


  def test_parse_listings_first_card_wbs_not_required() -> None:
      """First fixture card has 'WBS: nicht erforderlich' → requires_wbs=False."""
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      first = list(parse_listings(html))[0]

      assert first["requires_wbs"] is False


  def test_parse_listings_some_card_wbs_required() -> None:
      """At least one fixture card carries 'WBS: erforderlich' → requires_wbs=True.

      The fixture spans 10 cards from six municipal landlords; in practice
      multiple cards require a WBS. This test guards the parser branch
      that reads the affirmative form of the label.
      """
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      flats = list(parse_listings(html))
      assert any(flat.get("requires_wbs") is True for flat in flats), (
          "expected at least one fixture card with WBS: erforderlich"
      )
  ```

- [ ] **Step 2.2.2: Run the new tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_inberlinwohnen_scraper.py -v
  ```
  Expected: 6 passes total. If `test_parse_listings_some_card_wbs_required` fails (i.e. all 10 cards say "nicht erforderlich" today), inspect the fixture with `grep -n "WBS:" tests/fixtures/inberlinwohnen/search.html` and the next ~3 lines for each match — every fixture card with `WBS:</dt><dd>...erforderlich</dd>` (without "nicht") satisfies the assertion. If genuinely none do today, change the test to `assert all(flat.get("requires_wbs") is False for flat in flats)` and add the affirmative-WBS branch to a unit test on `_parse_card` with a hand-rolled minimal HTML snippet instead.

### 2.3 — Failing test that all parsed cards carry the required fields

- [ ] **Step 2.3.1: Append the resilience test**

  Append:

  ```python
  def test_parse_listings_every_card_has_required_fields() -> None:
      """Every emitted Flat carries external_id, listing_url, title — the
      three fields the orchestrator requires (see Flat TypedDict in
      ``flatpilot.scrapers.base``)."""
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = FIXTURE.read_text()
      flats = list(parse_listings(html))
      assert flats, "fixture should produce at least one flat"
      for f in flats:
          assert f["external_id"]
          assert f["listing_url"].startswith(("http://", "https://"))
          assert f["title"]


  def test_parse_listings_skips_unrelated_apartment_id_divs() -> None:
      """A loose ``<div id="apartment-foo">`` (non-numeric) must not become a Flat."""
      from flatpilot.scrapers.inberlinwohnen import parse_listings

      html = (
          "<html><body>"
          '<div id="apartment-not-a-number" class="mb-3">'
          '  <div class="list__details">'
          "    <span class=\"text-xl block\">title</span>"
          '    <a target="_blank" href="https://example.com/x">Alle Details</a>'
          "  </div>"
          "</div>"
          "</body></html>"
      )
      assert list(parse_listings(html)) == []
  ```

- [ ] **Step 2.3.2: Run all parser tests**

  Run:
  ```bash
  .venv/bin/pytest tests/test_inberlinwohnen_scraper.py -v
  ```
  Expected: 8 passes.

### 2.4 — Failing test that the scraper class is wired up correctly

- [ ] **Step 2.4.1: Append the scraper-class wiring test**

  Append:

  ```python
  def test_scraper_class_attributes(tmp_db) -> None:
      from flatpilot.scrapers import get_scraper, supports_city
      from flatpilot.scrapers.inberlinwohnen import InBerlinWohnenScraper

      assert get_scraper("inberlinwohnen") is InBerlinWohnenScraper
      assert InBerlinWohnenScraper.platform == "inberlinwohnen"
      assert InBerlinWohnenScraper.supported_cities == frozenset({"Berlin"})
      assert supports_city(InBerlinWohnenScraper, "Berlin") is True
      assert supports_city(InBerlinWohnenScraper, "Munich") is False


  def test_fetch_new_uses_polite_session_with_search_url(
      tmp_db, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      """fetch_new wires SEARCH_URL into a SessionConfig and drains parse_listings."""
      from flatpilot.profile import Profile
      from flatpilot.scrapers import inberlinwohnen as ib

      captured: dict[str, Any] = {}

      class _FakeCtxMgr:
          def __init__(self, config: Any) -> None:
              captured["config"] = config

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
                      captured["goto_url"] = url

                      class _R:
                          status = 200

                      return _R()

                  def content(self) -> str:
                      return FIXTURE.read_text()

              return _P()

          def __exit__(self, *_exc: Any) -> None:
              return None

      monkeypatch.setattr(ib, "polite_session", _FakeCtxMgr)
      monkeypatch.setattr(ib, "session_page", _FakePageCtxMgr)

      profile = Profile.load_example().model_copy(update={"city": "Berlin"})

      scraper = ib.InBerlinWohnenScraper()
      flats = list(scraper.fetch_new(profile))

      assert captured["config"].platform == "inberlinwohnen"
      assert captured["config"].warmup_url == ib.WARMUP_URL
      assert captured["goto_url"] == ib.SEARCH_URL
      assert len(flats) == 10
  ```

- [ ] **Step 2.4.2: Run the new tests**

  Run:
  ```bash
  .venv/bin/pytest tests/test_inberlinwohnen_scraper.py -v
  ```
  Expected: 10 passes.

### 2.5 — Wrap up Task 2

- [ ] **Step 2.5.1: Run the full test suite + lint**

  Run:
  ```bash
  .venv/bin/pytest tests/ 2>&1 | tail -20
  .venv/bin/ruff check src/flatpilot/scrapers/inberlinwohnen.py tests/test_inberlinwohnen_scraper.py
  ```
  Expected: all tests pass; ruff reports zero issues.

- [ ] **Step 2.5.2: Commit Task 2**

  ```bash
  git add src/flatpilot/scrapers/inberlinwohnen.py tests/test_inberlinwohnen_scraper.py
  git commit -m "FlatPilot-rqks: add inberlinwohnen.de Wohnungsfinder scraper

  Berlin-only (declared via supported_cities frozenset). Parses the
  server-rendered apartment-* div blocks on
  https://inberlinwohnen.de/wohnungsfinder/ — extracts external_id,
  off-site operator deeplink, title, address/district, German-formatted
  numbers (rooms, area, cold/extra/total rent), occupation date, and the
  explicit WBS label (requires_wbs=True for 'erforderlich' or absent,
  False for 'nicht erforderlich')."
  ```

---

## Task 3 — Pipeline bootstrap registration (FlatPilot-rqks)

**Files:**
- Modify: `src/flatpilot/cli.py` (lines 213–215 inside `_run_pipeline_scrape`, lines 273–275 inside `scrape`)

### 3.1 — Failing test that the pipeline registers inberlinwohnen on bootstrap

- [ ] **Step 3.1.1: Append a pipeline integration test**

  Append to `tests/test_pipeline_city_gating.py`:

  ```python
  def test_scrape_command_runs_inberlinwohnen_for_berlin_profile(
      tmp_db, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      """`flatpilot scrape` (which wires the scrape bootstrap) drives
      inberlinwohnen for a Berlin profile.

      Stubs fetch_new on every scraper class so the test never hits the
      network. The assertion that inberlinwohnen.fetch_new was called
      proves the bootstrap import line is in place — the scraper class
      can only enter the registry if @register fires, which only fires
      when the module is imported.
      """
      from typer.testing import CliRunner

      from flatpilot.cli import app
      from flatpilot.profile import Profile, save_profile
      from flatpilot.scrapers import inberlinwohnen as ib
      from flatpilot.scrapers import kleinanzeigen as kz
      from flatpilot.scrapers import wg_gesucht as wg

      profile = Profile.load_example().model_copy(update={"city": "Berlin"})
      save_profile(profile)

      called: dict[str, str] = {}

      def _capture(self, profile):
          called[type(self).platform] = profile.city
          yield from ()

      monkeypatch.setattr(ib.InBerlinWohnenScraper, "fetch_new", _capture)
      monkeypatch.setattr(kz.KleinanzeigenScraper, "fetch_new", _capture)
      monkeypatch.setattr(wg.WGGesuchtScraper, "fetch_new", _capture)

      runner = CliRunner()
      result = runner.invoke(app, ["scrape"])

      assert result.exit_code == 0, result.output
      assert called.get("inberlinwohnen") == "Berlin"
      assert called.get("kleinanzeigen") == "Berlin"
      assert called.get("wg-gesucht") == "Berlin"


  def test_pipeline_filters_inberlinwohnen_for_non_berlin_profile(
      tmp_db, monkeypatch: pytest.MonkeyPatch
  ) -> None:
      """A Munich profile drives _run_scrape_pass to skip both kleinanzeigen
      and inberlinwohnen (Berlin-only) but still call wg-gesucht (multi-city)."""
      from flatpilot.cli import _run_scrape_pass
      from flatpilot.profile import Profile
      from flatpilot.scrapers import inberlinwohnen as ib
      from flatpilot.scrapers import kleinanzeigen as kz
      from flatpilot.scrapers import wg_gesucht as wg

      called: dict[str, str] = {}

      def _capture(self, profile):
          called[type(self).platform] = profile.city
          yield from ()

      monkeypatch.setattr(ib.InBerlinWohnenScraper, "fetch_new", _capture)
      monkeypatch.setattr(kz.KleinanzeigenScraper, "fetch_new", _capture)
      monkeypatch.setattr(wg.WGGesuchtScraper, "fetch_new", _capture)

      profile = Profile.load_example().model_copy(update={"city": "Munich"})
      console = Console(record=True)

      scrapers = [
          ib.InBerlinWohnenScraper(),
          kz.KleinanzeigenScraper(),
          wg.WGGesuchtScraper(),
      ]
      _run_scrape_pass(scrapers, profile, console)

      assert "inberlinwohnen" not in called
      assert "kleinanzeigen" not in called
      assert called.get("wg-gesucht") == "Munich"

      output = console.export_text()
      assert "inberlinwohnen: skipping — city 'Munich' not supported" in output
      assert "kleinanzeigen: skipping — city 'Munich' not supported" in output
  ```

- [ ] **Step 3.1.2: Run the new test to verify the bootstrap-registration test fails**

  Run:
  ```bash
  .venv/bin/pytest tests/test_pipeline_city_gating.py::test_scrape_command_runs_inberlinwohnen_for_berlin_profile -v
  ```
  Expected: fails with `assert called.get("inberlinwohnen") == "Berlin"` returning `None == "Berlin"` — `flatpilot scrape` does not currently import `flatpilot.scrapers.inberlinwohnen`, so `@register` never fires for it, so `all_scrapers()` returns only the two existing platforms. (The other new test, `test_pipeline_filters_inberlinwohnen_for_non_berlin_profile`, instantiates the class directly and should already pass — Task 2's tests caused the inberlinwohnen module to be imported in this pytest session.)

- [ ] **Step 3.1.3: Add the bootstrap import to `_run_pipeline_scrape`**

  Edit `src/flatpilot/cli.py`. Find `_run_pipeline_scrape` (lines 213–222). Replace the import block:

  ```python
  def _run_pipeline_scrape(profile, console) -> None:
      import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
      import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register
      from flatpilot.scrapers import all_scrapers
  ```

  with:

  ```python
  def _run_pipeline_scrape(profile, console) -> None:
      import flatpilot.scrapers.inberlinwohnen  # noqa: F401 — triggers @register
      import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
      import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register
      from flatpilot.scrapers import all_scrapers
  ```

- [ ] **Step 3.1.4: Add the bootstrap import to the `scrape` command**

  Still in `src/flatpilot/cli.py`. Find the `scrape` command (lines 258–311). Replace this block (lines 273–277):

  ```python
      import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
      import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register
      from flatpilot.database import init_db
      from flatpilot.profile import load_profile
      from flatpilot.scrapers import all_scrapers, get_scraper, supports_city
  ```

  with:

  ```python
      import flatpilot.scrapers.inberlinwohnen  # noqa: F401 — triggers @register
      import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
      import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register
      from flatpilot.database import init_db
      from flatpilot.profile import load_profile
      from flatpilot.scrapers import all_scrapers, get_scraper, supports_city
  ```

- [ ] **Step 3.1.5: Run the new tests to verify they pass**

  Run:
  ```bash
  .venv/bin/pytest tests/test_pipeline_city_gating.py -v
  ```
  Expected: 6 passes (4 from Task 1 + 2 new).

### 3.2 — Wrap up Task 3

- [ ] **Step 3.2.1: Run the full test suite + lint**

  Run:
  ```bash
  .venv/bin/pytest tests/ 2>&1 | tail -20
  .venv/bin/ruff check src/ tests/
  ```
  Expected: all tests pass; ruff reports zero issues.

- [ ] **Step 3.2.2: Commit Task 3**

  ```bash
  git add src/flatpilot/cli.py tests/test_pipeline_city_gating.py
  git commit -m "FlatPilot-rqks: register inberlinwohnen scraper in pipeline bootstrap

  The scrape and run commands import inberlinwohnen alongside the
  existing kleinanzeigen and wg-gesucht modules so its @register
  decorator fires before the orchestrator iterates the registry. Adds
  pipeline-level integration tests covering both the bootstrap-
  registration path and the city-gating filter for a non-Berlin profile."
  ```

---

## Final verification

- [ ] **F.1: Run the full test suite**

  Run:
  ```bash
  .venv/bin/pytest tests/ -v 2>&1 | tail -40
  ```
  Expected: every test passes. Note any new tests added by this PR (~16 across `test_registry_city_gating.py`, `test_inberlinwohnen_scraper.py`, `test_pipeline_city_gating.py`) and confirm none are skipped.

- [ ] **F.2: Run ruff**

  Run:
  ```bash
  .venv/bin/ruff check src/ tests/
  ```
  Expected: no issues.

- [ ] **F.3: Verify the commit log on the branch**

  Run:
  ```bash
  git log --oneline origin/main..HEAD
  ```
  Expected: 4 commits (or 5 if you committed the plan first), each with a `FlatPilot-<id>:` prefix and no AI co-author trailers.

- [ ] **F.4: Smoke-test the CLI manually (no network — uses fake fetch)**

  Run:
  ```bash
  .venv/bin/python - <<'PY'
  from flatpilot.scrapers import _REGISTRY, supports_city
  import flatpilot.scrapers.inberlinwohnen  # noqa: F401
  import flatpilot.scrapers.kleinanzeigen  # noqa: F401
  import flatpilot.scrapers.wg_gesucht  # noqa: F401

  for plat, cls in sorted(_REGISTRY.items()):
      cities = cls.supported_cities
      label = "any" if cities is None else ", ".join(sorted(cities))
      print(f"{plat}: {label}")
  PY
  ```
  Expected output:
  ```
  inberlinwohnen: Berlin
  kleinanzeigen: Berlin
  wg-gesucht: Berlin, Bremen, Cologne, Dortmund, Dresden, Düsseldorf, Essen, Frankfurt, Frankfurt am Main, Hamburg, Hannover, Köln, Leipzig, München, Munich, Nuremberg, Nürnberg, Stuttgart
  ```
  (Order within the wg-gesucht line is sorted; exact set is whatever's in `wg_gesucht.CITY_IDS`.)

- [ ] **F.5: Push the branch and open the PR**

  ```bash
  git push -u origin feat/inberlinwohnen-scraper
  gh pr create --base main --head feat/inberlinwohnen-scraper --fill
  ```
  Then write the PR body manually with sections: Summary, Closes (FlatPilot-rqks, FlatPilot-4fir, FlatPilot-jzj3 = Epic J), Commit grouping, Test plan checklist, Behavior-change notes (kleinanzeigen non-Berlin profile change), Deferred follow-ups.

- [ ] **F.6: Close beads after PR opens**

  ```bash
  bd close FlatPilot-rqks FlatPilot-4fir FlatPilot-jzj3 --reason="Closed by PR #<n>"
  git add .beads/issues.jsonl
  git commit -m "FlatPilot-rqks: close beads + epic J after PR #<n> opened"
  git push
  ```

---

## Notes for the executing engineer

- **Profile shape:** `Profile.load_example()` ships `city="Frankfurt am Main"`. Always `model_copy(update={"city": "Berlin"})` (or another exact-case city name) before passing to anything that touches `supports_city`. Inline `Profile.model_validate({...})` is **forbidden** — there are ~15 required fields and you will lose track of one.
- **Attachments overrides in tests:** if any test mutates attachments (none in this plan, but if a follow-up needs it), use `Attachments(default=[...], per_platform={})` from `flatpilot.profile` — never raw dicts (pydantic will warn).
- **TDD discipline:** every Task-N.M step pair is `write failing test → run (fail) → implement → run (pass)`. Do not skip the run-fail step — it confirms the test is genuinely exercising the new code.
- **Commit author:** verify `git config user.email` returns `ibrohimovmuhammad2020@gmail.com` before the first commit on this branch. No AI co-author / "Generated with Claude" trailers.
- **Beads pre-commit hook:** `.beads/issues.jsonl` may auto-drift on commit. Let it ride. Do **not** pass `--no-verify`.
- **Ruff and pytest paths:** always invoke via `.venv/bin/ruff` and `.venv/bin/pytest`. The system `python3` and `pytest` on this Mac do not have the project deps installed.
