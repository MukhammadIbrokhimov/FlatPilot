# Apply Server Hardening — Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the residual concurrency holes left after PR #27 — make the apply timeout configurable (FlatPilot-c3e), enforce a cross-process apply lock so two FlatPilot processes can't double-message a landlord (FlatPilot-bxm), and add a watchdog that auto-clears stuck `_inflight_flats` slots in the dashboard (FlatPilot-da5). Also drop one unreachable defensive branch in `_spawn_apply` while we're touching that file.

**Architecture:** A new `apply_timeout_sec()` helper in `flatpilot.apply` reads `FLATPILOT_APPLY_TIMEOUT_SEC` (default 180s), used by both `_spawn_apply` (server.py) and the new lock primitives (apply.py). A new `apply_locks` SQLite table provides cross-process serialization via `INSERT OR FAIL` on a `PRIMARY KEY` — the loser raises `AlreadyAppliedError("apply already in progress")`, distinct from the existing `AlreadyAppliedError("already has a submitted application")`. Stale rows older than `apply_timeout_sec() + 60` are reaped on every acquire to recover from process crashes. The dashboard's `_inflight_flats` becomes `dict[int, float]` (flat_id → monotonic acquire time) and gets a check-on-acquire sweep using the same `apply_timeout_sec() + 60` threshold — defense-in-depth against a future refactor that removes the subprocess timeout.

**Tech Stack:** Python 3.11+, SQLite (WAL, autocommit), pydantic, pytest, multiprocessing (for one cross-process race test).

**Topological order:** c3e first (introduces the helper), then bxm and da5 build on it. PR #27 left `APPLY_TIMEOUT_SEC = 180` as a module constant in `server.py`; that becomes `DEFAULT_APPLY_TIMEOUT_SEC` in `apply.py` once c3e ships.

**Out of scope:**
- No Profile model change (env var only — see deviation log).
- No PID liveness checks (time-based stale cleanup is portable and sufficient).
- No structural change to the existing in-process server lock (`_inflight_flats` becomes a `dict` for da5, but its semantics — "fast-path 409" — stay the same).

**Deviations from bead descriptions:**
- **bxm** description proposed a hardcoded 30-min stale threshold; advisor flagged that this drifts as soon as c3e makes the timeout configurable. We use `apply_timeout_sec() + 60` instead so all three pieces stay self-consistent.
- **c3e** description offered profile field OR env var; we chose env var to keep `Profile.model_config = ConfigDict(extra="forbid")` simple and because timeout tuning is a Phase-3 ergonomics knob, not a user-facing setting.
- **bxm** description suggested testing via two `subprocess.run` invocations; we use `multiprocessing.Process` for fidelity to the cross-process spec without paying CLI subprocess overhead in CI. SQLite WAL is genuinely process-safe so the assertion is no weaker.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/flatpilot/apply.py` | Modify | Add `DEFAULT_APPLY_TIMEOUT_SEC`, `apply_timeout_sec()`, `acquire_apply_lock()`, `release_apply_lock()`. Wire lock into `apply_to_flat`. |
| `src/flatpilot/schemas.py` | Modify | Register `apply_locks` CREATE TABLE. |
| `src/flatpilot/server.py` | Modify | Drop the local `APPLY_TIMEOUT_SEC` constant. `_spawn_apply` calls `apply_timeout_sec()` once per invocation. Delete unreachable `isinstance(captured, bytes)` branch. `_inflight_flats: set[int]` → `dict[int, float]` with watchdog sweep on acquire. |
| `tests/test_apply_timeout_config.py` | Create | Unit tests for the helper + assertion that `_spawn_apply` honors the env var. |
| `tests/test_apply_lock.py` | Create | Unit tests for the lock primitives + the multiprocessing race test + integration with `apply_to_flat`. |
| `tests/test_server.py` | Modify | Update three existing tests for the `set` → `dict` shape change in `_inflight_flats`. Add `test_inflight_watchdog_clears_stale_slot_on_acquire`. Update `test_spawn_apply_returns_structured_error_on_subprocess_timeout` to assert the configured value appears in the error string. |

No new modules. The helper, primitives, and integration all live in `apply.py` so the only cross-module dependency direction is `server.py → apply.py` (already established).

---

## Test Baseline

`.venv/bin/pytest --collect-only -q` reports **197 tests** on `origin/main`. Every step's "expected: PASS" assumes that baseline plus whatever this plan has added so far. The final passing count after Task 3 should be **197 + (new tests added below) − 0 deletions**.

`.venv/bin/ruff check .` reports **14 pre-existing errors** in untouched modules (tracked as FlatPilot-4wk). Ignore them. The new files in this plan must add **zero** ruff errors.

---

## Task 1 — FlatPilot-c3e: configurable APPLY_TIMEOUT_SEC + delete unreachable bytes branch

**Files:**
- Modify: `src/flatpilot/apply.py` — add `DEFAULT_APPLY_TIMEOUT_SEC` and `apply_timeout_sec()` near the top of the module.
- Modify: `src/flatpilot/server.py` — remove `APPLY_TIMEOUT_SEC = 180` constant; `_spawn_apply` calls `apply_timeout_sec()`; delete unreachable `isinstance(captured, bytes)` branch.
- Create: `tests/test_apply_timeout_config.py` — unit tests for the helper.
- Modify: `tests/test_server.py` — update `test_spawn_apply_returns_structured_error_on_subprocess_timeout` to assert the configured timeout appears in the error string.

The dead-code nit (the `isinstance(captured, bytes)` branch in `_spawn_apply`) rides this commit because both changes touch `_spawn_apply`'s body.

### Steps

- [ ] **Step 1.1: Write failing tests for `apply_timeout_sec()`**

Create `tests/test_apply_timeout_config.py`:

```python
"""Tests for the configurable apply subprocess timeout (FlatPilot-c3e).

The default is 180s. ``FLATPILOT_APPLY_TIMEOUT_SEC`` overrides it.
Invalid values (non-int, non-positive) log a warning and fall back to
the default rather than raising — the env var is an ergonomics knob, a
typo shouldn't break apply.
"""

from __future__ import annotations

import logging

import pytest


def test_apply_timeout_default_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    from flatpilot.apply import DEFAULT_APPLY_TIMEOUT_SEC, apply_timeout_sec

    monkeypatch.delenv("FLATPILOT_APPLY_TIMEOUT_SEC", raising=False)
    assert apply_timeout_sec() == DEFAULT_APPLY_TIMEOUT_SEC == 180


