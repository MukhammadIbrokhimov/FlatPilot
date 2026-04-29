# Apply Server Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four PR #24/#25 carry-overs that harden the dashboard server's Apply path: hoist duplicated exceptions into a shared module, move `init_db()` out of per-request handlers, add a subprocess timeout to `_spawn_apply`, and add a server-side concurrency lock that fails fast on duplicate in-flight applies for the same flat.

**Architecture:** All four are localized changes inside `src/flatpilot/server.py` plus a new `src/flatpilot/errors.py` and import shuffles across four files (`apply.py`, `matcher/runner.py`, `scrapers/wg_gesucht.py`, `scrapers/kleinanzeigen.py`, `cli.py`). The concurrency lock lives in the **server module** (not in `apply.py`) because the dashboard's Apply path forks a subprocess per request — a `threading.Lock` inside `apply_to_flat` would be in a different process from the racer. A module-level `threading.Lock` + `set[int]` of in-flight `flat_id`s in `server.py` serializes the two server threads BEFORE either subprocess is spawned. The CLI-vs-dashboard cross-process race (rare) remains documented as out of scope.

**Tech Stack:** Python 3.11+, stdlib `subprocess`/`threading`/`http.server.ThreadingHTTPServer`, pytest, ruff. No new dependencies.

**Bead-to-task mapping:**

| Task | Bead | Priority | Theme |
|---|---|---|---|
| 1 | FlatPilot-eps | P3 | Hoist `ProfileMissingError` + `UnknownCityError` to `flatpilot.errors` |
| 2 | FlatPilot-r7y | P3 | Move `init_db()` out of `_handle_skip` / `_handle_response` to `serve()` startup |
| 3 | FlatPilot-2yu | P2 | Add `subprocess.run(timeout=…)` + `TimeoutExpired` handling to `_spawn_apply` |
| 4 | FlatPilot-cy9 | P2 | Server-side `threading.Lock` + in-flight set returning 409 on concurrent dup |

**Order rationale:** Refactor first (Task 1 — exceptions hoist) gives a clean baseline. Task 2 (init_db hoist) is independent and small. Tasks 3 (timeout) and 4 (concurrency lock) both touch `_handle_apply` / `_spawn_apply`; doing 3 first means Task 4's tests can rely on a well-behaved spawn helper.

**Hard rules (verify each one):**
- Every commit message starts with the bead ID: `<BeadID>: <what>`.
- Commit author: `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. **No AI co-author trailers.**
- Use `.venv/bin/pytest` and `.venv/bin/ruff` — system pytest/ruff lack deps.
- Never `--no-verify`. The pre-commit hook auto-stages `.beads/issues.jsonl` — let it ride.
- TDD red-test integrity: if a "should fail" run actually passes, **STOP and report**. Never soften the test.
- Test fixtures: when a `Profile` is needed, use `Profile.load_example().model_copy(update={...})` — never `Profile.model_validate({...})`. For `Attachments`, use `Attachments(default=[...], per_platform={})`.
- Don't soften `Protocol` contracts to make a test pass. Fix the test, not the contract.
- After each task: run `.venv/bin/pytest -q` (full suite) and `.venv/bin/ruff check .` to confirm no regressions.

---

## File Structure

**New files:**
- `src/flatpilot/errors.py` — single source of truth for `ProfileMissingError` (raised when `apply_to_flat` or `run_match` runs before `flatpilot init`) and `UnknownCityError` (raised when a scraper's CITY_IDS lookup misses for the configured profile city).
- `tests/test_errors.py` — asserts the new module exists and the existing concrete classes alias to it (single-source-of-truth check).

**Modified files:**
- `src/flatpilot/apply.py` — delete local `class ProfileMissingError`; import from `flatpilot.errors`.
- `src/flatpilot/matcher/runner.py` — delete local `class ProfileMissingError`; import from `flatpilot.errors`.
- `src/flatpilot/scrapers/wg_gesucht.py` — delete local `class UnknownCityError`; import from `flatpilot.errors`.
- `src/flatpilot/scrapers/kleinanzeigen.py` — delete local `class UnknownCityError`; import from `flatpilot.errors`.
- `src/flatpilot/cli.py` — update `from flatpilot.apply import ProfileMissingError` → `from flatpilot.errors`. Same for `from flatpilot.matcher.runner import ProfileMissingError`.
- `src/flatpilot/server.py` — (a) move per-handler `init_db()` calls into `serve()`; (b) add subprocess timeout to `_spawn_apply`; (c) add module-level `_inflight_lock` + `_inflight_flats` set + `APPLY_INFLIGHT_HTTP_STATUS = 409` constant; (d) wrap `_spawn_apply` invocation in `_handle_apply` with claim/release pattern.
- `tests/test_apply_cli.py` — update `from flatpilot.apply import ApplyOutcome, ProfileMissingError` → split into apply (ApplyOutcome) and errors (ProfileMissingError).
- `tests/test_apply_orchestrator.py` — update `from flatpilot.apply import ProfileMissingError` to `from flatpilot.errors import ProfileMissingError`.
- `tests/test_server.py` — add three new tests (init_db hoist verification, subprocess timeout, concurrent-apply 409).

---

## Task 1: Hoist `ProfileMissingError` + `UnknownCityError` into `flatpilot.errors` (FlatPilot-eps)

**Why this task:** `ProfileMissingError` is currently defined twice (`src/flatpilot/apply.py:46` and `src/flatpilot/matcher/runner.py:36`) — two distinct exception classes with the same name and same purpose. `UnknownCityError` is also defined twice (`src/flatpilot/scrapers/wg_gesucht.py:96` and `src/flatpilot/scrapers/kleinanzeigen.py:89`). Future scrapers (Phase 4 ImmoScout RSS) will need a third copy if not consolidated. This task creates `src/flatpilot/errors.py` as the single source of truth.

**Bead:** `FlatPilot-eps`

**Files:**
- Create: `src/flatpilot/errors.py`
- Create: `tests/test_errors.py`
- Modify: `src/flatpilot/apply.py:46-47` (delete class, add import)
- Modify: `src/flatpilot/matcher/runner.py:36-37` (delete class, add import)
- Modify: `src/flatpilot/scrapers/wg_gesucht.py:96-97` (delete class, add import)
- Modify: `src/flatpilot/scrapers/kleinanzeigen.py:89-90` (delete class, add import)
- Modify: `src/flatpilot/cli.py:18-23` and `src/flatpilot/cli.py:429`
- Modify: `tests/test_apply_cli.py:10`
- Modify: `tests/test_apply_orchestrator.py:249`

- [ ] **Step 1: Write the failing red-test for the consolidation invariant**

Create `tests/test_errors.py`:

```python
"""Single-source-of-truth checks for shared exceptions."""

