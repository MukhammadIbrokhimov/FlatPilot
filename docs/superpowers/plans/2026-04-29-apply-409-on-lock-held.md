# Apply: HTTP 409 on cross-process lock contention — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Bead:** FlatPilot-wsp (depends on closed FlatPilot-bxm + FlatPilot-da5).

**Goal:** When the dashboard's `POST /api/applications` spawns `flatpilot apply <id>` and the cross-process apply lock is already held by another process, the dashboard must respond **HTTP 409 Conflict** (semantically: "apply already in progress, retry later") instead of the current **HTTP 500 Internal Server Error**. The post-submit duplicate-row case (already-completed application) and every other failure mode (filler errors, timeouts, profile-missing, …) keep responding 500. The CLI exit-code disambiguation that drives this is also exposed for direct CLI users.

**Architecture:**

1. Introduce `ApplyLockHeldError(AlreadyAppliedError)` as a subclass in `flatpilot.apply`. Only `acquire_apply_lock` raises this subclass; `apply_to_flat`'s post-submit duplicate-row check keeps raising plain `AlreadyAppliedError`. Subclassing (vs introducing a parallel error type) keeps every existing `except AlreadyAppliedError` and `pytest.raises(AlreadyAppliedError, ...)` callsite working unchanged because `isinstance(ApplyLockHeldError(), AlreadyAppliedError) is True`.
2. Export `APPLY_LOCK_HELD_EXIT = 4` from `flatpilot.apply`. The CLI's `apply` command `try`/`except` block adds a subclass-first clause that exits with this code; the parent `except AlreadyAppliedError` clause keeps exiting 1. **Order is load-bearing**: Python evaluates `except` clauses top-to-bottom, so the subclass clause MUST come first or the exit-4 branch is unreachable.
3. The dashboard's `_handle_apply` already has `result["returncode"]` from `_spawn_apply`. Add a single dispatch step before the existing `ok ? 200 : 500` mapping: `if result["returncode"] == 4: 409`. The explicit returncode-4 branch is load-bearing because lock-held returns `ok=False` — the original `OK if ok else 500` ternary unconditionally maps it to 500. The replacement must check returncode == 4 explicitly; collapsing back to a binary ok/!ok shape (or omitting the new branch) silently regresses 409 → 500.

**Tech Stack:**
- Python 3.11+, typer for CLI, `http.server` for the dashboard, pytest with the existing `tmp_db` conftest fixture, `unittest.mock.patch` for CLI/server mocks.
- No new dependencies. No schema changes.

**Exit-code choice — exit 4, deliberate:** sysexits.h reserves `EX_TEMPFAIL=75` for "temporary failure, retry later," which semantically matches lock contention. Python's stdlib does not honor sysexits, and the FlatPilot CLI already uses 0/1/2/130. **4 is chosen because it is the smallest unused integer in this CLI's existing convention** — closer in style to typer/click's `2 = misuse` than to sysexits' `75 = tempfail`. Adopting sysexits here in isolation would create a single sysexits-style exit code surrounded by ad-hoc ones, which is worse than internally consistent but unconventional. Future CLI-wide exit-code overhaul can revisit.

**Subclass vs sentinel attribute:** subclass is correct (advisor-confirmed). Don't relitigate. Subclass plays nicely with `isinstance`, with `pytest.raises(SubclassOrParent)`, and with type checkers. A sentinel attribute (`exc.is_lock_contention = True`) would require every consumer to read the attribute and is fragile to AttributeError.

---

## File Structure

**Files modified by this plan (4):**

- `src/flatpilot/apply.py` — add `APPLY_LOCK_HELD_EXIT` constant, `ApplyLockHeldError` subclass, switch `acquire_apply_lock`'s raise from parent to subclass.
- `src/flatpilot/cli.py` — extend `apply` command imports; add subclass-first `except ApplyLockHeldError` clause exiting `APPLY_LOCK_HELD_EXIT`.
- `src/flatpilot/server.py` — extend `_handle_apply` with returncode-4 → HTTP 409 dispatch, before the existing ok/500 branch. Import `APPLY_LOCK_HELD_EXIT` for the constant cross-reference.
- `tests/test_apply_lock.py` — three tests: subclass identity, parent isinstance preservation, constant value.
- `tests/test_apply_orchestrator.py` — one regression-pin test: post-submit duplicate-row path raises plain `AlreadyAppliedError` and is NOT `ApplyLockHeldError`.
- `tests/test_apply_cli.py` — two tests: exit-4 on subclass, exit-1 regression on parent.
- `tests/test_server.py` — three tests: returncode-4 → 409, returncode-1 → 500 regression, returncode-None timeout → 500 regression.

