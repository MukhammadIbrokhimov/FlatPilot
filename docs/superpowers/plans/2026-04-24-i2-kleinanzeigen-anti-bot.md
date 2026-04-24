# I2. Kleinanzeigen Anti-Bot Handling + Rate Limit (FlatPilot-6hix) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Kleinanzeigen scraper with Playwright stealth tweaks, a residential-style user-agent pool pinned per session, classifier-driven detection of Cloudflare/Turnstile challenges and rate-limit text, and an adaptive per-platform exponential backoff that keeps `flatpilot run --watch` from hammering the site after a block.

**Architecture:** Four generic, per-concern modules under `src/flatpilot/scrapers/` (`ua_pool.py`, `block_detect.py`, `backoff.py`, plus a `stealth` option added to the existing `session.py` `SessionConfig`) are wired into the existing Kleinanzeigen scraper and the CLI pipeline (`cli.py::_run_scrape_pass`). Cookie-jar path is unchanged (`~/.flatpilot/sessions/kleinanzeigen/state.json`); a new sibling `fingerprint.json` pins the UA so the Playwright fingerprint stays consistent for the life of the cookie. Backoff state is in-memory per process, per platform.

**Tech Stack:** Python 3.11+, Playwright (Chromium), BeautifulSoup (already in use), pytest, pydantic. No new third-party libraries — clean-room policy plus the supply-chain aversion mean we hand-roll stealth flags instead of pulling `playwright-stealth`.

**Context for a fresh engineer:**

