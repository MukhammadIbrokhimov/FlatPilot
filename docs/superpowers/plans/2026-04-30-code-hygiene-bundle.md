# Code Hygiene Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `FlatPilot-4wk` (12 ruff errors) + `FlatPilot-e83` (annotate 5 `conn` helpers) as a single chore PR with one commit.

**Architecture:** Pure hygiene change — no behavior change, no new tests. Auto-fix the linter findings, sweep `conn: sqlite3.Connection` across the 5 lock/record helpers, verify with `ruff check .` (zero errors) and `pytest` (suite still green). One commit referencing both bead IDs, opened as a PR per CLAUDE.md.

**Tech Stack:** Python 3.11+, ruff 0.15.12, pytest, sqlite3 (stdlib).

**User constraint:** Single commit. No TDD ceremony — there is no behavior to test; verification is `ruff` + the existing suite.

---

## File Structure

- **Modify:** `src/flatpilot/__main__.py` — ruff I001 import sort.
- **Modify:** `src/flatpilot/database.py` — ruff I001 import sort.
- **Modify:** `src/flatpilot/log.py` — ruff I001 import sort.
- **Modify:** `src/flatpilot/matcher/distance.py` — ruff I001 + 3× UP017 (`timezone.utc` → `UTC`).
- **Modify:** `src/flatpilot/matcher/filters.py` — ruff I001 + UP035 (`Callable` from `collections.abc`).
- **Modify:** `src/flatpilot/notifications/email.py` — ruff I001 import sort.
- **Modify:** `src/flatpilot/notifications/telegram.py` — ruff I001 import sort.
- **Modify:** `src/flatpilot/stats.py` — UP017.
- **Modify:** `src/flatpilot/apply.py:134, 186, 329` — annotate `conn: sqlite3.Connection` on three helpers (`sqlite3` already imported).
- **Modify:** `src/flatpilot/applications.py:18, 43` — add `import sqlite3`, annotate `conn: sqlite3.Connection` on two helpers.

No new files. No tests added — these changes are stylistic / type-only and verified by tooling.

---

### Task 1: Branch from `origin/main`

**Files:** none yet.

- [ ] **Step 1.1: Stash the dirty `.beads/issues.jsonl`**

```bash
git stash push -u -m "wip beads sync" -- .beads/issues.jsonl
```

Expected: "Saved working directory and index state On main: wip beads sync".

- [ ] **Step 1.2: Fetch and create branch**

```bash
git fetch origin
git checkout -b chore/code-hygiene origin/main
```

Expected: "Switched to a new branch 'chore/code-hygiene'", branch tracks nothing yet.

- [ ] **Step 1.3: Restore beads file (so bd commands work locally)**

```bash
git stash pop
```

Expected: `.beads/issues.jsonl` shows as modified again on the new branch (not staged).

---

### Task 2: Apply ruff auto-fixes (FlatPilot-4wk)

**Files:** the 9 modules listed in the File Structure section.

- [ ] **Step 2.1: Confirm starting error count**

```bash
ruff check . --output-format=concise
```

Expected: ends with `Found 12 errors.` and `[*] 12 fixable with the \`--fix\` option.`

- [ ] **Step 2.2: Run auto-fix**

```bash
ruff check . --fix
```

Expected: `Found 12 errors (12 fixed, 0 remaining).`

- [ ] **Step 2.3: Verify zero errors**

```bash
ruff check .
```

Expected: `All checks passed!` and exit 0.

- [ ] **Step 2.4: Inspect the diff for sanity**

```bash
git diff --stat
```

Expected: 9 files modified across `src/flatpilot/`, only import-ordering and `timezone.utc → UTC` / `typing.Callable → collections.abc.Callable` changes. No behavior change — verify by skimming `git diff src/flatpilot/matcher/distance.py` and `src/flatpilot/matcher/filters.py` to confirm only the flagged lines moved.

---