**No file is created or deleted.** Every change is additive on top of PR #28.

---

## Task 1: `ApplyLockHeldError` subclass + `APPLY_LOCK_HELD_EXIT` constant in `flatpilot.apply`

**Files:**
- Modify: `src/flatpilot/apply.py` (after line 57, after `STALE_APPLY_BUFFER_SEC`; the class definition currently at lines 94–102; the raise at line 154)
- Test (extend): `tests/test_apply_lock.py`
- Test (extend): `tests/test_apply_orchestrator.py`

**Run pytest with `.venv/bin/pytest`** (the system pytest lacks deps).

- [ ] **Step 1.1: Write the failing test — subclass and constant exist, lock-held raises subclass**

Append to `tests/test_apply_lock.py` (end of file is fine; the file is grouped by behavior, not strictly ordered). Use the existing `tmp_db` fixture from `conftest.py`.

```python
# --------------------------------------------------------------------------- #
# FlatPilot-wsp: subclass + constant for HTTP-409 disambiguation.
# acquire_apply_lock raises ApplyLockHeldError (a subclass of
# AlreadyAppliedError) so cli.py can exit APPLY_LOCK_HELD_EXIT (4) and
# server._handle_apply can map that returncode to HTTP 409. The post-submit
# duplicate-row path in apply_to_flat is unaffected — see
# test_apply_orchestrator.py for that pin.
# --------------------------------------------------------------------------- #


def test_apply_lock_held_exit_constant_is_four():
    """APPLY_LOCK_HELD_EXIT is the exit code cli.py emits on lock contention.

    The HTTP layer in server._handle_apply maps returncode == APPLY_LOCK_HELD_EXIT
    to HTTP 409. A silent change to this value is a silent dashboard regression
    — pin it.
    """
    from flatpilot.apply import APPLY_LOCK_HELD_EXIT

    assert APPLY_LOCK_HELD_EXIT == 4


def test_acquire_apply_lock_raises_apply_lock_held_error_subclass(tmp_db):
    """Lock contention surfaces as ApplyLockHeldError, not just AlreadyAppliedError.

    Disambiguates from the post-submit duplicate-row path (which keeps
    raising plain AlreadyAppliedError). The subclass MUST still be an
    instance of AlreadyAppliedError so existing `except AlreadyAppliedError`
    callers and `pytest.raises(AlreadyAppliedError, ...)` assertions
    keep working unchanged.
    """
    from flatpilot.apply import (
        AlreadyAppliedError,
        ApplyLockHeldError,
        acquire_apply_lock,
    )

    acquire_apply_lock(tmp_db, flat_id=42)
    with pytest.raises(ApplyLockHeldError) as exc_info:
        acquire_apply_lock(tmp_db, flat_id=42)

    # Subclass identity preserved.
    assert isinstance(exc_info.value, AlreadyAppliedError)
    # Existing message contract from test_acquire_second_call_raises_in_progress_message.
    assert "apply already in progress" in str(exc_info.value)
```