def test_apply_timeout_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch):
    from flatpilot.apply import apply_timeout_sec

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "42")
    assert apply_timeout_sec() == 42


def test_apply_timeout_invalid_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    from flatpilot.apply import DEFAULT_APPLY_TIMEOUT_SEC, apply_timeout_sec

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "not-a-number")
    with caplog.at_level(logging.WARNING, logger="flatpilot.apply"):
        assert apply_timeout_sec() == DEFAULT_APPLY_TIMEOUT_SEC
    assert "FLATPILOT_APPLY_TIMEOUT_SEC" in caplog.text


def test_apply_timeout_non_positive_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    from flatpilot.apply import DEFAULT_APPLY_TIMEOUT_SEC, apply_timeout_sec

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "0")
    with caplog.at_level(logging.WARNING, logger="flatpilot.apply"):
        assert apply_timeout_sec() == DEFAULT_APPLY_TIMEOUT_SEC
    assert "FLATPILOT_APPLY_TIMEOUT_SEC" in caplog.text


def test_spawn_apply_passes_configured_timeout_to_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end: setting the env var changes the timeout subprocess.run sees."""
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    captured_kwargs: dict = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)

        class _Done:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Done()

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "5")
    with patch("flatpilot.server.subprocess.run", side_effect=fake_run):
        result = _spawn_apply(42)

    assert result["ok"] is True
    assert captured_kwargs.get("timeout") == 5


def test_spawn_apply_timeout_error_string_reports_configured_value(
    monkeypatch: pytest.MonkeyPatch,
):
    """The configured timeout must appear in the timeout-error message.

    Otherwise the user sees 'timed out after 180s' regardless of what
    they actually set, and we have no proof the env var is honored.
    """
    import subprocess
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else kwargs.get("args", []),
            timeout=kwargs.get("timeout", 180),
            output="",
            stderr="",
        )

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "7")
    with patch("flatpilot.server.subprocess.run", side_effect=raise_timeout):
        result = _spawn_apply(42)

    assert result["ok"] is False
    assert "timed out after 7s" in result["stdout_tail"]
```

- [ ] **Step 1.2: Run new tests, verify they fail**

Run: `.venv/bin/pytest tests/test_apply_timeout_config.py -v`

Expected: ALL six tests fail, but with **two distinct failure modes**:

- **Four** tests fail with `ImportError: cannot import name 'apply_timeout_sec' from 'flatpilot.apply'` (or `DEFAULT_APPLY_TIMEOUT_SEC`). These are: `test_apply_timeout_default_when_env_unset`, `test_apply_timeout_env_var_overrides_default`, `test_apply_timeout_invalid_env_falls_back_to_default`, `test_apply_timeout_non_positive_falls_back_to_default`.
- **Two** tests fail with `AssertionError`. These import only `_spawn_apply` (which already exists). They run successfully against current code, but the assertions fail because `_spawn_apply` still reads the module constant `APPLY_TIMEOUT_SEC = 180` and ignores the env var. Specifically:
  - `test_spawn_apply_passes_configured_timeout_to_subprocess_run` — asserts `captured_kwargs.get("timeout") == 5`, gets 180.
  - `test_spawn_apply_timeout_error_string_reports_configured_value` — asserts `"timed out after 7s" in result["stdout_tail"]`, gets `"timed out after 180s"`.

**STOP if any of these tests pass at this stage** — that means the implementation has leaked into the test or a previous run. Inspect and fix before moving on.

- [ ] **Step 1.3: Add `DEFAULT_APPLY_TIMEOUT_SEC` and `apply_timeout_sec()` to `apply.py`**

In `src/flatpilot/apply.py`, **add the import**:

```python
import os
```

(near the existing `import json`, `import logging` block — keep them sorted by `ruff` rules.)

Add this section right after the existing `logger = logging.getLogger(__name__)` line and before `class AlreadyAppliedError(...)`:

```python
DEFAULT_APPLY_TIMEOUT_SEC = 180


def apply_timeout_sec() -> int:
    """Resolve the per-call apply subprocess timeout.

    Default 180s — a reasonable upper bound for a headed Playwright
    apply (load + login + fill + submit + screenshot typically takes
    20-60s, with margin for slow networks and one CAPTCHA-equivalent
    prompt). Override via ``FLATPILOT_APPLY_TIMEOUT_SEC`` for users on
    very slow networks (raise it) or paranoid CI environments (lower
    it). Invalid values (non-int, non-positive) log a warning and fall
    back to the default — the caller has no recourse here, and a typo
    in an ergonomics env var shouldn't break apply.
    """
    raw = os.environ.get("FLATPILOT_APPLY_TIMEOUT_SEC")
    if raw is None:
        return DEFAULT_APPLY_TIMEOUT_SEC
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "FLATPILOT_APPLY_TIMEOUT_SEC=%r is not an int; using default %ds",
            raw,
            DEFAULT_APPLY_TIMEOUT_SEC,
        )
        return DEFAULT_APPLY_TIMEOUT_SEC
    if v <= 0:
        logger.warning(
            "FLATPILOT_APPLY_TIMEOUT_SEC=%d must be > 0; using default %ds",
            v,
            DEFAULT_APPLY_TIMEOUT_SEC,
        )
        return DEFAULT_APPLY_TIMEOUT_SEC
    return v
```

- [ ] **Step 1.4: Wire `_spawn_apply` to the helper, drop dead bytes branch**

In `src/flatpilot/server.py`:

**Remove** the module-level constant block (lines 48-52, the `APPLY_TIMEOUT_SEC = 180` and its docstring-style comment).

**Add** an import alongside the existing `from flatpilot.database import ...`:

```python
from flatpilot.apply import apply_timeout_sec
```

**Replace** the body of `_spawn_apply` (lines 61-102) with this exact block. The two changes from the current version: (1) `timeout_sec = apply_timeout_sec()` captured once at call start, used in both the `subprocess.run(timeout=...)` argument and the timeout-error message; (2) the `isinstance(captured, bytes)` branch and its comment are deleted (`text=True` is hard-coded so capture is always `str`).

```python
def _spawn_apply(flat_id: int) -> dict:
    """Run ``flatpilot apply <flat_id>`` as a subprocess.

    Captures stdout/stderr; returns a small dict the handler can ship to
    the browser. Stdout is tail-trimmed to ~2 KB so a verbose Playwright
    log doesn't bloat the JSON response.

    Bounded by ``apply_timeout_sec()`` (default 180s, override via
    ``FLATPILOT_APPLY_TIMEOUT_SEC``): a hung child (e.g. Playwright stuck
    on a CAPTCHA wait) is killed and surfaced as ``ok=False`` with the
    captured-so-far output, so the dashboard thread is freed.

    Patched in tests so we don't actually invoke the CLI.
    """
    timeout_sec = apply_timeout_sec()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "flatpilot", "apply", str(flat_id)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        captured = (exc.stdout or "") + (exc.stderr or "")
        tail_body = captured[-2000:].strip()
        prefix = f"timed out after {timeout_sec}s"
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

- [ ] **Step 1.5: Update existing test that hard-codes the value**

`tests/test_server.py` has `test_spawn_apply_returns_structured_error_on_subprocess_timeout` (around line 302). Its current assertion is `"timed out" in result["stdout_tail"].lower()`. Strengthen it to verify the default (180s) appears explicitly, and neutralize ambient `FLATPILOT_APPLY_TIMEOUT_SEC` so the test doesn't drift if CI happens to set the env var for some other reason.

Add a `monkeypatch` parameter to the test signature (it currently takes `tmp_db` only — keep `tmp_db` if it's there, add `monkeypatch`). Add this line at the top of the test body, before the existing `import subprocess`:

```python
    monkeypatch.delenv("FLATPILOT_APPLY_TIMEOUT_SEC", raising=False)
```

Replace the existing assertions block (the three lines starting `assert result["ok"] is False`) with:

```python
    assert result["ok"] is False
    assert result["returncode"] is None
    assert "timed out after 180s" in result["stdout_tail"]
    # Captured-before-timeout output should still surface so the user
    # sees how far the apply got.
    assert "logged in" in result["stdout_tail"]
```

(The default is 180 because the env var is unset by `monkeypatch.delenv`.)

- [ ] **Step 1.6: Run all changed tests, verify they pass**

Run: `.venv/bin/pytest tests/test_apply_timeout_config.py tests/test_server.py -v`

Expected: ALL pass. The new file's six tests pass; existing `test_server.py` tests still pass (the timeout error-string test now asserts the explicit `"timed out after 180s"` substring).

- [ ] **Step 1.7: Run full suite + ruff, verify nothing else regressed**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/flatpilot/apply.py src/flatpilot/server.py tests/test_apply_timeout_config.py tests/test_server.py`

Expected:
- pytest: `203 passed` (was 197, +6 new from this task).
- ruff (scoped to changed files): zero errors. The 14 pre-existing errors elsewhere are FlatPilot-4wk's problem.

- [ ] **Step 1.8: Commit**

```bash
git add src/flatpilot/apply.py src/flatpilot/server.py tests/test_apply_timeout_config.py tests/test_server.py
git commit -m "FlatPilot-c3e: make apply subprocess timeout configurable via env var"
```

The pre-commit hook will auto-stage `.beads/issues.jsonl` (already staged from the claim). Don't `--no-verify`.

Commit body should mention:
- Adds `apply_timeout_sec()` helper in `flatpilot.apply` (default 180s, override via `FLATPILOT_APPLY_TIMEOUT_SEC`).
- `_spawn_apply` reads the helper instead of a module constant; the configured value flows into both `subprocess.run(timeout=)` and the timeout-error message string.
- Deletes the unreachable `isinstance(captured, bytes)` branch in `_spawn_apply` (`text=True` is hard-coded so capture is always `str`). PR #27 final-review nit.

---

## Task 2 — FlatPilot-bxm: cross-process apply lock via `apply_locks` table

**Files:**
- Modify: `src/flatpilot/schemas.py` — register `apply_locks` CREATE TABLE.
- Modify: `src/flatpilot/apply.py` — add `acquire_apply_lock()` and `release_apply_lock()` near `apply_to_flat`; integrate into `apply_to_flat` between the existing `AlreadyAppliedError` SELECT-check and `filler.fill()`.
- Create: `tests/test_apply_lock.py` — unit tests for the primitives + integration with `apply_to_flat` + cross-process race test using `multiprocessing.Process`.

The new lock layer doesn't replace the existing in-process server lock (`_inflight_flats`) — it complements it. The server lock is the fast 409 for dashboard double-clicks; the DB lock is the cross-process correctness layer for CLI-vs-dashboard.

The existing SELECT-check (`SELECT id FROM applications WHERE flat_id = ? AND status = 'submitted'`) is **kept** — it's the post-submission idempotency guard, distinct from the in-flight lock. The two raise `AlreadyAppliedError` with **different messages** so the user can tell which condition tripped.

### Steps

- [ ] **Step 2.1: Write failing tests for the lock primitives**

Create `tests/test_apply_lock.py`:

```python
"""Cross-process apply lock (FlatPilot-bxm).