- `FlatPilot-6hix` closes epic `FlatPilot-agw1` (Epic I. Kleinanzeigen scraper, Phase 2). Epic I has two leaves: I1 (merged in PR #20, commit `36dd1d6`) and I2 (this task).
- The D0 anti-bot probe logged on `FlatPilot-3hu2` ran 178/178 ok polls over 4.5 h at 90 s cadence — **this task is preemptive hardening, not a response to observed blocks.** Flag that in the PR description so reviewers don't assume Kleinanzeigen is already challenging us.
- The existing Kleinanzeigen scraper at `src/flatpilot/scrapers/kleinanzeigen.py` talks to `src/flatpilot/scrapers/session.py::polite_session()` for cookies and warm-up. Session state is at `~/.flatpilot/sessions/kleinanzeigen/state.json`; the shipped `DEFAULT_USER_AGENT` is Firefox 121 on Linux. That UA is what D0 validated — we keep it as the pool default.
- The probe script `scripts/kleinanzeigen_probe.py` already contains the classifier heuristics (CAPTCHA_IFRAME_SELECTORS, CHALLENGE_KEYWORDS, BLOCK_KEYWORDS, MIN_BODY_CHARS). We **port** those into a reusable module rather than re-implement. One keyword must change: `"einen moment"` has false positives in benign German loading UI — we tighten it to `"einen moment, bitte"` (see Task 3 Step 2 which proves this with the real fixture).
- The pipeline (`src/flatpilot/cli.py::_run_scrape_pass`, line 303) catches `RateLimitedError` and `continue`s. With adaptive backoff, we additionally skip platforms whose backoff timer hasn't expired, and we react to a new `ChallengeDetectedError`.

**Commit rules (every code commit MUST satisfy all of these):**

- Author: `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. Run `git config user.email` to verify before the first commit on a fresh branch.
- Never add AI co-author or tool trailers. No `Co-Authored-By: Claude …`, no `🤖 Generated with Claude Code`. Commits are authored by the human only.
- Every commit message starts with `FlatPilot-6hix:` followed by a short, imperative summary of what the commit does.
- Never push to `main`. This work ships via a PR on branch `feat/i2-kleinanzeigen-anti-bot`.
- `.beads/issues.jsonl` is auto-updated by the pre-commit hook — let it ride; **do not** use `--no-verify`.
- Do not run `git commit --amend`. Always create new commits. A failing pre-commit hook means the commit did not happen — fix the issue and re-commit.

**Design decisions made up front:**

1. **UA pinning via fingerprint.json.** A scraper's cookies (`state.json`) are issued under one UA fingerprint; presenting the *same* cookie jar with a *different* UA on the next run is *more* suspicious than staying stale. So the UA pool rotates **only when `state.json` is absent**. When `state.json` exists but `fingerprint.json` doesn't, we fall back to the shipped `DEFAULT_USER_AGENT` (Firefox 121) so the D0-validated fingerprint wins.
2. **Stealth is minimal and in-tree.** One Chromium launch arg (`--disable-blink-features=AutomationControlled`) plus one `add_init_script` that drops `navigator.webdriver`. No third-party stealth library.
3. **Blocks extend backoff; they do not wipe cookies.** Wiping cookies when we're IP-flagged might help; wiping when mid-challenge hurts. Safer default: keep cookies, extend backoff, log loudly. Cookie-wipe is a follow-up beads task, not I2.
4. **"unknown" classifier outcome is pass-through, not block.** A legitimate search with few results is thin but valid — the scraper yields 0 flats and the pipeline moves on. Only `captcha` / `challenge_cloudflare` / `block_keyword` raise `ChallengeDetectedError`.
5. **Detection order in `fetch_new`.** `goto` → `check_rate_limit(status)` → iframe check on live page → `page.content()` → content classification → parse. Iframe detection needs the live `Page` object; content classification is a pure function on the HTML string.
6. **Backoff is per-platform.** Kleinanzeigen cooling off must not pause WG-Gesucht. State lives in a module-level `dict[str, BackoffState]` inside `src/flatpilot/scrapers/backoff.py` — no Typer dependency, trivially testable. In-memory, lives for the process, resets on restart.

---

## File Structure

| Path | Status | Responsibility |
| --- | --- | --- |
| `src/flatpilot/scrapers/ua_pool.py` | **new** | Residential-style UA pool + `pin_user_agent(platform)` with `fingerprint.json` sidecar |
| `src/flatpilot/scrapers/block_detect.py` | **new** | Classifier constants + `has_captcha_iframe(page)` + `classify_content(html, city)` + `ChallengeDetectedError` (re-exported) |
| `src/flatpilot/scrapers/backoff.py` | **new** | `BackoffState`, module-level per-platform dict, `should_skip / on_failure / on_success / reset` |
| `src/flatpilot/scrapers/session.py` | **modify** | Add `stealth: bool = False` to `SessionConfig`; apply launch arg + init script when set. Add `ChallengeDetectedError` exception class. |
| `src/flatpilot/scrapers/kleinanzeigen.py` | **modify** | Wire UA pool, stealth, iframe + content classifier into `fetch_new`. Extract response-handling helper for testability. |
| `src/flatpilot/cli.py` | **modify** | `_run_scrape_pass` consults `backoff.should_skip` before scraping; records `on_failure` / `on_success` and catches `ChallengeDetectedError`. |
| `tests/test_ua_pool.py` | **new** | Pool non-empty, pin-on-first-session, reuse-on-subsequent |
| `tests/test_stealth_config.py` | **new** | SessionConfig emits launch arg + init script path when stealth=True |
| `tests/test_block_detect.py` | **new** | Fixture HTML → "ok"; fabricated challenge/block HTML → right outcome; "einen moment" false-positive guard |
| `tests/test_backoff.py` | **new** | No history → no skip; failure extends delay; success resets; cap; per-platform isolation |
| `tests/test_kleinanzeigen_scraper.py` | **new** | Response-handling helper: fixture HTML → flats; injected challenge HTML → `ChallengeDetectedError`; UA pool consulted |
| `tests/test_pipeline_backoff.py` | **new** | Mock scrapers; pipeline skips when backoff active; resets on success |

---

## Task 1: UA Pool with Per-Session Pinning

**Files:**
- Create: `src/flatpilot/scrapers/ua_pool.py`
- Test: `tests/test_ua_pool.py`

**Goal:** Produce a tiny, deterministic-when-pinned UA source. First call for a platform picks a UA at random from a realistic pool, writes it to `~/.flatpilot/sessions/<platform>/fingerprint.json`, and returns it. Every subsequent call reads that file and returns the same UA as long as the file exists. If `fingerprint.json` is deleted, the next call picks a new one and writes fresh.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ua_pool.py`:

```python
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
from pathlib import Path

import pytest


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ua_pool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flatpilot.scrapers.ua_pool'`.

- [ ] **Step 3: Implement `src/flatpilot/scrapers/ua_pool.py`**

```python
"""Residential-style user-agent pool with per-session pinning.

The Kleinanzeigen scraper (FlatPilot-6hix) uses a small set of realistic
Firefox / Chrome UAs so repeated fresh sessions don't all share a single
fingerprint. Within a *single* cookie jar we must not rotate: presenting
a cookie issued under UA X from UA Y is a stronger bot signal than
staying on one UA. :func:`pin_user_agent` therefore picks a UA at random
on first call for a platform, writes it to a sidecar
``fingerprint.json`` next to ``state.json``, and returns the pinned
value on every subsequent call until that file is deleted.
"""

from __future__ import annotations

import json
import logging
import random

from flatpilot.scrapers.base import session_dir

logger = logging.getLogger(__name__)


# Firefox 121 Linux must stay at index 0 — that's the exact fingerprint
# the D0 probe validated over 4.5 h of polling. Additional entries are
# here so repeated fresh sessions don't all present the same string.
POOL: tuple[str, ...] = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)


def pin_user_agent(platform: str) -> str:
    """Return a persistent UA for ``platform``.

    First call writes ``~/.flatpilot/sessions/<platform>/fingerprint.json``
    and returns the picked value. Subsequent calls read it back. If the
    file is missing or malformed, a new UA is picked and persisted.
    """
    path = session_dir(platform) / "fingerprint.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            ua = payload.get("user_agent")
            if isinstance(ua, str) and ua in POOL:
                return ua
            logger.warning(
                "%s: fingerprint.json has unknown UA %r; re-pinning", platform, ua
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("%s: fingerprint.json unreadable (%s); re-pinning", platform, exc)

    ua = random.choice(POOL)
    path.write_text(json.dumps({"user_agent": ua}))
    logger.info("%s: pinned user-agent fingerprint", platform)
    return ua
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ua_pool.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/scrapers/ua_pool.py tests/test_ua_pool.py
git commit -m "FlatPilot-6hix: add user-agent pool with per-session pinning"
```

---

## Task 2: Stealth Launch Flags in SessionConfig

**Files:**
- Modify: `src/flatpilot/scrapers/session.py`
- Test: `tests/test_stealth_config.py`

**Goal:** Add an opt-in `stealth: bool = False` knob to `SessionConfig`. When set, Chromium launch args include `--disable-blink-features=AutomationControlled` and the `BrowserContext` receives an `add_init_script` that drops `navigator.webdriver`. Existing callers (WG-Gesucht) are untouched.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stealth_config.py`:

```python
"""Unit tests for the stealth knob on SessionConfig.

The stealth option layers two hand-rolled tweaks on top of the existing
session: a launch arg that prevents Chromium from announcing it's
automation-controlled, plus an init script that deletes
navigator.webdriver before any page script sees it. We do not pull
playwright-stealth — clean-room policy plus supply-chain aversion.

The tests drive the config via a Playwright stub that records the
arguments each call receives, so we can assert on the exact shape of
the launch + context + init-script calls without starting a real
browser.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class _FakePage:
    def __init__(self) -> None:
        self.default_timeout: int | None = None

    def set_default_navigation_timeout(self, ms: int) -> None:
        self.default_timeout = ms

    def goto(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def locator(self, *_args: Any, **_kwargs: Any) -> Any:
        class _Null:
            def is_visible(self, **_kw: Any) -> bool:
                return False

            first = property(lambda self: self)

        return _Null()

    def close(self) -> None:
        pass

    def content(self) -> str:
        return ""


class _FakeContext:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []
        self.closed = False
        self.saved_paths: list[str] = []

    def new_page(self) -> _FakePage:
        return _FakePage()

    def add_init_script(self, *, script: str) -> None:
        self.init_scripts.append(script)

    def storage_state(self, *, path: str) -> None:
        self.saved_paths.append(path)

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, launch_args: list[str]) -> None:
        self.launch_args = launch_args
        self.contexts: list[_FakeContext] = []
        self.closed = False

    def new_context(self, **_kwargs: Any) -> _FakeContext:
        ctx = _FakeContext()
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.browsers: list[_FakeBrowser] = []

    def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launches.append(kwargs)
        browser = _FakeBrowser(launch_args=list(kwargs.get("args", [])))
        self.browsers.append(browser)
        return browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()

    def __enter__(self) -> "_FakePlaywright":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> _FakePlaywright:
    fake = _FakePlaywright()

    def _factory() -> _FakePlaywright:
        return fake

    # Patch the module so polite_session's lazy import picks up our stub.
    import playwright.sync_api as pw

    monkeypatch.setattr(pw, "sync_playwright", _factory)
    return fake


def test_stealth_defaults_to_off(tmp_db, fake_playwright: _FakePlaywright) -> None:
    from flatpilot.scrapers.session import SessionConfig, polite_session

    with polite_session(SessionConfig(platform="kleinanzeigen")):
        pass

    launch = fake_playwright.chromium.launches[-1]
    assert "--disable-blink-features=AutomationControlled" not in launch.get("args", [])
    # And no init script was injected on the default path.
    browser = fake_playwright.chromium.browsers[-1]
    assert browser.contexts[-1].init_scripts == []


def test_stealth_adds_automation_flag_and_init_script(
    tmp_db, fake_playwright: _FakePlaywright
) -> None:
    from flatpilot.scrapers.session import SessionConfig, polite_session

    with polite_session(SessionConfig(platform="kleinanzeigen", stealth=True)):
        pass

    launch = fake_playwright.chromium.launches[-1]
    assert "--disable-blink-features=AutomationControlled" in launch["args"]

    # The context saw one add_init_script call with a snippet that drops
    # navigator.webdriver.
    ctx = fake_playwright.chromium.browsers[-1].contexts[-1]
    assert len(ctx.init_scripts) == 1
    snippet = ctx.init_scripts[0]
    assert "navigator" in snippet
    assert "webdriver" in snippet


def test_stealth_preserves_caller_launch_args(
    tmp_db, fake_playwright: _FakePlaywright
) -> None:
    """Interactive flows already pass --start-maximized; stealth must append, not replace."""
    from flatpilot.scrapers.session import SessionConfig, polite_session

    cfg = SessionConfig(
        platform="kleinanzeigen",
        stealth=True,
        launch_args=("--start-maximized",),
    )
    with polite_session(cfg):
        pass

    launch = fake_playwright.chromium.launches[-1]
    assert "--start-maximized" in launch["args"]
    assert "--disable-blink-features=AutomationControlled" in launch["args"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stealth_config.py -v`
Expected: FAIL — `SessionConfig` has no `stealth` argument.

- [ ] **Step 3: Modify `src/flatpilot/scrapers/session.py`**

Patch 1 — add a new exception class next to `RateLimitedError` (inserted directly below it, around line 57):

```python
class ChallengeDetectedError(RuntimeError):
    """Cloudflare / Turnstile / rate-limit text detected in the response body.

    Raised by scrapers when :func:`flatpilot.scrapers.block_detect.classify_content`
    (or a live-page iframe check) signals a challenge or block. The CLI
    pipeline catches this and asks
    :mod:`flatpilot.scrapers.backoff` for a longer cool-off than a plain
    HTTP 429.
    """
```

Patch 2 — add a `stealth: bool = False` field to `SessionConfig` (insert near the other bool fields, right after `no_viewport`):

```python
    # Hand-rolled Chromium stealth: appends
    # --disable-blink-features=AutomationControlled to launch args and
    # injects an init script that deletes navigator.webdriver before
    # any page script runs. Opt-in per platform.
    stealth: bool = False
```

Patch 3 — inside `polite_session`, change the `launch` call and add an init-script call when stealth is on. Locate the block starting at `browser = pw.chromium.launch(` and replace it with:

```python
        launch_args = list(config.launch_args)
        if config.stealth and "--disable-blink-features=AutomationControlled" not in launch_args:
            launch_args.append("--disable-blink-features=AutomationControlled")

        browser = pw.chromium.launch(
            headless=config.headless,
            args=launch_args,
        )
```

Then, inside the `browser.new_context(**context_kwargs)` block — right after `context = browser.new_context(**context_kwargs)` — add:

```python
            if config.stealth:
                # Runs before any page script on every navigation; masks
                # the most common automation tell so Playwright contexts
                # behave like a human's browser to naive bot-check code.
                context.add_init_script(
                    script=(
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined});"
                    )
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_stealth_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full existing suite to ensure nothing broke**

Run: `pytest -q`
Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/scrapers/session.py tests/test_stealth_config.py
git commit -m "FlatPilot-6hix: add opt-in stealth launch flags to polite_session"
```

---

## Task 3: Block / Challenge Classifier

**Files:**
- Create: `src/flatpilot/scrapers/block_detect.py`
- Test: `tests/test_block_detect.py`

**Goal:** One generic classifier module that Kleinanzeigen (and future platforms) can ask: "is this page we just got back a real search result, a Cloudflare challenge, a hard block, or unparseable thin content?" The heuristics port from `scripts/kleinanzeigen_probe.py::_classify` with one material change: the challenge keyword `"einen moment"` gets tightened to `"einen moment, bitte"` because the loose form collides with benign German "just a moment" loading UIs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_block_detect.py`:

```python
"""Unit tests for src/flatpilot/scrapers/block_detect.py.

The classifier is the gatekeeper between "parse these listings" and
"back off, we're flagged." False negatives cost us applied flats; false
positives trigger unnecessary cool-offs and the user never sees
anything. The tests below exercise both directions:

- Real Kleinanzeigen search HTML (tests/fixtures/kleinanzeigen/search.html)
  must classify as "ok" — proves we don't regress a happy path after
  the "einen moment" tightening.
- Fabricated challenge + block HTML exercises each outcome branch.
- The "unknown" outcome stays pass-through — short result pages are
  legitimate, not blocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "kleinanzeigen" / "search.html"


def test_real_search_page_is_ok() -> None:
    """The shipped fixture (from the live site) must not trip any block heuristic."""
    from flatpilot.scrapers.block_detect import classify_content

    html = FIXTURE.read_text()
    assert classify_content(html, city="Berlin") == "ok"


def test_cloudflare_interstitial_classifies_as_challenge() -> None:
    from flatpilot.scrapers.block_detect import classify_content

    html = """<html><body>
      <h1>Just a moment...</h1>
      <p>Checking your browser before accessing the site.</p>
    </body></html>"""
    assert classify_content(html, city="Berlin") == "challenge_cloudflare"


def test_german_cloudflare_interstitial_classifies_as_challenge() -> None:
    """Tightened form: 'Einen Moment, bitte' is the real Cloudflare DE text."""
    from flatpilot.scrapers.block_detect import classify_content

    html = """<html><body>
      <h1>Einen Moment, bitte...</h1>
      <p>Wir überprüfen Ihren Browser.</p>
    </body></html>"""
    assert classify_content(html, city="Berlin") == "challenge_cloudflare"


def test_plain_einen_moment_is_not_a_challenge() -> None:
    """Bare 'einen moment' (without bitte) is common benign loading text."""
    from flatpilot.scrapers.block_detect import classify_content

    html = (
        "<html><body><h1>Berlin Mietwohnungen</h1>"
        "<p>Einen Moment - wir laden weitere Ergebnisse.</p>"
        + "<div>" + ("filler " * 2000) + "</div>"
        + "</body></html>"
    )
    assert classify_content(html, city="Berlin") == "ok"


def test_block_keyword_classifies_as_block() -> None:
    from flatpilot.scrapers.block_detect import classify_content

    for phrase in (
        "Your IP address has been flagged for unusual traffic.",
        "Ungewöhnlichen Datenverkehr festgestellt.",
        "Too many requests from your network.",
        "Access denied — please try again later.",
    ):
        html = f"<html><body>{phrase}</body></html>"
        assert (
            classify_content(html, city="Berlin") == "block_keyword"
        ), f"expected block_keyword for: {phrase!r}"


def test_short_result_page_is_unknown_not_block() -> None:
    """A legitimate search with few results must pass through to the parser."""
    from flatpilot.scrapers.block_detect import classify_content

    html = "<html><body><main>No results for Berlin today.</main></body></html>"
    assert classify_content(html, city="Berlin") == "unknown"


def test_ok_requires_city_in_body() -> None:
    """Large pages that don't mention the city look stale/broken, not blocked."""
    from flatpilot.scrapers.block_detect import classify_content

    html = "<html><body><main>" + ("filler " * 2000) + "</main></body></html>"
    assert classify_content(html, city="Berlin") == "unknown"


def test_has_captcha_iframe_returns_true_when_iframe_present() -> None:
    from flatpilot.scrapers.block_detect import has_captcha_iframe

    class _Locator:
        def __init__(self, count: int) -> None:
            self._count = count

        def count(self) -> int:
            return self._count

    class _Page:
        def __init__(self, hit_selector: str | None) -> None:
            self._hit = hit_selector

        def locator(self, selector: str):
            return _Locator(1 if selector == self._hit else 0)

    assert has_captcha_iframe(_Page("iframe[src*='challenges.cloudflare.com']")) is True
    assert has_captcha_iframe(_Page("iframe[src*='turnstile']")) is True
    assert has_captcha_iframe(_Page("iframe[src*='hcaptcha.com']")) is True
    assert has_captcha_iframe(_Page(None)) is False


def test_challenge_detected_error_is_exported() -> None:
    """block_detect re-exports the canonical exception from session.py."""
    from flatpilot.scrapers.block_detect import ChallengeDetectedError as Re
    from flatpilot.scrapers.session import ChallengeDetectedError as Canon

    assert Re is Canon
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_block_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flatpilot.scrapers.block_detect'`.

- [ ] **Step 3: Implement `src/flatpilot/scrapers/block_detect.py`**

```python
"""Classifier for Playwright responses from anti-bot-fronted sites.

The Kleinanzeigen D0 probe established a small, reliable set of block
heuristics — captcha iframes, Cloudflare interstitial keywords, and
rate-limit phrases. This module exposes them as reusable primitives so
the Kleinanzeigen scraper (and, later, any other Cloudflare-fronted
site) can call one function per check instead of reimplementing the
probe's ``_classify`` method each time.

Outcome vocabulary
------------------
- ``ok``                   — page mentions the target city, body is
                             substantial, no challenge or block signals.
- ``challenge_cloudflare`` — body contains a tight Cloudflare soft
                             challenge phrase.
- ``block_keyword``        — body contains a hard rate-limit phrase.
- ``unknown``              — thin body or no city mention; the scraper
                             treats this as *parse what you've got* and
                             moves on (a legitimate empty search is
                             thin by definition).

Tightening note: the probe included the bare phrase ``einen moment`` in
its challenge keyword list. That phrase appears in benign German
loading-UI strings — ``test_plain_einen_moment_is_not_a_challenge``
nails that down. We match only ``einen moment, bitte`` which is what
Cloudflare's German-locale interstitial actually renders.
"""

from __future__ import annotations

from typing import Any, Literal

from flatpilot.scrapers.session import ChallengeDetectedError

__all__ = [
    "BLOCK_KEYWORDS",
    "CAPTCHA_IFRAME_SELECTORS",
    "CHALLENGE_KEYWORDS",
    "ChallengeDetectedError",
    "MIN_BODY_CHARS",
    "Outcome",
    "classify_content",
    "has_captcha_iframe",
]


Outcome = Literal["ok", "challenge_cloudflare", "block_keyword", "unknown"]


# A real search page has a big rendered body; anything substantially
# smaller is either an empty search or a stub response we shouldn't
# try to parse. Proven out by the D0 probe over 178 ok polls.
MIN_BODY_CHARS: int = 5_000

# Cloudflare + Turnstile serve challenges via these iframe origins.
CAPTCHA_IFRAME_SELECTORS: tuple[str, ...] = (
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='recaptcha']",
)

# Soft Cloudflare interstitial phrases. The tightened "einen moment,
# bitte" is deliberate — "einen moment" alone hits benign loading UI.
CHALLENGE_KEYWORDS: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "einen moment, bitte",
)

# Hard rate-limit / block phrases.
BLOCK_KEYWORDS: tuple[str, ...] = (
    "unusual traffic",
    "ungewöhnlichen datenverkehr",
    "ungewoehnlichen datenverkehr",
    "too many requests",
    "access denied",
)


def has_captcha_iframe(page: Any) -> bool:
    """Return ``True`` if the live ``Page`` contains a Turnstile / captcha iframe.

    Must be called *before* ``page.content()`` because some challenge
    iframes are stripped from the serialised HTML by Cloudflare.
    """
    for selector in CAPTCHA_IFRAME_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            # Locator lookup can throw on a navigation-in-progress page;
            # treat as no-match rather than propagating.
            continue
    return False


def classify_content(html: str, city: str) -> Outcome:
    """Pure-function classifier over a response body.

    ``city`` is folded to lowercase and checked against the body; a
    genuine search page always echoes the requested city name in its
    header, breadcrumbs, or result summary.
    """
    body = (html or "").lower()
    if any(kw in body for kw in CHALLENGE_KEYWORDS):
        return "challenge_cloudflare"
    if any(kw in body for kw in BLOCK_KEYWORDS):
        return "block_keyword"
    if len(body) >= MIN_BODY_CHARS and city.lower() in body:
        return "ok"
    return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_block_detect.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/scrapers/block_detect.py tests/test_block_detect.py
git commit -m "FlatPilot-6hix: add challenge/block classifier with tightened keywords"
```

---

## Task 4: Per-Platform Adaptive Backoff

**Files:**
- Create: `src/flatpilot/scrapers/backoff.py`
- Test: `tests/test_backoff.py`

**Goal:** In-memory per-platform exponential backoff so the CLI `run --watch` loop skips a platform until its cool-off expires. Rate-limit failures use a short ladder (60 s → 30 min cap); challenge failures use a longer one (10 min → 60 min cap). Success resets. One `dict[str, BackoffState]` at module scope, with `reset()` for tests. No Typer, no clock coupling — the caller passes `now`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backoff.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backoff.py -v`
Expected: FAIL — module does not exist yet.

- [ ] **Step 3: Implement `src/flatpilot/scrapers/backoff.py`**

```python
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
from dataclasses import dataclass, field
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
    ladder = _ladder_for(kind)
    idx = min(consecutive - 1, len(ladder) - 1)
    return ladder[idx]


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backoff.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/flatpilot/scrapers/backoff.py tests/test_backoff.py
git commit -m "FlatPilot-6hix: add per-platform exponential backoff"
```

---

## Task 5: Wire Anti-Bot Hardening into the Kleinanzeigen Scraper

**Files:**
- Modify: `src/flatpilot/scrapers/kleinanzeigen.py`
- Test: `tests/test_kleinanzeigen_scraper.py`

**Goal:** Rework `KleinanzeigenScraper.fetch_new` to: (1) pull the UA from `ua_pool.pin_user_agent`, (2) set `stealth=True` on `SessionConfig`, (3) after `page.goto` and the existing `check_rate_limit(status)` call, run `has_captcha_iframe(page)` → if true raise `ChallengeDetectedError`, then `classify_content(html, city)` → raise `ChallengeDetectedError` on `challenge_cloudflare` or `block_keyword`; pass `ok` and `unknown` through to `parse_listings`.

Extract the post-goto handling into a private helper `_handle_response(page, city)` that returns the HTML string (or raises). That makes the scraper trivially testable without Playwright: tests construct a fake page and call the helper directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kleinanzeigen_scraper.py`:

```python
"""Integration tests for the Kleinanzeigen scraper's anti-bot wiring.

These tests cover the thin layer between ``fetch_new`` and the
session / classifier primitives. The parsing path is already covered
separately by ``parse_listings`` usage in PR #20; here we verify:

- _handle_response with fixture HTML returns the body unchanged.
- _handle_response with an injected captcha iframe raises
  ChallengeDetectedError before calling content().
- _handle_response with a Cloudflare challenge body raises.
- _handle_response with a block keyword body raises.
- _handle_response with an "unknown" outcome returns the body
  (lets parse_listings yield 0 flats — a valid empty pass).
- KleinanzeigenScraper.user_agent still resolves from the UA pool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "kleinanzeigen" / "search.html"


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, *, html: str, iframes: tuple[str, ...] = ()) -> None:
        self._html = html
        self._iframes = iframes
        self.content_calls = 0

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(1 if selector in self._iframes else 0)

    def content(self) -> str:
        self.content_calls += 1
        return self._html


def test_handle_response_returns_body_for_real_search_page() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response

    html = FIXTURE.read_text()
    page = _FakePage(html=html)
    assert _handle_response(page, city="Berlin") == html
    assert page.content_calls == 1


def test_handle_response_raises_on_captcha_iframe_before_content() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response
    from flatpilot.scrapers.session import ChallengeDetectedError

    page = _FakePage(
        html="ignored",
        iframes=("iframe[src*='challenges.cloudflare.com']",),
    )
    with pytest.raises(ChallengeDetectedError):
        _handle_response(page, city="Berlin")
    assert page.content_calls == 0  # iframe check fires before content()


def test_handle_response_raises_on_cloudflare_interstitial() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response
    from flatpilot.scrapers.session import ChallengeDetectedError

    html = "<html><body><h1>Just a moment...</h1></body></html>"
    with pytest.raises(ChallengeDetectedError):
        _handle_response(_FakePage(html=html), city="Berlin")


def test_handle_response_raises_on_block_keyword() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response
    from flatpilot.scrapers.session import ChallengeDetectedError

    html = "<html><body>Access denied.</body></html>"
    with pytest.raises(ChallengeDetectedError):
        _handle_response(_FakePage(html=html), city="Berlin")


def test_handle_response_passes_through_unknown() -> None:
    from flatpilot.scrapers.kleinanzeigen import _handle_response

    html = "<html><body>empty page</body></html>"
    assert _handle_response(_FakePage(html=html), city="Berlin") == html


def test_scraper_user_agent_comes_from_pool(tmp_db) -> None:
    from flatpilot.scrapers.kleinanzeigen import KleinanzeigenScraper
    from flatpilot.scrapers.ua_pool import POOL

    scraper = KleinanzeigenScraper()
    assert scraper.resolve_user_agent() in POOL


def test_fetch_new_uses_pinned_ua_and_stealth(tmp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_new hands a stealth-enabled SessionConfig with the pinned UA to polite_session."""
    from flatpilot.profile import Profile
    from flatpilot.scrapers import kleinanzeigen as kz
    from flatpilot.scrapers.ua_pool import pin_user_agent

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
                def goto(self, *_a: Any, **_kw: Any) -> Any:
                    class _R:
                        status = 200

                    return _R()

                def locator(self, _s: str) -> Any:
                    class _L:
                        def count(self) -> int:
                            return 0

                    return _L()

                def content(self) -> str:
                    return FIXTURE.read_text()

            return _P()

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(kz, "polite_session", _FakeCtxMgr)
    monkeypatch.setattr(kz, "session_page", _FakePageCtxMgr)

    # Profile has ~15 required fields; load the shipped example and flip
    # the city to Berlin (the only entry in Kleinanzeigen CITY_IDS).
    # Constructing a Profile inline is fragile — if the schema grows a
    # field, every test breaks (PR #22 lesson).
    base = Profile.load_example()
    profile = base.model_copy(update={"city": "Berlin"})

    pinned = pin_user_agent("kleinanzeigen")
    scraper = kz.KleinanzeigenScraper()
    list(scraper.fetch_new(profile))  # drain generator

    cfg = captured["config"]
    assert cfg.platform == "kleinanzeigen"
    assert cfg.user_agent == pinned
    assert cfg.stealth is True
```

Note on `Profile`: it has ~15 required fields (see `src/flatpilot/profile.py`) including `rent_min_warm` / `rent_max_warm`, `status`, `net_income_eur`, `move_in_date`, and more. **Always build test profiles from `Profile.load_example()` + `model_copy(update=...)`** — never inline `model_validate({...})`, which breaks the moment the schema grows a field. This is the PR #22 lesson.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_kleinanzeigen_scraper.py -v`
Expected: FAIL — `_handle_response` and `resolve_user_agent` don't exist.

- [ ] **Step 3: Modify `src/flatpilot/scrapers/kleinanzeigen.py`**

First, read the current file (it's reproduced above in the context) and apply these edits:

(a) Update the module imports to add UA pool + block detect:

```python
from flatpilot.scrapers.block_detect import (
    ChallengeDetectedError,
    classify_content,
    has_captcha_iframe,
)
from flatpilot.scrapers.session import (
    DEFAULT_USER_AGENT,
    SessionConfig,
    check_rate_limit,
    polite_session,
)
from flatpilot.scrapers.session import (
    page as session_page,
)
from flatpilot.scrapers.ua_pool import pin_user_agent
```

Drop the now-unused `DEFAULT_USER_AGENT` import if nothing else uses it in the file. (It still appears as the classvar default for `user_agent`; see below.)

(b) Replace the `KleinanzeigenScraper` class body with:

```python
@register
class KleinanzeigenScraper:
    platform: ClassVar[str] = "kleinanzeigen"
    # Kept for protocol compatibility. The actual UA used per call is
    # picked from the pool via resolve_user_agent() so repeated fresh
    # sessions don't all share one fingerprint.
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT

    def resolve_user_agent(self) -> str:
        return pin_user_agent(self.platform)

    def fetch_new(self, profile: Profile) -> Iterable[Flat]:
        loc_id = CITY_IDS.get(profile.city)
        if loc_id is None:
            raise UnknownCityError(
                f"{self.platform}: no location_id for {profile.city!r}; "
                f"extend CITY_IDS in {__name__}"
            )

        url = self._search_url(profile.city, loc_id, profile.radius_km)
        config = SessionConfig(
            platform=self.platform,
            user_agent=self.resolve_user_agent(),
            warmup_url=WARMUP_URL,
            consent_selectors=CONSENT_SELECTORS,
            stealth=True,
        )

        logger.info("%s: fetching %s", self.platform, url)
        with polite_session(config) as context, session_page(context) as pg:
            response = pg.goto(url, wait_until="domcontentloaded")
            if response is None:
                logger.warning("%s: null response from %s", self.platform, url)
                return
            check_rate_limit(response.status, self.platform)
            if response.status >= 400:
                logger.warning(
                    "%s: search returned HTTP %d", self.platform, response.status
                )
                return
            html = _handle_response(pg, city=profile.city)

        flats = list(parse_listings(html))
        logger.info(
            "%s: parsed %d listings from %s", self.platform, len(flats), profile.city
        )
        yield from flats

    @staticmethod
    def _search_url(city: str, loc_id: int, radius_km: int | None) -> str:
        slug = city.strip().lower().replace(" ", "-")
        suffix = f"r{radius_km}" if radius_km and radius_km > 0 else ""
        return f"{HOST}/s-wohnung-mieten/{slug}/c203l{loc_id}{suffix}"
```

(c) Add `_handle_response` as a module-level function, immediately below the class (so it's importable by tests):

```python
def _handle_response(page: Any, *, city: str) -> str:
    """Classify the current ``page`` and return its HTML on ok/unknown.

    Raises :class:`ChallengeDetectedError` on a captcha iframe,
    Cloudflare soft challenge, or hard block keyword. The ``unknown``
    classifier outcome — thin body or city not mentioned — is
    deliberately passed through: a legitimate empty search is thin by
    definition and must not trigger a cool-off.
    """
    if has_captcha_iframe(page):
        raise ChallengeDetectedError(f"kleinanzeigen: captcha iframe present for {city}")

    html = page.content()
    outcome = classify_content(html, city=city)
    if outcome in ("challenge_cloudflare", "block_keyword"):
        raise ChallengeDetectedError(f"kleinanzeigen: {outcome} detected for {city}")
    # ok and unknown both return the HTML; parse_listings yields zero
    # on an empty page without error.
    return html
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_kleinanzeigen_scraper.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/scrapers/kleinanzeigen.py tests/test_kleinanzeigen_scraper.py
git commit -m "FlatPilot-6hix: wire UA pool, stealth, and block detection into Kleinanzeigen"
```

---

## Task 6: Wire Backoff into the CLI Pipeline

**Files:**
- Modify: `src/flatpilot/cli.py` (the `_run_scrape_pass` function around line 303)
- Test: `tests/test_pipeline_backoff.py`

**Goal:** `_run_scrape_pass` must (1) ask `backoff.should_skip(platform)` before calling `fetch_new`, skipping + logging remaining seconds if blocked, (2) call `backoff.on_failure(platform, "rate_limit")` when `RateLimitedError` fires, (3) call `backoff.on_failure(platform, "challenge")` when `ChallengeDetectedError` fires, (4) call `backoff.on_success(platform)` after a successful pass. Leave the generic `except Exception` branch alone — unknown errors don't feed the backoff ladder (they're bugs, not site signals).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline_backoff.py`:

```python
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
    remaining = (st.skip_until - __import__("datetime").datetime.now(__import__("datetime").UTC)).total_seconds()
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_backoff.py -v`
Expected: FAIL — the pipeline does not know about `backoff` or `ChallengeDetectedError` yet.

- [ ] **Step 3: Modify `src/flatpilot/cli.py::_run_scrape_pass`**

Replace the body of `_run_scrape_pass` (currently lines 303-331) with:

```python
def _run_scrape_pass(scrapers: list, profile, console) -> None:
    from datetime import datetime

    from flatpilot.database import get_conn
    from flatpilot.scrapers import backoff
    from flatpilot.scrapers.session import ChallengeDetectedError, RateLimitedError

    conn = get_conn()
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    for scraper in scrapers:
        plat = scraper.platform
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline_backoff.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: every test in the suite passes.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/cli.py tests/test_pipeline_backoff.py
git commit -m "FlatPilot-6hix: backoff on rate limits and anti-bot challenges in CLI loop"
```

---

## Final Checks Before Opening the PR

- [ ] **Run the full suite once more**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Lint (if configured)**

Run: `ruff check src/flatpilot/scrapers src/flatpilot/cli.py tests`
Expected: no errors (if `ruff` is configured — check `pyproject.toml`).

- [ ] **Smoke-verify the scraper still works end-to-end**

If a working Kleinanzeigen cookie jar exists on the dev host, run:

```bash
docker compose run --rm --entrypoint flatpilot flatpilot scrape --platform kleinanzeigen
```

Expected: a handful of listings parsed; no `ChallengeDetectedError`. If the host has no cookies, document in the PR that smoke was deferred.

- [ ] **Open the PR**

```bash
git push -u origin feat/i2-kleinanzeigen-anti-bot
gh pr create --base main --head feat/i2-kleinanzeigen-anti-bot --title "FlatPilot-6hix: I2. Kleinanzeigen anti-bot + rate limit" --body-file <(printf "%s\n" "## Summary" "- Adds residential-style UA pool with per-session pinning (\`ua_pool.py\`)." "- Adds opt-in stealth launch flags to \`polite_session\` (hand-rolled, no \`playwright-stealth\` dependency)." "- Adds a block / challenge classifier with tightened \`einen moment, bitte\` keyword and a pure-function API (\`block_detect.py\`)." "- Adds per-platform adaptive backoff with separate ladders for HTTP rate limits and Cloudflare challenges (\`backoff.py\`)." "- Wires all of the above into \`KleinanzeigenScraper.fetch_new\` and \`cli._run_scrape_pass\`." "" "## Framing" "This is **preemptive hardening**, not a response to observed blocks: the D0 probe on FlatPilot-3hu2 logged 178/178 ok polls over 4.5 h at 90 s cadence. The scraper currently relies on a single hard-coded UA and has no challenge detection, so we're closing that gap before it bites us." "" "## Closes" "- FlatPilot-6hix (this task) — I2. Anti-bot handling + rate limit." "- FlatPilot-agw1 — Epic I. Kleinanzeigen scraper (Phase 2), now that I1 and I2 are both landed." "" "## Test plan" "- [x] \`pytest tests/test_ua_pool.py -v\` (pool + pin + sidecar JSON)." "- [x] \`pytest tests/test_stealth_config.py -v\` (launch arg + init script)." "- [x] \`pytest tests/test_block_detect.py -v\` (fixture HTML = ok; tightened keyword; all outcome branches)." "- [x] \`pytest tests/test_backoff.py -v\` (ladder, cap, reset, platform isolation)." "- [x] \`pytest tests/test_kleinanzeigen_scraper.py -v\` (iframe short-circuit; unknown pass-through; UA plumbing)." "- [x] \`pytest tests/test_pipeline_backoff.py -v\` (skip → record failure → skip → success → reset)." "- [x] Full suite: \`pytest -q\`.")
```

Return the PR URL and stop.

---

## Self-Review Checklist (performed while writing)

- **Spec coverage:** All four items in the task description — stealth, residential UA pool, captcha-backoff, cookie jar path — map to tasks (2, 1, 3+4+6, already-done). Confirmed.
- **Placeholder scan:** No `TBD`, `similar to`, or `handle edge cases` without actual code. Each task ships with real code.
- **Type consistency:** `FailureKind` literal matches the strings passed in tasks 4 and 6. `Outcome` literal matches strings checked in tasks 3 and 5. `BackoffState` fields are read consistently between `should_skip` and tests.
- **Naming:** `has_captcha_iframe`, `classify_content`, `pin_user_agent`, `should_skip`, `on_failure`, `on_success`, `reset` — same names throughout.