- [ ] **Step 1.2: Run the new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_apply_lock.py::test_apply_lock_held_exit_constant_is_four tests/test_apply_lock.py::test_acquire_apply_lock_raises_apply_lock_held_error_subclass -v
```

Expected: BOTH tests FAIL with `ImportError: cannot import name 'APPLY_LOCK_HELD_EXIT' from 'flatpilot.apply'` and `ImportError: cannot import name 'ApplyLockHeldError' from 'flatpilot.apply'`. **If either passes, STOP** — the symbol must not pre-exist; a passing red-test means the test is not exercising the new behavior. Do not soften.

- [ ] **Step 1.3: Add the regression-pin test for the duplicate-row path**

The post-submit duplicate-row path at `apply.py:236` must keep raising **plain** `AlreadyAppliedError`, not the subclass. Without this pin, a future "tidy up" could promote that raise to the subclass and silently flip its HTTP behavior from 500 → 409, which is wrong (a completed application is not "in progress").

This test is a **regression pin**, not a TDD-red driver. After Task 1's implementation lands, it should pass first run because the line 236 raise is intentionally not changed. Pinning it now ensures Task 1's diff stays scoped.

Append to `tests/test_apply_orchestrator.py` immediately after `test_apply_refuses_double_submit_when_submitted_row_exists`:

```python
def test_double_submit_raises_plain_already_applied_not_subclass(tmp_db, tmp_path, monkeypatch):
    """Regression pin: the post-submit duplicate-row path raises PLAIN
    AlreadyAppliedError, NOT the ApplyLockHeldError subclass.

    The two raise sites in apply.py have semantically different meanings
    that drive different HTTP statuses:

    * acquire_apply_lock contention      → ApplyLockHeldError → exit 4 → HTTP 409
    * post-submit duplicate-row check    → AlreadyAppliedError → exit 1 → HTTP 500

    Promoting the duplicate-row raise to the subclass would silently flip
    its dashboard mapping to 409 ("retry later"), which is wrong — a
    completed application should NOT be retried.
    """
    from flatpilot.apply import (
        AlreadyAppliedError,
        ApplyLockHeldError,
        apply_to_flat,
    )

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, submitted=True)

    # First apply lands the submitted row.
    apply_to_flat(flat_id, dry_run=False)

    # Second apply must raise the parent class, NOT the subclass.
    with pytest.raises(AlreadyAppliedError) as exc_info:
        apply_to_flat(flat_id, dry_run=False)

    assert not isinstance(exc_info.value, ApplyLockHeldError), (
        "post-submit duplicate-row path must raise plain AlreadyAppliedError; "
        "promoting it to ApplyLockHeldError would flip HTTP 500 → 409 silently"
    )
    assert f"flat {flat_id} already has" in str(exc_info.value)
```

- [ ] **Step 1.4: Run the regression-pin to verify it currently fails for the right reason**

```bash
.venv/bin/pytest tests/test_apply_orchestrator.py::test_double_submit_raises_plain_already_applied_not_subclass -v
```

Expected: FAIL with `ImportError: cannot import name 'ApplyLockHeldError'`. (After Task 1's implementation lands and `ApplyLockHeldError` is exported, this test will pass because the line 236 raise stays plain.)

- [ ] **Step 1.5: Implement subclass + constant in `apply.py`**

Edit `src/flatpilot/apply.py`. Two distinct changes:

**Change 1 — add `APPLY_LOCK_HELD_EXIT` next to `STALE_APPLY_BUFFER_SEC`.** Currently lines 50–58 read:

```python
# Buffer added to apply_timeout_sec() before reaping stale state.
# Both the apply_locks stale-row reaper (acquire_apply_lock below)
# and the dashboard's _inflight_watchdog_threshold_sec
# (flatpilot/server.py) read this exact name. Keep them aligned: a
# future tuning of the slack window must not drift the two layers
# apart, and the constant lives in flatpilot.apply (the lower-level
# module) so server.py is the importer, not the source of truth.
STALE_APPLY_BUFFER_SEC = 60
```

Add immediately after, before `def apply_timeout_sec`:

```python
# Exit code emitted by `flatpilot apply` when the cross-process
# apply_locks lock for the target flat is already held by another
# process. The dashboard's _handle_apply maps this returncode to
# HTTP 409 (Conflict) — semantically "apply already in progress,
# retry later" — instead of the generic 500. Exit 1 stays reserved
# for everything else (post-submit duplicate row, FillError,
# ProfileMissingError, AttachmentError, TemplateError). FlatPilot-wsp.
APPLY_LOCK_HELD_EXIT = 4
```

**Change 2 — add `ApplyLockHeldError` subclass.** Currently lines 94–102 read:

```python
class AlreadyAppliedError(RuntimeError):
    """Raised when a flat already has a successful submitted application.

    The schema allows multiple ``applications`` rows per flat (so a failed
    submit followed by a retry both leave a trail), but two ``status='submitted'``
    rows mean we sent the landlord two messages — almost always a mistake.
    The CLI / dashboard surfaces this as a user-correctable error so a
    double-click or two open dashboards can't accidentally double-submit.
    """