from __future__ import annotations


def test_profile_missing_error_is_single_class():
    """apply.py and matcher/runner.py must reference the same exception class.

    Pre-fix they were two unrelated `class ProfileMissingError(RuntimeError)`
    definitions; an `except ProfileMissingError` block bound to one wouldn't
    catch instances of the other.
    """
    from flatpilot import errors
    from flatpilot.apply import ProfileMissingError as ApplyPME
    from flatpilot.matcher.runner import ProfileMissingError as MatcherPME

    assert ApplyPME is errors.ProfileMissingError
    assert MatcherPME is errors.ProfileMissingError


def test_unknown_city_error_is_single_class():
    """Both scrapers must reference the same exception class.

    Pre-fix wg_gesucht.py and kleinanzeigen.py defined separate classes,
    so a caller couldn't write one `except UnknownCityError` block to
    cover both scrapers.
    """
    from flatpilot import errors
    from flatpilot.scrapers.kleinanzeigen import UnknownCityError as KleinUCE
    from flatpilot.scrapers.wg_gesucht import UnknownCityError as WgUCE

    assert WgUCE is errors.UnknownCityError
    assert KleinUCE is errors.UnknownCityError


def test_profile_missing_error_subclasses_runtime_error():
    """Existing call sites raise/catch ``RuntimeError`` semantics; preserve."""
    from flatpilot.errors import ProfileMissingError

    assert issubclass(ProfileMissingError, RuntimeError)


def test_unknown_city_error_subclasses_value_error():
    """Existing call sites use ValueError semantics; preserve."""
    from flatpilot.errors import UnknownCityError

    assert issubclass(UnknownCityError, ValueError)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_errors.py -v`

Expected: All four tests fail with `ModuleNotFoundError: No module named 'flatpilot.errors'`. **Observe** the failure mode — it must be the missing module, not e.g. an unrelated pytest import error. If you see a different failure, stop and investigate.

- [ ] **Step 3: Create `src/flatpilot/errors.py`**

```python
"""Cross-module exception classes.

Centralised here so different layers raising/catching the same conceptual
error all reference one class object — an ``except ProfileMissingError``
block bound to ``flatpilot.errors`` catches whether the raise came from
``apply_to_flat`` or ``run_match``.

Add new shared exceptions here when more than one module needs to
raise/catch the same conceptual error. Don't dump every project
exception in this module — module-local ones (e.g. ``FillError`` inside
fillers/) stay where they are used.
"""

from __future__ import annotations


class ProfileMissingError(RuntimeError):
    """Raised when an entry point runs before ``flatpilot init``.

    Both ``apply_to_flat`` and ``run_match`` short-circuit when
    ``load_profile()`` returns ``None`` — they need a profile to do
    anything useful, and a missing profile is user-correctable.
    """


class UnknownCityError(ValueError):
    """Raised by a scraper when ``profile.city`` has no platform city ID mapped.

    Each scraper keeps its own ``CITY_IDS`` table (the ID format is
    platform-specific) but they all raise this when the lookup misses,
    so the orchestrator can render one consistent error message.
    """
```

- [ ] **Step 4: Update `src/flatpilot/apply.py`**

Replace the local class definition. Find lines 46-47:

```python
class ProfileMissingError(RuntimeError):
    """Raised when ``apply_to_flat`` runs before ``flatpilot init``."""
```

Delete those lines. Add to the imports block (the existing block ending around line 41):

```python
from flatpilot.errors import ProfileMissingError
```

The exact placement: insert this import alphabetically among the existing `from flatpilot.…` imports near the top (after `from flatpilot.database import …`).

- [ ] **Step 5: Update `src/flatpilot/matcher/runner.py`**

Find lines 36-37:

```python
class ProfileMissingError(RuntimeError):
    """Raised when ``flatpilot match`` runs before ``flatpilot init``."""
```

Delete those lines. Add an import among the existing `flatpilot.…` imports:

```python
from flatpilot.errors import ProfileMissingError
```

- [ ] **Step 6: Update `src/flatpilot/scrapers/wg_gesucht.py`**

Find lines 96-97:

```python
class UnknownCityError(ValueError):
    """Profile city has no WG-Gesucht city_id mapped."""
```

Delete those lines. Add to the imports near the top of the file:

```python
from flatpilot.errors import UnknownCityError
```

- [ ] **Step 7: Update `src/flatpilot/scrapers/kleinanzeigen.py`**

Find lines 89-90:

```python
class UnknownCityError(ValueError):
    """Profile city has no Kleinanzeigen location ID mapped."""