Two FlatPilot processes (CLI ``flatpilot apply 42`` + dashboard
``POST /api/applications {"flat_id": 42}``) hitting the same flat_id
must NOT both reach ``filler.fill(submit=True)`` — that sends two
messages to the landlord. The new ``apply_locks`` table provides
cross-process serialization via ``INSERT OR FAIL`` on a primary key:
the loser raises ``AlreadyAppliedError("apply already in progress")``.

The lock is acquired AFTER the existing AlreadyAppliedError SELECT
check (so a fully-submitted flat still 409s with its older message)
and BEFORE the filler runs. It's released in a finally so a crash
inside filler.fill() doesn't strand the slot.

Stale rows older than ``apply_timeout_sec() + 60`` are reaped on every
acquire, so a process crash (kill -9) doesn't permanently block future
applies for that flat.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flatpilot.apply import (
    AlreadyAppliedError,
    acquire_apply_lock,
    release_apply_lock,
)


# --------------------------------------------------------------------------- #
# Unit tests against the lock primitives directly.
# --------------------------------------------------------------------------- #


def test_acquire_first_call_succeeds(tmp_db):
    acquire_apply_lock(tmp_db, flat_id=42)
    row = tmp_db.execute(
        "SELECT flat_id, pid FROM apply_locks WHERE flat_id = 42"
    ).fetchone()
    assert row is not None
    assert row["flat_id"] == 42
    assert row["pid"] == os.getpid()


def test_acquire_second_call_raises_in_progress_message(tmp_db):
    acquire_apply_lock(tmp_db, flat_id=42)
    with pytest.raises(AlreadyAppliedError, match="apply already in progress"):
        acquire_apply_lock(tmp_db, flat_id=42)