```

Append immediately after the existing class (still before `def acquire_apply_lock`):

```python
class ApplyLockHeldError(AlreadyAppliedError):
    """Raised by :func:`acquire_apply_lock` when the cross-process lock
    for ``flat_id`` is already held by another process.

    Subclasses :class:`AlreadyAppliedError` so existing
    ``except AlreadyAppliedError`` and
    ``pytest.raises(AlreadyAppliedError, ...)`` callsites keep matching.
    The CLI uses this discrimination to exit ``APPLY_LOCK_HELD_EXIT``
    (4) instead of 1, which the dashboard's ``_handle_apply`` maps to
    HTTP 409 instead of 500.

    The post-submit duplicate-row path in :func:`apply_to_flat`
    intentionally keeps raising plain :class:`AlreadyAppliedError` —
    that case is a logically-completed application (not a transient
    contention), and exit 1 / HTTP 500 is the correct surface (the
    user should not retry). Promoting that raise to this subclass
    would be a behavioral regression. FlatPilot-wsp.
    """
```

**Change 3 — switch `acquire_apply_lock`'s raise.** Currently `apply.py:154` reads:

```python
        raise AlreadyAppliedError(msg) from exc
```

Change to:

```python
        raise ApplyLockHeldError(msg) from exc
```

Leave `apply_to_flat`'s raise at line 236 (`raise AlreadyAppliedError(...)`) untouched — that's the duplicate-row path the regression pin protects.

- [ ] **Step 1.6: Run the three new tests to verify they pass**

```bash
.venv/bin/pytest tests/test_apply_lock.py::test_apply_lock_held_exit_constant_is_four tests/test_apply_lock.py::test_acquire_apply_lock_raises_apply_lock_held_error_subclass tests/test_apply_orchestrator.py::test_double_submit_raises_plain_already_applied_not_subclass -v
```

Expected: all 3 PASS.

- [ ] **Step 1.7: Run the full test_apply_lock.py and test_apply_orchestrator.py suites to verify no existing test regressed**

The existing assertions `pytest.raises(AlreadyAppliedError, match="apply already in progress")` at `test_apply_lock.py:80` and `:153` MUST keep passing because `ApplyLockHeldError` IS-A `AlreadyAppliedError`. If they regress, do NOT change them — the implementation is wrong.

```bash
.venv/bin/pytest tests/test_apply_lock.py tests/test_apply_orchestrator.py -v
```

Expected: full suite PASSES (existing tests + 3 new ones). Total before this PR: 219. After Task 1: 222.

- [ ] **Step 1.8: Commit**

```bash
git add src/flatpilot/apply.py tests/test_apply_lock.py tests/test_apply_orchestrator.py
git commit -m "FlatPilot-wsp: introduce ApplyLockHeldError subclass + APPLY_LOCK_HELD_EXIT

Disambiguates the lock-contention raise (acquire_apply_lock) from the
post-submit duplicate-row raise (apply_to_flat). The subclass keeps
existing AlreadyAppliedError callers and pytest.raises assertions
working. APPLY_LOCK_HELD_EXIT = 4 is the wire-format the CLI and
dashboard layers will agree on in subsequent commits."
```

---

## Task 2: CLI exits `APPLY_LOCK_HELD_EXIT` on the subclass

**Files:**
- Modify: `src/flatpilot/cli.py` (imports at line 18-22; apply command's `except` block at line 581-583)
- Test (extend): `tests/test_apply_cli.py`

- [ ] **Step 2.1: Write the failing test — subclass exits 4, parent regression exits 1**

Append to `tests/test_apply_cli.py`:

```python
def test_apply_lock_held_error_exits_apply_lock_held_exit():
    """Lock contention exits APPLY_LOCK_HELD_EXIT (4), not 1.

    The dashboard's _handle_apply translates this returncode to HTTP 409.
    Direct CLI users see a yellow message + exit 4 they can branch on.
    """
    from flatpilot.apply import APPLY_LOCK_HELD_EXIT, ApplyLockHeldError

    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=ApplyLockHeldError(
            "flat 5 apply already in progress (pid=12345, since 2026-04-29T10:00:00+00:00)"
        ),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == APPLY_LOCK_HELD_EXIT == 4, result.output
    assert "apply already in progress" in result.output