```

Delete those lines. Add to the imports near the top of the file:

```python
from flatpilot.errors import UnknownCityError
```

The `# profile.city lookups that miss this table raise UnknownCityError so we` comment block at line 67 can stay — it documents semantics, not the location of the class.

- [ ] **Step 8: Update `src/flatpilot/cli.py`**

Two import sites need updating.

**Site 1 (lines 18-23):**

```python
from flatpilot.apply import (
    AlreadyAppliedError,
    ApplyOutcome,
    ProfileMissingError,
    apply_to_flat,
)
```

Change to:

```python
from flatpilot.apply import (
    AlreadyAppliedError,
    ApplyOutcome,
    apply_to_flat,
)
from flatpilot.errors import ProfileMissingError
```

**Site 2 (line 429, inside the `match` command):**

```python
    from flatpilot.matcher.runner import ProfileMissingError, run_match
```

Change to:

```python
    from flatpilot.errors import ProfileMissingError
    from flatpilot.matcher.runner import run_match
```

- [ ] **Step 9: Update test imports**

**`tests/test_apply_cli.py` line 10:**

```python
from flatpilot.apply import ApplyOutcome, ProfileMissingError
```

Change to:

```python
from flatpilot.apply import ApplyOutcome
from flatpilot.errors import ProfileMissingError
```

**`tests/test_apply_orchestrator.py` line 249** (inside `test_apply_no_profile_raises`):

```python
    from flatpilot.apply import ProfileMissingError
```

Change to:

```python
    from flatpilot.errors import ProfileMissingError
```

- [ ] **Step 10: Verify nothing else still imports the old names**

Run:

```bash
grep -rn "from flatpilot.apply import.*ProfileMissingError\|from flatpilot.matcher.runner import.*ProfileMissingError\|from flatpilot.scrapers.wg_gesucht import.*UnknownCityError\|from flatpilot.scrapers.kleinanzeigen import.*UnknownCityError" src tests
```

Expected: no output. If any line is reported, update that file too.

- [ ] **Step 11: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass, including the four new tests in `tests/test_errors.py`. The new red-tests turn green; existing tests in `test_apply_cli.py` and `test_apply_orchestrator.py` still pass with the rebound import.

- [ ] **Step 12: Run ruff**

Run: `.venv/bin/ruff check .`