def test_acquire_second_call_message_distinct_from_submitted_row_message(tmp_db):
    """Two AlreadyAppliedError causes must surface distinct messages.

    'already has a submitted application' = the SELECT-check path.
    'apply already in progress'           = the lock-acquire path.

    Same exception class is fine — the messages disambiguate so the
    user knows whether to wait (in-progress) or accept (already done).
    """
    acquire_apply_lock(tmp_db, flat_id=42)
    with pytest.raises(AlreadyAppliedError) as exc_info:
        acquire_apply_lock(tmp_db, flat_id=42)
    msg = str(exc_info.value)
    assert "apply already in progress" in msg
    assert "already has a submitted application" not in msg


def test_release_then_reacquire_succeeds(tmp_db):
    acquire_apply_lock(tmp_db, flat_id=42)
    release_apply_lock(tmp_db, flat_id=42)
    # Should not raise.
    acquire_apply_lock(tmp_db, flat_id=42)


def test_release_unheld_lock_is_a_noop(tmp_db):
    """release on a flat we never acquired must not raise."""
    release_apply_lock(tmp_db, flat_id=42)
    # No row, no error.
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM apply_locks WHERE flat_id = 42"
        ).fetchone()[0]
        == 0
    )


def test_acquire_clears_stale_lock(tmp_db, monkeypatch):
    """A row older than apply_timeout_sec() + 60 is reaped on next acquire.

    Simulates a kill -9'd process: it inserted a row, then died without
    running release. The next process to try this flat sees the stale row
    and reaps it before its own INSERT.
    """
    # Fake holder row, timestamped well past the threshold.
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s
    stale_ts = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (42, stale_ts, 99999),
    )

    # Should NOT raise — stale row is reaped first, then we INSERT fresh.
    acquire_apply_lock(tmp_db, flat_id=42)

    row = tmp_db.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = 42"
    ).fetchone()
    assert row["pid"] == os.getpid()


def test_acquire_does_not_clear_fresh_lock(tmp_db, monkeypatch):
    """A row inside the threshold must NOT be reaped — it's a real holder."""
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s
    fresh_ts = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (42, fresh_ts, 99999),
    )

    with pytest.raises(AlreadyAppliedError, match="apply already in progress"):
        acquire_apply_lock(tmp_db, flat_id=42)


def test_acquire_only_clears_stale_for_target_flat(tmp_db, monkeypatch):
    """Stale-row reaping must not cascade into other unrelated flats.

    Insert two stale rows; acquire flat 42; flat 99's stale row must
    survive because it's a different flat. (Cleanup is bounded to the
    minimum that unblocks the requested flat — global sweeps would
    interact awkwardly with parallel acquires for sibling flats.)
    """
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s
    stale_ts = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (42, stale_ts, 11111),
    )
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (99, stale_ts, 22222),
    )

    acquire_apply_lock(tmp_db, flat_id=42)

    # 42 is now held by us.
    row42 = tmp_db.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = 42"
    ).fetchone()
    assert row42["pid"] == os.getpid()

    # 99 still has its old stale row — bounded cleanup.
    row99 = tmp_db.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = 99"
    ).fetchone()
    assert row99["pid"] == 22222


# --------------------------------------------------------------------------- #
# Integration with apply_to_flat — the lock must wrap filler.fill().
# --------------------------------------------------------------------------- #


def _profile_for_test(tmp_path: Path):
    from flatpilot.profile import Attachments, Profile, save_profile

    pdf = tmp_path / ".flatpilot" / "attachments" / "schufa.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    profile = Profile.load_example().model_copy(
        update={
            "city": "Berlin",
            "attachments": Attachments(default=["schufa.pdf"], per_platform={}),
        }
    )
    save_profile(profile)
    return profile


def _write_template(tmp_path: Path) -> None:
    tpl_dir = tmp_path / ".flatpilot" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "wg-gesucht.md").write_text(
        "Hallo, ich bin interessiert an $title.\n",
        encoding="utf-8",
    )