def test_apply_plain_already_applied_error_still_exits_one():
    """Regression: the post-submit duplicate-row path keeps exit 1.

    Two AlreadyAppliedError causes have intentionally different exit
    codes (and HTTP statuses): the lock-contention case is transient
    (retry-able, 409); the duplicate-row case is terminal (do-not-retry,
    500). Don't conflate them.
    """
    from flatpilot.apply import AlreadyAppliedError

    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=AlreadyAppliedError(
            "flat 5 already has a submitted application (application_id=42); "
            "refusing to double-submit"
        ),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 1, result.output
    assert "already has a submitted application" in result.output
```

- [ ] **Step 2.2: Run the new tests to verify they fail**

```bash
.venv/bin/pytest tests/test_apply_cli.py::test_apply_lock_held_error_exits_apply_lock_held_exit tests/test_apply_cli.py::test_apply_plain_already_applied_error_still_exits_one -v
```

Expected:
- `test_apply_lock_held_error_exits_apply_lock_held_exit` FAILS with `assert 1 == 4` (cli.py currently catches the subclass via the parent `except AlreadyAppliedError` clause and exits 1).
- `test_apply_plain_already_applied_error_still_exits_one` PASSES (current behavior already matches — this is a regression pin).

**If `test_apply_lock_held_error_exits_apply_lock_held_exit` passes**, STOP. The whole point of this PR is that the CLI doesn't yet exit 4 on lock contention. A passing red-test means something is already routing differently and the test is wrong — investigate before continuing.

- [ ] **Step 2.3: Update CLI imports**

Edit `src/flatpilot/cli.py`. Currently lines 18–22 read:

```python
from flatpilot.apply import (
    AlreadyAppliedError,
    ApplyOutcome,
    apply_to_flat,
)
```

Change to (alphabetical, mirrors the existing pattern):

```python
from flatpilot.apply import (
    APPLY_LOCK_HELD_EXIT,
    AlreadyAppliedError,
    ApplyLockHeldError,
    ApplyOutcome,
    apply_to_flat,
)
```

- [ ] **Step 2.4: Add the subclass-first `except` clause**

Currently `cli.py:581-583` reads:

```python
    except AlreadyAppliedError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
```

Replace with (note: subclass first; order is load-bearing — Python matches the first compatible `except`):

```python
    except ApplyLockHeldError as exc:
        # Lock-contention case (acquire_apply_lock). Exit
        # APPLY_LOCK_HELD_EXIT (4) so server._handle_apply can translate
        # to HTTP 409 ("apply already in progress, retry later"). MUST
        # come before the parent except — Python matches first compatible
        # clause. FlatPilot-wsp.
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(APPLY_LOCK_HELD_EXIT) from exc
    except AlreadyAppliedError as exc:
        # Post-submit duplicate-row case (apply_to_flat). Application
        # already completed earlier; do NOT retry. Exit 1 → HTTP 500.
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
```

- [ ] **Step 2.5: Run the two new tests to verify they pass**

```bash
.venv/bin/pytest tests/test_apply_cli.py::test_apply_lock_held_error_exits_apply_lock_held_exit tests/test_apply_cli.py::test_apply_plain_already_applied_error_still_exits_one -v
```

Expected: both PASS.

- [ ] **Step 2.6: Run the full test_apply_cli.py suite to confirm no existing exit-code assertion regressed**

The existing apply CLI tests at lines 42, 56, 69, 78, 90 of `test_apply_cli.py` all assert specific exit codes. None of them go through `AlreadyAppliedError` so all should pass.

```bash
.venv/bin/pytest tests/test_apply_cli.py -v
```

Expected: all PASS (5 existing + 2 new = 7 total).

- [ ] **Step 2.7: Commit**

```bash
git add src/flatpilot/cli.py tests/test_apply_cli.py
git commit -m "FlatPilot-wsp: cli apply exits APPLY_LOCK_HELD_EXIT on ApplyLockHeldError