Expected: no errors. (If ruff complains about an unused import in `apply.py` for `RuntimeError` etc, fix the specific complaint — don't add `# noqa`.)

- [ ] **Step 13: Commit**

```bash
git add src/flatpilot/errors.py src/flatpilot/apply.py src/flatpilot/matcher/runner.py src/flatpilot/scrapers/wg_gesucht.py src/flatpilot/scrapers/kleinanzeigen.py src/flatpilot/cli.py tests/test_errors.py tests/test_apply_cli.py tests/test_apply_orchestrator.py
git commit -m "FlatPilot-eps: hoist ProfileMissingError and UnknownCityError to flatpilot.errors"
```

Confirm `git log -1` shows author `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>` and no `Co-Authored-By` trailer. If a different author is committed, **stop** — fix the local git config (`git config user.email ibrohimovmuhammad2020@gmail.com && git config user.name "Mukhammad Ibrokhimov"`) and use `git commit --amend --reset-author` only on this just-made local commit.

---

## Task 2: Hoist `init_db()` out of per-request server handlers (FlatPilot-r7y)

**Why this task:** `src/flatpilot/server.py` calls `init_db()` inside `_handle_skip` (line 154) and `_handle_response` (line 175), so the database is initialised on **every** POST that mutates state. `database.init_db()` is idempotent and reasonably cheap, but per-request init is wasted work and obscures the lifecycle. Moving the call into `serve()` (the entry point that creates the HTTPServer) means it runs once at startup. Secondary benefit: the server now fails fast at startup if the SQLite path is broken instead of failing on the first user click.

**Bead:** `FlatPilot-r7y`

**Files:**
- Modify: `src/flatpilot/server.py:154` (delete `init_db()` call)
- Modify: `src/flatpilot/server.py:175` (delete `init_db()` call)
- Modify: `src/flatpilot/server.py:211-235` (call `init_db()` once inside `serve()` before returning the bound server)
- Modify: `tests/test_server.py` (add new test asserting startup-once behaviour)

- [ ] **Step 1: Write the failing red-test**

Append to `tests/test_server.py` (place it after the existing `test_get_unknown_path_returns_404` and before `_post`):

```python
def test_init_db_runs_once_at_serve_startup_not_per_request(tmp_db):
    """init_db must be called once when serve() binds, not per POST request.

    Pre-fix _handle_skip and _handle_response each called init_db, so a
    sequence of N writing requests called init_db N times. The hoist
    moves the call into serve() startup so it runs exactly once.
    """
    from unittest.mock import patch

    _seed_match_with_profile(tmp_db)
    app_id = _seed_application(tmp_db)

    # Patch the bound name `flatpilot.server.init_db` (server.py imports it
    # `from flatpilot.database import ..., init_db`, so this is the reference
    # the per-request handlers and serve() both reach).
    with patch("flatpilot.server.init_db") as mock_init_db:
        # serve() is what we expect to call init_db once at startup.
        from flatpilot.server import serve

        server, port = serve(host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # One skip POST + one response POST — both used to call init_db.
            match_id = tmp_db.execute(
                "SELECT id FROM matches LIMIT 1"
            ).fetchone()[0]
            _post(f"http://127.0.0.1:{port}/api/matches/{match_id}/skip")
            _post(
                f"http://127.0.0.1:{port}/api/applications/{app_id}/response",
                body=json.dumps(
                    {"status": "rejected", "response_text": ""}
                ).encode("utf-8"),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    # serve() should have called init_db exactly once during startup.
    # Per-request handlers must no longer call it.
    assert mock_init_db.call_count == 1, (
        f"expected init_db to be called once at serve() startup, "
        f"got {mock_init_db.call_count} call(s)"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_server.py::test_init_db_runs_once_at_serve_startup_not_per_request -v`

Expected: FAIL with `assert mock_init_db.call_count == 1` — `call_count` is **2** pre-fix (one per POST that hits `_handle_skip` / `_handle_response`; `serve()` doesn't call it yet).

If `call_count` is 0 (neither path called), check the patch path is correct — `flatpilot.server.init_db` must be a name imported at module level. If `call_count` is 1 already, **STOP** — the test is misreading the code; investigate before softening.

- [ ] **Step 3: Update `src/flatpilot/server.py` — `_handle_skip`**

Find this block around line 146-161:

```python
    def _handle_skip(self, match_id: int) -> None:
        profile = load_profile()
        if profile is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "no profile — run `flatpilot init` first"},
            )
            return
        init_db()
        conn = get_conn()
        try:
            record_skip(conn, match_id=match_id, profile_hash=profile_hash(profile))
        except LookupError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "match_id": match_id})
```

Delete the `init_db()` line so it becomes:

```python
    def _handle_skip(self, match_id: int) -> None:
        profile = load_profile()
        if profile is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "no profile — run `flatpilot init` first"},
            )
            return
        conn = get_conn()
        try:
            record_skip(conn, match_id=match_id, profile_hash=profile_hash(profile))
        except LookupError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "match_id": match_id})
```

- [ ] **Step 4: Update `src/flatpilot/server.py` — `_handle_response`**

Find the `init_db()` call around line 175 (inside `_handle_response`, just before `conn = get_conn()`). Delete the line. The rest of the function stays the same.

- [ ] **Step 5: Update `src/flatpilot/server.py` — `serve()`**

Current `serve()` (lines 211-235):

```python
def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> tuple[ThreadingHTTPServer, int]:
    """Bind and return the server (without starting its loop).

    Caller runs ``server.serve_forever()`` (blocks) and ``server.shutdown()``
    + ``server.server_close()`` for teardown. Returns the actually-bound
    port — useful when ``port=0`` was requested.
    """
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if port == DEFAULT_PORT:
            # Default port busy; fall back to ephemeral so dev can iterate.
            logger.warning(
                "port %d is in use (%s) — falling back to ephemeral port",
                port,
                exc,
            )
            server = ThreadingHTTPServer((host, 0), DashboardHandler)
        else:
            raise
    bound_port = server.server_address[1]
    return server, bound_port
```

Add an `init_db()` call at the top of the function body, before the bind. Updated:

```python
def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> tuple[ThreadingHTTPServer, int]:
    """Bind and return the server (without starting its loop).

    Caller runs ``server.serve_forever()`` (blocks) and ``server.shutdown()``
    + ``server.server_close()`` for teardown. Returns the actually-bound
    port — useful when ``port=0`` was requested.

    init_db() runs once here at startup so per-request handlers can
    assume the schema exists. Failing fast at bind time also means a
    broken SQLite path surfaces before the first user click.
    """
    init_db()
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if port == DEFAULT_PORT:
            # Default port busy; fall back to ephemeral so dev can iterate.
            logger.warning(
                "port %d is in use (%s) — falling back to ephemeral port",
                port,
                exc,
            )
            server = ThreadingHTTPServer((host, 0), DashboardHandler)
        else:
            raise
    bound_port = server.server_address[1]
    return server, bound_port
```

- [ ] **Step 6: Run the new test — verify it passes**

Run: `.venv/bin/pytest tests/test_server.py::test_init_db_runs_once_at_serve_startup_not_per_request -v`

Expected: PASS. `call_count == 1`.

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass. The conftest's `tmp_db` fixture already calls `database.init_db()` directly (line 51), so existing server tests still find their schema even though `_handle_skip` / `_handle_response` no longer call init_db themselves.

- [ ] **Step 8: Run ruff**

Run: `.venv/bin/ruff check .`

Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/flatpilot/server.py tests/test_server.py
git commit -m "FlatPilot-r7y: move init_db() out of per-request server handlers to startup"
```

Verify the commit with `git log -1`.

---

## Task 3: Add subprocess timeout to `_spawn_apply` (FlatPilot-2yu)

**Why this task:** `src/flatpilot/server.py:63` calls `subprocess.run([...flatpilot apply...])` with **no timeout**. A hung Playwright in the child (network stall, modal CAPTCHA waiting for input, anti-bot redirect loop) blocks the dashboard server thread indefinitely. Add `timeout=APPLY_TIMEOUT_SEC` and catch `subprocess.TimeoutExpired`, returning the same shape the success/failure paths already return so the dashboard JS handles it uniformly.

**Bead:** `FlatPilot-2yu`

**Files:**
- Modify: `src/flatpilot/server.py:54-75` (add timeout + TimeoutExpired handler)
- Modify: `tests/test_server.py` (add test for timeout path)

**Constants:**
- `APPLY_TIMEOUT_SEC = 180` — three minutes. A real headed Playwright apply (page load + login + form fill + submit + screenshot) usually takes 20-60 seconds; 180s gives margin for slow networks and one CAPTCHA-equivalent prompt while still bounding dashboard hang. Configurable later if needed.

- [ ] **Step 1: Write the failing red-test**

Append to `tests/test_server.py` (place after the existing `test_post_apply_invalid_body_returns_400`):

```python
def test_spawn_apply_returns_structured_error_on_subprocess_timeout(tmp_db):
    """A hung 'flatpilot apply' subprocess must surface as ok=False, not raise.

    Pre-fix _spawn_apply called subprocess.run with no timeout — a stuck
    Playwright would hang the entire dashboard server thread. This test
    monkeypatches subprocess.run to raise TimeoutExpired and asserts the
    function returns the structured error shape the dashboard handler
    expects.
    """
    import subprocess
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    fake_stdout = "starting apply for flat 42\nlogged in, opening listing\n"
    fake_stderr = ""

    def raise_timeout(*args, **kwargs):
        # subprocess.TimeoutExpired carries whatever stdout/stderr was
        # captured up to the timeout — include it so the surface area
        # matches the real failure mode.
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else kwargs.get("args", []),
            timeout=kwargs.get("timeout", 180),
            output=fake_stdout,
            stderr=fake_stderr,
        )

    with patch("flatpilot.server.subprocess.run", side_effect=raise_timeout):
        result = _spawn_apply(42)

    assert result["ok"] is False
    assert result["returncode"] is None
    assert "timed out" in result["stdout_tail"].lower()
    # Captured-before-timeout output should still surface so the user
    # sees how far the apply got.
    assert "logged in" in result["stdout_tail"]


def test_spawn_apply_passes_timeout_to_subprocess_run():
    """_spawn_apply must pass a finite timeout= keyword to subprocess.run.

    Guards against the function silently going back to no-timeout. We
    record the kwargs subprocess.run is called with and assert a
    positive numeric timeout was set. No DB needed — the function only
    talks to subprocess.run.
    """
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    captured_kwargs: dict = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        # Return a stand-in completed-process-like object with the
        # attributes the function reads.
        class _Done:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Done()

    with patch("flatpilot.server.subprocess.run", side_effect=fake_run):
        result = _spawn_apply(42)

    assert result["ok"] is True
    timeout = captured_kwargs.get("timeout")
    assert isinstance(timeout, (int, float)) and timeout > 0, (
        f"expected positive numeric timeout=, got {timeout!r}"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py::test_spawn_apply_returns_structured_error_on_subprocess_timeout tests/test_server.py::test_spawn_apply_passes_timeout_to_subprocess_run -v`

Expected:
- `test_spawn_apply_returns_structured_error_on_subprocess_timeout` FAILS with the `TimeoutExpired` propagating uncaught (pytest reports the exception, not an assertion error).
- `test_spawn_apply_passes_timeout_to_subprocess_run` FAILS with `assert isinstance(timeout, (int, float)) and timeout > 0` because `timeout` is `None` (the kwarg was never passed).

If either passes, **STOP** — the test is mis-targeted; investigate.

- [ ] **Step 3: Update `_spawn_apply` in `src/flatpilot/server.py`**

Find the current implementation (lines 54-75):

```python
def _spawn_apply(flat_id: int) -> dict:
    """Run ``flatpilot apply <flat_id>`` as a subprocess.

    Captures stdout/stderr; returns a small dict the handler can ship to
    the browser. Stdout is tail-trimmed to ~2 KB so a verbose Playwright
    log doesn't bloat the JSON response.

    Patched in tests so we don't actually invoke the CLI.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "flatpilot", "apply", str(flat_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = combined[-2000:].strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": tail,
    }
```

Add a module-level constant near the top of the file (just above `DEFAULT_PORT = 8765` is a natural spot):

```python
# Upper bound on a single dashboard-spawned `flatpilot apply` subprocess.
# A real headed Playwright apply (load + login + fill + submit + screenshot)
# typically takes 20-60s; 180s gives margin for slow networks and one
# CAPTCHA-equivalent prompt while still bounding dashboard hang.
APPLY_TIMEOUT_SEC = 180
```

Replace `_spawn_apply` body with:

```python
def _spawn_apply(flat_id: int) -> dict:
    """Run ``flatpilot apply <flat_id>`` as a subprocess.

    Captures stdout/stderr; returns a small dict the handler can ship to
    the browser. Stdout is tail-trimmed to ~2 KB so a verbose Playwright
    log doesn't bloat the JSON response.

    Bounded by ``APPLY_TIMEOUT_SEC``: a hung child (e.g. Playwright stuck
    on a CAPTCHA wait) is killed and surfaced as ``ok=False`` with the
    captured-so-far output, so the dashboard thread is freed.

    Patched in tests so we don't actually invoke the CLI.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "flatpilot", "apply", str(flat_id)],
            capture_output=True,
            text=True,
            check=False,
            timeout=APPLY_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        captured = (exc.stdout or "") + (exc.stderr or "")
        # subprocess.run with text=True normally yields str; defend against
        # the bytes path just in case a caller passed text=False.
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", errors="replace")
        tail_body = captured[-2000:].strip()
        prefix = f"timed out after {APPLY_TIMEOUT_SEC}s"
        tail = f"{prefix}\n{tail_body}".strip() if tail_body else prefix
        return {
            "ok": False,
            "returncode": None,
            "stdout_tail": tail,
        }
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = combined[-2000:].strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": tail,
    }
```

- [ ] **Step 4: Run the new tests — verify they pass**

Run: `.venv/bin/pytest tests/test_server.py::test_spawn_apply_returns_structured_error_on_subprocess_timeout tests/test_server.py::test_spawn_apply_passes_timeout_to_subprocess_run -v`

Expected: both PASS.

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass. The existing apply-success and apply-failure tests in `test_server.py` patch `_spawn_apply` directly, so they don't exercise the new timeout path and remain unaffected.

- [ ] **Step 6: Run ruff**

Run: `.venv/bin/ruff check .`

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/flatpilot/server.py tests/test_server.py
git commit -m "FlatPilot-2yu: add subprocess timeout to server._spawn_apply"
```

Verify with `git log -1`.

---

## Task 4: Server-side concurrency lock around `_spawn_apply` (FlatPilot-cy9)

**Why this task:** The dashboard's `_handle_apply` reads `body['flat_id']`, calls `_spawn_apply(flat_id)`, and returns the result. Two near-simultaneous POSTs for the same flat (a double-click on Apply, or two browser tabs open) cause two server threads to each fork a subprocess. Both subprocesses pass the `status='submitted'` idempotency check inside `apply_to_flat` (because neither has written its row yet), both call `filler.fill()`, and **the landlord receives two messages**.

The fix is a server-side `threading.Lock` + `set[int]` of in-flight `flat_id`s, **before** the subprocess is spawned. The first thread claims the slot; the second sees the slot occupied and returns 409 Conflict immediately. The slot is released in a `finally` block so a `_spawn_apply` exception (e.g. timeout from Task 3) can't strand the lock.

This lock lives in `server.py` (NOT in `apply.py`) because `apply_to_flat` runs in a forked subprocess — a Python-level lock there is in a different process from the racer. The CLI-vs-dashboard cross-process race (`flatpilot apply 42` typed in a terminal while the dashboard also fires Apply for flat 42) is rarer and intentionally out of scope for this task — the existing `AlreadyAppliedError` SELECT/INSERT in `apply.py` is best-effort defence for that path; closing the cross-process race would require an `apply_locks` table with stale-row handling and is filed as deferred follow-up.

**Bead:** `FlatPilot-cy9`

**Pre-existing context from earlier tasks:** Task 3 has already landed `APPLY_TIMEOUT_SEC` and a `try/except subprocess.TimeoutExpired` inside `_spawn_apply`, so by the time you implement Task 4 the helper returns a dict in normal paths and only raises for unexpected errors. The lock you add here releases the slot in `finally` regardless of whether `_spawn_apply` returns or raises.

**Files:**
- Modify: `src/flatpilot/server.py` (add `_inflight_lock`, `_inflight_flats`, claim/release in `_handle_apply`)
- Modify: `tests/test_server.py` (add concurrency test)

- [ ] **Step 1: Write the failing red-test**

Append to `tests/test_server.py` (place after the timeout tests added in Task 3):

```python
def test_post_apply_rejects_concurrent_request_for_same_flat(tmp_db):
    """Two near-simultaneous applies for the same flat: one wins, one 409s.

    Pre-fix both server threads called _spawn_apply, both subprocesses
    passed apply_to_flat's status='submitted' check (because neither
    had written yet), and the landlord received two messages. The fix
    is a module-level lock + set[int] of in-flight flat_ids in
    server.py: second concurrent POST for the same flat returns 409
    immediately without spawning anything.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    _seed_match_with_profile(tmp_db)

    in_spawn = threading.Event()
    release = threading.Event()
    spawn_calls: list[int] = []

    def slow_spawn(flat_id):
        spawn_calls.append(flat_id)
        in_spawn.set()
        # Block until the test releases — gives the second request a
        # chance to collide with this one.
        if not release.wait(timeout=5):
            raise AssertionError(
                "test never released the first spawn — "
                "deadlock or test ordering bug"
            )
        return {"ok": True, "stdout_tail": f"applied {flat_id}", "returncode": 0}

    body = json.dumps({"flat_id": 1}).encode("utf-8")

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", side_effect=slow_spawn),
    ):
        url = f"http://127.0.0.1:{port}/api/applications"
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(_post, url, body)
            try:
                # Wait until the first request is locked-in inside _spawn_apply
                # before firing the second — without this, the second might
                # arrive before the first has claimed the in-flight slot,
                # making the test order-dependent.
                assert in_spawn.wait(timeout=2), (
                    "first request never entered _spawn_apply"
                )
                f2 = ex.submit(_post, url, body)
                # The second should be rejected fast with 409 — without
                # the lock, f2 also enters slow_spawn and blocks until
                # release, so f2.result(timeout=2) raises TimeoutError
                # (the expected pre-fix failure mode).
                r2 = f2.result(timeout=2)
            finally:
                release.set()
            r1 = f1.result(timeout=10)

    assert r2[0] == 409, (
        f"expected 409 for concurrent apply, got {r2[0]}: {r2[1]!r}"
    )
    assert r1[0] == 200, (
        f"expected 200 for first apply, got {r1[0]}: {r1[1]!r}"
    )
    # The second request must NOT have spawned — the lock rejects before
    # _spawn_apply is even called.
    assert spawn_calls == [1], (
        f"expected exactly one spawn (the winner), got {spawn_calls}"
    )


def test_post_apply_releases_slot_after_completion_so_retry_succeeds(tmp_db):
    """After the first apply finishes, a fresh apply for the same flat goes through.

    Guards against a finally-clause regression that would leak slots in
    the in-flight set, blocking all future applies for that flat.
    """
    from unittest.mock import patch

    _seed_match_with_profile(tmp_db)

    fake_result = {"ok": True, "stdout_tail": "ok", "returncode": 0}

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", return_value=fake_result),
    ):
        url = f"http://127.0.0.1:{port}/api/applications"
        body = json.dumps({"flat_id": 1}).encode("utf-8")
        first = _post(url, body)
        second = _post(url, body)

    assert first[0] == 200
    assert second[0] == 200, (
        f"expected sequential applies to both succeed (slot released), "
        f"got {second[0]}: {second[1]!r}"
    )


def test_post_apply_releases_slot_when_spawn_raises(tmp_db):
    """If _spawn_apply itself raises, the in-flight slot must still be released.

    Otherwise a one-time bug or kill -9 would permanently block applies
    to that flat until the server restarts. We assert on
    ``flatpilot.server._inflight_flats`` directly because
    ``BaseHTTPRequestHandler`` does NOT translate handler exceptions into
    a 5xx — the connection drops mid-response and any HTTP-level
    assertion would observe ``RemoteDisconnected``/``URLError``, masking
    the actual finally-release we care about.
    """
    from unittest.mock import patch

    import flatpilot.server as server_mod

    _seed_match_with_profile(tmp_db)

    def crashing_spawn(flat_id):
        raise RuntimeError("simulated spawn crash")

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", side_effect=crashing_spawn),
    ):
        url = f"http://127.0.0.1:{port}/api/applications"
        body = json.dumps({"flat_id": 1}).encode("utf-8")
        # The crash inside _handle_apply causes the request thread to
        # propagate the exception and BaseHTTPRequestHandler closes the
        # socket without a structured response. We don't care which
        # connection error urllib surfaces — we care that _inflight_flats
        # is empty afterwards.
        try:
            _post(url, body)
        except Exception:
            pass

    assert 1 not in server_mod._inflight_flats, (
        f"_inflight_flats was not cleaned up after spawn crash: "
        f"{server_mod._inflight_flats!r}"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py::test_post_apply_rejects_concurrent_request_for_same_flat tests/test_server.py::test_post_apply_releases_slot_after_completion_so_retry_succeeds tests/test_server.py::test_post_apply_releases_slot_when_spawn_raises -v`

Expected:
- `test_post_apply_rejects_concurrent_request_for_same_flat` FAILS with `concurrent.futures.TimeoutError` (or pytest renders it as the inner `TimeoutError`) raised from `f2.result(timeout=2)` — pre-fix the second request also blocks in `slow_spawn` and never returns within the timeout.
- `test_post_apply_releases_slot_after_completion_so_retry_succeeds` PASSES pre-fix (the slot machinery doesn't exist, so nothing can leak). This is a regression guard, not a red-test — it earns its keep after the lock lands by failing loudly if a future change breaks the `finally` cleanup. Document the pre-fix pass in the run output.
- `test_post_apply_releases_slot_when_spawn_raises` FAILS pre-fix with `AttributeError: module 'flatpilot.server' has no attribute '_inflight_flats'` — because the assertion `1 not in server_mod._inflight_flats` references a name that doesn't exist yet. This is a true red-test: it can only pass after Task 4 introduces the module-level `_inflight_flats` set AND the `finally` cleanup.

If `test_post_apply_rejects_concurrent_request_for_same_flat` or `test_post_apply_releases_slot_when_spawn_raises` passes pre-fix, **STOP** — the test is misreading the code; investigate before softening.

(Note: `test_post_apply_releases_slot_after_completion_so_retry_succeeds` is deliberately a regression guard rather than a red-test. It is included in the same "make the lock not leak" specification because the cost is low and the loud signal on future refactors is high.)

- [ ] **Step 3: Update `src/flatpilot/server.py` — add module-level lock state**

Add a module-level state block. Place it near the top, just before `class DashboardHandler` (around line 78). The constants and global state are explicit so other modules / future maintainers can see the contract.

```python
# Per-flat concurrency control for the Apply path.
#
# The dashboard's POST /api/applications endpoint shells out to
# `flatpilot apply <flat_id>` via _spawn_apply. Two near-simultaneous
# POSTs for the same flat (e.g. a double-click) used to fork two
# subprocesses; both passed apply_to_flat's status='submitted' check and
# the landlord received two messages.
#
# The lock here serializes the two server threads BEFORE either
# subprocess is spawned. Second concurrent caller for the same flat_id
# returns 409 immediately. The lock is per-flat (not global) so applies
# to DIFFERENT flats still run in parallel.
#
# Scope. This closes the in-process race that causes the dashboard
# double-click bug. The cross-process race (a CLI `flatpilot apply N`
# running while the dashboard also applies to N) is rarer, partially
# mitigated by apply_to_flat's existing AlreadyAppliedError SELECT/INSERT
# check, and intentionally out of scope here.
_inflight_lock = threading.Lock()
_inflight_flats: set[int] = set()
```

You'll also need `import threading` near the top of the file. Check the existing imports — if `threading` isn't already imported, add it alphabetically among the stdlib imports (after `import sys`).

- [ ] **Step 4: Update `_handle_apply` in `src/flatpilot/server.py` — claim/release pattern**

Current `_handle_apply` (lines 108-123):

```python
    def _handle_apply(self) -> None:
        body = self._read_json_body()
        if body is None:
            return  # _read_json_body already responded.
        flat_id = body.get("flat_id")
        # bool is a subclass of int — guard explicitly so {"flat_id": true}
        # doesn't slip through and spawn `flatpilot apply True`.
        if not isinstance(flat_id, int) or isinstance(flat_id, bool):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "request body must be {'flat_id': <int>}"},
            )
            return
        result = _spawn_apply(flat_id)
        status = HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status, result)
```

Replace with:

```python
    def _handle_apply(self) -> None:
        body = self._read_json_body()
        if body is None:
            return  # _read_json_body already responded.
        flat_id = body.get("flat_id")
        # bool is a subclass of int — guard explicitly so {"flat_id": true}
        # doesn't slip through and spawn `flatpilot apply True`.
        if not isinstance(flat_id, int) or isinstance(flat_id, bool):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "request body must be {'flat_id': <int>}"},
            )
            return

        # Claim an in-flight slot for this flat. If another request is
        # already applying to it, fail fast with 409 — don't queue, the
        # caller will see the (eventually) submitted row on the next
        # dashboard refresh.
        with _inflight_lock:
            if flat_id in _inflight_flats:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "error": (
                            f"apply already in progress for flat {flat_id}; "
                            "wait for it to finish"
                        ),
                    },
                )
                return
            _inflight_flats.add(flat_id)
        try:
            result = _spawn_apply(flat_id)
        finally:
            with _inflight_lock:
                _inflight_flats.discard(flat_id)
        status = HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status, result)
```

Key invariants the implementer must preserve:
1. The `_inflight_flats.add(flat_id)` happens **inside** the `_inflight_lock` block so the check-then-add is atomic.
2. `_spawn_apply(flat_id)` runs **outside** the `_inflight_lock` so it doesn't serialize applies to *different* flats. Only the in-flight set membership check is locked.
3. The `_inflight_flats.discard(flat_id)` is in `finally` so a `_spawn_apply` exception (e.g. unexpected error from Task 3's TimeoutExpired path is already caught inside `_spawn_apply` itself, but defence-in-depth) doesn't strand the slot.
4. The `discard` (not `remove`) is intentional — guards against double-release if the surrounding code is ever refactored.

- [ ] **Step 5: Run the new tests — verify they pass**

Run: `.venv/bin/pytest tests/test_server.py::test_post_apply_rejects_concurrent_request_for_same_flat tests/test_server.py::test_post_apply_releases_slot_after_completion_so_retry_succeeds tests/test_server.py::test_post_apply_releases_slot_when_spawn_raises -v`

Expected: all three PASS.

For the third test (`test_post_apply_releases_slot_when_spawn_raises`): when `_spawn_apply` raises a `RuntimeError`, the `try/finally` releases the slot but the exception propagates up. `BaseHTTPRequestHandler` catches it and returns 500. The test asserts `first[0] >= 500`.

If `BaseHTTPRequestHandler` swallows the exception silently (no response sent at all), `urllib` raises a `URLError` and `_post` doesn't currently catch it — that would surface as a test failure with the URLError. If you observe that, look at `_post` and add a URLError catch returning a synthetic `(500, b"")`. **But don't soften the test's assertion** — the test correctly says "5xx is acceptable, anything <500 is wrong."

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Run ruff**

Run: `.venv/bin/ruff check .`

Expected: no errors. (If ruff complains about the new `_inflight_flats: set[int] = set()` annotation in older Python, the project is 3.11+ per CLAUDE.md, so this is fine.)

- [ ] **Step 8: Commit**

```bash
git add src/flatpilot/server.py tests/test_server.py
git commit -m "FlatPilot-cy9: server-side per-flat concurrency lock around _spawn_apply"
```

Verify with `git log -1`.

---

## Final Verification

After all four tasks land:

- [ ] **Run the full test suite once more**

Run: `.venv/bin/pytest -q`

Expected: all tests pass, including the 7 new tests across `test_errors.py` and `test_server.py`.

- [ ] **Run ruff once more**

Run: `.venv/bin/ruff check .`

Expected: no errors.

- [ ] **Confirm the four commits are in order with the right format**

Run: `git log --oneline origin/main..HEAD`

Expected output (4 commits, in this order):

```
<hash> FlatPilot-cy9: server-side per-flat concurrency lock around _spawn_apply
<hash> FlatPilot-2yu: add subprocess timeout to server._spawn_apply
<hash> FlatPilot-r7y: move init_db() out of per-request server handlers to startup
<hash> FlatPilot-eps: hoist ProfileMissingError and UnknownCityError to flatpilot.errors
```

(Order is reverse-chronological — the most recent commit is on top.)

- [ ] **Confirm commit author**

Run: `git log --format='%h %an <%ae>' origin/main..HEAD`

Expected: every line shows `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. No AI co-author trailers anywhere.

- [ ] **Confirm no `.beads/issues.jsonl` regression**

Run: `git diff origin/main..HEAD -- .beads/issues.jsonl`

Expected: only the four bd-claim status updates (open → in_progress) for FlatPilot-eps, FlatPilot-r7y, FlatPilot-2yu, FlatPilot-cy9. No accidental edits to other beads.

---

## Deferred Follow-Ups to File Before Opening the PR

These are out-of-scope concerns the implementation is intentionally not closing. **File these as new beads with `bd dep add <new-id> <main-id>` linking back to the relevant task** before opening the PR, otherwise they get lost.

1. **Cross-process Apply race** — the in-process server lock in Task 4 closes the dashboard double-click race but not "CLI `flatpilot apply N` typed at a terminal while the dashboard also applies to N." A proper fix needs an `apply_locks` table (or partial unique index) with stale-row handling. File as P3, dep on FlatPilot-cy9.

2. **`APPLY_TIMEOUT_SEC` configurability** — currently hard-coded to 180s. A user with a slow network or many CAPTCHA-equivalent prompts may want longer; a paranoid user may want shorter. Consider exposing via the profile or an env var. File as P4, dep on FlatPilot-2yu.

3. **`_inflight_flats` cleanup if `_spawn_apply` itself never returns** — Task 3's timeout means `_spawn_apply` always returns within 180s, but if someone refactors that out, `_inflight_flats` grows unboundedly because the `finally` only runs when `_spawn_apply` returns or raises. Consider a watchdog / cap. P4, dep on FlatPilot-cy9.