def _insert_flat(conn, *, external_id: str = "ext-1") -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            rent_warm_eur, rooms, district,
            scraped_at, first_seen_at, requires_wbs
        ) VALUES (?, 'wg-gesucht', 'https://x/1', 'T1', 900.0, 2.0, 'X', ?, ?, 0)
        """,
        (external_id, now, now),
    )
    return int(cur.lastrowid)


def test_apply_to_flat_acquires_and_releases_lock_on_success(
    tmp_db, tmp_path, monkeypatch
):
    """apply_to_flat must DELETE the lock row on the success path."""
    from flatpilot.apply import apply_to_flat
    from flatpilot.fillers.base import FillReport

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)

    def fake_fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None):
        # While the filler is running, the lock row must exist.
        row = tmp_db.execute(
            "SELECT pid FROM apply_locks WHERE flat_id = ?", (flat_id,)
        ).fetchone()
        assert row is not None, "lock row must be held during filler.fill()"
        return FillReport(
            platform="wg-gesucht",
            listing_url=listing_url,
            contact_url=listing_url,
            fields_filled={"message": message},
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=None,
            submitted=True,
            started_at="2026-04-29T00:00:00+00:00",
            finished_at="2026-04-29T00:00:01+00:00",
        )

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill", fake_fill, raising=True
    )

    apply_to_flat(flat_id, dry_run=False)

    # After return, the lock must be released.
    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM apply_locks WHERE flat_id = ?", (flat_id,)
    ).fetchone()[0]
    assert cnt == 0


def test_apply_to_flat_releases_lock_on_filler_error(tmp_db, tmp_path, monkeypatch):
    """When filler.fill() raises, the lock must still be released."""
    from flatpilot.apply import apply_to_flat
    from flatpilot.fillers.base import NotAuthenticatedError

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)

    def boom(self, *args, **kwargs):
        raise NotAuthenticatedError("session expired")

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill", boom, raising=True
    )

    with pytest.raises(NotAuthenticatedError):
        apply_to_flat(flat_id, dry_run=False)

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM apply_locks WHERE flat_id = ?", (flat_id,)
    ).fetchone()[0]
    assert cnt == 0


def test_apply_to_flat_dry_run_does_not_touch_lock(tmp_db, tmp_path, monkeypatch):
    """Dry-run is a preview — no lock acquired (matches existing behavior
    where dry-run also skips the AlreadyAppliedError SELECT check).
    """
    from flatpilot.apply import apply_to_flat
    from flatpilot.fillers.base import FillReport

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)

    def fake_fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None):
        return FillReport(
            platform="wg-gesucht",
            listing_url=listing_url,
            contact_url=listing_url,
            fields_filled={},
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=None,
            submitted=False,
            started_at="2026-04-29T00:00:00+00:00",
            finished_at="2026-04-29T00:00:01+00:00",
        )

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill", fake_fill, raising=True
    )

    apply_to_flat(flat_id, dry_run=True)

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM apply_locks WHERE flat_id = ?", (flat_id,)
    ).fetchone()[0]
    assert cnt == 0


# --------------------------------------------------------------------------- #
# Cross-process race test using multiprocessing.Process.
#
# SQLite WAL is process-safe. Two real OS processes opening the same
# database file race on INSERT INTO apply_locks; exactly one wins.
# This is the test that proves the lock works across the actual
# CLI-vs-dashboard topology, not just across threads.
# --------------------------------------------------------------------------- #


def _hold_lock_in_child(db_path: str, flat_id: int, acquired_evt, release_evt):
    """Top-level (picklable) child entry point — runs in a separate process.

    Opens its own sqlite3 connection (not the parent's thread-local one),
    acquires the lock, signals the parent, then waits for the parent to
    signal it can release and exit.
    """
    import sqlite3
    from datetime import UTC, datetime  # noqa: F401  — used by acquire_apply_lock indirectly

    from flatpilot.apply import acquire_apply_lock

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        acquire_apply_lock(conn, flat_id)
        acquired_evt.set()
        # Hold the lock until parent finishes its assertion. 5s ceiling
        # so a buggy parent doesn't leak this process forever.
        release_evt.wait(timeout=5)
    finally:
        conn.close()


def test_concurrent_apply_lock_across_processes(tmp_db):
    """Real cross-process race: child holds lock, parent's acquire raises.

    Uses multiprocessing.Process — a separate OS process with its own
    SQLite connection — so we exercise WAL's process-level
    serialization. This is what stops two ``flatpilot apply 42``
    invocations (one CLI, one dashboard subprocess) from both reaching
    ``filler.fill(submit=True)`` and double-messaging the landlord.
    """
    # `tmp_db` monkeypatches `flatpilot.config.DB_PATH` to a tmp file
    # before this body runs. `from flatpilot.config import DB_PATH`
    # reads the *current* attribute value off the module, so this
    # picks up the patched path — NOT the user's real
    # ~/.flatpilot/flatpilot.db. Pass as a string to the child; the
    # spawn-mode child re-imports flatpilot.config without the
    # monkeypatch, so it must use this argument directly.
    from flatpilot.config import DB_PATH

    # The child needs the apply_locks schema; tmp_db's init_db() in the
    # parent created it on the same file already.
    db_path = str(DB_PATH)

    ctx = mp.get_context("spawn")  # explicit; default on macOS, portable
    acquired_evt = ctx.Event()
    release_evt = ctx.Event()
    proc = ctx.Process(
        target=_hold_lock_in_child,
        args=(db_path, 42, acquired_evt, release_evt),
    )
    proc.start()
    try:
        assert acquired_evt.wait(timeout=10), "child never acquired lock"
        with pytest.raises(AlreadyAppliedError, match="apply already in progress"):
            acquire_apply_lock(tmp_db, flat_id=42)
    finally:
        release_evt.set()
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            pytest.fail("child process did not exit cleanly")
    assert proc.exitcode == 0, f"child exited with {proc.exitcode}"
```

- [ ] **Step 2.2: Run tests, verify they fail in expected ways**

Run: `.venv/bin/pytest tests/test_apply_lock.py -v`

Expected: pytest fails at **collection time** with `ImportError: cannot import name 'acquire_apply_lock' from 'flatpilot.apply'`, because the test file's top-level `from flatpilot.apply import (..., acquire_apply_lock, release_apply_lock)` runs before any individual test body. None of the tests will even start executing — pytest reports a collection error for the whole file.

**Sanity check:** if pytest reports `0 collected` with NO error message, the file has a syntax error — fix it. If pytest reports any test as PASSED at this stage, the implementation has leaked — investigate. The expected report is something like `ERROR tests/test_apply_lock.py - ImportError: cannot import name 'acquire_apply_lock'` followed by a non-zero exit.

- [ ] **Step 2.3: Register `apply_locks` schema**

Append to `src/flatpilot/schemas.py` (after the existing `SCHEMAS["applications"] = APPLICATIONS_CREATE_SQL` line):

```python


# A row exists for the duration of an in-flight apply for ``flat_id``.
# Cross-process correctness layer: two FlatPilot processes (CLI +
# dashboard) racing on the same flat both attempt INSERT — the
# PRIMARY KEY makes exactly one win, the loser raises
# AlreadyAppliedError. Stale rows older than ``apply_timeout_sec() +
# 60`` are reaped on next acquire to recover from process crashes
# (kill -9, OS panic) so the slot doesn't block forever.
APPLY_LOCKS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS apply_locks (
    flat_id INTEGER PRIMARY KEY,
    acquired_at TEXT NOT NULL,
    pid INTEGER NOT NULL
)
"""

SCHEMAS["apply_locks"] = APPLY_LOCKS_CREATE_SQL
```

- [ ] **Step 2.4: Add lock primitives + integrate into `apply_to_flat`**

In `src/flatpilot/apply.py`:

**Add imports** (alongside existing ones):

```python
import sqlite3
from datetime import UTC, datetime, timedelta
```

(`UTC` and `datetime` are already imported. Add `timedelta` and `sqlite3`. Group with the existing `from datetime import ...` line via ruff isort.)

**Add `acquire_apply_lock` and `release_apply_lock`** between the existing `class AlreadyAppliedError` definition and the existing `ApplyStatus = Literal[...]` line. The helpers raise `AlreadyAppliedError`, so they must be defined AFTER it.

Final top-to-bottom order in `apply.py` after this step:

1. `DEFAULT_APPLY_TIMEOUT_SEC` constant (from Task 1)
2. `apply_timeout_sec()` function (from Task 1)
3. `class AlreadyAppliedError` (existing — unchanged)
4. `acquire_apply_lock` (new — Task 2)
5. `release_apply_lock` (new — Task 2)
6. `ApplyStatus` / `ApplyOutcome` (existing — unchanged)
7. `apply_to_flat` (existing — modified per Step 2.4 below)
8. `_record_application` (existing — unchanged)

Code to insert (between `class AlreadyAppliedError` and `ApplyStatus = Literal[...]`):

```python


def acquire_apply_lock(conn, flat_id: int) -> None:
    """Take a cross-process lock on ``flat_id``.

    Two FlatPilot processes — typically the CLI ``flatpilot apply 42``
    and the dashboard's ``POST /api/applications`` subprocess — racing
    on the same flat must not both reach ``filler.fill(submit=True)``,
    or the landlord receives two messages. The ``apply_locks`` table
    has ``flat_id`` as PRIMARY KEY: ``INSERT`` from the second caller
    raises ``sqlite3.IntegrityError`` and we surface it as
    ``AlreadyAppliedError`` with a message distinct from the existing
    "already has a submitted application" path.

    Stale rows (acquired_at older than ``apply_timeout_sec() + 60``)
    are reaped before the INSERT so a process crash (kill -9) doesn't
    permanently block future applies for that flat. The buffer of 60s
    on top of the apply timeout means we never reap a row that could
    legitimately still be held by a slow apply. Reaping is bounded to
    the target flat — siblings are left alone so parallel acquires for
    different flats don't trip on each other.
    """
    threshold_ts = (
        datetime.now(UTC) - timedelta(seconds=apply_timeout_sec() + 60)
    ).isoformat()
    conn.execute(
        "DELETE FROM apply_locks WHERE flat_id = ? AND acquired_at < ?",
        (flat_id, threshold_ts),
    )
    try:
        conn.execute(
            "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
            (flat_id, datetime.now(UTC).isoformat(), os.getpid()),
        )
    except sqlite3.IntegrityError as exc:
        existing = conn.execute(
            "SELECT pid, acquired_at FROM apply_locks WHERE flat_id = ?",
            (flat_id,),
        ).fetchone()
        if existing is not None:
            msg = (
                f"flat {flat_id} apply already in progress "
                f"(pid={existing['pid']}, since {existing['acquired_at']})"
            )
        else:
            # Race window: holder released between our INSERT failing
            # and our SELECT. Bubble up rather than auto-retry inside an
            # exception handler — a fresh user click is the explicit
            # recovery path.
            msg = f"flat {flat_id} apply already in progress (lock contention; please retry)"
        raise AlreadyAppliedError(msg) from exc


def release_apply_lock(conn, flat_id: int) -> None:
    """Release the cross-process lock for ``flat_id``.

    No-op if no row exists (e.g. the caller never successfully acquired,
    or a stale-row sweep already reaped it). Always safe in a finally.
    """
    conn.execute("DELETE FROM apply_locks WHERE flat_id = ?", (flat_id,))
```

**Modify `apply_to_flat`** — wrap the live-submit branch in `acquire_apply_lock` / `release_apply_lock`. The dry-run path is unchanged. The existing SELECT-check and its `AlreadyAppliedError("...already has a submitted application...")` are unchanged. Only the live-submit code path between the SELECT-check and the function's return is wrapped.

Replace the existing live-submit body (lines 123-179 in the current file — the SELECT-check, then the `try: filler.fill(...) except FillError ... else ...` block) with:

```python
    existing = conn.execute(
        "SELECT id FROM applications WHERE flat_id = ? AND status = 'submitted' LIMIT 1",
        (flat_id,),
    ).fetchone()
    if existing is not None:
        raise AlreadyAppliedError(
            f"flat {flat_id} already has a submitted application "
            f"(application_id={existing['id']}); refusing to double-submit"
        )

    acquire_apply_lock(conn, flat_id)
    try:
        try:
            report = filler.fill(
                listing_url=str(flat["listing_url"]),
                message=message,
                attachments=attachments,
                submit=True,
                screenshot_dir=screenshot_dir,
            )
        except FillError as exc:
            application_id = _record_application(
                conn,
                profile=profile,
                flat=flat,
                message=message,
                attachments=attachments,
                status="failed",
                notes=str(exc),
            )
            logger.warning(
                "apply: flat_id=%d failed: %s (application_id=%d)",
                flat_id,
                exc,
                application_id,
            )
            # Re-raise so the CLI / server caller can handle it (exit code,
            # 5xx response, etc.). The row write above is the durable trail.
            raise
        else:
            application_id = _record_application(
                conn,
                profile=profile,
                flat=flat,
                message=report.message_sent,
                attachments=report.attachments_sent,
                status="submitted",
                notes=None,
            )
            logger.info(
                "apply: flat_id=%d submitted (application_id=%d)",
                flat_id,
                application_id,
            )
            return ApplyOutcome(
                status="submitted",
                application_id=application_id,
                fill_report=report,
            )
    finally:
        release_apply_lock(conn, flat_id)
```

- [ ] **Step 2.5: Run new tests, verify they pass**

Run: `.venv/bin/pytest tests/test_apply_lock.py -v`

Expected: ALL pass.

The cross-process test takes ~1-2s because the spawned child runs through Python startup. That's acceptable; flag it if it ever exceeds 5s on local hardware.

- [ ] **Step 2.6: Run apply orchestrator regression suite**

Run: `.venv/bin/pytest tests/test_apply_orchestrator.py tests/test_apply_cli.py tests/test_applications.py -v`

Expected: ALL pass. The existing `test_apply_refuses_double_submit_when_submitted_row_exists` still passes — its SELECT-check path is untouched. Other apply tests still pass — the lock is acquired-and-released without observable effect on success cases.

- [ ] **Step 2.7: Run full suite + ruff**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/flatpilot/apply.py src/flatpilot/schemas.py tests/test_apply_lock.py`

Expected:
- pytest: `203 + N passed` where N = the count of new tests in `test_apply_lock.py` (12 by my count: 7 unit + 3 integration + 1 dry-run + 1 multiprocessing = 12).
- ruff: zero errors on the changed files.

- [ ] **Step 2.8: Commit**

```bash
git add src/flatpilot/apply.py src/flatpilot/schemas.py tests/test_apply_lock.py
git commit -m "FlatPilot-bxm: cross-process apply lock via apply_locks table"
```

Commit body should mention:
- New `apply_locks` table (flat_id PRIMARY KEY, acquired_at TEXT, pid INTEGER) — registered in `flatpilot.schemas` so `init_db()` creates it.
- `acquire_apply_lock(conn, flat_id)` / `release_apply_lock(conn, flat_id)` in `flatpilot.apply`.
- Stale-row reap on every acquire bounded by `apply_timeout_sec() + 60s` (recovers from kill -9'd holders).
- `apply_to_flat` wraps `filler.fill(submit=True)` in acquire/finally-release; dry-run path unchanged.
- `AlreadyAppliedError` messages disambiguated: "already has a submitted application" (post-submit) vs "apply already in progress" (in-flight, lock-held).
- Cross-process test uses `multiprocessing.Process` spawn context — exercises WAL-level process serialization, not just thread-level.

---

## Task 3 — FlatPilot-da5: watchdog for stuck `_inflight_flats` slots

**Files:**
- Modify: `src/flatpilot/server.py` — change `_inflight_flats: set[int]` to `dict[int, float]`; add check-on-acquire watchdog sweep.
- Modify: `tests/test_server.py` — three existing tests reference the `set` shape (`_inflight_flats.add`, `1 not in _inflight_flats`); update to dict semantics. Add `test_inflight_watchdog_clears_stale_slot_on_acquire`.

The watchdog threshold is `apply_timeout_sec() + 60` — same formula as bxm's stale-row reaper, so a future operator change to `FLATPILOT_APPLY_TIMEOUT_SEC` flows through both layers consistently.

### Steps

- [ ] **Step 3.1: Write the new failing watchdog test**

Add to `tests/test_server.py`, near the existing `test_post_apply_releases_slot_when_spawn_raises`:

```python
def test_inflight_watchdog_clears_stale_slot_on_acquire(tmp_db, monkeypatch):
    """A slot held longer than apply_timeout_sec() + 60 is auto-cleared.

    Defense-in-depth: today _spawn_apply has a subprocess timeout so a
    slot can't actually leak. But a future refactor that removes the
    timeout, or an in-process apply path, could leave a slot held
    forever. The check-on-acquire sweep ensures the dashboard
    self-heals on the next request rather than requiring a restart.

    The test seeds a stale slot directly into _inflight_flats (using
    the dict shape introduced for da5) and asserts a fresh acquire
    succeeds — i.e. the sweep ran, removed the stale entry, and the
    new request was let through.
    """
    import time
    from unittest.mock import patch

    import flatpilot.server as server_mod

    _seed_match_with_profile(tmp_db)

    # Set a tight timeout so the watchdog threshold (timeout + 60) is
    # short enough to step over with monkeypatched time.
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "1")  # threshold = 61s

    # Pre-seed a stale slot. The watchdog must reap it.
    fake_now = time.monotonic()
    server_mod._inflight_flats[1] = fake_now - 1000.0  # ~1000s old

    fake_result = {"ok": True, "stdout_tail": "ok", "returncode": 0}

    try:
        with (
            _running_server(tmp_db) as port,
            patch("flatpilot.server._spawn_apply", return_value=fake_result),
        ):
            url = f"http://127.0.0.1:{port}/api/applications"
            body = json.dumps({"flat_id": 1}).encode("utf-8")
            status, _ = _post(url, body)

        # The stale slot was reaped; the new request was let through to
        # _spawn_apply (which we mocked) and succeeded.
        assert status == 200
    finally:
        # Defensive: don't leak module-level state across tests.
        server_mod._inflight_flats.clear()


def test_inflight_watchdog_does_not_clear_fresh_slot(tmp_db, monkeypatch):
    """A slot held for less than the threshold must NOT be reaped.

    Otherwise the watchdog would race with legitimate slow applies and
    auto-409 could turn into auto-200-with-double-submit.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from unittest.mock import patch

    import flatpilot.server as server_mod

    _seed_match_with_profile(tmp_db)

    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s

    in_spawn = threading.Event()
    release = threading.Event()

    def slow_spawn(flat_id):
        in_spawn.set()
        if not release.wait(timeout=5):
            raise AssertionError("test never released first spawn")
        return {"ok": True, "stdout_tail": "ok", "returncode": 0}

    body = json.dumps({"flat_id": 1}).encode("utf-8")

    try:
        with (
            _running_server(tmp_db) as port,
            patch("flatpilot.server._spawn_apply", side_effect=slow_spawn),
        ):
            url = f"http://127.0.0.1:{port}/api/applications"
            with ThreadPoolExecutor(max_workers=2) as ex:
                f1 = ex.submit(_post, url, body)
                try:
                    assert in_spawn.wait(timeout=2)
                    # Second request fires while first is still held —
                    # the held slot is fresh (< 120s), so the sweep
                    # must NOT reap it. Second request gets 409.
                    f2 = ex.submit(_post, url, body)
                    r2 = f2.result(timeout=2)
                finally:
                    release.set()
                r1 = f1.result(timeout=10)

        assert r1[0] == 200
        assert r2[0] == 409, f"fresh slot was incorrectly reaped: {r2}"
    finally:
        server_mod._inflight_flats.clear()
```

- [ ] **Step 3.2: Run new tests, verify they fail**

Run: `.venv/bin/pytest tests/test_server.py::test_inflight_watchdog_clears_stale_slot_on_acquire tests/test_server.py::test_inflight_watchdog_does_not_clear_fresh_slot -v`

Expected: BOTH fail. The first test fails because today `_inflight_flats` is a `set` and `set[int] = float` is a TypeError (`unhashable type: 'float'` is wrong direction — actually `set[1] = X` raises `TypeError: 'set' object does not support item assignment`). The second test fails for the same reason — fixture setup hits a TypeError.

If the failure is `KeyError` or `set.add` related, that means the watchdog logic was implemented in a previous step — investigate. Expected error class: `TypeError: 'set' object does not support item assignment`.

- [ ] **Step 3.3: Convert `_inflight_flats` to dict + add watchdog sweep**

In `src/flatpilot/server.py`:

**Add import** (with the existing `import threading`):

```python
import time
```

**Replace** the existing `_inflight_flats` declaration block (lines 105-124, the comment block plus the two-line declaration) with:

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
# Slots are tracked as flat_id -> monotonic acquire time so the
# watchdog can reap orphaned slots on the next acquire. APPLY's
# subprocess timeout already bounds _spawn_apply, so the dict can't
# actually leak today; the watchdog is defense-in-depth against a
# future refactor that removes the subprocess timeout or replaces
# _spawn_apply with a genuinely-blocking in-process call.
#
# Cross-process race (CLI + dashboard on the same flat) is handled by
# the apply_locks SQLite table — see flatpilot.apply.acquire_apply_lock.
_inflight_lock = threading.Lock()
_inflight_flats: dict[int, float] = {}

# Buffer above the apply subprocess timeout — same formula as the
# apply_locks stale-row reaper. Pulling from apply_timeout_sec()
# (which honors FLATPILOT_APPLY_TIMEOUT_SEC) keeps the watchdog and
# the DB lock self-consistent.
_INFLIGHT_WATCHDOG_BUFFER_SEC = 60


def _inflight_watchdog_threshold_sec() -> float:
    return apply_timeout_sec() + _INFLIGHT_WATCHDOG_BUFFER_SEC
```

**Update the acquire/release** in `_handle_apply` (currently lines 175-193). Replace the existing `with _inflight_lock: ... try: ... finally: with _inflight_lock: _inflight_flats.discard(flat_id)` block with:

```python
        # Claim an in-flight slot for this flat. If another request is
        # already applying to it (and the slot is fresh), fail fast with
        # 409 — don't queue, the caller will see the (eventually)
        # submitted row on the next dashboard refresh.
        #
        # Sweep stale slots first (held longer than apply_timeout +
        # buffer) so a future code path that genuinely hangs doesn't
        # block all subsequent applies for that flat until restart.
        now = time.monotonic()
        with _inflight_lock:
            threshold = _inflight_watchdog_threshold_sec()
            stale = [
                fid
                for fid, acquired_at in _inflight_flats.items()
                if now - acquired_at > threshold
            ]
            for fid in stale:
                logger.warning(
                    "apply: watchdog clearing stale in-flight slot for flat_id=%d",
                    fid,
                )
                _inflight_flats.pop(fid, None)
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
            _inflight_flats[flat_id] = now
        try:
            result = _spawn_apply(flat_id)
        finally:
            with _inflight_lock:
                _inflight_flats.pop(flat_id, None)
```

- [ ] **Step 3.4: Update existing tests for the dict shape**

Three existing tests reference the `set` shape:
1. `test_post_apply_releases_slot_when_spawn_raises` (line 471 in current test_server.py) — `assert 1 not in server_mod._inflight_flats` still works on a dict (membership test). **No change needed.**
2. `test_post_apply_releases_slot_after_completion_so_retry_succeeds` — doesn't touch `_inflight_flats` directly. **No change needed.**
3. `test_post_apply_rejects_concurrent_request_for_same_flat` — doesn't touch `_inflight_flats` directly. **No change needed.**

Verify by `grep _inflight_flats tests/test_server.py` — every reference should be a `not in` test, which works on dicts.

If any reference uses `.add()` or `.discard()`, fix it: `dict[fid] = time.monotonic()` and `dict.pop(fid, None)` respectively.

- [ ] **Step 3.5: Run all server tests, verify everything passes**

Run: `.venv/bin/pytest tests/test_server.py -v`

Expected: ALL pass — original 16 tests + 2 new watchdog tests.

If a previously-passing test fails after the dict conversion, the failure is almost certainly because the test seeds `_inflight_flats` directly somewhere we missed. Search and fix.

- [ ] **Step 3.6: Run full suite + ruff**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/flatpilot/server.py tests/test_server.py`

Expected:
- pytest: full suite passes. New count = 197 (origin/main) + 6 (Task 1) + 12 (Task 2) + 2 (Task 3) = **217 passed**.
- ruff: zero errors on changed files.

- [ ] **Step 3.7: Commit**

```bash
git add src/flatpilot/server.py tests/test_server.py
git commit -m "FlatPilot-da5: watchdog for stuck _inflight_flats slots in dashboard"
```

Commit body should mention:
- `_inflight_flats: set[int]` → `dict[int, float]` (flat_id → monotonic acquire time).
- Check-on-acquire sweep reaps slots older than `apply_timeout_sec() + 60` — same formula as the bxm DB lock reaper, so both layers honor `FLATPILOT_APPLY_TIMEOUT_SEC` consistently.
- Defense-in-depth: today `_spawn_apply`'s subprocess timeout means the dict can't actually leak; watchdog protects against a future refactor that removes the timeout or replaces the subprocess with a genuinely-blocking in-process call.
- Two new tests: stale slot reaped on acquire, fresh slot NOT reaped on parallel acquire.

---

## Final Branch Verification

Before opening the PR:

- [ ] **F.1: Full quality gate**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/flatpilot tests`

Expected:
- pytest: **217 passed** (197 baseline + 20 new).
- ruff: 14 errors (all in pre-existing untouched files tracked by FlatPilot-4wk; **none** in files this branch modified).

If ruff reports more than 14, it means this branch introduced a new lint error — fix it before pushing.

- [ ] **F.2: Diff reasonability check**

Run: `git log origin/main..HEAD --oneline` — should show exactly 4 commits (plan-doc + Task 1 + Task 2 + Task 3, in that order).

Run: `git diff --stat origin/main..HEAD` — sanity-check the file footprint matches the File Structure table at the top of this plan.

- [ ] **F.3: Branch-level code review**

Dispatch `superpowers:code-reviewer` (opus) with the prompt: "review feat/apply-server-hardening-round-2 against origin/main". Apply Important findings as additional commits before opening the PR. Skip Nit-level findings unless they cluster.

---

## Self-Review Checklist (run before handing this plan to an implementer)

1. **Spec coverage:**
   - [x] FlatPilot-c3e (configurable timeout) → Task 1.
   - [x] FlatPilot-bxm (cross-process lock) → Task 2.
   - [x] FlatPilot-da5 (watchdog) → Task 3.
   - [x] Dead-code nit (unreachable bytes branch) → Task 1, step 1.4.
2. **Placeholder scan:** no "TBD", no "Add appropriate error handling", no "similar to Task N".
3. **Type/name consistency:**
   - `apply_timeout_sec()` is the helper name throughout (not `_apply_timeout_sec`, not `get_apply_timeout`).
   - `acquire_apply_lock` / `release_apply_lock` (public, no underscore prefix) — they're called from tests and the multiprocessing child entry point.
   - `apply_locks` is the table name (lowercase, plural — matches `applications`, `matches`, `flats`).
   - `_inflight_flats` keeps its leading underscore (module-private) but changes type from `set[int]` to `dict[int, float]`.
   - `DEFAULT_APPLY_TIMEOUT_SEC = 180` (constant, all caps).
   - `_INFLIGHT_WATCHDOG_BUFFER_SEC = 60` (constant, all caps, leading underscore for module-private).
4. **Order coherence:** c3e first (introduces `apply_timeout_sec()`), bxm second (uses it for stale threshold), da5 third (uses it for watchdog threshold). Reverse order would force placeholder values into bxm/da5 that c3e then has to replace.