Subclass-first except clause routes lock contention to exit 4 while
keeping the post-submit duplicate-row case (plain AlreadyAppliedError)
on exit 1. Order is load-bearing — Python matches the first compatible
except clause, so subclass must come before parent."
```

---

## Task 3: Dashboard `_handle_apply` maps `returncode == APPLY_LOCK_HELD_EXIT` → HTTP 409

**Files:**
- Modify: `src/flatpilot/server.py` (imports at line 43; `_handle_apply` final dispatch at line 218)
- Test (extend): `tests/test_server.py`

- [ ] **Step 3.1: Write the failing test — returncode 4 → 409 (and regression pins)**

Append to `tests/test_server.py` immediately after `test_post_apply_subprocess_failure_returns_500`:

```python
def test_post_apply_returncode_four_returns_409(tmp_db):
    """Cross-process apply lock contention surfaces as HTTP 409.

    When the spawned `flatpilot apply <id>` subprocess exits
    APPLY_LOCK_HELD_EXIT (4) — i.e. acquire_apply_lock raised
    ApplyLockHeldError because another FlatPilot process is mid-apply
    on the same flat — the dashboard responds 409 ("apply already in
    progress, retry later"), the same status the in-process
    _inflight_flats fast-path returns. Without this mapping, the
    response is 500, which is operationally misleading (500 means
    "unexpected server error"; the apply subprocess behaved correctly).
    FlatPilot-wsp.
    """
    from flatpilot.apply import APPLY_LOCK_HELD_EXIT

    _seed_match_with_profile(tmp_db)
    fake_result = {
        "ok": False,
        "stdout_tail": (
            "flat 1 apply already in progress (pid=99999, "
            "since 2026-04-29T10:00:00+00:00)"
        ),
        "returncode": APPLY_LOCK_HELD_EXIT,
    }

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", return_value=fake_result),
    ):
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications",
            body=json.dumps({"flat_id": 1}).encode("utf-8"),
        )

    assert status == 409
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "apply already in progress" in payload["stdout_tail"]


def test_post_apply_subprocess_timeout_still_returns_500(tmp_db):
    """Regression: subprocess timeout (returncode is None on TimeoutExpired)
    must keep returning 500, not 409.

    `None == APPLY_LOCK_HELD_EXIT` is False so the fall-through is correct
    today; this test pins the ordering so a future refactor that conflates
    "no returncode" with "non-zero returncode" doesn't silently regress
    timeout from 500 to 409.
    """
    _seed_match_with_profile(tmp_db)
    fake_result = {
        "ok": False,
        "stdout_tail": "timed out after 180s\n<truncated playwright output>",
        "returncode": None,
    }

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", return_value=fake_result),
    ):
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications",
            body=json.dumps({"flat_id": 1}).encode("utf-8"),
        )

    assert status == 500
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "timed out" in payload["stdout_tail"]
```

(`test_post_apply_subprocess_failure_returns_500` already pins returncode 1 → 500, so we don't add a third regression test for that.)

- [ ] **Step 3.2: Run the new tests to verify they fail / pass appropriately**

```bash
.venv/bin/pytest tests/test_server.py::test_post_apply_returncode_four_returns_409 tests/test_server.py::test_post_apply_subprocess_timeout_still_returns_500 -v
```

Expected:
- `test_post_apply_returncode_four_returns_409` FAILS with `assert 500 == 409` (current code maps `ok=False` → 500 regardless of returncode).
- `test_post_apply_subprocess_timeout_still_returns_500` PASSES (current behavior — regression pin only).

**If `test_post_apply_returncode_four_returns_409` passes**, STOP. The whole point of this Task is that the dashboard does not yet differentiate exit-4 from any other failure.

- [ ] **Step 3.3: Add the import for `APPLY_LOCK_HELD_EXIT` in `server.py`**

Currently `server.py:43` reads:

```python
from flatpilot.apply import STALE_APPLY_BUFFER_SEC, apply_timeout_sec
```

Change to (alphabetical with constants first):

```python
from flatpilot.apply import (
    APPLY_LOCK_HELD_EXIT,
    STALE_APPLY_BUFFER_SEC,
    apply_timeout_sec,
)
```

- [ ] **Step 3.4: Replace the dispatch at the end of `_handle_apply`**

Currently `server.py:218-219` reads:

```python
        status = HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status, result)