### Task 3: Annotate `conn` parameter (FlatPilot-e83)

**Files:** `src/flatpilot/apply.py`, `src/flatpilot/applications.py`.

- [ ] **Step 3.1: Edit `src/flatpilot/apply.py:134`**

Change the signature of `acquire_apply_lock` from:

```python
def acquire_apply_lock(conn, flat_id: int) -> None:
```

to:

```python
def acquire_apply_lock(conn: sqlite3.Connection, flat_id: int) -> None:
```

(`sqlite3` is already imported at line 28; no import edit needed.)

- [ ] **Step 3.2: Edit `src/flatpilot/apply.py:186`**

Change the signature of `release_apply_lock` from:

```python
def release_apply_lock(conn, flat_id: int) -> None:
```

to:

```python
def release_apply_lock(conn: sqlite3.Connection, flat_id: int) -> None:
```

- [ ] **Step 3.3: Edit `src/flatpilot/apply.py:329-330`**

Change the signature of `_record_application` from:

```python
def _record_application(
    conn,
    *,
    profile: Profile,
```

to:

```python
def _record_application(
    conn: sqlite3.Connection,
    *,
    profile: Profile,
```

- [ ] **Step 3.4: Add `import sqlite3` to `src/flatpilot/applications.py`**

The current import block (lines 10–13) is:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
```

Add `import sqlite3` so it becomes:

```python
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal
```

(Stdlib imports come before stdlib-from imports per ruff's isort convention.)

- [ ] **Step 3.5: Edit `src/flatpilot/applications.py:18`**

Change the signature of `record_skip` from:

```python
def record_skip(conn, *, match_id: int, profile_hash: str) -> None:
```

to:

```python
def record_skip(conn: sqlite3.Connection, *, match_id: int, profile_hash: str) -> None:
```

- [ ] **Step 3.6: Edit `src/flatpilot/applications.py:43-44`**

Change the signature of `record_response` from:

```python
def record_response(
    conn,
    *,
    application_id: int,
```

to:

```python
def record_response(
    conn: sqlite3.Connection,
    *,
    application_id: int,
```

---

### Task 4: Verify

**Files:** none.

- [ ] **Step 4.1: Re-run ruff (catches any drift from the new `import sqlite3`)**

```bash
ruff check .
```

Expected: `All checks passed!` exit 0. If `applications.py` import order is flagged, run `ruff check . --fix` to normalize and rerun.

- [ ] **Step 4.2: Run the test suite**

```bash
pytest -q
```

Expected: all tests pass (no test added or modified by this PR; we are only verifying we did not regress). If the suite was already green on `main`, it must remain green here.

- [ ] **Step 4.3: Spot-check the diff**

```bash
git diff --stat
git diff src/flatpilot/apply.py src/flatpilot/applications.py
```

Expected: ~10 modified files, ~14–18 changed lines total. No edits outside the planned files.

---

### Task 5: Commit and PR

**Files:** the modified Python files only — explicitly do NOT stage `.beads/issues.jsonl`.

- [ ] **Step 5.1: Stage only the touched Python files**

```bash
git add src/flatpilot/__main__.py \
        src/flatpilot/database.py \
        src/flatpilot/log.py \
        src/flatpilot/matcher/distance.py \
        src/flatpilot/matcher/filters.py \
        src/flatpilot/notifications/email.py \
        src/flatpilot/notifications/telegram.py \
        src/flatpilot/stats.py \
        src/flatpilot/apply.py \
        src/flatpilot/applications.py
git status
```

Expected: 10 staged files; `.beads/issues.jsonl` listed as unstaged (left alone).

- [ ] **Step 5.2: Single commit referencing both bead IDs**

The repo's commit-style for multi-bead chore commits uses a slash-separated ID list (see `ad63d26 FlatPilot-emz/05s/m7x/tl6: auto-apply follow-up polish`):

```bash
git commit -m "$(cat <<'EOF'
FlatPilot-4wk/e83: chore code hygiene

Apply ruff auto-fixes across nine pre-existing modules (import sorting,
datetime.UTC alias, Callable from collections.abc) and annotate the five
sqlite3.Connection helpers in apply.py and applications.py. No behavior
change.

Closes FlatPilot-4wk and FlatPilot-e83.
EOF
)"
```

Expected: one commit on `chore/code-hygiene`, author `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`, no AI trailer.

- [ ] **Step 5.3: Push branch**

```bash
git push -u origin chore/code-hygiene
```

Expected: branch published; PR-create hint shown.

- [ ] **Step 5.4: Open PR**

```bash
gh pr create --base main --head chore/code-hygiene \
  --title "chore: ruff auto-fix + annotate sqlite3.Connection helpers" \
  --body "$(cat <<'EOF'
## Summary
- Apply `ruff check --fix` across nine pre-existing modules (12 errors → 0): import sorting, `datetime.timezone.utc` → `datetime.UTC`, `typing.Callable` → `collections.abc.Callable`.
- Annotate `conn: sqlite3.Connection` on the five lock/record helpers in `apply.py` (`acquire_apply_lock`, `release_apply_lock`, `_record_application`) and `applications.py` (`record_skip`, `record_response`). Adds `import sqlite3` to `applications.py`.

Closes FlatPilot-4wk and FlatPilot-e83. No behavior change.

## Test Plan
- [x] `ruff check .` → `All checks passed!`
- [x] `pytest -q` → suite green
EOF
)"
```

Expected: PR URL printed; PR open against `main`.

- [ ] **Step 5.5: Return PR URL to user, stop.**

Per CLAUDE.md, the human reviews and merges. Do not merge.

**Do NOT run `bd close` yet.** Closing the beads now would mark them done before the PR lands on `main`. The commit body's `Closes FlatPilot-4wk and FlatPilot-e83.` line documents intent; the actual `bd close FlatPilot-4wk FlatPilot-e83` runs only after the human merges the PR.

---

## Self-Review

**Spec coverage:**
- `FlatPilot-4wk` "ruff check . reports zero errors" → Step 2.3 + Step 4.1 verify this.
- `FlatPilot-e83` "uniform sweep across all five callsites" → Steps 3.1–3.6 cover all five helpers with the exact `sqlite3.Connection` type matching `database.get_conn()`'s return.

**Placeholder scan:** No TBDs, no "add appropriate", no "similar to". Each edit shows the actual code.

**Type consistency:** `sqlite3.Connection` is used in all five edits; matches `src/flatpilot/database.py:29` `def get_conn(...) -> sqlite3.Connection`.

**Divergence from beads (intentional):**
- `bd-4wk` description references 14 errors and a manual SIM102 — current `main` shows 12 errors, all auto-fixable. The plan reflects current reality, not the stale description.
- `bd-e83` description places `_record_application` in `applications.py`; it actually lives in `apply.py:329`. Plan reflects current reality.

These divergences should be noted in the PR description's commentary if reviewers ask, but no plan change is needed.

---

## Addendum (post-review scope extension)

Code-quality review surfaced one additional unannotated `conn` parameter not listed in `bd-e83`: `src/flatpilot/notifications/dispatcher.py:92` `_mark_stale_matches_notified(conn, current_hash: str) -> None`. After the planned 5-helper sweep, this was the only bare `conn` parameter remaining anywhere in `src/flatpilot/`, which would have reproduced exactly the "annotated some, not others" inconsistency that motivated `bd-e83`.

User approved including it in this PR. Two extra lines: add `import sqlite3` to the import block of `dispatcher.py` and annotate the parameter. No additional verification beyond the existing `ruff check .` gate (annotations are runtime no-ops).

Final shipped scope: 9 ruff-fixed modules + 6 `conn: sqlite3.Connection` annotations across `apply.py`, `applications.py`, and `dispatcher.py`.