```

Replace with (explicit returncode-4 branch is required — the original `OK if ok else 500` ternary maps lock-held to 500 because lock-held has `ok=False`; collapsing back to a binary ok/!ok shape silently regresses 409 → 500):

```python
        # Map subprocess returncode to HTTP status.
        #
        # APPLY_LOCK_HELD_EXIT (4) means acquire_apply_lock raised
        # ApplyLockHeldError because another FlatPilot process holds the
        # cross-process lock for this flat — semantically "in progress,
        # retry later" → 409 Conflict, mirroring the in-process
        # _inflight_flats fast-path 409 above.
        #
        # The explicit returncode==4 branch is load-bearing: lock-held
        # exits with ok=False, so the previous `OK if ok else 500`
        # ternary mapped it to 500. Collapsing back to a binary ok/!ok
        # shape silently regresses 409 → 500. None (subprocess timeout)
        # and 1 (filler error, ProfileMissing, post-submit duplicate
        # row, ...) both fall through to the 500 branch.
        # FlatPilot-wsp.
        if result["returncode"] == APPLY_LOCK_HELD_EXIT:
            status = HTTPStatus.CONFLICT
        elif result["ok"]:
            status = HTTPStatus.OK
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status, result)
```

- [ ] **Step 3.5: Run the new tests to verify they pass**

```bash
.venv/bin/pytest tests/test_server.py::test_post_apply_returncode_four_returns_409 tests/test_server.py::test_post_apply_subprocess_timeout_still_returns_500 -v
```

Expected: both PASS.

- [ ] **Step 3.6: Run the full test_server.py suite — no existing 200/500/409/400 assertion may regress**

```bash
.venv/bin/pytest tests/test_server.py -v
```

Critical regression pins to verify in output:
- `test_post_apply_spawns_subprocess_and_returns_result` → 200 (returncode 0, ok=True).
- `test_post_apply_subprocess_failure_returns_500` → 500 (returncode 1, ok=False).
- `test_post_apply_invalid_body_returns_400` → 400 (no subprocess invoked).
- The existing `test_post_apply_rejects_concurrent_request_for_same_flat` at `tests/test_server.py:374` — in-process `_inflight_flats` double-click 409 — must still pass (this is the original 409 path that PR #28 added; the new returncode==4 → 409 dispatch is a parallel path for the cross-process case).

Expected: all pass.

- [ ] **Step 3.7: Run the FULL test suite to verify no cross-cutting regression**

```bash
.venv/bin/pytest -v
```

Expected pass count: 219 (pre-PR baseline) + 3 (Task 1) + 2 (Task 2) + 2 (Task 3) = **226 passing**. Zero failures.

Pre-existing 14 ruff errors in untouched modules (FlatPilot-4wk) are NOT this PR's concern. Ruff is run separately:

```bash
.venv/bin/ruff check src/flatpilot/apply.py src/flatpilot/cli.py src/flatpilot/server.py tests/test_apply_lock.py tests/test_apply_orchestrator.py tests/test_apply_cli.py tests/test_server.py
```

Expected: `All checks passed!` for the files this PR touches.

- [ ] **Step 3.8: Commit**

```bash
git add src/flatpilot/server.py tests/test_server.py
git commit -m "FlatPilot-wsp: dashboard returns 409 on apply-lock contention

_handle_apply now maps subprocess returncode == APPLY_LOCK_HELD_EXIT
(4) to HTTP 409 Conflict, mirroring the in-process _inflight_flats
fast-path 409. Other non-zero returncodes (1 for filler / profile /
duplicate-row errors, None for timeout) keep returning 500. Order is
load-bearing — lock-held has ok=False so the returncode check must
precede the ok/500 fallback."
```

---

## Plan-Doc Commit (precedes implementation)

Per FlatPilot's PR conventions, the plan doc is committed first on the branch as a single bead-tagged commit before any implementation.

```bash
git add docs/superpowers/plans/2026-04-29-apply-409-on-lock-held.md
git commit -m "FlatPilot-wsp: write implementation plan"
```

This commit happens **before** Task 1 begins. It's listed last here in the plan to keep Tasks 1–3 in execution order.

---

## Self-Review

**1. Spec coverage (FlatPilot-wsp description vs. tasks):**

| Spec requirement | Covered by |
| --- | --- |
| Add `APPLY_LOCK_HELD_EXIT = 4` constant in `flatpilot.apply` | Task 1 (Step 1.5 Change 1) + tested in 1.1 |
| `cli.py` exits `APPLY_LOCK_HELD_EXIT` on `AlreadyAppliedError` from lock-acquire | Task 2 (Step 2.4) + tested in 2.1 first test |
| Post-submit SELECT-check raises stay exit 1 | Task 1 keeps `apply_to_flat:236` untouched + Task 2 keeps parent except clause + tested in 1.3 + 2.1 second test |
| `server._spawn_apply` / `_handle_apply` map returncode == 4 → HTTP 409 | Task 3 (Step 3.4) + tested in 3.1 first test |
| Tests on both sides | CLI side: Task 2.1; server side: Task 3.1 |

✅ All spec requirements have a task and at least one test.

**2. Placeholder scan:** No "TBD" / "implement later" / "add appropriate error handling" anywhere. Every step has the actual code. ✅

**3. Type / name consistency:**
- `ApplyLockHeldError` (single spelling, used in apply.py raise + cli.py except + tests).
- `APPLY_LOCK_HELD_EXIT` (single spelling, value `4`, asserted in test 1.1, imported in cli.py 2.3 and server.py 3.3, used as both exit value and HTTP-mapping key).
- `apply_to_flat` raise at `apply.py:236` is **never** changed in this plan — explicitly noted three times (Step 1.5 Change 3, Step 1.7, the regression pin in 1.3).
- Subclass `except` precedes parent in cli.py (Step 2.4) — load-bearing, called out twice (Architecture + step comment).
- Returncode dispatch precedes ok/500 in server.py (Step 3.4) — load-bearing, called out twice.

✅ All names and orders consistent.

**4. TDD-red integrity:**
- Step 1.2: tests fail with ImportError because the symbols don't exist yet. Real failure.
- Step 1.4: regression-pin fails with ImportError until Task 1's implementation lands. Real failure.
- Step 2.2: subclass-routing test fails because cli.py routes through parent `except`; regression-pin passes immediately (intentional).
- Step 3.2: 409 test fails because all `ok=False` paths currently 500; timeout-pin passes immediately (intentional).

Each TDD-red step explicitly documents the expected failure mode + a STOP instruction if the test passes when it shouldn't (test 2.1 first test, test 3.1 first test). ✅

**5. Test count:**
- Baseline (origin/main, post-PR-28): 219 passing.
- Task 1 adds: 3 tests (constant, subclass identity, regression pin in orchestrator).
- Task 2 adds: 2 tests (subclass exits 4, parent regression exits 1).
- Task 3 adds: 2 tests (returncode 4 → 409, returncode None timeout → 500 regression).
- Final expected: **226 passing**. ✅

**6. Files actually touched (sanity check vs. File Structure section):** apply.py, cli.py, server.py, test_apply_lock.py, test_apply_orchestrator.py, test_apply_cli.py, test_server.py. 7 files. Matches. ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-29-apply-409-on-lock-held.md`.

**Recommended approach:** subagent-driven-development. Three independent tasks (each scoped to its own files: apply.py for Task 1, cli.py for Task 2, server.py for Task 3), each with TDD red→green→commit. After each task lands, dispatch a combined-stage spec+quality reviewer (sonnet, OK for ≤2-file tasks). Final-branch opus review over the whole branch before opening the PR.
