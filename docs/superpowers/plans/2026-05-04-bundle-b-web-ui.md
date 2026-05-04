# Bundle B — Web UI Phase 5 Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land FastAPI + Next.js + magic-link auth + the three dashboard tabs ported with mutation parity + a Connections page that drives headed-Playwright login per platform. CLI continues to operate as the seed user (`id=1`); new Web UI users get fresh accounts.

**Architecture:** FastAPI server at `src/flatpilot/server/` (replaces today's `server.py` role), Next.js + Tailwind + shadcn/ui frontend at `web/`. Magic-link auth uses `itsdangerous` for signed tokens and signed-cookie sessions. Login engine refactored to a callable driven by an `Awaitable[None]` completion signal — CLI passes a stdin-driven coroutine, FastAPI passes `asyncio.Event.wait()`. Per-user filesystem namespacing applies only to sessions in this PR (`~/.flatpilot/users/<uid>/sessions/<platform>/state.json` for users ≥ 2; user 1 stays at the legacy path). Single-worker uvicorn only. Local-only deployment — public-internet exposure is blocked until `FlatPilot-j1k` ships an allowlist.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, itsdangerous, sqlglot (test-only), pytest, pytest-asyncio, httpx (test client), Playwright (existing), TypeScript 5.x, Next.js 15 App Router, Tailwind CSS, shadcn/ui, Vitest (NOT in this PR — `FlatPilot-biv`), Playwright Test for e2e.

**Branch:** `feat/bundle-b-web-ui` (already created off `main`; design spec committed at `f3d390c`, revised at `40cd3b8` and `af4867a`).

**Spec reference:** `docs/superpowers/specs/2026-05-04-bundle-b-web-ui-design.md`. Every section number cited below (`§3.5`, `§4.2`, etc.) refers to that spec — re-read the relevant section before starting each task. The spec is the source of truth; this plan is the order of operations.

**Commit policy:** **Phase commits, not per-task commits.** Each of the 12 phases below ends with a single commit covering all its tasks. Tasks within a phase are TDD-strict (test → fail → implement → pass) but do not commit until the phase concludes. Hold the branch local until the whole plan is done; do not push until the user explicitly approves.

**Author identity:** Per project CLAUDE.md, commit author is `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. NEVER add `Co-Authored-By: Claude` or any AI trailer. NEVER push directly to `main`. Every commit message references the Beads task IDs (`FlatPilot-ix2m`, `FlatPilot-8jx`, plus the auth work which extends ADR 0001).

---

## File structure

### Files to create

**Backend (`src/flatpilot/server/`):**
- `__init__.py` — package marker.
- `app.py` — FastAPI instance, middleware registration, route registration, startup/shutdown hooks (orphan-Chromium scan, magic-link cleanup).
- `deps.py` — `get_current_user`, `get_db` dependencies.
- `auth.py` — `itsdangerous` serializers for magic-link tokens and session cookies; helpers `issue_magic_link_token`, `verify_magic_link_token`, `sign_session_cookie`, `verify_session_cookie`.
- `email_links.py` — `send_magic_link(email, link)` thin wrapper around `notifications/email.py`.
- `schemas.py` — Pydantic request/response models (`MagicLinkRequest`, `MagicLinkVerify`, `MeResponse`, `MatchOut`, `ApplicationOut`, `ConnectionOut`, etc.).
- `routes/__init__.py` — package marker.
- `routes/auth.py` — `/api/auth/{request,verify,session,me}`.
- `routes/matches.py` — `/api/matches`, `/api/matches/{id}/skip`.
- `routes/applications.py` — `/api/applications`, `/api/applications/{id}/response`.
- `routes/connections.py` — `/api/connections`, `/api/connections/{platform}/{start,done}`. Holds `_pending`, `_pending_tasks`, `_last_result` registries.

**Login engine (`src/flatpilot/sessions/`):**
- `__init__.py` — package marker.
- `login_engine.py` — `run_login_session(...)` plus `LoginResult` enum, `UnknownPlatform` exception, PID-file tracking helpers.
- `platforms.py` — `PlatformLogin` dataclass and `PLATFORMS` registry (`wg-gesucht`, `kleinanzeigen`, `immoscout24`).
- `paths.py` — `session_storage_path(user_id, platform)`.

**CLI additions:**
- (modifications to `src/flatpilot/cli.py` — see "Files to modify" below — for the new `set-email` subcommand)

**Frontend (`web/`):**
- `package.json`, `package-lock.json` (committed), `next.config.js`, `tailwind.config.ts`, `tsconfig.json`, `postcss.config.js`, `.gitignore`.
- `src/middleware.ts` — cookie-presence redirect for protected routes.
- `src/app/layout.tsx` — root layout, `<Toaster>`.
- `src/app/(authed)/layout.tsx` — protected nested layout, fetches `/api/auth/me`, renders top nav, provides `UserContext`.
- `src/app/page.tsx` — Matches/Applied/Responses tabs.
- `src/app/login/page.tsx`, `src/app/verify/page.tsx`, `src/app/connections/page.tsx`.
- `src/components/ui/` — shadcn-generated components (Button, Input, Form, Tabs, Card, Dialog, Toast, Badge, Label, Textarea, Select).
- `src/components/MatchCard.tsx`, `ApplicationRow.tsx`, `ResponseForm.tsx`, `ConnectionRow.tsx`, `TopNav.tsx`, `EmptyState.tsx`.
- `src/lib/api.ts` — typed fetch wrapper, 401 redirect.
- `src/lib/auth.ts` — `useUser()` hook, `UserContext`.
- `src/lib/types.ts` — TypeScript mirrors of the Pydantic response models.
- `tests/e2e/smoke.spec.ts` — single happy-path e2e.
- `tests/e2e/smtp_stub.ts` — local SMTP intercept fixture.
- `playwright.config.ts` — Playwright Test config (separate from the Python Playwright used by login engine).

**Tests (Python, `tests/`):**
- `test_server_auth.py` — magic-link request/verify/me/session.
- `test_server_routes.py` — matches/applications/connections endpoints + isolation + static SQL audit + GET-no-mutate audit.
- `test_login_engine.py` — engine state machine, mocked Playwright.
- `test_server_connections.py` — `/start`, `/done`, `_last_result` paths, beforeunload beacon.
- `test_set_email_cli.py` — new CLI command.
- `test_wizard_email.py` — wizard prompt extension.

**Documentation:**
- `docs/adr/0002-bundle-b-deployment-caveats.md` — public-exposure hard rule.

### Files to modify

- `src/flatpilot/database.py` — extend `init_db()` to register `magic_link_tokens` table, run `email_normalized` ALTER + backfill, run startup magic-link cleanup. Wire orphan-Chromium scan into FastAPI startup hook (lives in `server/app.py` but reads from `~/.flatpilot/runtime/playwright_pids/` written by the engine).
- `src/flatpilot/schemas.py` — register `magic_link_tokens` in `SCHEMAS`. Add `email_normalized` to `users` `CREATE TABLE` string. Register the partial unique index on `email_normalized`. Wire `email_normalized` into `ensure_columns()` ALTER path.
- `src/flatpilot/cli.py` — add `set-email <addr>` subcommand. Add doctor row stub call (the new check lives in `doctor.py`).
- `src/flatpilot/wizard/init.py` — add email prompt step that writes both `email` and `email_normalized` to `users.id=1`.
- `src/flatpilot/doctor.py` — append "seed user has no email" check.
- `src/flatpilot/login.py` (or wherever today's `flatpilot login` lives — verified during Phase 5) — refactored CLI shim around `run_login_session`. CLI surface preserved.
- `pyproject.toml` — add runtime dep `itsdangerous`; add test-only dep `sqlglot`.
- `README.md` — Bundle B section: how to run dev stack, the public-exposure hard rule, link to ADR 0002.

### Files NOT modified

- `src/flatpilot/server.py` (legacy localhost dashboard) — stays alongside the new server. Retiring it is a follow-up PR.
- `src/flatpilot/view.py` (legacy HTML generator) — same.
- `src/flatpilot/scrapers/*.py` — scrapers stay user-unaware; flats are global. They keep reading `state.json` from the legacy path because user 1's storage stays at the legacy path.
- `src/flatpilot/profile.py` — profile.json editing UI is `FlatPilot-2p3` epic territory, not this PR.
- `src/flatpilot/matcher/*.py`, `src/flatpilot/auto_apply.py`, `src/flatpilot/pipeline.py`, `src/flatpilot/applications.py`, `src/flatpilot/apply.py`, `src/flatpilot/notifications/*.py`, `src/flatpilot/stats.py` — already user-scoped after foundations PR. New endpoints reuse them via `apply.py` engine + `run_in_executor`, no behavior change.

---

## Phase index

1. **Schema additions** — `email_normalized` column + `magic_link_tokens` table + migrations + tests.
2. **Auth primitives** — `itsdangerous` setup, token + cookie sign/verify helpers, tests.
3. **FastAPI app skeleton** — `app.py`, `deps.py`, middleware, error handlers, startup hooks. No routes yet.
4. **Auth routes** — `/api/auth/{request,verify,session,me}` with tests.
5. **Login engine refactor** — `sessions/login_engine.py`, `platforms.py`, `paths.py`, CLI shim. Existing CLI behavior preserved.
6. **Connection routes** — `/api/connections/{,start,done}`, registries, beforeunload beacon, late-completion cache.
7. **Matches & applications routes** — `/api/matches`, `/api/applications`, scoping, mutation parity. Static SQL audit + GET-no-mutate audit live here.
8. **CLI additions** — `flatpilot set-email`, wizard prompt, doctor row.
9. **Next.js scaffold** — `web/` package, Tailwind, shadcn init, middleware, layouts, API client.
10. **Next.js pages** — `/login`, `/verify`, `/`, `/connections`.
11. **e2e smoke test** — SMTP stub fixture + happy-path Playwright Test spec.
12. **Docs** — ADR 0002, README section, final acceptance pass.

Each phase ends with one commit. Phase 12 ends with the branch ready for `git push -u origin feat/bundle-b-web-ui` and `gh pr create` (only after explicit user approval).

---

## Phase 1 — Schema additions

**Spec sections:** §4.1, §4.2, §4.3, §11.

**Files:**
- Modify: `src/flatpilot/schemas.py`
- Modify: `src/flatpilot/database.py`
- Create: `tests/test_schema_bundle_b.py`

### Task 1.1 — Register `magic_link_tokens` in SCHEMAS and add `email_normalized` to `users`

- [ ] **Step 1.1.1: Read the foundations spec section §4 to recall the existing `users` SCHEMAS shape.** No code change in this step — context-loading only. Open `src/flatpilot/schemas.py` and find the `users` `CREATE TABLE` string registered after foundations.

- [ ] **Step 1.1.2: Write the failing test for the new `users` `email_normalized` column on a fresh DB.**

Create `tests/test_schema_bundle_b.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flatpilot.database import init_db


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: {"type": r[2], "notnull": r[3], "dflt_value": r[4], "pk": r[5]} for r in rows}


def test_fresh_install_has_email_normalized_column(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        cols = _columns(conn, "users")
        assert "email_normalized" in cols
        assert cols["email_normalized"]["type"] == "TEXT"
        assert cols["email_normalized"]["notnull"] == 0  # nullable for the seed user
    finally:
        conn.close()
```

- [ ] **Step 1.1.3: Run it and confirm it fails.**

```
pytest tests/test_schema_bundle_b.py::test_fresh_install_has_email_normalized_column -xvs
```

Expected: `AssertionError: assert 'email_normalized' in {...}` (the column doesn't exist yet).

- [ ] **Step 1.1.4: Update the `users` `CREATE TABLE` string in `schemas.py` to declare the column inline.**

In `src/flatpilot/schemas.py`, find the existing `USERS_CREATE_SQL` (added by foundations PR) and update to:

```python
USERS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    email_normalized TEXT,
    created_at TEXT NOT NULL
)
"""
```

The `email UNIQUE` constraint is preserved (per spec §4.1, redundant defense). Do NOT add `UNIQUE` on `email_normalized` here — the partial unique index handles it.

- [ ] **Step 1.1.5: Re-run the test, confirm it passes.**

```
pytest tests/test_schema_bundle_b.py::test_fresh_install_has_email_normalized_column -xvs
```

Expected: PASS.

- [ ] **Step 1.1.6: Write the failing test for the partial unique index on `email_normalized`.**

Append to `tests/test_schema_bundle_b.py`:

```python
def test_email_normalized_partial_unique_index(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        # Verify the index exists with the right shape.
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'users'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert "idx_users_email_normalized" in names
        sql = next(r[1] for r in rows if r[0] == "idx_users_email_normalized")
        assert "UNIQUE" in sql.upper()
        assert "WHERE EMAIL_NORMALIZED IS NOT NULL" in sql.upper().replace("\n", " ")
    finally:
        conn.close()


def test_email_normalized_allows_multiple_nulls(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        # Two users with NULL email_normalized must coexist (the partial index excludes them).
        conn.execute(
            "INSERT INTO users (email, email_normalized, created_at) VALUES (?, NULL, ?)",
            ("a@x.com", "2026-05-04T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO users (email, email_normalized, created_at) VALUES (?, NULL, ?)",
            ("b@x.com", "2026-05-04T00:00:00Z"),
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM users WHERE email_normalized IS NULL").fetchone()[0]
        # 1 seed user (id=1, NULL) + 2 just inserted = 3 NULL rows
        assert count == 3
    finally:
        conn.close()


def test_email_normalized_rejects_duplicate_non_null(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            "INSERT INTO users (email, email_normalized, created_at) VALUES (?, ?, ?)",
            ("Foo@x.com", "foo@x.com", "2026-05-04T00:00:00Z"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            # Same normalized form → blocked by partial unique index.
            conn.execute(
                "INSERT INTO users (email, email_normalized, created_at) VALUES (?, ?, ?)",
                ("foo@x.com", "foo@x.com", "2026-05-04T00:00:00Z"),
            )
            conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 1.1.7: Run them, confirm they fail.**

```
pytest tests/test_schema_bundle_b.py -k "email_normalized_partial_unique or email_normalized_allows_multiple_nulls or email_normalized_rejects_duplicate" -xvs
```

Expected: 3 failures (no index registered yet).

- [ ] **Step 1.1.8: Register the partial unique index in SCHEMAS.**

In `src/flatpilot/schemas.py`, after `USERS_CREATE_SQL`, add:

```python
USERS_EMAIL_NORMALIZED_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_normalized
    ON users(email_normalized) WHERE email_normalized IS NOT NULL
"""
```

Then in the `SCHEMAS` registration block, add:

```python
SCHEMAS["idx_users_email_normalized"] = USERS_EMAIL_NORMALIZED_INDEX_SQL
```

(The existing iteration in `init_db()` runs `CREATE TABLE IF NOT EXISTS` for each `SCHEMAS` value; SQLite accepts both `CREATE TABLE` and `CREATE INDEX` strings as long as the underlying execute is generic. Verify this matches the existing pattern from foundations — if foundations chose a separate `INDEXES` registry, follow that pattern instead. Read `database.py:init_db` before deciding.)

- [ ] **Step 1.1.9: Re-run the three tests, confirm they pass.**

```
pytest tests/test_schema_bundle_b.py -k "email_normalized" -xvs
```

Expected: PASS.

- [ ] **Step 1.1.10: Write the failing test for `magic_link_tokens` table existence.**

Append to `tests/test_schema_bundle_b.py`:

```python
def test_fresh_install_has_magic_link_tokens_table(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        cols = _columns(conn, "magic_link_tokens")
        assert set(cols) == {"jti", "email", "issued_at", "expires_at", "used_at"}
        assert cols["jti"]["pk"] == 1
        assert cols["jti"]["notnull"] == 1
        assert cols["email"]["notnull"] == 1
        assert cols["issued_at"]["notnull"] == 1
        assert cols["expires_at"]["notnull"] == 1
        assert cols["used_at"]["notnull"] == 0  # nullable until consumed

        # Index on expires_at exists.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'magic_link_tokens'"
        ).fetchall()
        assert "idx_magic_link_tokens_expires" in {r[0] for r in rows}
    finally:
        conn.close()
```

- [ ] **Step 1.1.11: Run, confirm it fails.**

```
pytest tests/test_schema_bundle_b.py::test_fresh_install_has_magic_link_tokens_table -xvs
```

Expected: `sqlite3.OperationalError: no such table: magic_link_tokens`.

- [ ] **Step 1.1.12: Register the table and its index in SCHEMAS.**

In `src/flatpilot/schemas.py`:

```python
MAGIC_LINK_TOKENS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    jti TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
)
"""

MAGIC_LINK_TOKENS_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_expires
    ON magic_link_tokens(expires_at)
"""

SCHEMAS["magic_link_tokens"] = MAGIC_LINK_TOKENS_CREATE_SQL
SCHEMAS["idx_magic_link_tokens_expires"] = MAGIC_LINK_TOKENS_EXPIRES_INDEX_SQL
```

- [ ] **Step 1.1.13: Re-run, confirm it passes.**

```
pytest tests/test_schema_bundle_b.py::test_fresh_install_has_magic_link_tokens_table -xvs
```

Expected: PASS.

### Task 1.2 — `email_normalized` ALTER + backfill on existing installs

- [ ] **Step 1.2.1: Write the failing test for the upgrade path (pre-Bundle-B DB has no `email_normalized` column).**

Append to `tests/test_schema_bundle_b.py`:

```python
def test_upgrade_adds_email_normalized_column_to_existing_install(tmp_path: Path) -> None:
    """Simulate a foundations-shipped DB (no email_normalized column) and run init_db."""
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        # Pre-Bundle-B users shape (foundations-only).
        conn.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                created_at TEXT NOT NULL
            );
            INSERT INTO users (id, email, created_at) VALUES (1, NULL, '2026-04-01T00:00:00Z');
            INSERT INTO users (id, email, created_at) VALUES (2, 'CLI-User@Example.com', '2026-04-02T00:00:00Z');
        """)
        conn.commit()

        # Run init_db; it must ALTER ADD email_normalized + backfill.
        init_db(conn)

        cols = _columns(conn, "users")
        assert "email_normalized" in cols

        # Seed user 1 stays NULL.
        row = conn.execute("SELECT email, email_normalized FROM users WHERE id = 1").fetchone()
        assert row == (None, None)

        # User 2's email is backfilled to lowercase-normalized.
        row = conn.execute("SELECT email, email_normalized FROM users WHERE id = 2").fetchone()
        assert row == ("CLI-User@Example.com", "cli-user@example.com")
    finally:
        conn.close()


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Running init_db twice on an already-upgraded DB must be a no-op."""
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        init_db(conn)  # second call must not raise
        cols = _columns(conn, "users")
        assert "email_normalized" in cols
    finally:
        conn.close()
```

- [ ] **Step 1.2.2: Run, confirm both fail.**

```
pytest tests/test_schema_bundle_b.py -k "upgrade" -xvs
```

Expected: failures — `email_normalized` column doesn't get added to the pre-existing table; `init_db` either errors or leaves the column missing.

- [ ] **Step 1.2.3: Wire the ALTER ADD COLUMN + backfill into `database.py`.**

In `src/flatpilot/database.py`, find the existing `ensure_columns()` mechanism (added by foundations PR for forward column ALTERs). Add a new entry for `email_normalized`:

```python
# In ensure_columns() or its data structure (foundations PR sets up the pattern):
{
    "table": "users",
    "column": "email_normalized",
    "definition": "TEXT",
    # Backfill query runs after the ALTER, gated on the column being NULL.
    "backfill_sql": (
        "UPDATE users SET email_normalized = LOWER(TRIM(email)) "
        "WHERE email IS NOT NULL AND email_normalized IS NULL"
    ),
}
```

If the foundations pattern doesn't support a `backfill_sql` field, extend `ensure_columns()` to accept one and run it inside the same transaction as the ALTER. Reading the existing implementation first is mandatory before this edit.

Critical: the backfill uses SQLite's `LOWER()` here for the one-shot migration of existing rows — case-different duplicates would already have failed the byte-level `email UNIQUE` constraint at original-insert time, so byte-level lower is safe for the backfill. All *new* writes computed in Python via `email.lower().strip()` (Phase 2 onward) — never trust SQLite's `LOWER()` for non-ASCII.

- [ ] **Step 1.2.4: Re-run both upgrade tests, confirm they pass.**

```
pytest tests/test_schema_bundle_b.py -k "upgrade" -xvs
```

Expected: PASS.

### Task 1.3 — Magic-link cleanup query runs on `init_db`

- [ ] **Step 1.3.1: Write the failing test.**

Append to `tests/test_schema_bundle_b.py`:

```python
def test_init_db_purges_old_magic_link_tokens(tmp_path: Path) -> None:
    """Tokens whose expires_at is more than 1 day in the past are deleted on init_db."""
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        # Seed: one fresh token, one ancient token.
        conn.executescript("""
            INSERT INTO magic_link_tokens (jti, email, issued_at, expires_at, used_at)
                VALUES ('fresh', 'a@x.com', datetime('now'), datetime('now', '+15 minutes'), NULL);
            INSERT INTO magic_link_tokens (jti, email, issued_at, expires_at, used_at)
                VALUES ('ancient', 'b@x.com', datetime('now', '-30 days'),
                        datetime('now', '-30 days', '+15 minutes'), NULL);
        """)
        conn.commit()

        # Re-run init_db; it must purge the ancient row.
        init_db(conn)

        rows = conn.execute("SELECT jti FROM magic_link_tokens").fetchall()
        jtis = {r[0] for r in rows}
        assert jtis == {"fresh"}
    finally:
        conn.close()
```

- [ ] **Step 1.3.2: Run, confirm it fails.**

```
pytest tests/test_schema_bundle_b.py::test_init_db_purges_old_magic_link_tokens -xvs
```

Expected: assertion failure (ancient row still present).

- [ ] **Step 1.3.3: Add the cleanup query to `init_db()`.**

In `src/flatpilot/database.py`, at the end of `init_db()` (after table/index creation and `ensure_columns()`):

```python
def _cleanup_expired_magic_link_tokens(conn: sqlite3.Connection) -> None:
    """Drop magic-link rows whose expires_at is more than 1 day in the past.

    Called from init_db on every server startup. Bounds memory cheaply; the
    table never grows past a few hundred rows on a hobbyist deploy.
    """
    conn.execute(
        "DELETE FROM magic_link_tokens WHERE expires_at < datetime('now', '-1 day')"
    )
    conn.commit()
```

Then call it at the end of `init_db()`:

```python
def init_db(conn: sqlite3.Connection | None = None) -> None:
    # ... existing logic (CREATE TABLE iteration, ensure_default_user, rebuilds, ensure_columns) ...
    _cleanup_expired_magic_link_tokens(conn)
```

- [ ] **Step 1.3.4: Re-run, confirm it passes.**

```
pytest tests/test_schema_bundle_b.py::test_init_db_purges_old_magic_link_tokens -xvs
```

Expected: PASS.

### Task 1.4 — Phase 1 verification

- [ ] **Step 1.4.1: Run the full Phase 1 test suite.**

```
pytest tests/test_schema_bundle_b.py -xvs
```

Expected: all tests PASS.

- [ ] **Step 1.4.2: Run the existing test suite to confirm no regression.**

```
pytest -x
```

Expected: all PASS. The new schema additions are forward-compatible and do not affect existing behavior.

- [ ] **Step 1.4.3: Run linters.**

```
ruff check src/ tests/
mypy src/flatpilot/database.py src/flatpilot/schemas.py
```

Expected: clean.

### Task 1.5 — Phase 1 commit

- [ ] **Step 1.5.1: Stage and commit.**

```bash
git add src/flatpilot/schemas.py src/flatpilot/database.py tests/test_schema_bundle_b.py
git commit -m "FlatPilot-ix2m/8jx: phase 1 — schema for email_normalized + magic_link_tokens

- Add users.email_normalized column (nullable, backfilled from email on
  upgrade) plus a partial unique index on non-NULL values.
- Register magic_link_tokens table for single-use enforcement of
  magic-link tokens, indexed on expires_at for the cleanup sweep.
- Cleanup query (>1 day past expires_at) runs on init_db so the table
  stays bounded without a cron.
- email UNIQUE on users is preserved as redundant defense per spec
  §4.1; partial unique index on email_normalized is the operative
  constraint.
- All new writes will compute email_normalized in Python (Unicode-safe
  lower()); the one-shot backfill uses SQLite LOWER() since byte-level
  duplicates would already have failed the existing email UNIQUE."
```

- [ ] **Step 1.5.2: Verify the commit.**

```bash
git log --oneline -1
git status
```

Expected: one new commit on `feat/bundle-b-web-ui`; working tree clean.

---

## Phase 2 — Auth primitives

**Spec sections:** §3.2, §3.5, §4.2, §5.1, §5.6.

**Files:**
- Modify: `pyproject.toml` (add `itsdangerous` runtime dep, `sqlglot` test-only dep)
- Create: `src/flatpilot/server/__init__.py` (empty package marker)
- Create: `src/flatpilot/server/auth.py`
- Create: `tests/test_server_auth_primitives.py`

### Task 2.1 — Add `itsdangerous` and `sqlglot` deps

- [ ] **Step 2.1.1: Inspect `pyproject.toml` to confirm structure (PEP 621 vs setuptools vs poetry).**

```bash
head -50 pyproject.toml
```

- [ ] **Step 2.1.2: Add `itsdangerous` to runtime deps and `sqlglot` to dev/test deps.**

Edit `pyproject.toml`. Find the `dependencies = [...]` block (or `[tool.poetry.dependencies]` if Poetry is used) and append `itsdangerous>=2.2.0`. Find the dev/test deps block (`[project.optional-dependencies]` `dev` array or equivalent) and append `sqlglot>=23.0.0`.

- [ ] **Step 2.1.3: Install and verify.**

```bash
pip install -e '.[dev]'
python -c "import itsdangerous; import sqlglot; print(itsdangerous.__version__, sqlglot.__version__)"
```

Expected: prints two version numbers without error.

### Task 2.2 — Magic-link token sign/verify

- [ ] **Step 2.2.1: Write the failing test for token round-trip.**

Create `tests/test_server_auth_primitives.py`:

```python
from __future__ import annotations

import time
from unittest import mock

import pytest

from flatpilot.server.auth import (
    InvalidToken,
    issue_magic_link_token,
    verify_magic_link_token,
)


def test_magic_link_token_round_trip() -> None:
    secret = "x" * 64
    payload = issue_magic_link_token("user@example.com", secret=secret)
    decoded = verify_magic_link_token(payload.token, secret=secret, max_age=900)
    assert decoded.email == "user@example.com"
    assert decoded.jti == payload.jti


def test_magic_link_token_rejects_tampered() -> None:
    secret = "x" * 64
    payload = issue_magic_link_token("user@example.com", secret=secret)
    tampered = payload.token[:-2] + ("AA" if payload.token[-2:] != "AA" else "BB")
    with pytest.raises(InvalidToken):
        verify_magic_link_token(tampered, secret=secret, max_age=900)


def test_magic_link_token_rejects_wrong_secret() -> None:
    payload = issue_magic_link_token("user@example.com", secret="x" * 64)
    with pytest.raises(InvalidToken):
        verify_magic_link_token(payload.token, secret="y" * 64, max_age=900)


def test_magic_link_token_rejects_expired() -> None:
    secret = "x" * 64
    payload = issue_magic_link_token("user@example.com", secret=secret)
    # Fast-forward time by patching itsdangerous's time provider.
    future = time.time() + 1000  # max_age is 900s
    with mock.patch("itsdangerous.timed.time.time", return_value=future):
        with pytest.raises(InvalidToken):
            verify_magic_link_token(payload.token, secret=secret, max_age=900)


def test_magic_link_token_jti_is_unique() -> None:
    secret = "x" * 64
    a = issue_magic_link_token("user@example.com", secret=secret)
    b = issue_magic_link_token("user@example.com", secret=secret)
    assert a.jti != b.jti
    assert len(a.jti) == 32  # uuid4 hex


def test_magic_link_token_payload_carries_jti_and_email() -> None:
    secret = "x" * 64
    payload = issue_magic_link_token("user@example.com", secret=secret)
    decoded = verify_magic_link_token(payload.token, secret=secret, max_age=900)
    assert decoded.jti == payload.jti
    assert decoded.email == payload.email
```

- [ ] **Step 2.2.2: Run, confirm all fail (ImportError).**

```
pytest tests/test_server_auth_primitives.py -k "magic_link" -xvs
```

Expected: `ModuleNotFoundError: No module named 'flatpilot.server'` (or similar).

- [ ] **Step 2.2.3: Create the package marker and the auth module.**

Create empty `src/flatpilot/server/__init__.py`.

Create `src/flatpilot/server/auth.py`:

```python
"""Auth primitives for the FastAPI server: magic-link tokens + session cookies.

Sign/verify uses itsdangerous. Magic-link tokens carry a per-request `jti`
(UUID hex) so the database can enforce single-use; session cookies carry the
user_id directly. Both use the same `SECRET` but distinct salts so cross-
domain reuse is impossible.

Token expiry is enforced HERE via `serializer.loads(max_age=...)`. The
`magic_link_tokens` table's `expires_at` column is for cleanup driving only,
not validation (see spec §4.2). Do not add belt-and-braces DB-level expiry
checks to the verify path.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_MAGIC_LINK_SALT = "flatpilot.magic-link"
_SESSION_SALT = "flatpilot.session"


class InvalidToken(Exception):
    """Raised when a magic-link token is tampered, expired, or malformed."""


class InvalidSession(Exception):
    """Raised when a session cookie is tampered or malformed."""


@dataclass(frozen=True)
class MagicLinkPayload:
    token: str
    jti: str
    email: str


@dataclass(frozen=True)
class DecodedMagicLink:
    jti: str
    email: str


def issue_magic_link_token(email: str, *, secret: str) -> MagicLinkPayload:
    """Sign a fresh magic-link token bound to (jti, email).

    The `email` is stored as-given; lookup at verify time normalizes via
    LOWER(TRIM(...)). Caller persists the jti to magic_link_tokens before
    emailing the link.
    """
    jti = uuid4().hex
    serializer = URLSafeTimedSerializer(secret, salt=_MAGIC_LINK_SALT)
    token = serializer.dumps({"jti": jti, "email": email})
    return MagicLinkPayload(token=token, jti=jti, email=email)


def verify_magic_link_token(token: str, *, secret: str, max_age: int) -> DecodedMagicLink:
    """Verify signature and expiry; return jti+email. Raises InvalidToken otherwise."""
    serializer = URLSafeTimedSerializer(secret, salt=_MAGIC_LINK_SALT)
    try:
        payload = serializer.loads(token, max_age=max_age)
    except SignatureExpired as exc:
        raise InvalidToken("expired") from exc
    except BadSignature as exc:
        raise InvalidToken("bad_signature") from exc
    if not isinstance(payload, dict) or "jti" not in payload or "email" not in payload:
        raise InvalidToken("malformed_payload")
    return DecodedMagicLink(jti=payload["jti"], email=payload["email"])
```

- [ ] **Step 2.2.4: Re-run, confirm all five token tests pass.**

```
pytest tests/test_server_auth_primitives.py -k "magic_link" -xvs
```

Expected: 5 PASS.

### Task 2.3 — Session cookie sign/verify

- [ ] **Step 2.3.1: Write the failing tests.**

Append to `tests/test_server_auth_primitives.py`:

```python
from flatpilot.server.auth import InvalidSession, sign_session_cookie, verify_session_cookie


def test_session_cookie_round_trip() -> None:
    secret = "x" * 64
    cookie = sign_session_cookie(user_id=42, secret=secret)
    assert verify_session_cookie(cookie, secret=secret) == 42


def test_session_cookie_rejects_tampered() -> None:
    secret = "x" * 64
    cookie = sign_session_cookie(user_id=42, secret=secret)
    tampered = cookie.replace(cookie[-3], ("A" if cookie[-3] != "A" else "B"))
    with pytest.raises(InvalidSession):
        verify_session_cookie(tampered, secret=secret)


def test_session_cookie_rejects_unsigned() -> None:
    """A naive value like '42' must NOT be accepted as a valid session."""
    with pytest.raises(InvalidSession):
        verify_session_cookie("42", secret="x" * 64)


def test_session_cookie_rejects_wrong_secret() -> None:
    cookie = sign_session_cookie(user_id=42, secret="x" * 64)
    with pytest.raises(InvalidSession):
        verify_session_cookie(cookie, secret="y" * 64)


def test_session_cookie_uses_distinct_salt_from_magic_link() -> None:
    """A magic-link token signed with the same secret must NOT verify as a session cookie."""
    secret = "x" * 64
    payload = issue_magic_link_token("user@example.com", secret=secret)
    with pytest.raises(InvalidSession):
        verify_session_cookie(payload.token, secret=secret)
```

- [ ] **Step 2.3.2: Run, confirm they fail (ImportError on `sign_session_cookie`).**

```
pytest tests/test_server_auth_primitives.py -k "session_cookie" -xvs
```

Expected: failures.

- [ ] **Step 2.3.3: Add the session helpers to `auth.py`.**

Append to `src/flatpilot/server/auth.py`:

```python
from itsdangerous import URLSafeSerializer  # untimed; cookie TTL is set via Set-Cookie Max-Age


def sign_session_cookie(*, user_id: int, secret: str) -> str:
    """Sign a session cookie value carrying the user_id.

    No expiry encoded into the cookie; the Set-Cookie header sets Max-Age
    (30 days). Server-side revocation is filed as FlatPilot-xzg; this PR
    relies solely on cookie TTL.
    """
    serializer = URLSafeSerializer(secret, salt=_SESSION_SALT)
    return serializer.dumps({"uid": int(user_id)})


def verify_session_cookie(value: str, *, secret: str) -> int:
    """Verify the cookie signature and return user_id. Raises InvalidSession otherwise."""
    serializer = URLSafeSerializer(secret, salt=_SESSION_SALT)
    try:
        payload = serializer.loads(value)
    except BadSignature as exc:
        raise InvalidSession("bad_signature") from exc
    if not isinstance(payload, dict) or "uid" not in payload:
        raise InvalidSession("malformed_payload")
    return int(payload["uid"])
```

- [ ] **Step 2.3.4: Re-run, confirm all five session tests pass.**

```
pytest tests/test_server_auth_primitives.py -k "session_cookie" -xvs
```

Expected: 5 PASS.

### Task 2.4 — Phase 2 verification

- [ ] **Step 2.4.1: Run the full Phase 2 test suite + linters.**

```
pytest tests/test_server_auth_primitives.py -xvs
ruff check src/flatpilot/server/ tests/test_server_auth_primitives.py
mypy src/flatpilot/server/auth.py
```

Expected: 10 tests PASS, lint clean.

- [ ] **Step 2.4.2: Run the existing suite (regression).**

```
pytest -x
```

Expected: all PASS.

### Task 2.5 — Phase 2 commit

- [ ] **Step 2.5.1: Commit.**

```bash
git add pyproject.toml src/flatpilot/server/__init__.py src/flatpilot/server/auth.py tests/test_server_auth_primitives.py
git commit -m "FlatPilot-ix2m/8jx: phase 2 — auth primitives (itsdangerous tokens + cookies)

- Magic-link tokens carry a UUID jti and email; signed with itsdangerous
  URLSafeTimedSerializer using salt 'flatpilot.magic-link'. Token expiry
  is enforced ONLY by serializer.loads(max_age=...) — no DB-level expiry
  re-check (per spec §4.2).
- Session cookies carry user_id; signed with URLSafeSerializer using a
  distinct salt 'flatpilot.session' so a magic-link token can't be
  replayed as a session cookie. Cookie TTL is set via Set-Cookie
  Max-Age, not encoded into the value.
- Strict type-checked verify functions raise InvalidToken / InvalidSession
  on tampering, expiry, wrong secret, or malformed payload.
- Adds itsdangerous (runtime) and sqlglot (test-only) deps.

Sign/verify is the foundation Phase 4's auth routes will use directly
and Phase 3's get_current_user dependency will call."
```

---

## Phase 3 — FastAPI app skeleton

**Spec sections:** §3.4, §3.5, §5.5, §5.6, §9 (orphan Chromium row).

**Files:**
- Create: `src/flatpilot/server/settings.py`
- Create: `src/flatpilot/server/deps.py`
- Create: `src/flatpilot/server/errors.py`
- Create: `src/flatpilot/server/app.py`
- Create: `src/flatpilot/server/runtime/` (directory for orphan-Chromium PID files; `__init__.py` empty)
- Create: `tests/test_server_app.py`
- Create: `tests/test_server_deps.py`

### Task 3.1 — Settings (env-loaded session secret + paths)

- [ ] **Step 3.1.1: Write the failing test.**

Create `tests/test_server_app.py`:

```python
from __future__ import annotations

import os

import pytest

from flatpilot.server.settings import Settings, get_settings


def test_settings_reads_session_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLATPILOT_SESSION_SECRET", "x" * 64)
    monkeypatch.delenv("FLATPILOT_DEV_AUTOGEN_SECRET", raising=False)
    s = Settings.from_env()
    assert s.session_secret == "x" * 64


def test_settings_rejects_missing_secret_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    monkeypatch.delenv("FLATPILOT_DEV_AUTOGEN_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="FLATPILOT_SESSION_SECRET"):
        Settings.from_env()


def test_settings_dev_autogen_generates_per_process_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """In dev mode, the secret is auto-generated per process so reload kills sessions cleanly."""
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")
    s = Settings.from_env()
    assert len(s.session_secret) >= 64
    # Same call returns same value within the process (cached).
    s2 = Settings.from_env()
    assert s.session_secret == s2.session_secret


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b
```

- [ ] **Step 3.1.2: Run, confirm failures (ImportError).**

```
pytest tests/test_server_app.py -k "settings" -xvs
```

Expected: failures.

- [ ] **Step 3.1.3: Create `src/flatpilot/server/settings.py`.**

```python
"""FastAPI server configuration loaded from environment.

Two modes:
- Production: FLATPILOT_SESSION_SECRET must be set (≥32 bytes recommended).
  Hard fail if missing.
- Dev: FLATPILOT_DEV_AUTOGEN_SECRET=1 generates a per-process random secret.
  Reloading the dev server invalidates all active sessions (acceptable for dev).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    session_secret: str
    magic_link_max_age_sec: int = 900     # 15 minutes
    session_cookie_max_age_sec: int = 60 * 60 * 24 * 30   # 30 days
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("FLATPILOT_SESSION_SECRET")
        if not secret:
            if os.environ.get("FLATPILOT_DEV_AUTOGEN_SECRET") == "1":
                secret = _autogen_secret()
            else:
                raise RuntimeError(
                    "FLATPILOT_SESSION_SECRET is required. Set a long random "
                    "value, or set FLATPILOT_DEV_AUTOGEN_SECRET=1 for dev."
                )
        return cls(session_secret=secret)


_AUTOGEN_CACHE: str | None = None


def _autogen_secret() -> str:
    global _AUTOGEN_CACHE
    if _AUTOGEN_CACHE is None:
        _AUTOGEN_CACHE = secrets.token_urlsafe(64)
    return _AUTOGEN_CACHE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
```

- [ ] **Step 3.1.4: Re-run, confirm all four tests pass.**

```
pytest tests/test_server_app.py -k "settings" -xvs
```

Expected: PASS.

### Task 3.2 — `get_current_user` and `get_db` dependencies

- [ ] **Step 3.2.1: Write the failing tests.**

Create `tests/test_server_deps.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi import Request

from flatpilot.server.auth import sign_session_cookie
from flatpilot.server.deps import User, get_current_user
from flatpilot.server.settings import Settings


def _make_request(cookies: dict[str, str]) -> Request:
    """Construct a minimal Request with the given cookies (test helper)."""
    scope = {
        "type": "http",
        "headers": [
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode())
        ] if cookies else [],
    }
    return Request(scope)


def test_get_current_user_returns_user_for_valid_cookie(tmp_path: Path) -> None:
    secret = "x" * 64
    cookie = sign_session_cookie(user_id=7, secret=secret)
    request = _make_request({"fp_session": cookie})

    settings = Settings(session_secret=secret)
    # get_current_user resolves user_id from cookie, looks up user row in DB.
    # Inject DB via parameter or fixture — see Step 3.2.3 for the actual signature.
    user = get_current_user(request=request, settings=settings, conn=_seed_user_conn(tmp_path, user_id=7))
    assert isinstance(user, User)
    assert user.id == 7


def test_get_current_user_401s_without_cookie(tmp_path: Path) -> None:
    request = _make_request({})
    settings = Settings(session_secret="x" * 64)
    with pytest.raises(HTTPException) as exc:
        get_current_user(request=request, settings=settings, conn=_empty_conn(tmp_path))
    assert exc.value.status_code == 401


def test_get_current_user_401s_for_tampered_cookie(tmp_path: Path) -> None:
    secret = "x" * 64
    cookie = sign_session_cookie(user_id=7, secret=secret)
    tampered = cookie[:-3] + ("AAA" if not cookie.endswith("AAA") else "BBB")
    request = _make_request({"fp_session": tampered})
    settings = Settings(session_secret=secret)
    with pytest.raises(HTTPException) as exc:
        get_current_user(request=request, settings=settings, conn=_empty_conn(tmp_path))
    assert exc.value.status_code == 401


def test_get_current_user_401s_when_user_row_deleted(tmp_path: Path) -> None:
    """A valid cookie for a user that no longer exists in DB returns 401, not 500."""
    secret = "x" * 64
    cookie = sign_session_cookie(user_id=999, secret=secret)
    request = _make_request({"fp_session": cookie})
    settings = Settings(session_secret=secret)
    with pytest.raises(HTTPException) as exc:
        get_current_user(request=request, settings=settings, conn=_empty_conn(tmp_path))
    assert exc.value.status_code == 401


# Helper fixtures
def _seed_user_conn(tmp_path: Path, user_id: int):
    import sqlite3
    from flatpilot.database import init_db
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO users (id, email, email_normalized, created_at) "
        "VALUES (?, NULL, NULL, '2026-05-04T00:00:00Z')",
        (user_id,),
    )
    conn.commit()
    return conn


def _empty_conn(tmp_path: Path):
    import sqlite3
    from flatpilot.database import init_db
    db_path = tmp_path / "fp.db"
    conn = sqlite3.connect(db_path)
    init_db(conn)
    return conn
```

- [ ] **Step 3.2.2: Run, confirm failures (ImportError on `flatpilot.server.deps`).**

```
pytest tests/test_server_deps.py -xvs
```

Expected: failures.

- [ ] **Step 3.2.3: Create `src/flatpilot/server/deps.py`.**

```python
"""FastAPI dependencies: get_db (yields a sqlite3 connection) and
get_current_user (extracts user from signed session cookie).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status

from flatpilot.database import connect
from flatpilot.server.auth import InvalidSession, verify_session_cookie
from flatpilot.server.settings import Settings, get_settings

SESSION_COOKIE_NAME = "fp_session"


@dataclass(frozen=True)
class User:
    id: int
    email: str | None


def get_db() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection scoped to the request lifecycle."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> User:
    """Extract user_id from the signed session cookie, return User or 401."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no_session")
    try:
        user_id = verify_session_cookie(cookie, secret=settings.session_secret)
    except InvalidSession:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_gone")
    return User(id=row[0], email=row[1])
```

- [ ] **Step 3.2.4: Re-run, confirm all four tests pass.**

```
pytest tests/test_server_deps.py -xvs
```

Expected: 4 PASS.

### Task 3.3 — Error handler

- [ ] **Step 3.3.1: Create `src/flatpilot/server/errors.py`.**

```python
"""Unified error response shape: {"error": str, "detail": str | null}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("flatpilot.server")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": _slug_for_status(exc.status_code), "detail": str(exc.detail) if exc.detail else None},
        )

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "validation_error", "detail": None},
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled server exception", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_server_error", "detail": None},
        )


def _slug_for_status(code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
    }.get(code, "error")
```

- [ ] **Step 3.3.2: Write the failing tests for the error envelope.**

Append to `tests/test_server_app.py`:

```python
from fastapi.testclient import TestClient

from flatpilot.server.app import create_app


def test_error_handler_404_envelope() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/nonexistent")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "detail": "Not Found"}


def test_error_handler_500_envelope_logs_traceback(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app()

    @app.get("/api/_test_boom")
    async def boom():  # noqa: ANN202
        raise RuntimeError("intentional")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level("ERROR", logger="flatpilot.server"):
        r = client.get("/api/_test_boom")
    assert r.status_code == 500
    assert r.json() == {"error": "internal_server_error", "detail": None}
    assert any("intentional" in rec.message or "intentional" in str(rec.exc_info) for rec in caplog.records)
```

- [ ] **Step 3.3.3: Run, confirm failures (no `create_app` yet).**

```
pytest tests/test_server_app.py -k "error_handler" -xvs
```

Expected: failures.

### Task 3.4 — Orphan-Chromium PID-file scan + cleanup

- [ ] **Step 3.4.1: Write the failing test.**

Append to `tests/test_server_app.py`:

```python
from pathlib import Path
from unittest import mock

from flatpilot.server.app import scan_and_kill_orphan_chromium


def test_scan_and_kill_orphan_skips_nonexistent_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = tmp_path / "runtime" / "playwright_pids"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "abc.pid").write_text("999999\n")  # almost certainly doesn't exist

    killed = []
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)) or (_ for _ in ()).throw(ProcessLookupError()))

    scan_and_kill_orphan_chromium(runtime_dir=runtime_dir)
    # Stale PID file is removed even if process is gone.
    assert not (runtime_dir / "abc.pid").exists()


def test_scan_and_kill_orphan_validates_before_killing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = tmp_path / "runtime" / "playwright_pids"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "abc.pid").write_text(f"{os.getpid()}\n")

    # The current process is NOT a Playwright Chromium — validator must reject.
    killed = []
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(
        "flatpilot.server.app._is_playwright_chromium",
        lambda pid: False,
    )

    scan_and_kill_orphan_chromium(runtime_dir=runtime_dir)
    assert killed == []
    assert not (runtime_dir / "abc.pid").exists()  # PID file still cleaned up


def test_scan_and_kill_orphan_kills_validated_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir = tmp_path / "runtime" / "playwright_pids"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "abc.pid").write_text("12345\n")

    killed = []
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(
        "flatpilot.server.app._is_playwright_chromium",
        lambda pid: pid == 12345,
    )

    scan_and_kill_orphan_chromium(runtime_dir=runtime_dir)
    assert killed and killed[0][0] == 12345
    assert not (runtime_dir / "abc.pid").exists()
```

- [ ] **Step 3.4.2: Run, confirm failures.**

```
pytest tests/test_server_app.py -k "orphan" -xvs
```

Expected: failures.

### Task 3.5 — Implement `app.py` (create_app + startup hook + middleware)

- [ ] **Step 3.5.1: Create `src/flatpilot/server/app.py`.**

```python
"""FastAPI application factory.

create_app() wires middleware, error handlers, route routers, and startup
hooks. Routers are imported lazily inside create_app to keep app.py importable
even before the route modules exist (Phase 4-7 add them).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from flatpilot.database import init_db
from flatpilot.server.errors import install_error_handlers
from flatpilot.server.settings import get_settings

logger = logging.getLogger("flatpilot.server")

RUNTIME_DIR = Path.home() / ".flatpilot" / "runtime" / "playwright_pids"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="FlatPilot")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_error_handlers(app)

    @app.on_event("startup")
    async def startup() -> None:
        # Ensure schema + run cleanup queries.
        init_db()
        # Best-effort orphan Chromium scan.
        scan_and_kill_orphan_chromium(runtime_dir=RUNTIME_DIR)
        logger.info("flatpilot server started")

    # Route registration (Phase 4-7 will append):
    _register_routers(app)

    return app


def _register_routers(app: FastAPI) -> None:
    # Imported lazily so the module is importable before all routers exist.
    try:
        from flatpilot.server.routes.auth import router as auth_router
        app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    except ImportError:
        pass
    try:
        from flatpilot.server.routes.matches import router as matches_router
        app.include_router(matches_router, prefix="/api/matches", tags=["matches"])
    except ImportError:
        pass
    try:
        from flatpilot.server.routes.applications import router as applications_router
        app.include_router(applications_router, prefix="/api/applications", tags=["applications"])
    except ImportError:
        pass
    try:
        from flatpilot.server.routes.connections import router as connections_router
        app.include_router(connections_router, prefix="/api/connections", tags=["connections"])
    except ImportError:
        pass


def scan_and_kill_orphan_chromium(*, runtime_dir: Path) -> None:
    """Read leftover PID files written by the engine; SIGTERM each validated PID."""
    if not runtime_dir.exists():
        return
    for pid_file in runtime_dir.glob("*.pid"):
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
            continue
        try:
            if _is_playwright_chromium(pid):
                os.kill(pid, signal.SIGTERM)
                logger.warning("killed orphan playwright chromium pid=%s", pid)
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("orphan-scan: pid=%s validation/kill failed: %s", pid, exc)
        finally:
            pid_file.unlink(missing_ok=True)


def _is_playwright_chromium(pid: int) -> bool:
    """Conservative validator: only return True when we're confident."""
    if sys.platform == "linux":
        exe = Path(f"/proc/{pid}/exe")
        try:
            target = os.readlink(exe)
        except (OSError, FileNotFoundError):
            return False
        return "chromium" in target.lower() or "chrome" in target.lower()
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["lsof", "-p", str(pid), "-Fn"], stderr=subprocess.DEVNULL, text=True
            )
        except subprocess.CalledProcessError:
            return False
        return "chromium" in out.lower() or "chrome" in out.lower()
    return False  # other platforms: refuse to kill


# Module-level instance for ASGI servers (uvicorn flatpilot.server.app:app).
app = create_app()
```

- [ ] **Step 3.5.2: Re-run all Phase 3 tests.**

```
pytest tests/test_server_app.py tests/test_server_deps.py -xvs
```

Expected: all PASS.

### Task 3.6 — Phase 3 verification

- [ ] **Step 3.6.1: Manually start uvicorn and verify it boots.**

```bash
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/auth/me  # expect 401
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/nonexistent  # expect 404
kill %1
```

Expected: `401`, `404`.

- [ ] **Step 3.6.2: Linters.**

```
ruff check src/flatpilot/server/ tests/test_server_*.py
mypy src/flatpilot/server/
```

Expected: clean.

### Task 3.7 — Phase 3 commit

- [ ] **Step 3.7.1: Commit.**

```bash
git add src/flatpilot/server/ tests/test_server_app.py tests/test_server_deps.py
git commit -m "FlatPilot-ix2m/8jx: phase 3 — FastAPI app skeleton

- Settings (env-loaded session secret with dev autogen escape hatch).
- get_db / get_current_user dependencies — cookie verify + DB-row
  lookup, 401 on any failure (no leak between 'no cookie', 'bad
  signature', 'user gone').
- Unified error envelope ({error, detail}) for HTTPException,
  validation errors, and unhandled exceptions.
- create_app() factory wires CORS (localhost:3000), error handlers,
  startup hook (init_db + orphan-Chromium scan), and lazily registers
  route modules (Phase 4-7 will fill them in).
- Orphan-Chromium scan reads PID files written by the login engine,
  validates each PID is a Playwright Chromium via /proc/<pid>/exe
  (Linux) or lsof (macOS) before SIGTERM. Other platforms refuse to
  kill — never blast the user's other Chromium windows.

uvicorn flatpilot.server.app:app starts and serves 401 on protected
routes / 404 with the unified envelope on unknowns. Ready for Phase 4
to add /api/auth routes."
```

---

## Phase 4 — Auth routes

**Spec sections:** §3.2, §3.5, §5.1.

**Files:**
- Create: `src/flatpilot/server/email_links.py`
- Create: `src/flatpilot/server/schemas.py`
- Create: `src/flatpilot/server/routes/__init__.py` (empty)
- Create: `src/flatpilot/server/routes/auth.py`
- Create: `tests/test_server_auth_routes.py`

### Task 4.1 — Pydantic schemas for auth

- [ ] **Step 4.1.1: Create `src/flatpilot/server/schemas.py`.**

```python
"""Pydantic request/response models for the FastAPI server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerify(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class OkResponse(BaseModel):
    ok: Literal[True] = True


class VerifyResponse(BaseModel):
    user_id: int


class MeResponse(BaseModel):
    user_id: int
    email: str | None
```

`EmailStr` requires `email-validator`; if it isn't already a dep, add it to `pyproject.toml` runtime deps and `pip install -e '.[dev]'`.

### Task 4.2 — `email_links.py` (SMTP shim)

- [ ] **Step 4.2.1: Inspect the existing email transport.**

```bash
grep -n "^def \|^async def " src/flatpilot/notifications/email.py | head -20
```

Identify the existing send function (e.g. `send_email(to, subject, body)`).

- [ ] **Step 4.2.2: Write the failing test.**

Create `tests/test_server_email_links.py`:

```python
from __future__ import annotations

from unittest import mock

from flatpilot.server.email_links import send_magic_link


def test_send_magic_link_calls_existing_smtp_transport() -> None:
    with mock.patch("flatpilot.notifications.email.send_email") as send:
        send_magic_link(email="user@example.com", link="http://localhost:3000/verify?t=ABC")
    send.assert_called_once()
    args, kwargs = send.call_args
    # Subject + body must mention the link verbatim.
    body = kwargs.get("body", args[2] if len(args) >= 3 else "")
    assert "http://localhost:3000/verify?t=ABC" in body
    assert kwargs.get("to", args[0] if args else "") == "user@example.com"
```

- [ ] **Step 4.2.3: Create `src/flatpilot/server/email_links.py`.**

```python
"""Send magic-link emails via the existing notifications/email.py SMTP transport."""

from __future__ import annotations

from flatpilot.notifications.email import send_email


SUBJECT = "Your FlatPilot sign-in link"

BODY_TEMPLATE = """\
Click the link below to sign in to FlatPilot. It expires in 15 minutes
and can only be used once.

{link}

If you didn't request this, ignore this email.
"""


def send_magic_link(*, email: str, link: str) -> None:
    """Send a magic-link email. Synchronous (SMTP transport is blocking)."""
    send_email(to=email, subject=SUBJECT, body=BODY_TEMPLATE.format(link=link))
```

If the existing `send_email` signature differs (positional, async, etc.), match it. The test asserts the contract; the implementation adapts.

- [ ] **Step 4.2.4: Re-run, confirm pass.**

```
pytest tests/test_server_email_links.py -xvs
```

Expected: PASS.

### Task 4.3 — `POST /api/auth/request` (always 200)

- [ ] **Step 4.3.1: Write the failing test.**

Create `tests/test_server_auth_routes.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A FastAPI TestClient backed by a temp DB and dev-autogen secret."""
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")

    # Reset the lru_cache so settings pick up the env vars.
    from flatpilot.server.settings import get_settings
    get_settings.cache_clear()

    from flatpilot.server.app import create_app
    return TestClient(create_app())


def test_request_returns_ok_for_unknown_email(client: TestClient) -> None:
    with mock.patch("flatpilot.server.routes.auth.send_magic_link") as send:
        r = client.post("/api/auth/request", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    send.assert_called_once()


def test_request_creates_token_row(client: TestClient) -> None:
    import sqlite3
    from flatpilot.config import DEFAULT_DB_PATH

    with mock.patch("flatpilot.server.routes.auth.send_magic_link"):
        client.post("/api/auth/request", json={"email": "user@example.com"})

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    rows = conn.execute("SELECT email, used_at FROM magic_link_tokens").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "user@example.com"
    assert rows[0][1] is None  # not yet consumed


def test_request_400s_on_invalid_email_shape(client: TestClient) -> None:
    r = client.post("/api/auth/request", json={"email": "not-an-email"})
    assert r.status_code == 400
    assert r.json()["error"] == "validation_error"
```

- [ ] **Step 4.3.2: Run, confirm failures (route doesn't exist).**

```
pytest tests/test_server_auth_routes.py -k "request" -xvs
```

- [ ] **Step 4.3.3: Create `src/flatpilot/server/routes/__init__.py` (empty) and `src/flatpilot/server/routes/auth.py`.**

```python
"""Magic-link auth routes."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from flatpilot.server.auth import (
    InvalidToken,
    issue_magic_link_token,
    sign_session_cookie,
    verify_magic_link_token,
)
from flatpilot.server.deps import SESSION_COOKIE_NAME, User, get_current_user, get_db
from flatpilot.server.email_links import send_magic_link
from flatpilot.server.schemas import (
    MagicLinkRequest,
    MagicLinkVerify,
    MeResponse,
    OkResponse,
    VerifyResponse,
)
from flatpilot.server.settings import Settings, get_settings

router = APIRouter()

VERIFY_PAGE_URL = "http://localhost:3000/verify"  # Phase-12 README: configurable via env later.


@router.post("/request", response_model=OkResponse)
async def auth_request(
    body: MagicLinkRequest,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> OkResponse:
    """Issue a magic-link token, persist its jti, email it. Always 200."""
    payload = issue_magic_link_token(body.email, secret=settings.session_secret)
    now = datetime.now(UTC).isoformat()
    expires = (datetime.now(UTC) + timedelta(seconds=settings.magic_link_max_age_sec)).isoformat()
    conn.execute(
        "INSERT INTO magic_link_tokens (jti, email, issued_at, expires_at) VALUES (?, ?, ?, ?)",
        (payload.jti, body.email, now, expires),
    )
    conn.commit()
    link = f"{VERIFY_PAGE_URL}?t={payload.token}"
    send_magic_link(email=body.email, link=link)
    return OkResponse()
```

- [ ] **Step 4.3.4: Re-run, confirm pass.**

```
pytest tests/test_server_auth_routes.py -k "request" -xvs
```

Expected: 3 PASS.

### Task 4.4 — `POST /api/auth/verify` (lookup-or-create, single-use)

- [ ] **Step 4.4.1: Write the failing tests.**

Append to `tests/test_server_auth_routes.py`:

```python
def _request_token(client: TestClient, email: str) -> str:
    """Submit /api/auth/request and return the token from the captured email."""
    captured: list[str] = []

    def fake_send(email: str, link: str) -> None:
        captured.append(link.split("?t=")[1])

    with mock.patch("flatpilot.server.routes.auth.send_magic_link", side_effect=fake_send):
        client.post("/api/auth/request", json={"email": email})

    assert captured, "send_magic_link was never called"
    return captured[0]


def test_verify_creates_user_for_new_email(client: TestClient) -> None:
    token = _request_token(client, "new@example.com")
    r = client.post("/api/auth/verify", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] >= 1

    # Cookie was set.
    assert "fp_session" in r.cookies


def test_verify_logs_in_existing_user_without_creating_new_row(client: TestClient) -> None:
    import sqlite3
    from flatpilot.config import DEFAULT_DB_PATH

    token1 = _request_token(client, "repeat@example.com")
    r1 = client.post("/api/auth/verify", json={"token": token1})
    first_uid = r1.json()["user_id"]

    token2 = _request_token(client, "repeat@example.com")
    r2 = client.post("/api/auth/verify", json={"token": token2})
    second_uid = r2.json()["user_id"]

    assert first_uid == second_uid

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    n = conn.execute(
        "SELECT COUNT(*) FROM users WHERE email_normalized = 'repeat@example.com'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_verify_email_lookup_is_case_insensitive(client: TestClient) -> None:
    t1 = _request_token(client, "Foo@Example.com")
    r1 = client.post("/api/auth/verify", json={"token": t1})
    uid1 = r1.json()["user_id"]

    t2 = _request_token(client, "foo@example.com")
    r2 = client.post("/api/auth/verify", json={"token": t2})
    uid2 = r2.json()["user_id"]

    assert uid1 == uid2


def test_verify_rejects_reused_token(client: TestClient) -> None:
    token = _request_token(client, "once@example.com")
    r1 = client.post("/api/auth/verify", json={"token": token})
    assert r1.status_code == 200

    r2 = client.post("/api/auth/verify", json={"token": token})
    assert r2.status_code == 400
    assert r2.json()["error"] == "bad_request"


def test_verify_rejects_tampered_token(client: TestClient) -> None:
    token = _request_token(client, "tamper@example.com")
    bad = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    r = client.post("/api/auth/verify", json={"token": bad})
    assert r.status_code == 400


def test_verify_rejects_unknown_jti(client: TestClient) -> None:
    """A signed-but-unknown token (e.g. row was cleaned up) returns 400, not 500."""
    from flatpilot.server.auth import issue_magic_link_token
    from flatpilot.server.settings import get_settings

    secret = get_settings().session_secret
    payload = issue_magic_link_token("ghost@example.com", secret=secret)
    # Don't insert the row — simulate a cleaned-up jti.
    r = client.post("/api/auth/verify", json={"token": payload.token})
    assert r.status_code == 400


def test_verify_endpoint_rejects_get(client: TestClient) -> None:
    """CSRF safety: only POST consumes tokens — see spec §3.5."""
    r = client.get("/api/auth/verify?t=anything")
    assert r.status_code == 405
```

- [ ] **Step 4.4.2: Run, confirm failures.**

```
pytest tests/test_server_auth_routes.py -k "verify" -xvs
```

- [ ] **Step 4.4.3: Add the `/verify` route to `routes/auth.py`.**

```python
@router.post("/verify", response_model=VerifyResponse)
async def auth_verify(
    body: MagicLinkVerify,
    response: Response,
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_db),
) -> VerifyResponse:
    """Verify token signature + single-use, log user in (creating if needed)."""
    try:
        decoded = verify_magic_link_token(
            body.token, secret=settings.session_secret, max_age=settings.magic_link_max_age_sec
        )
    except InvalidToken:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_token")

    row = conn.execute(
        "SELECT used_at FROM magic_link_tokens WHERE jti = ?", (decoded.jti,)
    ).fetchone()
    if row is None:
        # JTI not on file — could be a cleaned-up row or a forged token from
        # a leaked secret. Either way: reject.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_token")
    if row[0] is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token_already_used")

    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE magic_link_tokens SET used_at = ? WHERE jti = ?", (now, decoded.jti)
    )

    # Lookup-or-create user, normalizing email in Python.
    normalized = decoded.email.strip().lower()
    user_row = conn.execute(
        "SELECT id FROM users WHERE email_normalized = ?", (normalized,)
    ).fetchone()
    if user_row is None:
        cur = conn.execute(
            "INSERT INTO users (email, email_normalized, created_at) VALUES (?, ?, ?)",
            (decoded.email, normalized, now),
        )
        user_id = int(cur.lastrowid)
    else:
        user_id = int(user_row[0])
    conn.commit()

    cookie_value = sign_session_cookie(user_id=user_id, secret=settings.session_secret)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=settings.session_cookie_max_age_sec,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return VerifyResponse(user_id=user_id)
```

- [ ] **Step 4.4.4: Re-run, confirm all 7 verify tests pass.**

```
pytest tests/test_server_auth_routes.py -k "verify" -xvs
```

Expected: 7 PASS.

### Task 4.5 — `DELETE /api/auth/session` (logout) and `GET /api/auth/me`

- [ ] **Step 4.5.1: Write the failing tests.**

Append to `tests/test_server_auth_routes.py`:

```python
def _login(client: TestClient, email: str) -> dict[str, str]:
    token = _request_token(client, email)
    r = client.post("/api/auth/verify", json={"token": token})
    return {"fp_session": r.cookies["fp_session"]}


def test_get_me_returns_user_id_and_email(client: TestClient) -> None:
    cookies = _login(client, "me@example.com")
    r = client.get("/api/auth/me", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] >= 1
    assert body["email"] == "me@example.com"


def test_get_me_401s_without_cookie(client: TestClient) -> None:
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_logout_clears_cookie_and_subsequent_me_401s(client: TestClient) -> None:
    cookies = _login(client, "logout@example.com")
    r1 = client.delete("/api/auth/session", cookies=cookies)
    assert r1.status_code == 204

    # Server-side: client still has the (now-uncleared) cookie locally,
    # but the response must include a Set-Cookie that clears it. Browsers
    # honor that. In tests we manually drop and verify.
    set_cookie = r1.headers.get("set-cookie", "")
    assert "fp_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie

    r2 = client.get("/api/auth/me")  # fresh, no cookie
    assert r2.status_code == 401


def test_protected_route_returns_401_with_invalid_cookie(client: TestClient) -> None:
    r = client.get("/api/auth/me", cookies={"fp_session": "garbage"})
    assert r.status_code == 401
```

- [ ] **Step 4.5.2: Run, confirm failures.**

```
pytest tests/test_server_auth_routes.py -k "me or logout or invalid_cookie" -xvs
```

- [ ] **Step 4.5.3: Add the `/session` and `/me` routes.**

Append to `src/flatpilot/server/routes/auth.py`:

```python
@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def auth_logout(response: Response, _: User = Depends(get_current_user)) -> Response:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
async def auth_me(user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(user_id=user.id, email=user.email)
```

- [ ] **Step 4.5.4: Re-run, confirm all 4 pass.**

```
pytest tests/test_server_auth_routes.py -k "me or logout or invalid_cookie" -xvs
```

Expected: PASS.

### Task 4.6 — Phase 4 verification

- [ ] **Step 4.6.1: Run the full Phase 4 test suite.**

```
pytest tests/test_server_auth_routes.py tests/test_server_email_links.py -xvs
```

Expected: all PASS.

- [ ] **Step 4.6.2: Manual end-to-end smoke (curl).**

```bash
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000 &
sleep 2
curl -i -X POST http://localhost:8000/api/auth/request \
  -H "Content-Type: application/json" -d '{"email":"smoke@test.local"}'
# Expect 200 {"ok":true}
curl -i http://localhost:8000/api/auth/me
# Expect 401
kill %1
```

- [ ] **Step 4.6.3: Linters.**

```
ruff check src/flatpilot/server/ tests/test_server_*.py
mypy src/flatpilot/server/
```

### Task 4.7 — Phase 4 commit

- [ ] **Step 4.7.1: Commit.**

```bash
git add src/flatpilot/server/email_links.py src/flatpilot/server/schemas.py \
  src/flatpilot/server/routes/__init__.py src/flatpilot/server/routes/auth.py \
  tests/test_server_email_links.py tests/test_server_auth_routes.py
git commit -m "FlatPilot-ix2m/8jx: phase 4 — magic-link auth routes

- POST /api/auth/request always 200 (no existence reveal). Inserts
  magic_link_tokens row, sends link via existing SMTP transport.
- POST /api/auth/verify enforces signature (itsdangerous max_age),
  single-use (used_at column flips), case-insensitive email lookup,
  lookup-or-create user, signed-cookie session issued. No DB-level
  expiry re-check (per spec §4.2 — itsdangerous owns expiry).
- DELETE /api/auth/session clears the cookie (Set-Cookie Max-Age=0).
- GET /api/auth/me returns {user_id, email} for the cookie-bound user.
- GET /api/auth/verify returns 405 — only POST consumes tokens. CSRF
  property test_verify_endpoint_rejects_get makes this a runnable
  invariant per spec §3.5.

Web UI's /login + /verify pages (Phase 10) call these endpoints. Phase
5 starts the login engine refactor."
```

---

## Phase 5 — Login engine refactor

**Spec sections:** §7.1, §7.2, §7.3, §7.4, §7.6, §7.7, §9 (orphan-process row).

**Files:**
- Create: `src/flatpilot/sessions/__init__.py` (empty)
- Create: `src/flatpilot/sessions/paths.py`
- Create: `src/flatpilot/sessions/platforms.py`
- Create: `src/flatpilot/sessions/login_engine.py`
- Modify: existing `flatpilot login` CLI command (path verified during Step 5.0)
- Create: `tests/test_login_engine.py`
- Create: `tests/test_session_paths.py`
- Create: `tests/test_session_platforms.py`

### Task 5.0 — Locate and read the existing CLI login command

- [ ] **Step 5.0.1: Find where today's `flatpilot login <platform>` is implemented.**

```bash
grep -rn "def.*login\|@app\.command.*login\|polite_session.*headed\|headless=False" src/flatpilot/ | head -20
```

Identify the file (likely `src/flatpilot/cli.py` or `src/flatpilot/login.py` or similar). Read it end-to-end — note:
- The exact CLI invocation surface (`flatpilot login <platform>`).
- Where storage_state is written (`~/.flatpilot/sessions/<platform>/state.json`).
- The stdin prompt text (preserve verbatim).
- Per-platform login URLs and any cookie-detection logic (lift to `platforms.py` registry).

Document findings in a scratch comment at the top of `tests/test_login_engine.py` so Step 5.1+ can reference them.

### Task 5.1 — `paths.py` — per-user storage path resolver

- [ ] **Step 5.1.1: Write the failing test.**

Create `tests/test_session_paths.py`:

```python
from __future__ import annotations

from pathlib import Path

from flatpilot.sessions.paths import session_storage_path
from flatpilot.users import DEFAULT_USER_ID


def test_legacy_path_for_default_user() -> None:
    p = session_storage_path(DEFAULT_USER_ID, "wg-gesucht")
    assert p == Path.home() / ".flatpilot" / "sessions" / "wg-gesucht" / "state.json"


def test_namespaced_path_for_non_default_user() -> None:
    p = session_storage_path(7, "wg-gesucht")
    assert p == Path.home() / ".flatpilot" / "users" / "7" / "sessions" / "wg-gesucht" / "state.json"


def test_path_creates_no_directories() -> None:
    """The function is pure; directory creation is the caller's responsibility."""
    p = session_storage_path(99, "kleinanzeigen")
    assert not p.parent.exists() or p.parent.is_dir()  # tolerate prior runs
```

- [ ] **Step 5.1.2: Run, confirm failures.**

```
pytest tests/test_session_paths.py -xvs
```

- [ ] **Step 5.1.3: Create `src/flatpilot/sessions/__init__.py` (empty) and `src/flatpilot/sessions/paths.py`.**

```python
"""Per-user filesystem path resolver for browser session storage.

User 1 (the seed CLI user) stays at the legacy ~/.flatpilot/sessions/<platform>/
path so existing scrapers keep loading their cookies unchanged. User N >= 2
lives under the per-user namespace ~/.flatpilot/users/<uid>/sessions/<platform>/.

Bundle B implements this only for sessions; per-user namespacing for
profile.json / templates / attachments is FlatPilot-2p3 territory.
"""

from __future__ import annotations

from pathlib import Path

from flatpilot.users import DEFAULT_USER_ID


def session_storage_path(user_id: int, platform: str) -> Path:
    """Return the absolute path to <user, platform>'s storage_state.json."""
    base = Path.home() / ".flatpilot"
    if user_id == DEFAULT_USER_ID:
        return base / "sessions" / platform / "state.json"
    return base / "users" / str(user_id) / "sessions" / platform / "state.json"
```

- [ ] **Step 5.1.4: Re-run, confirm pass.**

```
pytest tests/test_session_paths.py -xvs
```

Expected: 3 PASS.

### Task 5.2 — `platforms.py` — platform registry

- [ ] **Step 5.2.1: Write the failing test.**

Create `tests/test_session_platforms.py`:

```python
from __future__ import annotations

import pytest

from flatpilot.sessions.platforms import PLATFORMS, PlatformLogin, UnknownPlatform, get_platform


def test_known_platforms_registered() -> None:
    assert "wg-gesucht" in PLATFORMS
    assert "kleinanzeigen" in PLATFORMS
    assert "immoscout24" in PLATFORMS


def test_get_platform_returns_definition() -> None:
    p = get_platform("wg-gesucht")
    assert isinstance(p, PlatformLogin)
    assert p.name == "wg-gesucht"
    assert p.login_url.startswith("https://")


def test_get_platform_raises_for_unknown() -> None:
    with pytest.raises(UnknownPlatform):
        get_platform("nope")


def test_is_authenticated_callable_per_platform() -> None:
    """Each platform's is_authenticated heuristic accepts a list of cookies."""
    for name, plat in PLATFORMS.items():
        assert callable(plat.is_authenticated), f"{name} missing is_authenticated"
        # Empty cookie list must always be unauthenticated.
        assert plat.is_authenticated([]) is False, f"{name} false-positives on empty cookies"
```

- [ ] **Step 5.2.2: Run, confirm failures.**

```
pytest tests/test_session_platforms.py -xvs
```

- [ ] **Step 5.2.3: Lift the cookie-detection heuristics from existing scrapers.**

For each platform, find the existing logic that decides "this storage_state has authenticated cookies" — likely scattered across `src/flatpilot/scrapers/wg_gesucht.py`, `kleinanzeigen.py`, `immoscout24.py`. Common patterns: presence of a session cookie name (`WGG_SESSION`, `lp_xxxx`, etc.), or a non-empty value in some specific cookie.

Read each scraper's auth-detection code first; do NOT invent new heuristics.

- [ ] **Step 5.2.4: Create `src/flatpilot/sessions/platforms.py`.**

```python
"""Per-platform login URL + cookie-based authentication heuristic.

Heuristics are LIFTED VERBATIM from the existing scraper auth-detection logic.
This module is a centralization, not a redesign — if you find yourself
inventing a new cookie name here, stop and read the scrapers first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


class UnknownPlatform(Exception):
    """Raised when a platform name isn't in the PLATFORMS registry."""


@dataclass(frozen=True)
class PlatformLogin:
    name: str
    login_url: str
    is_authenticated: Callable[[Sequence[dict[str, Any]]], bool]


def _wg_gesucht_authed(cookies: Sequence[dict[str, Any]]) -> bool:
    # TODO Step 5.2.3: replace with the actual cookie name(s) from
    # scrapers/wg_gesucht.py — do NOT guess.
    names = {c.get("name") for c in cookies}
    return "X-Client-Id" in names or "WGG_SESSION" in names


def _kleinanzeigen_authed(cookies: Sequence[dict[str, Any]]) -> bool:
    # TODO Step 5.2.3: replace with the actual cookie name(s) from
    # scrapers/kleinanzeigen.py.
    names = {c.get("name") for c in cookies}
    return "u_uid" in names or "session_id" in names


def _immoscout24_authed(cookies: Sequence[dict[str, Any]]) -> bool:
    # TODO Step 5.2.3: replace with the actual cookie name(s) from
    # scrapers/immoscout24.py.
    names = {c.get("name") for c in cookies}
    return "reese84" in names or "is24-session" in names


PLATFORMS: dict[str, PlatformLogin] = {
    "wg-gesucht": PlatformLogin(
        name="wg-gesucht",
        login_url="https://www.wg-gesucht.de/mein-wg-gesucht-login.html",
        is_authenticated=_wg_gesucht_authed,
    ),
    "kleinanzeigen": PlatformLogin(
        name="kleinanzeigen",
        login_url="https://www.kleinanzeigen.de/m-einloggen.html",
        is_authenticated=_kleinanzeigen_authed,
    ),
    "immoscout24": PlatformLogin(
        name="immoscout24",
        login_url="https://www.immobilienscout24.de/anmelden.html",
        is_authenticated=_immoscout24_authed,
    ),
}


def get_platform(name: str) -> PlatformLogin:
    try:
        return PLATFORMS[name]
    except KeyError as exc:
        raise UnknownPlatform(name) from exc
```

The `TODO Step 5.2.3` markers are explicit — Step 5.2.3 finds the real cookie names. Leaving placeholder names will fail in production; the test in Step 5.2.5 catches one half of this (empty-cookie rejection); the other half (real cookie name correctness) is verified in the manual smoke at the end of Phase 5.

- [ ] **Step 5.2.5: Re-run, confirm pass.**

```
pytest tests/test_session_platforms.py -xvs
```

Expected: 4 PASS.

### Task 5.3 — `login_engine.py` — `run_login_session`

- [ ] **Step 5.3.1: Write the failing tests.**

Create `tests/test_login_engine.py`:

```python
"""Tests for the headless-Playwright-driven login engine.

Playwright is mocked end-to-end; no real browser is launched. Real
platform login is verified manually at the end of Phase 5.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from flatpilot.sessions.login_engine import LoginResult, run_login_session
from flatpilot.sessions.platforms import UnknownPlatform


@pytest.fixture
def fake_playwright(tmp_path: Path):
    """Mock playwright.async_api.async_playwright() context manager + browser hierarchy."""
    pw = mock.AsyncMock()
    chromium = mock.AsyncMock()
    browser = mock.AsyncMock()
    context = mock.AsyncMock()
    page = mock.AsyncMock()

    pw.chromium = chromium
    chromium.launch = mock.AsyncMock(return_value=browser)
    browser.new_context = mock.AsyncMock(return_value=context)
    context.new_page = mock.AsyncMock(return_value=page)
    context.cookies = mock.AsyncMock(return_value=[])  # default: not authenticated
    context.storage_state = mock.AsyncMock()

    cm = mock.AsyncMock()
    cm.__aenter__ = mock.AsyncMock(return_value=pw)
    cm.__aexit__ = mock.AsyncMock(return_value=None)

    with mock.patch("flatpilot.sessions.login_engine.async_playwright", return_value=cm):
        yield {
            "playwright": pw,
            "chromium": chromium,
            "browser": browser,
            "context": context,
            "page": page,
        }


@pytest.mark.asyncio
async def test_unknown_platform_raises() -> None:
    with pytest.raises(UnknownPlatform):
        await run_login_session(
            "nope",
            storage_state_path=Path("/tmp/x.json"),
            completion_signal=asyncio.sleep(0),
        )


@pytest.mark.asyncio
async def test_saves_state_when_signal_fires_with_auth_cookies(
    fake_playwright, tmp_path: Path
) -> None:
    fake_playwright["context"].cookies.return_value = [{"name": "WGG_SESSION", "value": "x"}]
    storage = tmp_path / "state.json"

    event = asyncio.Event()
    event.set()  # fire immediately

    result = await run_login_session(
        "wg-gesucht",
        storage_state_path=storage,
        completion_signal=event.wait(),
        timeout_sec=5.0,
    )
    assert result == LoginResult.SAVED
    fake_playwright["context"].storage_state.assert_awaited_once()
    args, kwargs = fake_playwright["context"].storage_state.call_args
    assert kwargs.get("path") == storage


@pytest.mark.asyncio
async def test_abandoned_when_signal_fires_without_auth_cookies(
    fake_playwright, tmp_path: Path
) -> None:
    fake_playwright["context"].cookies.return_value = []  # no auth
    storage = tmp_path / "state.json"

    event = asyncio.Event()
    event.set()

    result = await run_login_session(
        "wg-gesucht",
        storage_state_path=storage,
        completion_signal=event.wait(),
        timeout_sec=5.0,
    )
    assert result == LoginResult.ABANDONED
    fake_playwright["context"].storage_state.assert_not_awaited()
    assert not storage.exists()


@pytest.mark.asyncio
async def test_times_out_when_signal_never_fires(fake_playwright, tmp_path: Path) -> None:
    storage = tmp_path / "state.json"
    never = asyncio.Event()  # never set

    result = await run_login_session(
        "wg-gesucht",
        storage_state_path=storage,
        completion_signal=never.wait(),
        timeout_sec=0.1,
    )
    assert result == LoginResult.TIMED_OUT


@pytest.mark.asyncio
async def test_browser_closes_on_cancellation(fake_playwright, tmp_path: Path) -> None:
    storage = tmp_path / "state.json"
    never = asyncio.Event()

    task = asyncio.create_task(
        run_login_session(
            "wg-gesucht",
            storage_state_path=storage,
            completion_signal=never.wait(),
            timeout_sec=10.0,
        )
    )
    await asyncio.sleep(0.05)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    fake_playwright["browser"].close.assert_awaited()


@pytest.mark.asyncio
async def test_writes_pid_file_for_orphan_scan(fake_playwright, tmp_path: Path, monkeypatch) -> None:
    """Engine writes its Chromium pid to ~/.flatpilot/runtime/playwright_pids/<random>.pid
    so server startup can scan and SIGTERM orphans."""
    runtime = tmp_path / "runtime" / "playwright_pids"
    monkeypatch.setattr("flatpilot.sessions.login_engine.RUNTIME_DIR", runtime)

    fake_playwright["browser"].process = mock.MagicMock()
    fake_playwright["browser"].process.pid = 54321

    storage = tmp_path / "state.json"
    event = asyncio.Event()
    event.set()

    await run_login_session(
        "wg-gesucht",
        storage_state_path=storage,
        completion_signal=event.wait(),
        timeout_sec=5.0,
    )
    # Pid file is removed on clean exit, but the directory was created.
    assert runtime.exists()
    # No leftover pid files (clean exit).
    assert list(runtime.glob("*.pid")) == []
```

- [ ] **Step 5.3.2: Run, confirm failures.**

```
pytest tests/test_login_engine.py -xvs
```

Expected: ImportError on `flatpilot.sessions.login_engine`.

- [ ] **Step 5.3.3: Create `src/flatpilot/sessions/login_engine.py`.**

```python
"""Headless-Playwright-driven login engine, parameterized on a completion signal.

The completion_signal is an Awaitable[None]:
- CLI: `await asyncio.to_thread(input, "Press Enter when…")`
- FastAPI: `await asyncio.Event().wait()` (set by POST /api/connections/<p>/done)

The engine itself is signal-agnostic. On signal: inspect cookies, decide
SAVED vs ABANDONED, write storage_state on SAVED, return.

PID-file tracking lets the server's orphan-Chromium scan (server/app.py)
clean up after a crash mid-session. The engine writes its own PID file in
the engine's `try`-block and unlinks it in `finally`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable
from contextlib import suppress
from enum import Enum, auto
from pathlib import Path

from playwright.async_api import async_playwright

from flatpilot.sessions.platforms import UnknownPlatform, get_platform

logger = logging.getLogger("flatpilot.sessions.login_engine")

RUNTIME_DIR = Path.home() / ".flatpilot" / "runtime" / "playwright_pids"


class LoginResult(Enum):
    SAVED = auto()
    ABANDONED = auto()
    TIMED_OUT = auto()
    CANCELLED = auto()


async def run_login_session(
    platform: str,
    *,
    storage_state_path: Path,
    completion_signal: Awaitable[None],
    timeout_sec: float = 300.0,
) -> LoginResult:
    """Open headed Chromium at <platform>'s login URL, wait for completion_signal,
    capture storage_state if cookies look authenticated, close the browser."""
    plat = get_platform(platform)  # raises UnknownPlatform

    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    pid_file: Path | None = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        try:
            if browser.process is not None:
                pid_file = RUNTIME_DIR / f"{secrets.token_hex(8)}.pid"
                pid_file.write_text(f"{browser.process.pid}\n")

            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(plat.login_url)

            timeout_task = asyncio.create_task(asyncio.sleep(timeout_sec))
            signal_task = asyncio.ensure_future(completion_signal)

            try:
                done, pending = await asyncio.wait(
                    {timeout_task, signal_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                signal_task.cancel()
                timeout_task.cancel()
                return LoginResult.CANCELLED  # noqa: B012 — finally still runs

            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

            if signal_task in done and not signal_task.cancelled():
                cookies = await context.cookies()
                if plat.is_authenticated(cookies):
                    await context.storage_state(path=storage_state_path)
                    return LoginResult.SAVED
                return LoginResult.ABANDONED

            return LoginResult.TIMED_OUT
        finally:
            with suppress(Exception):
                await browser.close()
            if pid_file is not None and pid_file.exists():
                pid_file.unlink(missing_ok=True)
```

Note on the cancellation path: returning from inside an `async with` block runs the `finally` cleanup before the return propagates, so `browser.close()` always runs. The `LoginResult.CANCELLED` return inside the except branch is not user-visible to a `task.cancel()` caller (the `CancelledError` is re-raised at task boundary), but it ensures the engine reports `CANCELLED` if the signal task itself raised cancel from inside.

- [ ] **Step 5.3.4: Run the engine tests.**

```
pytest tests/test_login_engine.py -xvs
```

Expected: 6 PASS. If `test_browser_closes_on_cancellation` is flaky on first run, the implementation needs the `try/finally` around `browser.close()` strictly outside the `asyncio.wait` block (which it is in the code above) — re-read carefully if it fails.

Also ensure `pyproject.toml`'s test deps include `pytest-asyncio` (likely already present given existing async code; if not, add it and reinstall).

### Task 5.4 — Refactor the CLI shim

- [ ] **Step 5.4.1: Identify the existing CLI command from Step 5.0.**

Open the file containing today's `flatpilot login <platform>` implementation. Note its exact name, signature, and how it's registered with typer (if applicable).

- [ ] **Step 5.4.2: Write a regression test that asserts the existing CLI surface is preserved.**

Append to `tests/test_login_engine.py`:

```python
import sys

from typer.testing import CliRunner

from flatpilot.cli import app as cli_app


def test_cli_login_invokes_run_login_session(monkeypatch, tmp_path) -> None:
    """`flatpilot login wg-gesucht` calls run_login_session with the legacy storage path."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")

    captured: dict = {}

    async def fake_run(platform, **kwargs):
        captured["platform"] = platform
        captured["storage_state_path"] = kwargs["storage_state_path"]
        captured["timeout_sec"] = kwargs.get("timeout_sec")
        return LoginResult.SAVED

    monkeypatch.setattr("flatpilot.sessions.login_engine.run_login_session", fake_run)

    # Patch input() so the engine's stdin signal doesn't block.
    monkeypatch.setattr("builtins.input", lambda *_args, **_kw: "")

    runner = CliRunner()
    result = runner.invoke(cli_app, ["login", "wg-gesucht"])
    assert result.exit_code == 0
    assert captured["platform"] == "wg-gesucht"
    assert captured["storage_state_path"] == tmp_path / ".flatpilot" / "sessions" / "wg-gesucht" / "state.json"


def test_cli_login_unknown_platform_returns_nonzero() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app, ["login", "no-such-platform"])
    assert result.exit_code != 0
```

- [ ] **Step 5.4.3: Refactor the CLI command.**

In the file that holds today's `flatpilot login <platform>` (from Step 5.0.1), replace the body of the command with:

```python
import asyncio

from flatpilot.sessions.login_engine import LoginResult, run_login_session
from flatpilot.sessions.paths import session_storage_path
from flatpilot.sessions.platforms import UnknownPlatform
from flatpilot.users import DEFAULT_USER_ID


@app.command("login")
def cli_login(platform: str) -> None:
    """Open a headed browser, wait for the user to log in by hand, save cookies."""
    storage = session_storage_path(DEFAULT_USER_ID, platform)

    async def stdin_signal() -> None:
        await asyncio.to_thread(input, "Press Enter when you see your dashboard… ")

    try:
        result = asyncio.run(
            run_login_session(
                platform,
                storage_state_path=storage,
                completion_signal=stdin_signal(),
            )
        )
    except UnknownPlatform:
        typer.echo(f"unknown platform: {platform}", err=True)
        raise typer.Exit(code=1)

    msg = {
        LoginResult.SAVED: f"saved cookies to {storage}",
        LoginResult.ABANDONED: "no auth cookies captured — try again",
        LoginResult.TIMED_OUT: "timed out (5 minutes elapsed)",
        LoginResult.CANCELLED: "cancelled",
    }[result]
    typer.echo(msg)
    if result != LoginResult.SAVED:
        raise typer.Exit(code=1)
```

If the existing implementation has additional surface (e.g. a `--timeout` flag, a different prompt, etc.) — preserve it. The contract from Step 5.4.2's regression test is the floor, not the ceiling.

- [ ] **Step 5.4.4: Re-run.**

```
pytest tests/test_login_engine.py -xvs
```

Expected: all 8 tests PASS (6 engine + 2 CLI).

### Task 5.5 — Phase 5 verification

- [ ] **Step 5.5.1: Manual smoke against a real platform** (NOT required to pass CI but required before commit).

```bash
# Confirm cookies still land at the legacy path:
flatpilot login wg-gesucht
# Log in by hand in the spawned Chromium window, press Enter.
# Verify:
ls -la ~/.flatpilot/sessions/wg-gesucht/state.json
# Confirm an existing scrape still works:
flatpilot scrape wg-gesucht --dry-run
```

If the scrape fails with `NotAuthenticated`, the cookie names in `platforms.py:_wg_gesucht_authed` are wrong — fix them by re-reading `scrapers/wg_gesucht.py`'s auth detection.

- [ ] **Step 5.5.2: Run full Phase 5 test suite + linters.**

```
pytest tests/test_login_engine.py tests/test_session_paths.py tests/test_session_platforms.py -xvs
ruff check src/flatpilot/sessions/ tests/test_session_*.py tests/test_login_engine.py
mypy src/flatpilot/sessions/
```

Expected: all PASS, lint clean.

- [ ] **Step 5.5.3: Existing test suite (regression).**

```
pytest -x
```

Expected: all PASS. The CLI refactor preserves the external contract.

### Task 5.6 — Phase 5 commit

- [ ] **Step 5.6.1: Commit.**

```bash
git add src/flatpilot/sessions/ src/flatpilot/cli.py \
  tests/test_login_engine.py tests/test_session_paths.py tests/test_session_platforms.py
# Plus whichever file actually held the original 'flatpilot login' command.
git commit -m "FlatPilot-ix2m/8jx: phase 5 — login engine refactored to async, signal-driven

- src/flatpilot/sessions/login_engine.py: run_login_session(...) takes a
  completion_signal Awaitable[None]. CLI passes a stdin coroutine,
  FastAPI (Phase 6) passes an asyncio.Event. SAVED only when cookies
  pass the platform's is_authenticated heuristic; ABANDONED otherwise
  with no file write (don't overwrite working state.json with broken).
- src/flatpilot/sessions/platforms.py: PLATFORMS registry centralizes
  per-platform login URL + cookie auth heuristic. Heuristics lifted
  from existing scrapers — no new platform knowledge introduced.
- src/flatpilot/sessions/paths.py: session_storage_path resolves user
  1 to the legacy ~/.flatpilot/sessions/<p>/state.json path (so
  scrapers keep working unchanged); user >= 2 to the namespaced
  ~/.flatpilot/users/<uid>/sessions/<p>/state.json.
- Engine writes Chromium PID to ~/.flatpilot/runtime/playwright_pids/
  for the server's orphan-process scan; unlinks on clean exit.
- CLI shim is async now (preserves UX and exit codes verbatim).

Phase 6 wires this into FastAPI's connections routes."
```

---

## Phase 6 — Connection routes

**Spec sections:** §3.3, §5.4, §6.5 (frontend mirror), §7.5.

**Files:**
- Modify: `src/flatpilot/server/schemas.py` (add `ConnectionOut`)
- Create: `src/flatpilot/server/routes/connections.py`
- Create: `tests/test_server_connections.py`

### Task 6.1 — `ConnectionOut` schema + `GET /api/connections`

- [ ] **Step 6.1.1: Add the schema.**

Append to `src/flatpilot/server/schemas.py`:

```python
from typing import Literal

ConnectionStatus = Literal["connected", "expired", "disconnected", "in_progress"]


class ConnectionOut(BaseModel):
    platform: str
    status: ConnectionStatus
    expires_at: str | None  # ISO timestamp; null when disconnected
```

- [ ] **Step 6.1.2: Write the failing test for `GET /api/connections`.**

Create `tests/test_server_connections.py`:

```python
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")
    from flatpilot.server.settings import get_settings
    get_settings.cache_clear()

    from flatpilot.server.app import create_app
    return TestClient(create_app())


def _login(client: TestClient, email: str = "user@example.com") -> dict[str, str]:
    captured: list[str] = []

    def fake_send(email: str, link: str) -> None:
        captured.append(link.split("?t=")[1])

    with mock.patch("flatpilot.server.routes.auth.send_magic_link", side_effect=fake_send):
        client.post("/api/auth/request", json={"email": email})
    r = client.post("/api/auth/verify", json={"token": captured[0]})
    return {"fp_session": r.cookies["fp_session"]}


def _write_state_json(path: Path, *, expires_in_sec: float, cookie_name: str = "WGG_SESSION") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expires = time.time() + expires_in_sec
    data = {
        "cookies": [{"name": cookie_name, "value": "abc", "expires": expires}],
        "origins": [],
    }
    path.write_text(json.dumps(data))


def test_get_connections_returns_disconnected_for_all_when_no_state_files(
    client: TestClient,
) -> None:
    cookies = _login(client)
    r = client.get("/api/connections", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    by_plat = {c["platform"]: c for c in body["connections"]}
    assert set(by_plat) == {"wg-gesucht", "kleinanzeigen", "immoscout24"}
    for c in by_plat.values():
        assert c["status"] == "disconnected"
        assert c["expires_at"] is None


def test_get_connections_reports_connected_when_state_json_present_with_fresh_cookie(
    client: TestClient, tmp_path: Path
) -> None:
    cookies = _login(client)
    state = tmp_path / ".flatpilot" / "sessions" / "wg-gesucht" / "state.json"
    _write_state_json(state, expires_in_sec=3600)

    r = client.get("/api/connections", cookies=cookies)
    by_plat = {c["platform"]: c for c in r.json()["connections"]}
    assert by_plat["wg-gesucht"]["status"] == "connected"
    assert by_plat["wg-gesucht"]["expires_at"] is not None


def test_get_connections_reports_expired_when_all_cookies_stale(
    client: TestClient, tmp_path: Path
) -> None:
    cookies = _login(client)
    state = tmp_path / ".flatpilot" / "sessions" / "wg-gesucht" / "state.json"
    _write_state_json(state, expires_in_sec=-3600)

    r = client.get("/api/connections", cookies=cookies)
    by_plat = {c["platform"]: c for c in r.json()["connections"]}
    assert by_plat["wg-gesucht"]["status"] == "expired"


def test_get_connections_uses_per_user_path_for_non_default_user(
    client: TestClient, tmp_path: Path
) -> None:
    """Non-seed users see status from ~/.flatpilot/users/<uid>/sessions/<p>/state.json."""
    cookies = _login(client, "non.seed@example.com")
    # _login created users.id=2 (seed user 1 has email=NULL by default).
    # state.json under user 2's namespace:
    state = tmp_path / ".flatpilot" / "users" / "2" / "sessions" / "wg-gesucht" / "state.json"
    _write_state_json(state, expires_in_sec=3600)

    r = client.get("/api/connections", cookies=cookies)
    by_plat = {c["platform"]: c for c in r.json()["connections"]}
    assert by_plat["wg-gesucht"]["status"] == "connected"


def test_get_connections_401s_without_cookie(client: TestClient) -> None:
    r = client.get("/api/connections")
    assert r.status_code == 401
```

- [ ] **Step 6.1.3: Run, confirm failures.**

```
pytest tests/test_server_connections.py -k "get_connections" -xvs
```

Expected: ImportError on the routes module.

- [ ] **Step 6.1.4: Create `src/flatpilot/server/routes/connections.py` with `GET /` only.**

```python
"""Connections page backend: per-platform connect status + start/done flow.

Status derives from ~/.flatpilot/[users/<uid>/]sessions/<platform>/state.json:
- file exists + at least one cookie's expires > now → connected
- file exists + every cookie's expires < now      → expired
- file absent                                     → disconnected
- (user, platform) in _pending                    → in_progress

The (user, platform) → asyncio.Event registry is in-process (single-worker
uvicorn only). Multi-worker support is FlatPilot-28o.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from flatpilot.server.deps import User, get_current_user
from flatpilot.server.schemas import ConnectionOut
from flatpilot.sessions.login_engine import LoginResult
from flatpilot.sessions.paths import session_storage_path
from flatpilot.sessions.platforms import PLATFORMS

router = APIRouter()

# In-process registries (Phase 6 docstring above).
_pending: dict[tuple[int, str], asyncio.Event] = {}
_pending_tasks: dict[tuple[int, str], asyncio.Task[LoginResult]] = {}
_last_result: dict[tuple[int, str], tuple[LoginResult, float]] = {}  # (result, set_at)
_LAST_RESULT_TTL_SEC = 60.0


def _read_state_cookies(path: Path) -> Sequence[dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    cookies = data.get("cookies", [])
    return cookies if isinstance(cookies, list) else []


def _connection_status(user_id: int, platform: str) -> ConnectionOut:
    if (user_id, platform) in _pending:
        return ConnectionOut(platform=platform, status="in_progress", expires_at=None)
    state = session_storage_path(user_id, platform)
    if not state.exists():
        return ConnectionOut(platform=platform, status="disconnected", expires_at=None)
    cookies = _read_state_cookies(state)
    if not cookies:
        return ConnectionOut(platform=platform, status="disconnected", expires_at=None)
    now = time.time()
    fresh = [c for c in cookies if isinstance(c.get("expires"), (int, float)) and c["expires"] > now]
    if not fresh:
        return ConnectionOut(platform=platform, status="expired", expires_at=None)
    earliest = min(c["expires"] for c in fresh)
    iso = datetime.fromtimestamp(earliest, tz=UTC).isoformat()
    return ConnectionOut(platform=platform, status="connected", expires_at=iso)


@router.get("", response_model=dict)
async def list_connections(user: User = Depends(get_current_user)) -> dict:
    return {
        "connections": [
            _connection_status(user.id, name).model_dump() for name in PLATFORMS
        ]
    }
```

- [ ] **Step 6.1.5: Re-run, confirm pass.**

```
pytest tests/test_server_connections.py -k "get_connections" -xvs
```

Expected: 5 PASS.

### Task 6.2 — `POST /api/connections/{platform}/start`

- [ ] **Step 6.2.1: Write the failing tests.**

Append to `tests/test_server_connections.py`:

```python
def test_start_returns_202_and_calls_run_login_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login(client)

    captured: dict = {}

    async def fake_run(platform, **kw):
        captured["platform"] = platform
        captured["storage_state_path"] = kw["storage_state_path"]
        # Emulate engine waiting on the signal.
        await kw["completion_signal"]
        return LoginResult.SAVED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    r = client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    assert r.status_code == 202
    assert r.json() == {"status": "in_progress"}

    # The handler returns BEFORE the engine completes; let the loop run a tick.
    import asyncio as _asyncio
    _asyncio.get_event_loop().run_until_complete(_asyncio.sleep(0))

    assert captured["platform"] == "wg-gesucht"


def test_start_404s_for_unknown_platform(client: TestClient) -> None:
    cookies = _login(client)
    r = client.post("/api/connections/nope/start", cookies=cookies)
    assert r.status_code == 404


def test_start_409s_when_already_in_progress(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login(client)

    blocking = asyncio.Event()

    async def fake_run(platform, **kw):
        await blocking.wait()
        return LoginResult.SAVED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    r1 = client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    assert r1.status_code == 202

    r2 = client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    assert r2.status_code == 409
    blocking.set()  # let the first one finish for cleanup
```

- [ ] **Step 6.2.2: Run, confirm failures.**

```
pytest tests/test_server_connections.py -k "start" -xvs
```

- [ ] **Step 6.2.3: Add `/start` to `routes/connections.py`.**

```python
@router.post("/{platform}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_connect(platform: str, user: User = Depends(get_current_user)) -> dict:
    if platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail="unknown_platform")
    key = (user.id, platform)
    if key in _pending:
        raise HTTPException(status_code=409, detail="already_in_progress")

    # Drop any stale cached result from a prior session.
    _last_result.pop(key, None)
    _sweep_last_result()

    event = asyncio.Event()
    _pending[key] = event

    async def runner() -> LoginResult:
        try:
            result = await run_login_session(
                platform,
                storage_state_path=session_storage_path(user.id, platform),
                completion_signal=event.wait(),
                timeout_sec=300.0,
            )
        except BaseException:
            _last_result[key] = (LoginResult.CANCELLED, time.time())
            raise
        else:
            _last_result[key] = (result, time.time())
            return result
        finally:
            _pending.pop(key, None)
            _pending_tasks.pop(key, None)

    _pending_tasks[key] = asyncio.create_task(runner())
    return {"status": "in_progress"}


def _sweep_last_result() -> None:
    """Drop _last_result entries older than the TTL. O(n) — n is small."""
    now = time.time()
    stale = [k for k, (_, ts) in _last_result.items() if now - ts > _LAST_RESULT_TTL_SEC]
    for k in stale:
        _last_result.pop(k, None)
```

Add the missing import at the top of `routes/connections.py`:

```python
from flatpilot.sessions.login_engine import run_login_session
```

- [ ] **Step 6.2.4: Re-run.**

```
pytest tests/test_server_connections.py -k "start" -xvs
```

Expected: 3 PASS.

### Task 6.3 — `POST /api/connections/{platform}/done` (synchronous-with-timeout + last_result cache)

- [ ] **Step 6.3.1: Write the failing tests for the synchronous-with-result behavior.**

Append to `tests/test_server_connections.py`:

```python
def test_done_returns_saved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login(client)

    async def fake_run(platform, **kw):
        await kw["completion_signal"]
        return LoginResult.SAVED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    r = client.post("/api/connections/wg-gesucht/done", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"result": "saved"}


def test_done_returns_abandoned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login(client)

    async def fake_run(platform, **kw):
        await kw["completion_signal"]
        return LoginResult.ABANDONED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    r = client.post("/api/connections/wg-gesucht/done", cookies=cookies)
    assert r.json() == {"result": "abandoned"}


def test_done_404s_when_no_start_and_no_cached_result(client: TestClient) -> None:
    cookies = _login(client)
    r = client.post("/api/connections/wg-gesucht/done", cookies=cookies)
    assert r.status_code == 404


def test_done_uses_cached_result_when_runner_already_finished(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Engine times out at 300s; runner's finally stashes TIMED_OUT in _last_result.
    A /done arriving after returns the cached value, not 404."""
    cookies = _login(client)

    async def fake_run(platform, **kw):
        # Don't wait on the signal — return immediately as TIMED_OUT.
        return LoginResult.TIMED_OUT

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    # Let the runner's finally stash the result.
    import asyncio as _asyncio
    for _ in range(10):
        _asyncio.get_event_loop().run_until_complete(_asyncio.sleep(0))

    r = client.post("/api/connections/wg-gesucht/done", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"result": "timed_out"}


def test_last_result_is_consumed_single_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies = _login(client)

    async def fake_run(platform, **kw):
        return LoginResult.ABANDONED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    import asyncio as _asyncio
    for _ in range(10):
        _asyncio.get_event_loop().run_until_complete(_asyncio.sleep(0))

    r1 = client.post("/api/connections/wg-gesucht/done", cookies=cookies)
    assert r1.status_code == 200

    r2 = client.post("/api/connections/wg-gesucht/done", cookies=cookies)
    # Cache was consumed by r1; second /done has nothing to return.
    assert r2.status_code == 404


def test_done_does_not_affect_other_users_pending(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User 2's /done for wg-gesucht does not flip user 1's event."""
    cookies_a = _login(client, "alice@example.com")
    cookies_b = _login(client, "bob@example.com")

    blocking_a = asyncio.Event()

    async def fake_run(platform, **kw):
        await kw["completion_signal"]
        return LoginResult.SAVED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    client.post("/api/connections/wg-gesucht/start", cookies=cookies_a)
    # Bob calls /done — should 404 since his (uid_b, wg-gesucht) is not pending.
    r = client.post("/api/connections/wg-gesucht/done", cookies=cookies_b)
    assert r.status_code == 404


def test_done_handles_empty_body_beacon(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tab-close beacon: navigator.sendBeacon('/api/connections/<p>/done') with no body."""
    cookies = _login(client)

    async def fake_run(platform, **kw):
        await kw["completion_signal"]
        return LoginResult.ABANDONED

    monkeypatch.setattr(
        "flatpilot.server.routes.connections.run_login_session", fake_run
    )

    client.post("/api/connections/wg-gesucht/start", cookies=cookies)
    r = client.post("/api/connections/wg-gesucht/done", cookies=cookies, content=b"")
    assert r.status_code == 200
    assert r.json()["result"] == "abandoned"
```

- [ ] **Step 6.3.2: Run, confirm failures.**

```
pytest tests/test_server_connections.py -k "done or last_result" -xvs
```

- [ ] **Step 6.3.3: Add `/done` to `routes/connections.py`.**

```python
@router.post("/{platform}/done")
async def done_connect(
    platform: str, user: User = Depends(get_current_user)
) -> dict:
    if platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail="unknown_platform")
    key = (user.id, platform)
    runner_task = _pending_tasks.get(key)

    # Case 1: runner already finished — read cache.
    if runner_task is None:
        cached = _last_result.pop(key, None)
        if cached is None:
            raise HTTPException(status_code=404, detail="no_session_in_progress")
        result, _ = cached
        return {"result": result.name.lower()}

    # Case 2: runner still running — signal it and await the verdict.
    event = _pending.get(key)
    if event is not None:
        event.set()
    try:
        result = await asyncio.wait_for(asyncio.shield(runner_task), timeout=30.0)
    except asyncio.TimeoutError:
        result = LoginResult.TIMED_OUT
    return {"result": result.name.lower()}
```

The `asyncio.shield` prevents `wait_for`'s timeout from cancelling the underlying runner task — the engine's own 300s timeout owns its lifecycle. We just stop *waiting* for it.

- [ ] **Step 6.3.4: Re-run.**

```
pytest tests/test_server_connections.py -k "done or last_result" -xvs
```

Expected: 7 PASS.

### Task 6.4 — Wire connections router into the app

- [ ] **Step 6.4.1: Verify it's already wired (Phase 3's `_register_routers` imports lazily).**

```bash
grep -n "connections" src/flatpilot/server/app.py
```

Should show the lazy `try: from ... import router as connections_router` block. If the module exists now, the lazy import succeeds.

- [ ] **Step 6.4.2: Smoke check.**

```bash
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000 &
sleep 2
curl -s http://localhost:8000/api/connections -o /dev/null -w "%{http_code}\n"
# Expect 401 (no auth)
kill %1
```

### Task 6.5 — Phase 6 verification

- [ ] **Step 6.5.1: Full Phase 6 test suite + linters.**

```
pytest tests/test_server_connections.py -xvs
ruff check src/flatpilot/server/routes/connections.py tests/test_server_connections.py
mypy src/flatpilot/server/routes/connections.py
```

Expected: all PASS, lint clean.

- [ ] **Step 6.5.2: Existing suite (regression).**

```
pytest -x
```

### Task 6.6 — Phase 6 commit

- [ ] **Step 6.6.1: Commit.**

```bash
git add src/flatpilot/server/schemas.py src/flatpilot/server/routes/connections.py \
  tests/test_server_connections.py
git commit -m "FlatPilot-ix2m/8jx: phase 6 — connections routes (start/done/list)

- GET /api/connections derives status from ~/.flatpilot/sessions/<p>/state.json
  (or per-user namespace for users >= 2) — connected/expired/disconnected.
  in_progress when (user, platform) is in the pending registry.
- POST /api/connections/<platform>/start: 202, registers asyncio.Event +
  task, spawns run_login_session as a background task. 409 on duplicate,
  404 on unknown platform.
- POST /api/connections/<platform>/done is synchronous-with-timeout per
  spec §3.3 step 5 — calls event.set(), awaits the runner with a 30s
  timeout, returns 200 {result: saved|abandoned|timed_out|cancelled}.
  No post-Done polling needed by the frontend.
- _last_result cache: when the runner finishes before /done arrives
  (engine timeout / browser crash / cancellation), the runner's finally
  stashes the LoginResult so a late /done reads it instead of 404'ing.
  Single-read consumed; cleared on next /start for same key; swept by
  TTL.
- Empty-body /done supports navigator.sendBeacon from the modal's
  beforeunload handler — closing the tab cleans up the runner without
  the 300s engine timeout.

Phase 7 ports the matches and applications endpoints."
```

---

## Phase 7 — Matches & Applications routes + static audits

**Spec sections:** §3.4, §5.2, §5.3, §8.1 (audits).

**Files:**
- Modify: `src/flatpilot/server/schemas.py` (add `MatchOut`, `ApplicationOut`, `ApplyRequest`, `ResponseUpdate`)
- Create: `src/flatpilot/server/routes/matches.py`
- Create: `src/flatpilot/server/routes/applications.py`
- Create: `tests/test_server_matches.py`
- Create: `tests/test_server_applications.py`
- Create: `tests/test_server_static_audits.py`

### Task 7.0 — Read existing dashboard + apply engine

- [ ] **Step 7.0.1: Read the legacy dashboard's mutation endpoints to mirror their behavior.**

Read `src/flatpilot/server.py` (the legacy stdlib HTTP server) and `src/flatpilot/view.py`. Note:
- The exact SQL behind today's `/api/matches/{id}/skip` (likely `UPDATE matches SET decision='skipped' WHERE id=?`).
- How the legacy `/api/applications` POST shells out to apply via subprocess (`_spawn_apply` at `src/flatpilot/server.py:61`).
- The exact response_text + status fields in `/api/applications/{id}/response`.

The new endpoints must produce identical DB state for the same input.

- [ ] **Step 7.0.2: Confirm `apply.py`'s public callable.**

```bash
grep -n "^def \|^async def " src/flatpilot/apply.py
```

Identify the function the FastAPI route will call inside `run_in_executor` instead of shelling out (likely `apply_to_flat(flat_id: int, user_id: int) -> ApplicationResult` or similar). If the existing function is `argv`-shaped (CLI-only), wrap it.

### Task 7.1 — Schemas

- [ ] **Step 7.1.1: Append schemas.**

In `src/flatpilot/server/schemas.py`:

```python
class MatchOut(BaseModel):
    id: int
    flat_id: int
    title: str
    district: str | None
    rent_warm_eur: float | None
    rooms: float | None
    size_sqm: float | None
    listing_url: str
    decided_at: str
    matched_saved_searches: list[str]


class ApplicationOut(BaseModel):
    id: int
    flat_id: int
    platform: str
    listing_url: str
    title: str
    rent_warm_eur: float | None
    rooms: float | None
    size_sqm: float | None
    district: str | None
    applied_at: str
    method: str
    status: str
    response_text: str | None
    response_received_at: str | None
    notes: str | None
    triggered_by_saved_search: str | None


class ApplyRequest(BaseModel):
    flat_id: int


class ApplyResponse(BaseModel):
    application_id: int
    status: str


class ResponseUpdate(BaseModel):
    status: str
    response_text: str
```

### Task 7.2 — Matches routes (`GET /api/matches`, `POST /api/matches/{id}/skip`)

- [ ] **Step 7.2.1: Write the failing tests.**

Create `tests/test_server_matches.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from flatpilot.users import DEFAULT_USER_ID


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")
    from flatpilot.server.settings import get_settings
    get_settings.cache_clear()
    from flatpilot.server.app import create_app
    return TestClient(create_app())


def _login(client: TestClient, email: str = "user@example.com") -> tuple[dict[str, str], int]:
    captured: list[str] = []
    with mock.patch(
        "flatpilot.server.routes.auth.send_magic_link",
        side_effect=lambda email, link: captured.append(link.split("?t=")[1]),
    ):
        client.post("/api/auth/request", json={"email": email})
    r = client.post("/api/auth/verify", json={"token": captured[0]})
    return {"fp_session": r.cookies["fp_session"]}, r.json()["user_id"]


def _seed_match(conn: sqlite3.Connection, *, user_id: int, flat_id: int) -> int:
    """Insert a flats row + matches row owned by user_id; return matches.id."""
    conn.execute(
        "INSERT OR IGNORE INTO flats (id, listing_url, title, platform, scraped_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (flat_id, f"https://example.com/{flat_id}", f"Flat {flat_id}", "wg-gesucht", datetime.now(UTC).isoformat()),
    )
    cur = conn.execute(
        "INSERT INTO matches (user_id, flat_id, profile_version_hash, decision, "
        "decision_reasons_json, decided_at, matched_saved_searches_json) "
        "VALUES (?, ?, ?, 'match', '[]', ?, '[\"search-1\"]')",
        (user_id, flat_id, f"hash-{flat_id}", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_get_matches_returns_only_callers_user_id(
    client: TestClient, tmp_path: Path
) -> None:
    cookies, uid = _login(client, "alice@example.com")
    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        m_alice = _seed_match(conn, user_id=uid, flat_id=1)
        # User 1 (seed) match — alice must not see it.
        _seed_match(conn, user_id=DEFAULT_USER_ID, flat_id=2)
    finally:
        conn.close()

    r = client.get("/api/matches", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    ids = [m["id"] for m in body["matches"]]
    assert ids == [m_alice]


def test_skip_match_updates_decision(client: TestClient, tmp_path: Path) -> None:
    cookies, uid = _login(client)
    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        match_id = _seed_match(conn, user_id=uid, flat_id=1)
    finally:
        conn.close()

    r = client.post(f"/api/matches/{match_id}/skip", cookies=cookies)
    assert r.status_code == 204

    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        decision = conn.execute(
            "SELECT decision FROM matches WHERE id = ?", (match_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert decision == "skipped"


def test_skip_match_404s_for_other_users_match(
    client: TestClient, tmp_path: Path
) -> None:
    cookies, _uid = _login(client)
    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        # Match owned by seed user — alice cannot skip it.
        other_id = _seed_match(conn, user_id=DEFAULT_USER_ID, flat_id=99)
    finally:
        conn.close()

    r = client.post(f"/api/matches/{other_id}/skip", cookies=cookies)
    assert r.status_code == 404


def test_get_matches_401s_without_cookie(client: TestClient) -> None:
    r = client.get("/api/matches")
    assert r.status_code == 401
```

- [ ] **Step 7.2.2: Run, confirm failures.**

- [ ] **Step 7.2.3: Create `src/flatpilot/server/routes/matches.py`.**

```python
"""Matches: list user's matched flats; mark one skipped."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from flatpilot.server.deps import User, get_current_user, get_db
from flatpilot.server.schemas import MatchOut

router = APIRouter()


@router.get("", response_model=dict)
async def list_matches(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = conn.execute(
        "SELECT m.id, m.flat_id, f.title, f.district, f.rent_warm_eur, f.rooms, "
        "f.size_sqm, f.listing_url, m.decided_at, m.matched_saved_searches_json "
        "FROM matches m JOIN flats f ON m.flat_id = f.id "
        "WHERE m.user_id = ? AND m.decision = 'match' "
        "ORDER BY m.decided_at DESC LIMIT 200",
        (user.id,),
    ).fetchall()
    return {"matches": [_row_to_match(r) for r in rows]}


def _row_to_match(r: tuple[Any, ...]) -> dict:
    import json
    saved_searches = []
    try:
        parsed = json.loads(r[9]) if r[9] else []
        if isinstance(parsed, list):
            saved_searches = [str(x) for x in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return MatchOut(
        id=int(r[0]),
        flat_id=int(r[1]),
        title=str(r[2] or ""),
        district=r[3],
        rent_warm_eur=r[4],
        rooms=r[5],
        size_sqm=r[6],
        listing_url=str(r[7] or ""),
        decided_at=str(r[8] or ""),
        matched_saved_searches=saved_searches,
    ).model_dump()


@router.post("/{match_id}/skip", status_code=status.HTTP_204_NO_CONTENT)
async def skip_match(
    match_id: int,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    cur = conn.execute(
        "UPDATE matches SET decision = 'skipped' "
        "WHERE id = ? AND user_id = ? AND decision = 'match'",
        (match_id, user.id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="match_not_found")
```

- [ ] **Step 7.2.4: Re-run.**

```
pytest tests/test_server_matches.py -xvs
```

Expected: 4 PASS.

### Task 7.3 — Applications routes (`GET`, `POST`, `POST /{id}/response`)

- [ ] **Step 7.3.1: Write the failing tests.**

Create `tests/test_server_applications.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from flatpilot.users import DEFAULT_USER_ID


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")
    from flatpilot.server.settings import get_settings
    get_settings.cache_clear()
    from flatpilot.server.app import create_app
    return TestClient(create_app())


def _login(client: TestClient, email: str = "user@example.com") -> tuple[dict[str, str], int]:
    captured: list[str] = []
    with mock.patch(
        "flatpilot.server.routes.auth.send_magic_link",
        side_effect=lambda email, link: captured.append(link.split("?t=")[1]),
    ):
        client.post("/api/auth/request", json={"email": email})
    r = client.post("/api/auth/verify", json={"token": captured[0]})
    return {"fp_session": r.cookies["fp_session"]}, r.json()["user_id"]


def _seed_application(conn: sqlite3.Connection, *, user_id: int, flat_id: int, status: str = "submitted") -> int:
    conn.execute(
        "INSERT OR IGNORE INTO flats (id, listing_url, title, platform, scraped_at) "
        "VALUES (?, ?, ?, 'wg-gesucht', ?)",
        (flat_id, f"https://example.com/{flat_id}", f"Flat {flat_id}", datetime.now(UTC).isoformat()),
    )
    cur = conn.execute(
        "INSERT INTO applications (user_id, flat_id, platform, listing_url, title, "
        "applied_at, method, status) "
        "VALUES (?, ?, 'wg-gesucht', ?, ?, ?, 'manual', ?)",
        (user_id, flat_id, f"https://example.com/{flat_id}", f"Flat {flat_id}",
         datetime.now(UTC).isoformat(), status),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_get_applications_scoped_to_user(client: TestClient, tmp_path: Path) -> None:
    cookies, uid = _login(client)
    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        mine = _seed_application(conn, user_id=uid, flat_id=1)
        _seed_application(conn, user_id=DEFAULT_USER_ID, flat_id=2)
    finally:
        conn.close()

    r = client.get("/api/applications", cookies=cookies)
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["applications"]]
    assert ids == [mine]


def test_post_application_calls_apply_engine(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookies, uid = _login(client)

    # Seed a flat the user is "matched" against.
    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        conn.execute(
            "INSERT INTO flats (id, listing_url, title, platform, scraped_at) "
            "VALUES (777, 'https://example.com/777', 'Test Flat', 'wg-gesucht', ?)",
            (datetime.now(UTC).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    captured: dict = {}

    def fake_apply(flat_id: int, user_id: int):
        captured["flat_id"] = flat_id
        captured["user_id"] = user_id
        # Mimic the apply engine inserting an applications row.
        conn = sqlite3.connect(tmp_path / "fp.db")
        try:
            cur = conn.execute(
                "INSERT INTO applications (user_id, flat_id, platform, listing_url, title, "
                "applied_at, method, status) "
                "VALUES (?, ?, 'wg-gesucht', 'https://example.com/777', 'Test Flat', ?, "
                "'manual', 'submitted')",
                (user_id, flat_id, datetime.now(UTC).isoformat()),
            )
            conn.commit()
            return {"application_id": int(cur.lastrowid), "status": "submitted"}
        finally:
            conn.close()

    monkeypatch.setattr(
        "flatpilot.server.routes.applications.apply_to_flat", fake_apply
    )

    r = client.post("/api/applications", json={"flat_id": 777}, cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "submitted"
    assert captured == {"flat_id": 777, "user_id": uid}


def test_post_response_updates_application_for_owner_only(
    client: TestClient, tmp_path: Path
) -> None:
    cookies, uid = _login(client)
    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        mine = _seed_application(conn, user_id=uid, flat_id=1)
        other = _seed_application(conn, user_id=DEFAULT_USER_ID, flat_id=2)
    finally:
        conn.close()

    r1 = client.post(
        f"/api/applications/{mine}/response",
        json={"status": "viewing_invited", "response_text": "Come Saturday at 2pm."},
        cookies=cookies,
    )
    assert r1.status_code == 204

    # Other user's application: 404, not 403, no leak.
    r2 = client.post(
        f"/api/applications/{other}/response",
        json={"status": "viewing_invited", "response_text": "Same"},
        cookies=cookies,
    )
    assert r2.status_code == 404

    conn = sqlite3.connect(tmp_path / "fp.db")
    try:
        my_status = conn.execute(
            "SELECT status, response_text FROM applications WHERE id = ?", (mine,)
        ).fetchone()
        other_status = conn.execute(
            "SELECT status, response_text FROM applications WHERE id = ?", (other,)
        ).fetchone()
    finally:
        conn.close()

    assert my_status[0] == "viewing_invited"
    assert my_status[1] == "Come Saturday at 2pm."
    # Other user's row untouched.
    assert other_status[0] == "submitted"
    assert other_status[1] is None
```

- [ ] **Step 7.3.2: Run, confirm failures.**

- [ ] **Step 7.3.3: Create `src/flatpilot/server/routes/applications.py`.**

```python
"""Applications: list user's apps, submit a new one, paste landlord reply."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from flatpilot.apply import apply_to_flat  # see Step 7.0.2; wrap if needed
from flatpilot.server.deps import User, get_current_user, get_db
from flatpilot.server.schemas import (
    ApplicationOut,
    ApplyRequest,
    ApplyResponse,
    ResponseUpdate,
)

router = APIRouter()


@router.get("", response_model=dict)
async def list_applications(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    rows = conn.execute(
        "SELECT id, flat_id, platform, listing_url, title, rent_warm_eur, rooms, "
        "size_sqm, district, applied_at, method, status, response_text, "
        "response_received_at, notes, triggered_by_saved_search "
        "FROM applications WHERE user_id = ? ORDER BY applied_at DESC LIMIT 200",
        (user.id,),
    ).fetchall()
    return {"applications": [_row_to_app(r) for r in rows]}


def _row_to_app(r: tuple[Any, ...]) -> dict:
    return ApplicationOut(
        id=int(r[0]), flat_id=int(r[1]), platform=str(r[2] or ""),
        listing_url=str(r[3] or ""), title=str(r[4] or ""),
        rent_warm_eur=r[5], rooms=r[6], size_sqm=r[7], district=r[8],
        applied_at=str(r[9] or ""), method=str(r[10] or ""),
        status=str(r[11] or ""), response_text=r[12],
        response_received_at=r[13], notes=r[14],
        triggered_by_saved_search=r[15],
    ).model_dump()


@router.post("", response_model=ApplyResponse)
async def post_application(
    body: ApplyRequest,
    user: User = Depends(get_current_user),
) -> ApplyResponse:
    # apply_to_flat is sync (existing engine). Run in executor to avoid blocking.
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, apply_to_flat, body.flat_id, user.id
    )
    return ApplyResponse(
        application_id=int(result["application_id"]),
        status=str(result["status"]),
    )


@router.post("/{application_id}/response", status_code=status.HTTP_204_NO_CONTENT)
async def post_response(
    application_id: int,
    body: ResponseUpdate,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    from datetime import UTC, datetime
    cur = conn.execute(
        "UPDATE applications "
        "SET status = ?, response_text = ?, response_received_at = ? "
        "WHERE id = ? AND user_id = ?",
        (body.status, body.response_text, datetime.now(UTC).isoformat(), application_id, user.id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="application_not_found")
```

- [ ] **Step 7.3.4: If `apply.py` doesn't have a callable `apply_to_flat(flat_id, user_id)` matching the test's contract, add a thin adapter.**

In `src/flatpilot/apply.py` (or a new `src/flatpilot/apply_api.py` if `apply.py` is CLI-only):

```python
def apply_to_flat(flat_id: int, user_id: int) -> dict[str, int | str]:
    """Public adapter for the FastAPI server. Wraps the existing apply engine.

    Returns {'application_id': int, 'status': str}. Raises on engine errors.
    """
    # Call the existing apply implementation. The exact internal API may
    # differ — see existing CLI command for the reference.
    ...
```

The exact body depends on the existing apply engine — read it before writing. Don't duplicate any logic that already exists.

- [ ] **Step 7.3.5: Re-run.**

```
pytest tests/test_server_applications.py -xvs
```

Expected: 3 PASS.

### Task 7.4 — Static SQL audit (sqlglot-backed)

- [ ] **Step 7.4.1: Write the failing test.**

Create `tests/test_server_static_audits.py`:

```python
"""Static audits run as tests so they fail CI on regression."""

from __future__ import annotations

import ast
from pathlib import Path

import sqlglot

USER_SCOPED_TABLES = {"matches", "applications", "apply_locks"}
ROUTES_DIR = Path(__file__).resolve().parent.parent / "src" / "flatpilot" / "server" / "routes"


def _string_literals_in(file: Path) -> list[tuple[int, str]]:
    tree = ast.parse(file.read_text(), filename=str(file))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) > 20:  # only literals long enough to plausibly be SQL
                out.append((node.lineno, node.value))
    return out


def test_no_unscoped_sql_in_routes_module() -> None:
    """Every SQL string in server/routes/*.py touching user-scoped tables must
    parameter-bind user_id in WHERE / ON / SET context."""
    offenders: list[str] = []
    for py in ROUTES_DIR.glob("*.py"):
        for lineno, literal in _string_literals_in(py):
            try:
                stmt = sqlglot.parse_one(literal, dialect="sqlite")
            except sqlglot.errors.ParseError:
                continue
            if stmt is None:
                continue
            tables_touched = {
                t.name for t in stmt.find_all(sqlglot.exp.Table) if t.name in USER_SCOPED_TABLES
            }
            if not tables_touched:
                continue
            # Check the AST for any reference to a column literally named user_id.
            cols = {c.name for c in stmt.find_all(sqlglot.exp.Column) if c.name == "user_id"}
            if not cols:
                offenders.append(f"{py.relative_to(ROUTES_DIR.parent.parent.parent.parent)}:{lineno} touches {tables_touched} without user_id")
    assert not offenders, "unscoped SQL detected:\n  " + "\n  ".join(offenders)


# Declarative allowlist of GET handlers that are read-only by definition.
# Adding a new GET endpoint requires updating this list — that gate makes
# accidental state-changing GETs fail CI immediately.
READONLY_GET_HANDLERS = {
    "flatpilot.server.routes.auth.auth_me",
    "flatpilot.server.routes.matches.list_matches",
    "flatpilot.server.routes.applications.list_applications",
    "flatpilot.server.routes.connections.list_connections",
}


def test_no_get_endpoints_mutate_state() -> None:
    """Every GET route handler's qualified name must be in READONLY_GET_HANDLERS.

    See spec §3.5: state-changing operations must be POST/DELETE so SameSite=Lax
    blocks cross-site CSRF. A GET handler that quietly writes to the DB breaks
    that property.
    """
    from flatpilot.server.app import create_app

    app = create_app()
    offenders: list[str] = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "GET" not in methods:
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        qualname = f"{endpoint.__module__}.{endpoint.__qualname__}"
        # Skip docs / openapi / starlette internals.
        if qualname.startswith(("fastapi.", "starlette.")):
            continue
        if qualname not in READONLY_GET_HANDLERS:
            offenders.append(qualname)

    assert not offenders, (
        "GET route handlers not in READONLY_GET_HANDLERS — either they should "
        "be POST/DELETE, or this allowlist needs updating:\n  "
        + "\n  ".join(offenders)
    )
```

- [ ] **Step 7.4.2: Run; confirm both audits PASS** (since Phases 4–7 routes were authored under these constraints).

```
pytest tests/test_server_static_audits.py -xvs
```

Expected: 2 PASS. If either fails, the route author missed `user_id` scoping or registered a GET that mutates — fix the route, not the test.

### Task 7.5 — Phase 7 verification

- [ ] **Step 7.5.1: Full Phase 7 test suite + linters.**

```
pytest tests/test_server_matches.py tests/test_server_applications.py \
  tests/test_server_static_audits.py -xvs
ruff check src/flatpilot/server/routes/ tests/test_server_*.py
mypy src/flatpilot/server/routes/
```

Expected: all PASS, lint clean.

- [ ] **Step 7.5.2: Existing suite (regression) — including `flatpilot dashboard`'s legacy tests.**

```
pytest -x
```

Expected: all PASS. The legacy dashboard remains untouched.

### Task 7.6 — Phase 7 commit

- [ ] **Step 7.6.1: Commit.**

```bash
git add src/flatpilot/server/schemas.py \
  src/flatpilot/server/routes/matches.py src/flatpilot/server/routes/applications.py \
  tests/test_server_matches.py tests/test_server_applications.py \
  tests/test_server_static_audits.py
# Plus src/flatpilot/apply.py if Step 7.3.4 added an adapter.
git commit -m "FlatPilot-ix2m/8jx: phase 7 — matches/applications routes + static audits

- GET /api/matches: scoped by user_id, joins flats, ORDER BY decided_at DESC
  LIMIT 200. Returns the same shape the legacy dashboard renders.
- POST /api/matches/{id}/skip: 204; UPDATE matches SET decision='skipped'
  WHERE id = ? AND user_id = ?. 404 if not the caller's (no leak vs 403).
- GET /api/applications: scoped by user_id, ORDER BY applied_at DESC LIMIT 200.
- POST /api/applications {flat_id}: calls apply.apply_to_flat(...) inside
  run_in_executor — no subprocess shell-out (FastAPI is already long-running).
- POST /api/applications/{id}/response: 204; UPDATE scoped by (id, user_id).
  404 leak prevention same as skip.
- test_no_unscoped_sql_in_routes_module: sqlglot AST walks every SQL string
  in server/routes/*.py; if it touches matches/applications/apply_locks
  without a user_id column reference, fail CI. Real parser, not regex.
- test_no_get_endpoints_mutate_state: every GET handler's qualname must be
  in READONLY_GET_HANDLERS. Adding a new GET requires a deliberate two-line
  edit (route + allowlist), preventing accidental state-changing GETs from
  breaking the §3.5 CSRF property."
```

---

## Phase 8 — CLI additions (`set-email`, wizard prompt, doctor row)

**Spec sections:** §1 (CLI additions), §10 acceptance criteria for the CLI surface.

**Files:**
- Modify: `src/flatpilot/cli.py`
- Modify: `src/flatpilot/wizard/init.py`
- Modify: `src/flatpilot/doctor.py`
- Create: `tests/test_set_email_cli.py`
- Create: `tests/test_wizard_email.py`
- Create: `tests/test_doctor_seed_email.py`

### Task 8.1 — `flatpilot set-email <addr>` command

- [ ] **Step 8.1.1: Write the failing test.**

Create `tests/test_set_email_cli.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flatpilot.cli import app as cli_app


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    from flatpilot.database import init_db
    init_db()
    return tmp_path / "fp.db"


def _read_user(db: Path, uid: int = 1) -> tuple[str | None, str | None]:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT email, email_normalized FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()
    return row


def test_set_email_writes_both_columns(db: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app, ["set-email", "me@example.com"])
    assert result.exit_code == 0
    assert _read_user(db) == ("me@example.com", "me@example.com")


def test_set_email_normalizes_case_and_whitespace(db: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app, ["set-email", "  Me@Example.com  "])
    assert result.exit_code == 0
    email, normalized = _read_user(db)
    assert email == "Me@Example.com"  # strip applied; case preserved in `email`
    assert normalized == "me@example.com"


def test_set_email_is_idempotent(db: Path) -> None:
    runner = CliRunner()
    runner.invoke(cli_app, ["set-email", "you@example.com"])
    result = runner.invoke(cli_app, ["set-email", "you@example.com"])
    assert result.exit_code == 0
    assert _read_user(db) == ("you@example.com", "you@example.com")


def test_set_email_rejects_duplicate_normalized(db: Path) -> None:
    """Another user (id=2) already has the same normalized email."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO users (id, email, email_normalized, created_at) "
            "VALUES (2, 'taken@example.com', 'taken@example.com', '2026-04-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()

    runner = CliRunner()
    result = runner.invoke(cli_app, ["set-email", "TAKEN@example.com"])
    assert result.exit_code != 0
    assert "already in use" in result.output.lower() or "duplicate" in result.output.lower()


def test_set_email_rejects_obviously_invalid_email(db: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app, ["set-email", "not-an-email"])
    assert result.exit_code != 0
```

- [ ] **Step 8.1.2: Run, confirm failures.**

```
pytest tests/test_set_email_cli.py -xvs
```

- [ ] **Step 8.1.3: Add the command to `src/flatpilot/cli.py`.**

```python
import re
import sqlite3
from datetime import UTC, datetime

import typer

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.command("set-email")
def cli_set_email(addr: str) -> None:
    """Bind an email to the seed CLI user (users.id = 1) so the Web UI
    can recognize you on first magic-link login."""
    addr = addr.strip()
    if not _EMAIL_RE.match(addr):
        typer.echo(f"not a valid email: {addr}", err=True)
        raise typer.Exit(code=1)
    normalized = addr.lower()

    from flatpilot.database import connect
    from flatpilot.users import DEFAULT_USER_ID

    conn = connect()
    try:
        try:
            conn.execute(
                "UPDATE users SET email = ?, email_normalized = ? WHERE id = ?",
                (addr, normalized, DEFAULT_USER_ID),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            typer.echo(
                f"email already in use by another user: {addr}",
                err=True,
            )
            raise typer.Exit(code=2) from exc
    finally:
        conn.close()
    typer.echo(f"saved. seed user (id={DEFAULT_USER_ID}) email = {addr}")
```

- [ ] **Step 8.1.4: Re-run, confirm pass.**

```
pytest tests/test_set_email_cli.py -xvs
```

Expected: 5 PASS.

### Task 8.2 — Wizard email prompt

- [ ] **Step 8.2.1: Locate the wizard.**

```bash
grep -n "^def \|class " src/flatpilot/wizard/init.py | head -20
```

Identify the main flow function (likely `run_wizard()` or similar).

- [ ] **Step 8.2.2: Write the failing tests.**

Create `tests/test_wizard_email.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from flatpilot.wizard.init import run_wizard


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return tmp_path / "fp.db"


def test_wizard_prompts_for_email_and_writes_user_row(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the wizard prompts for email and the user provides one, both
    columns on users.id=1 are populated."""
    # The wizard has many prompts beyond email; mock all to defaults except email.
    # ASSUMES: the wizard exposes a function-level `_prompt_email()` helper that
    # returns the entered email, OR the wizard is parameterized so we can pass
    # an email via stdin. Step 8.2.3 establishes this if it doesn't exist yet.
    monkeypatch.setattr("flatpilot.wizard.init._prompt_email", lambda: "wiz@example.com")
    monkeypatch.setattr("flatpilot.wizard.init._prompt_other_fields", lambda: {})
    # If the wizard writes profile.json and other state, mock those too — only
    # the email path matters for this test.
    run_wizard()

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT email, email_normalized FROM users WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("wiz@example.com", "wiz@example.com")


def test_wizard_skip_email_leaves_seed_unbound(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty input at the email prompt leaves users.id=1 with email=NULL."""
    monkeypatch.setattr("flatpilot.wizard.init._prompt_email", lambda: "")
    monkeypatch.setattr("flatpilot.wizard.init._prompt_other_fields", lambda: {})
    run_wizard()

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT email, email_normalized FROM users WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == (None, None)
```

- [ ] **Step 8.2.3: Add the email prompt to `src/flatpilot/wizard/init.py`.**

Read the existing wizard structure first. Then:

```python
import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _prompt_email() -> str:
    """Ask for the email used for Web UI login. Empty input = skip."""
    while True:
        ans = input("Email for Web UI login (leave blank to skip): ").strip()
        if not ans:
            return ""
        if _EMAIL_RE.match(ans):
            return ans
        print("That doesn't look like a valid email. Try again or leave blank.")


def _save_seed_email(email: str) -> None:
    if not email:
        return
    from flatpilot.database import connect
    from flatpilot.users import DEFAULT_USER_ID
    conn = connect()
    try:
        conn.execute(
            "UPDATE users SET email = ?, email_normalized = ? WHERE id = ?",
            (email, email.lower(), DEFAULT_USER_ID),
        )
        conn.commit()
    finally:
        conn.close()
```

Wire `_prompt_email()` into the existing `run_wizard()` flow at the appropriate point (after `init_db` runs, before profile creation — so the seed user already exists). Call `_save_seed_email(email)` with the result.

- [ ] **Step 8.2.4: Re-run.**

```
pytest tests/test_wizard_email.py -xvs
```

Expected: 2 PASS.

### Task 8.3 — Doctor row for unbound seed user

- [ ] **Step 8.3.1: Write the failing test.**

Create `tests/test_doctor_seed_email.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flatpilot.doctor import run_doctor


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("flatpilot.config.DEFAULT_DB_PATH", tmp_path / "fp.db")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    from flatpilot.database import init_db
    init_db()
    return tmp_path / "fp.db"


def test_doctor_reports_unbound_seed_user(db: Path, capsys: pytest.CaptureFixture) -> None:
    run_doctor()
    out = capsys.readouterr().out
    assert "set-email" in out  # the upgrade hint
    assert "seed" in out.lower() or "no email" in out.lower()


def test_doctor_silent_when_seed_user_has_email(db: Path, capsys: pytest.CaptureFixture) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE users SET email = 'me@example.com', email_normalized = 'me@example.com' "
            "WHERE id = 1"
        )
        conn.commit()
    finally:
        conn.close()

    run_doctor()
    out = capsys.readouterr().out
    assert "set-email" not in out
```

- [ ] **Step 8.3.2: Run, confirm failures.**

- [ ] **Step 8.3.3: Add the check to `src/flatpilot/doctor.py`.**

Read the existing `doctor.py` shape (it has rows for the foundations PR's `users:` row and others). Append:

```python
def _check_seed_user_email(conn) -> str | None:
    """Return a hint string if seed user has no email bound; None otherwise."""
    row = conn.execute("SELECT email FROM users WHERE id = 1").fetchone()
    if row is None or row[0] is not None:
        return None
    return (
        "seed user has no email — run `flatpilot set-email <addr>` "
        "to enable Web UI login."
    )
```

Wire this into `run_doctor()` so the hint is printed when relevant. Match the existing visual style (e.g. emoji, color, bullet alignment).

- [ ] **Step 8.3.4: Re-run.**

```
pytest tests/test_doctor_seed_email.py -xvs
```

Expected: 2 PASS.

### Task 8.4 — Phase 8 verification + commit

- [ ] **Step 8.4.1: Full Phase 8 test suite + linters + regression.**

```
pytest tests/test_set_email_cli.py tests/test_wizard_email.py tests/test_doctor_seed_email.py -xvs
ruff check src/flatpilot/cli.py src/flatpilot/wizard/init.py src/flatpilot/doctor.py
mypy src/flatpilot/cli.py src/flatpilot/doctor.py
pytest -x
```

Expected: all PASS, lint clean.

- [ ] **Step 8.4.2: Commit.**

```bash
git add src/flatpilot/cli.py src/flatpilot/wizard/init.py src/flatpilot/doctor.py \
  tests/test_set_email_cli.py tests/test_wizard_email.py tests/test_doctor_seed_email.py
git commit -m "FlatPilot-ix2m/8jx: phase 8 — CLI surface for email binding

- flatpilot set-email <addr>: writes both email and email_normalized to
  users.id=1. Strips whitespace, preserves case in email column,
  lowercases for email_normalized. Validates email shape; rejects
  duplicate-normalized with exit code 2.
- Wizard init.py: new email prompt step (skippable with empty input).
- doctor: appends a row when seed user has email IS NULL pointing the
  user at the set-email command. Silent once email is bound.

CLI users now have a path to bind their existing identity to the Web UI:
run set-email once, then log in via magic link with that same address."
```

---

## Phase 9 — Next.js scaffold (Tailwind + shadcn + middleware + API client)

**Spec sections:** §3.6 (file layout), §6.1 (auth strategy), §6.6 (shared components), §10 acceptance criteria for `npm run dev`.

**Files:** all under `web/`. New directory at repo root (sibling of `src/`).

### Task 9.1 — Init the Next.js project

- [ ] **Step 9.1.1: Verify Node/npm versions are compatible (Next 15 requires Node >= 18.18).**

```bash
node --version
npm --version
```

If Node < 18.18, install via the project's preferred method (nvm, mise, asdf) before continuing.

- [ ] **Step 9.1.2: Scaffold via `create-next-app`.**

```bash
cd /Users/vividadmin/Desktop/FlatPilot
npx --yes create-next-app@latest web \
  --ts --tailwind --eslint --app --src-dir --import-alias "@/*" \
  --use-npm --no-turbopack
```

If the CLI prompts (it sometimes asks even with flags), accept defaults: App Router yes, src/ dir yes, Tailwind yes, ESLint yes, import alias `@/*`.

- [ ] **Step 9.1.3: Verify the scaffold builds and runs.**

```bash
cd web
npm install
npm run dev &
DEV_PID=$!
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000
# Expect 200
kill $DEV_PID
cd ..
```

- [ ] **Step 9.1.4: Configure `next.config.js` to proxy `/api/*` to FastAPI.**

Replace `web/next.config.js` (or `web/next.config.mjs` — match what the scaffold produced):

```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
```

- [ ] **Step 9.1.5: Smoke the proxy.**

```bash
# Terminal 1: FastAPI
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000

# Terminal 2: Next dev
cd web && npm run dev

# Terminal 3:
curl -i http://localhost:3000/api/auth/me
# Expect: 401 with {"error":"unauthorized","detail":"no_session"}
```

If the proxy returns 404 from Next, the rewrite isn't taking effect — restart `next dev`.

### Task 9.2 — shadcn/ui init

- [ ] **Step 9.2.1: Run `shadcn` init (interactive; accept the defaults aligned with our App Router + Tailwind setup).**

```bash
cd web
npx --yes shadcn@latest init
```

When prompted:
- Style: Default
- Base color: Slate (or Neutral — pick one and stick with it)
- CSS variables: Yes

- [ ] **Step 9.2.2: Add the components Bundle B needs.**

```bash
npx --yes shadcn@latest add button input label form tabs card dialog toast badge textarea select
```

This creates `web/src/components/ui/*.tsx` (Button.tsx, Input.tsx, …). They're checked-in source code, not a runtime dep.

- [ ] **Step 9.2.3: Verify the components render.**

Edit `web/src/app/page.tsx` to a smoke shape:

```tsx
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="p-8">
      <h1 className="text-3xl font-bold">FlatPilot</h1>
      <Button>OK</Button>
    </main>
  );
}
```

```bash
cd web && npm run dev
# Visit http://localhost:3000 — see the heading + a styled button.
```

Phase 10 replaces this `page.tsx` with the real tabs page.

### Task 9.3 — `.gitignore`, types, lockfile

- [ ] **Step 9.3.1: Verify `web/.gitignore` excludes `node_modules`, `.next/`, build artifacts.**

```bash
grep -E "^node_modules|^\.next" web/.gitignore
```

Should match. If not, append.

- [ ] **Step 9.3.2: Commit `web/package-lock.json`** (NOT `node_modules/`).

The next-init scaffold creates `package-lock.json` — it must be committed for reproducible installs.

### Task 9.4 — TypeScript types mirroring Pydantic schemas

- [ ] **Step 9.4.1: Create `web/src/lib/types.ts`.**

```typescript
/**
 * Mirrors of the FastAPI Pydantic response models. Keep in sync with
 * src/flatpilot/server/schemas.py — there is no codegen yet.
 */

export type ConnectionStatus = "connected" | "expired" | "disconnected" | "in_progress";

export interface ConnectionOut {
  platform: string;
  status: ConnectionStatus;
  expires_at: string | null;
}

export interface MatchOut {
  id: number;
  flat_id: number;
  title: string;
  district: string | null;
  rent_warm_eur: number | null;
  rooms: number | null;
  size_sqm: number | null;
  listing_url: string;
  decided_at: string;
  matched_saved_searches: string[];
}

export interface ApplicationOut {
  id: number;
  flat_id: number;
  platform: string;
  listing_url: string;
  title: string;
  rent_warm_eur: number | null;
  rooms: number | null;
  size_sqm: number | null;
  district: string | null;
  applied_at: string;
  method: string;
  status: string;
  response_text: string | null;
  response_received_at: string | null;
  notes: string | null;
  triggered_by_saved_search: string | null;
}

export interface MeResponse {
  user_id: number;
  email: string | null;
}

export type ConnectResult = "saved" | "abandoned" | "timed_out" | "cancelled";
```

### Task 9.5 — API client (`lib/api.ts`)

- [ ] **Step 9.5.1: Create `web/src/lib/api.ts`.**

```typescript
/**
 * Typed fetch wrapper for the FlatPilot API.
 *
 * - credentials: "include" on every request so the fp_session cookie is sent.
 * - On 401, redirect to /login (cleanest UX for stale/expired sessions).
 * - On other 4xx/5xx, throw a typed ApiError.
 */

export class ApiError extends Error {
  constructor(public status: number, public errorCode: string, public detail: string | null) {
    super(`${status} ${errorCode}${detail ? `: ${detail}` : ""}`);
    this.name = "ApiError";
  }
}

interface ApiOptions extends RequestInit {
  json?: unknown;
}

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  let body = opts.body;
  if (opts.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, { ...opts, headers, body, credentials: "include" });

  if (res.status === 401) {
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    throw new ApiError(401, "unauthorized", null);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    /* empty body */
  }

  if (!res.ok) {
    const errorCode = (payload as { error?: string } | null)?.error ?? "error";
    const detail = (payload as { detail?: string | null } | null)?.detail ?? null;
    throw new ApiError(res.status, errorCode, detail);
  }

  return payload as T;
}
```

### Task 9.6 — `middleware.ts` — cookie-presence gate for protected routes

- [ ] **Step 9.6.1: Create `web/src/middleware.ts`.**

```typescript
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = new Set(["/login", "/verify"]);

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC_PATHS.has(pathname) || pathname.startsWith("/_next") || pathname.startsWith("/api")) {
    return NextResponse.next();
  }
  const cookie = req.cookies.get("fp_session");
  if (!cookie) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: "/((?!_next/static|_next/image|favicon.ico).*)",
};
```

This is a **presence check only**, not a signature check — Next.js doesn't know the FastAPI `SESSION_SECRET`. Real auth enforcement happens server-side on every API call.

### Task 9.7 — User context + `useUser` hook

- [ ] **Step 9.7.1: Create `web/src/lib/auth.ts`.**

```typescript
"use client";

import { createContext, useContext } from "react";
import type { MeResponse } from "@/lib/types";

export const UserContext = createContext<MeResponse | null>(null);

export function useUser(): MeResponse {
  const u = useContext(UserContext);
  if (!u) {
    throw new Error("useUser called outside an authenticated layout");
  }
  return u;
}
```

### Task 9.8 — Root + authed layouts

- [ ] **Step 9.8.1: Update `web/src/app/layout.tsx`.**

```tsx
import { Toaster } from "@/components/ui/toaster";
import "./globals.css";

export const metadata = {
  title: "FlatPilot",
  description: "German rental flat-hunting agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <Toaster />
      </body>
    </html>
  );
}
```

If shadcn's Toast variant is `<Toaster />` from a different path, follow what `npx shadcn add toast` produced.

- [ ] **Step 9.8.2: Create `web/src/app/(authed)/layout.tsx`.**

```tsx
"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { UserContext } from "@/lib/auth";
import type { MeResponse } from "@/lib/types";
import TopNav from "@/components/TopNav";

export default function AuthedLayout({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<MeResponse>("/api/auth/me")
      .then(setUser)
      .catch((e: ApiError) => {
        if (e.status !== 401) setError(e.message);
        // 401 case: api.ts already redirected to /login.
      });
  }, []);

  if (error) {
    return <div className="p-8 text-red-600">Failed to load: {error}</div>;
  }
  if (!user) {
    return <div className="p-8 text-slate-500">Loading…</div>;
  }

  return (
    <UserContext.Provider value={user}>
      <TopNav />
      <main className="container mx-auto px-4 py-6">{children}</main>
    </UserContext.Provider>
  );
}
```

- [ ] **Step 9.8.3: Create `web/src/components/TopNav.tsx`.**

```tsx
"use client";

import Link from "next/link";
import { useUser } from "@/lib/auth";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

export default function TopNav() {
  const user = useUser();

  async function logout() {
    await api("/api/auth/session", { method: "DELETE" });
    window.location.assign("/login");
  }

  return (
    <header className="border-b">
      <div className="container mx-auto px-4 py-3 flex items-center justify-between">
        <Link href="/" className="text-xl font-semibold">FlatPilot</Link>
        <div className="flex items-center gap-4">
          {user.email && <span className="text-sm text-slate-600">{user.email}</span>}
          <Link href="/connections" className="text-sm hover:underline">Connections</Link>
          <Button variant="outline" size="sm" onClick={logout}>Logout</Button>
        </div>
      </div>
    </header>
  );
}
```

### Task 9.9 — Phase 9 verification + commit

- [ ] **Step 9.9.1: Type-check + lint.**

```bash
cd web
npm run lint
npx tsc --noEmit
cd ..
```

Expected: clean.

- [ ] **Step 9.9.2: Manual smoke (without real pages).**

```bash
# Terminal 1: FastAPI
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000

# Terminal 2: Next
cd web && npm run dev

# Visit http://localhost:3000 — middleware redirects to /login.
# /login is a 404 placeholder right now (Phase 10 builds it).
```

- [ ] **Step 9.9.3: Commit.**

```bash
git add web/ pyproject.toml  # (web/.gitignore excludes node_modules/.next)
git commit -m "FlatPilot-ix2m/8jx: phase 9 — Next.js scaffold (Tailwind + shadcn + middleware)

- npx create-next-app produced the App Router + TypeScript + Tailwind
  + ESLint baseline.
- next.config.js rewrites /api/* to http://localhost:8000/api/* so
  cookies share an origin from the browser's view (eliminates dev-time
  CORS gotchas).
- shadcn/ui initialized with Slate base; components added: button,
  input, label, form, tabs, card, dialog, toast, badge, textarea,
  select. Generated under web/src/components/ui/, owned-code (no
  runtime dep).
- middleware.ts: presence-only fp_session cookie check; absent →
  redirect to /login. Real auth lives in FastAPI.
- lib/api.ts: typed fetch wrapper, credentials: 'include', auto-redirect
  to /login on 401, ApiError on other failures.
- lib/types.ts: TypeScript mirrors of the Pydantic response models.
- lib/auth.ts: UserContext + useUser hook for protected pages.
- Root layout adds <Toaster>; (authed) nested layout fetches /api/auth/me
  on mount, gates rendering on user load, supplies UserContext + TopNav.

Phase 10 replaces the smoke page with /login, /verify, /, /connections."
```

---

## Phase 10 — Next.js pages (`/login`, `/verify`, `/`, `/connections`)

**Spec sections:** §6.2, §6.3, §6.4, §6.5.

**Files** (all under `web/src/`):
- `app/login/page.tsx`
- `app/verify/page.tsx`
- `app/(authed)/layout.tsx` — already exists from Phase 9
- `app/(authed)/page.tsx` — replaces `app/page.tsx` placeholder
- `app/(authed)/connections/page.tsx`
- `components/MatchCard.tsx`, `ApplicationRow.tsx`, `ResponseForm.tsx`, `ConnectionRow.tsx`, `ConnectModal.tsx`, `EmptyState.tsx`

Note: the Phase 9 layout split puts protected pages under the `(authed)` route group; this phase moves the home page accordingly. Delete the placeholder `app/page.tsx` after creating `app/(authed)/page.tsx`.

### Task 10.1 — `/login` page

- [ ] **Step 10.1.1: Create `web/src/app/login/page.tsx`.**

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";

type Phase = "form" | "sent" | "error";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [phase, setPhase] = useState<Phase>("form");
  const [submitting, setSubmitting] = useState(false);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [resendDisabled, setResendDisabled] = useState(false);

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setSubmitting(true);
    setErrorDetail(null);
    try {
      await api("/api/auth/request", { method: "POST", json: { email } });
      setPhase("sent");
      // Resend cooldown.
      setResendDisabled(true);
      setTimeout(() => setResendDisabled(false), 30_000);
    } catch (e) {
      setPhase("error");
      setErrorDetail(e instanceof ApiError ? e.errorCode : "unknown");
    } finally {
      setSubmitting(false);
    }
  }

  if (phase === "sent") {
    return (
      <main className="min-h-screen flex items-center justify-center p-8">
        <div className="max-w-md w-full space-y-4">
          <h1 className="text-2xl font-semibold">Check your email</h1>
          <p className="text-slate-600">
            We sent a sign-in link to <strong>{email}</strong>. The link expires
            in 15 minutes and can only be used once.
          </p>
          <Button
            type="button"
            variant="outline"
            disabled={resendDisabled || submitting}
            onClick={handleSubmit as any}
          >
            {resendDisabled ? "Resend (wait 30s)" : "Resend link"}
          </Button>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-8">
      <form onSubmit={handleSubmit} className="max-w-md w-full space-y-4">
        <h1 className="text-2xl font-semibold">Sign in to FlatPilot</h1>
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />
        </div>
        <Button type="submit" disabled={submitting || !email}>
          {submitting ? "Sending…" : "Send magic link"}
        </Button>
        <p className="text-sm text-slate-500">
          We&apos;ll email you a link to sign in. No password.
        </p>
        {phase === "error" && (
          <p className="text-sm text-red-600">
            Couldn&apos;t send the link ({errorDetail}). Try again.
          </p>
        )}
      </form>
    </main>
  );
}
```

- [ ] **Step 10.1.2: Manual check** — visit `http://localhost:3000/login`, confirm the form renders and the "sent" state appears after submit.

### Task 10.2 — `/verify` page

- [ ] **Step 10.2.1: Create `web/src/app/verify/page.tsx`.**

```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

export default function VerifyPage() {
  const params = useSearchParams();
  const router = useRouter();
  const [status, setStatus] = useState<"verifying" | "error">("verifying");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    const token = params.get("t");
    if (!token) {
      setStatus("error");
      setErrorMsg("Missing token in URL.");
      return;
    }
    api<{ user_id: number }>("/api/auth/verify", { method: "POST", json: { token } })
      .then(() => router.replace("/"))
      .catch((e: ApiError) => {
        setStatus("error");
        setErrorMsg(
          e.status === 400
            ? "This link has expired or already been used. Request a new one."
            : "Couldn't verify the link. Try again or request a new one."
        );
      });
  }, [params, router]);

  if (status === "verifying") {
    return <main className="p-8 text-slate-500">Signing you in…</main>;
  }
  return (
    <main className="min-h-screen flex items-center justify-center p-8">
      <div className="max-w-md w-full space-y-4">
        <h1 className="text-xl font-semibold">Sign-in failed</h1>
        <p className="text-slate-600">{errorMsg}</p>
        <Link href="/login" className="text-blue-600 hover:underline">Back to login</Link>
      </div>
    </main>
  );
}
```

- [ ] **Step 10.2.2: Manual check** — paste a valid magic-link URL from a real `/api/auth/request`'s captured email; verify redirect to `/`.

### Task 10.3 — Match / Application / Response components

- [ ] **Step 10.3.1: Create `web/src/components/EmptyState.tsx`.**

```tsx
import type { ReactNode } from "react";

export default function EmptyState({ title, body }: { title: string; body: ReactNode }) {
  return (
    <div className="border border-dashed rounded-lg p-8 text-center text-slate-500">
      <h2 className="text-lg font-medium text-slate-700">{title}</h2>
      <p className="mt-2 text-sm">{body}</p>
    </div>
  );
}
```

- [ ] **Step 10.3.2: Create `web/src/components/MatchCard.tsx`.**

```tsx
"use client";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { api, ApiError } from "@/lib/api";
import type { MatchOut } from "@/lib/types";

export default function MatchCard({
  match,
  onApplied,
  onSkipped,
}: {
  match: MatchOut;
  onApplied: (matchId: number) => void;
  onSkipped: (matchId: number) => void;
}) {
  const { toast } = useToast();

  async function apply() {
    try {
      const res = await api<{ application_id: number; status: string }>("/api/applications", {
        method: "POST",
        json: { flat_id: match.flat_id },
      });
      toast({ title: `Submitted (${res.status})` });
      onApplied(match.id);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.errorCode}: ${e.detail ?? ""}` : "Failed";
      toast({ title: "Apply failed", description: msg, variant: "destructive" });
    }
  }

  async function skip() {
    try {
      await api(`/api/matches/${match.id}/skip`, { method: "POST" });
      toast({ title: "Skipped" });
      onSkipped(match.id);
    } catch {
      toast({ title: "Skip failed", variant: "destructive" });
    }
  }

  function copyUrl() {
    navigator.clipboard.writeText(match.listing_url);
    toast({ title: "URL copied" });
  }

  return (
    <article className="border rounded-lg p-4 space-y-2">
      <h3 className="font-semibold">{match.title}</h3>
      <p className="text-sm text-slate-600">
        {match.district ?? "—"} · {match.rent_warm_eur ? `€${match.rent_warm_eur} warm` : "rent ?"}
        {match.rooms != null ? ` · ${match.rooms} rooms` : ""}
        {match.size_sqm != null ? ` · ${match.size_sqm} m²` : ""}
      </p>
      {match.matched_saved_searches.length > 0 && (
        <p className="text-xs text-slate-500">
          Matched: {match.matched_saved_searches.join(", ")}
        </p>
      )}
      <div className="flex gap-2">
        <Button size="sm" onClick={apply}>Apply</Button>
        <Button size="sm" variant="outline" onClick={skip}>Skip</Button>
        <Button size="sm" variant="ghost" onClick={copyUrl}>Copy URL</Button>
      </div>
    </article>
  );
}
```

- [ ] **Step 10.3.3: Create `web/src/components/ApplicationRow.tsx`.**

```tsx
"use client";

import { Badge } from "@/components/ui/badge";
import type { ApplicationOut } from "@/lib/types";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  submitted: "default",
  failed: "destructive",
  viewing_invited: "default",
  rejected: "destructive",
  no_response: "outline",
};

export default function ApplicationRow({ app }: { app: ApplicationOut }) {
  return (
    <article className="border-b py-3 grid grid-cols-[1fr_auto_auto_auto] gap-3 items-center">
      <div>
        <h4 className="font-medium">{app.title}</h4>
        <p className="text-xs text-slate-500">
          {app.platform} · {app.district ?? "—"} · applied {new Date(app.applied_at).toLocaleString()}
        </p>
      </div>
      <Badge variant={STATUS_VARIANT[app.status] ?? "outline"}>{app.status}</Badge>
      <span className="text-xs text-slate-500">{app.method}</span>
      <a className="text-xs text-blue-600 hover:underline" href={app.listing_url} target="_blank" rel="noopener noreferrer">view</a>
    </article>
  );
}
```

- [ ] **Step 10.3.4: Create `web/src/components/ResponseForm.tsx`.**

```tsx
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/api";
import type { ApplicationOut } from "@/lib/types";

const STATUSES = ["viewing_invited", "rejected", "no_response"] as const;

export default function ResponseForm({
  app,
  onSaved,
}: {
  app: ApplicationOut;
  onSaved: (id: number) => void;
}) {
  const { toast } = useToast();
  const [text, setText] = useState("");
  const [status, setStatus] = useState<typeof STATUSES[number]>("viewing_invited");
  const [submitting, setSubmitting] = useState(false);

  async function submit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!text.trim()) return;
    setSubmitting(true);
    try {
      await api(`/api/applications/${app.id}/response`, {
        method: "POST",
        json: { status, response_text: text },
      });
      toast({ title: "Response saved" });
      onSaved(app.id);
    } catch {
      toast({ title: "Save failed", variant: "destructive" });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="border rounded-lg p-4 space-y-3">
      <h4 className="font-medium">{app.title}</h4>
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste the landlord's reply…"
        rows={4}
      />
      <div className="flex gap-2 items-center">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof STATUSES[number])}
          className="border rounded px-2 py-1 text-sm"
        >
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <Button type="submit" size="sm" disabled={submitting || !text.trim()}>
          {submitting ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}
```

### Task 10.4 — `/` (tabs) page

- [ ] **Step 10.4.1: Delete the Phase-9 placeholder `web/src/app/page.tsx` and create `web/src/app/(authed)/page.tsx`.**

```tsx
"use client";

import { useEffect, useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import EmptyState from "@/components/EmptyState";
import MatchCard from "@/components/MatchCard";
import ApplicationRow from "@/components/ApplicationRow";
import ResponseForm from "@/components/ResponseForm";
import { api } from "@/lib/api";
import type { ApplicationOut, MatchOut } from "@/lib/types";

export default function HomePage() {
  const [matches, setMatches] = useState<MatchOut[] | null>(null);
  const [applications, setApplications] = useState<ApplicationOut[] | null>(null);

  function loadMatches() {
    api<{ matches: MatchOut[] }>("/api/matches").then((r) => setMatches(r.matches));
  }
  function loadApplications() {
    api<{ applications: ApplicationOut[] }>("/api/applications").then((r) =>
      setApplications(r.applications)
    );
  }

  useEffect(() => {
    loadMatches();
    loadApplications();
  }, []);

  const responsesPending = (applications ?? []).filter((a) => !a.response_received_at);

  return (
    <Tabs defaultValue="matches">
      <TabsList>
        <TabsTrigger value="matches">Matches</TabsTrigger>
        <TabsTrigger value="applied">Applied</TabsTrigger>
        <TabsTrigger value="responses">Responses</TabsTrigger>
      </TabsList>

      <TabsContent value="matches" className="space-y-3 mt-4">
        {matches === null ? (
          <p className="text-slate-500">Loading…</p>
        ) : matches.length === 0 ? (
          <EmptyState
            title="No matches yet"
            body="Profile and saved-search setup will appear here once available."
          />
        ) : (
          matches.map((m) => (
            <MatchCard
              key={m.id}
              match={m}
              onApplied={(id) => setMatches((curr) => curr?.filter((x) => x.id !== id) ?? null)}
              onSkipped={(id) => setMatches((curr) => curr?.filter((x) => x.id !== id) ?? null)}
            />
          ))
        )}
      </TabsContent>

      <TabsContent value="applied" className="mt-4">
        {applications === null ? (
          <p className="text-slate-500">Loading…</p>
        ) : applications.length === 0 ? (
          <EmptyState
            title="No applications yet"
            body="Applications you submit from the Matches tab will appear here."
          />
        ) : (
          applications.map((a) => <ApplicationRow key={a.id} app={a} />)
        )}
      </TabsContent>

      <TabsContent value="responses" className="space-y-3 mt-4">
        {applications === null ? (
          <p className="text-slate-500">Loading…</p>
        ) : responsesPending.length === 0 ? (
          <EmptyState
            title="No pending responses"
            body="When a landlord replies, paste their message here to record it."
          />
        ) : (
          responsesPending.map((a) => (
            <ResponseForm
              key={a.id}
              app={a}
              onSaved={() => loadApplications()}
            />
          ))
        )}
      </TabsContent>
    </Tabs>
  );
}
```

### Task 10.5 — Connections page

- [ ] **Step 10.5.1: Create `web/src/components/ConnectModal.tsx`.**

```tsx
"use client";

import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/api";
import type { ConnectResult } from "@/lib/types";

export default function ConnectModal({
  open,
  platform,
  onClose,
}: {
  open: boolean;
  platform: string | null;
  onClose: (result: ConnectResult | null) => void;
}) {
  const { toast } = useToast();
  const [submitting, setSubmitting] = useState(false);

  // Tab-close beacon: if the user closes the tab while open, ping /done so
  // the server can clean up promptly instead of waiting the engine timeout.
  useEffect(() => {
    if (!open || !platform) return;
    const handler = () => {
      navigator.sendBeacon(`/api/connections/${platform}/done`);
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [open, platform]);

  async function done(label: "Done" | "Cancel") {
    if (!platform) return;
    setSubmitting(true);
    try {
      const res = await api<{ result: ConnectResult }>(
        `/api/connections/${platform}/done`,
        { method: "POST" }
      );
      if (res.result === "saved") {
        toast({ title: `Connected to ${platform}` });
      } else if (res.result === "abandoned") {
        toast({
          title: "Login wasn't completed",
          description:
            "Make sure you see your dashboard before clicking Done. Try again.",
          variant: "destructive",
        });
      } else if (res.result === "timed_out") {
        toast({
          title: "Server didn't get an answer in time",
          description: "The browser window may still be open — finish logging in and click Done again.",
          variant: "destructive",
        });
      } else {
        toast({ title: `Connect ${res.result}`, variant: "destructive" });
      }
      onClose(res.result);
    } catch {
      toast({ title: "Connect failed", variant: "destructive" });
      onClose(null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Sign in to {platform}</DialogTitle>
        </DialogHeader>
        <p className="text-slate-600">
          We&apos;ve opened a browser window for you. Log in to {platform} in
          that window. When you see your dashboard, click <strong>Done</strong>.
        </p>
        <DialogFooter>
          <Button variant="outline" disabled={submitting} onClick={() => done("Cancel")}>
            Cancel
          </Button>
          <Button disabled={submitting} onClick={() => done("Done")}>
            {submitting ? "Working…" : "Done"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 10.5.2: Create `web/src/components/ConnectionRow.tsx`.**

```tsx
"use client";

import { Button } from "@/components/ui/button";
import type { ConnectionOut } from "@/lib/types";

const LABELS: Record<string, string> = {
  "wg-gesucht": "WG-Gesucht",
  "kleinanzeigen": "Kleinanzeigen",
  "immoscout24": "ImmoScout24",
};

export default function ConnectionRow({
  conn,
  onConnect,
  busy,
}: {
  conn: ConnectionOut;
  onConnect: (platform: string) => void;
  busy: boolean;
}) {
  const label = LABELS[conn.platform] ?? conn.platform;
  return (
    <div className="flex items-center justify-between border-b py-3">
      <div>
        <span className="font-medium">{label}</span>
        <p className="text-xs text-slate-500">
          {conn.status === "connected" && conn.expires_at &&
            `Connected · expires ${new Date(conn.expires_at).toLocaleDateString()}`}
          {conn.status === "expired" && "Cookies expired"}
          {conn.status === "disconnected" && "Not connected"}
          {conn.status === "in_progress" && "Sign-in in progress…"}
        </p>
      </div>
      <Button
        variant={conn.status === "connected" ? "outline" : "default"}
        size="sm"
        disabled={busy || conn.status === "in_progress"}
        onClick={() => onConnect(conn.platform)}
      >
        {conn.status === "connected" ? "Reconnect" : "Connect →"}
      </Button>
    </div>
  );
}
```

- [ ] **Step 10.5.3: Create `web/src/app/(authed)/connections/page.tsx`.**

```tsx
"use client";

import { useEffect, useState } from "react";
import ConnectionRow from "@/components/ConnectionRow";
import ConnectModal from "@/components/ConnectModal";
import { api } from "@/lib/api";
import type { ConnectionOut } from "@/lib/types";

export default function ConnectionsPage() {
  const [conns, setConns] = useState<ConnectionOut[] | null>(null);
  const [active, setActive] = useState<string | null>(null);

  function load() {
    api<{ connections: ConnectionOut[] }>("/api/connections").then((r) =>
      setConns(r.connections)
    );
  }
  useEffect(load, []);

  async function startConnect(platform: string) {
    try {
      await api(`/api/connections/${platform}/start`, { method: "POST" });
      setActive(platform);
    } catch {
      load();
    }
  }

  function closeModal() {
    setActive(null);
    load();
  }

  return (
    <section className="max-w-2xl">
      <h1 className="text-2xl font-semibold mb-4">Connected accounts</h1>
      {conns === null ? (
        <p className="text-slate-500">Loading…</p>
      ) : (
        conns.map((c) => (
          <ConnectionRow
            key={c.platform}
            conn={c}
            onConnect={startConnect}
            busy={active !== null}
          />
        ))
      )}
      <ConnectModal
        open={active !== null}
        platform={active}
        onClose={closeModal}
      />
    </section>
  );
}
```

### Task 10.6 — Phase 10 verification + commit

- [ ] **Step 10.6.1: Lint + type-check.**

```bash
cd web
npm run lint
npx tsc --noEmit
cd ..
```

Expected: clean.

- [ ] **Step 10.6.2: Manual smoke (full flow).**

```bash
# Terminal 1: FastAPI
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000

# Terminal 2: Next.js
cd web && npm run dev

# Browser:
# 1. http://localhost:3000  → redirects to /login (middleware).
# 2. Enter your email, click Send magic link → "Check your email" UI.
# 3. Read SMTP transport's stdout / your inbox; click the link.
# 4. /verify shows "Signing you in…" briefly, then redirects to /.
# 5. Top nav shows your email + Connections + Logout.
# 6. Matches/Applied/Responses tabs render with empty states.
# 7. /connections shows the three platforms; click Connect on wg-gesucht.
# 8. Headed Chromium opens; log in; click Done in the modal.
# 9. Toast confirms; row flips to "Connected · expires <date>".
# 10. Logout clears the cookie; refreshes redirect to /login.
```

If any step fails: identify the layer (middleware, FastAPI route, login engine) and re-read the relevant test before debugging blind.

- [ ] **Step 10.6.3: Commit.**

```bash
git add web/
# (Phase 10 only adds files; Phase 9's web/ tree already covers package-lock.json etc.)
git commit -m "FlatPilot-ix2m/8jx: phase 10 — Next.js pages (login/verify/tabs/connections)

- /login: email input form → POST /api/auth/request → 'Check your email'
  state with 30s resend cooldown.
- /verify: consumes ?t=<token> on mount via POST /api/auth/verify; success
  redirects to /; error renders 'expired or already used' fallback with a
  back-to-login link. (Verify is a SPA POST, never a GET — preserves the
  CSRF property from spec §3.5.)
- / (Tabs): Matches/Applied/Responses with shadcn Tabs, EmptyState copy
  that mentions no CLI and no time-bound 'soon' wording. Apply / Skip /
  Copy URL on each MatchCard. ResponseForm has a textarea + status
  dropdown + submit. State is per-tab fetch + setState; no SWR.
- /connections: ConnectionRow per platform; clicking Connect issues
  /start (202), opens ConnectModal with the spawned-window message.
  ConnectModal posts /done on Done or Cancel; renders the synchronous
  result (saved/abandoned/timed_out/cancelled) as a toast. beforeunload
  registers a navigator.sendBeacon('/api/connections/<p>/done') so a
  closed tab cleans up server-side.
- Top nav: wordmark + email + Connections link + Logout via DELETE
  /api/auth/session.

Phase 11 lands the Playwright e2e smoke that runs through this
entire flow with mocked SMTP + login engine."
```

---

## Phase 11 — e2e smoke test (Playwright Test)

**Spec sections:** §8.2, §10 acceptance criterion for the SMTP stub fixture.

**Files** (under `web/`):
- `playwright.config.ts`
- `tests/e2e/smoke.spec.ts`
- `tests/e2e/smtp_stub.ts`
- `tests/e2e/server_fixture.ts` — Python FastAPI lifecycle helper

The e2e spawns a real `uvicorn flatpilot.server.app:app` and a real `next dev` against a temp DB, with two server-side overrides:
- **SMTP** — env var redirects email to a stub SMTP listener that captures the magic-link URL.
- **Login engine** — env var redirects `flatpilot.sessions.login_engine.run_login_session` to a fake that immediately returns `LoginResult.SAVED` after writing a fake `state.json`. No real Chromium is spawned.

### Task 11.1 — Backend: env-driven test stubs

- [ ] **Step 11.1.1: Add an env-driven SMTP capture mode to the email transport.**

Read `src/flatpilot/notifications/email.py`. Add at the top:

```python
import os

_E2E_CAPTURE_FILE = os.environ.get("FLATPILOT_E2E_SMTP_CAPTURE")


def _maybe_capture(to: str, subject: str, body: str) -> bool:
    """In e2e mode, write the email to a JSONL file and return True (skip SMTP)."""
    if not _E2E_CAPTURE_FILE:
        return False
    import json
    with open(_E2E_CAPTURE_FILE, "a") as f:
        f.write(json.dumps({"to": to, "subject": subject, "body": body}) + "\n")
    return True


# Existing send_email function — at the start, add:
#     if _maybe_capture(to, subject, body): return
```

This is a tiny test-only escape hatch. Production never sets `FLATPILOT_E2E_SMTP_CAPTURE`.

- [ ] **Step 11.1.2: Add an env-driven engine-bypass.**

Edit `src/flatpilot/server/routes/connections.py`. At the top of `start_connect`'s `runner()` coroutine, before calling `run_login_session`:

```python
import os
import json

if os.environ.get("FLATPILOT_E2E_FAKE_ENGINE") == "1":
    # E2E mode: write a fake state.json and return SAVED.
    storage = session_storage_path(user.id, platform)
    storage.parent.mkdir(parents=True, exist_ok=True)
    expires = time.time() + 7 * 86400
    storage.write_text(json.dumps({
        "cookies": [{"name": "WGG_SESSION", "value": "e2e", "expires": expires}],
        "origins": [],
    }))
    _last_result[(user.id, platform)] = (LoginResult.SAVED, time.time())
    return LoginResult.SAVED
```

Insert this guard at the very top of `runner()` so the real engine is never invoked when `FLATPILOT_E2E_FAKE_ENGINE=1`.

- [ ] **Step 11.1.3: Run the existing connection tests to confirm the guard doesn't break non-e2e behavior.**

```
pytest tests/test_server_connections.py -xvs
```

Expected: still PASS (env var unset by default).

### Task 11.2 — Playwright config + server fixture

- [ ] **Step 11.2.1: Install `@playwright/test`.**

```bash
cd web
npm install -D @playwright/test
npx playwright install chromium
cd ..
```

- [ ] **Step 11.2.2: Create `web/playwright.config.ts`.**

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,           // single-worker uvicorn + shared temp DB
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? "list" : "html",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Servers are managed by smoke.spec.ts via beforeAll (so we can pass
  // per-test env vars and tempdirs); no `webServer` block here.
});
```

- [ ] **Step 11.2.3: Create `web/tests/e2e/server_fixture.ts`.**

```ts
import { spawn, ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

export interface E2EServers {
  fastapi: ChildProcess;
  next: ChildProcess;
  tmpDir: string;
  smtpCaptureFile: string;
}

export async function startServers(): Promise<E2EServers> {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "flatpilot-e2e-"));
  const dbPath = path.join(tmpDir, "fp.db");
  const homeDir = path.join(tmpDir, "home");
  fs.mkdirSync(homeDir, { recursive: true });
  const smtpCaptureFile = path.join(tmpDir, "smtp.jsonl");
  fs.writeFileSync(smtpCaptureFile, "");

  const env = {
    ...process.env,
    HOME: homeDir,
    FLATPILOT_DEV_AUTOGEN_SECRET: "1",
    FLATPILOT_E2E_SMTP_CAPTURE: smtpCaptureFile,
    FLATPILOT_E2E_FAKE_ENGINE: "1",
    FLATPILOT_DB_PATH: dbPath,                // honored by config.DEFAULT_DB_PATH
  };

  const fastapi = spawn(
    "uvicorn",
    ["flatpilot.server.app:app", "--port", "8000", "--log-level", "warning"],
    { env, stdio: "inherit" }
  );

  const next = spawn(
    "npm",
    ["run", "dev", "--silent"],
    { env, stdio: "inherit", cwd: path.join(__dirname, "..", "..") }
  );

  await waitForHttp("http://localhost:8000/api/auth/me", 30_000); // expects 401
  await waitForHttp("http://localhost:3000", 30_000);

  return { fastapi, next, tmpDir, smtpCaptureFile };
}

export async function stopServers(s: E2EServers): Promise<void> {
  s.fastapi.kill("SIGTERM");
  s.next.kill("SIGTERM");
  fs.rmSync(s.tmpDir, { recursive: true, force: true });
}

export function readMagicLinkUrl(captureFile: string): string {
  const lines = fs.readFileSync(captureFile, "utf8").trim().split("\n").filter(Boolean);
  if (lines.length === 0) throw new Error("no magic-link emails captured");
  const last = JSON.parse(lines[lines.length - 1]);
  const m = String(last.body).match(/(https?:\/\/[^\s]+\/verify\?t=[A-Za-z0-9_\-\.]+)/);
  if (!m) throw new Error(`couldn't find link in body: ${last.body}`);
  return m[1];
}

async function waitForHttp(url: string, timeoutMs: number): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return;
    } catch { /* retry */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${url}`);
}
```

The fixture relies on the FastAPI side honoring `FLATPILOT_DB_PATH`. Verify:

```bash
grep -n "FLATPILOT_DB_PATH\|DEFAULT_DB_PATH" src/flatpilot/config.py
```

If `config.py` doesn't read `FLATPILOT_DB_PATH`, add support:

```python
DEFAULT_DB_PATH = Path(os.environ.get("FLATPILOT_DB_PATH", str(Path.home() / ".flatpilot" / "flatpilot.db")))
```

This is a one-line config addition; covered by adding a unit test in `tests/test_config.py` if not already present.

### Task 11.3 — `smtp_stub.ts` (helper exposed for the spec)

- [ ] **Step 11.3.1: Create `web/tests/e2e/smtp_stub.ts`.**

```ts
/**
 * Read the captured magic-link URL written by the FastAPI server when
 * FLATPILOT_E2E_SMTP_CAPTURE is set. Wrapper over server_fixture's helper
 * so the spec file's import path matches what's referenced in the design
 * spec §10's acceptance criteria.
 */
export { readMagicLinkUrl } from "./server_fixture";
```

### Task 11.4 — `smoke.spec.ts`

- [ ] **Step 11.4.1: Create `web/tests/e2e/smoke.spec.ts`.**

```ts
import { test, expect } from "@playwright/test";
import { startServers, stopServers, type E2EServers } from "./server_fixture";
import { readMagicLinkUrl } from "./smtp_stub";

let servers: E2EServers;

test.beforeAll(async () => {
  servers = await startServers();
});

test.afterAll(async () => {
  await stopServers(servers);
});

test("happy path: login → verify → see app → connect → done → connected", async ({ page }) => {
  // 1. Visit /login.
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: /sign in to flatpilot/i })).toBeVisible();

  // 2. Submit email.
  await page.getByLabel("Email").fill("e2e@example.com");
  await page.getByRole("button", { name: /send magic link/i }).click();

  // 3. Confirm "Check your email" UI.
  await expect(page.getByRole("heading", { name: /check your email/i })).toBeVisible();

  // 4. Pull the magic-link URL from the captured SMTP log.
  const link = readMagicLinkUrl(servers.smtpCaptureFile);

  // 5. Visit the link.
  await page.goto(link);

  // 6. Land on / (authed) — top nav shows the email.
  await expect(page).toHaveURL(/\/$/, { timeout: 10_000 });
  await expect(page.getByText("e2e@example.com")).toBeVisible();

  // 7. Matches tab is empty.
  await expect(page.getByText(/no matches yet/i)).toBeVisible();

  // 8. Connections page.
  await page.getByRole("link", { name: /connections/i }).click();
  await expect(page.getByRole("heading", { name: /connected accounts/i })).toBeVisible();

  // 9. Click Connect on WG-Gesucht.
  await page.getByRole("button", { name: /connect →/i }).first().click();

  // 10. Modal appears.
  await expect(page.getByRole("heading", { name: /sign in to wg-gesucht/i })).toBeVisible();

  // 11. Click Done. (FAKE_ENGINE is on, so the runner returned SAVED already.)
  await page.getByRole("button", { name: "Done", exact: true }).click();

  // 12. Modal closes; the row reads Connected.
  await expect(page.getByRole("heading", { name: /sign in to wg-gesucht/i })).not.toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/connected · expires/i)).toBeVisible();

  // 13. Refresh; row still Connected.
  await page.reload();
  await expect(page.getByText(/connected · expires/i)).toBeVisible();
});
```

- [ ] **Step 11.4.2: Run.**

```bash
cd web
npx playwright test
cd ..
```

Expected: 1 PASS. If it fails, the trace is at `web/playwright-report/index.html`.

### Task 11.5 — Phase 11 verification + commit

- [ ] **Step 11.5.1: Run the full e2e against a clean checkout.**

```bash
# Make sure no other uvicorn/next is running locally first.
lsof -i :8000 -i :3000
cd web
npx playwright test --reporter=list
cd ..
```

Expected: PASS.

- [ ] **Step 11.5.2: Add an entry to `pyproject.toml`'s test optional-deps if anything new was added** (likely not — `pytest-asyncio` was added in Phase 5).

- [ ] **Step 11.5.3: Commit.**

```bash
git add web/playwright.config.ts web/tests/e2e/ web/package.json web/package-lock.json \
  src/flatpilot/notifications/email.py src/flatpilot/server/routes/connections.py \
  src/flatpilot/config.py
git commit -m "FlatPilot-ix2m/8jx: phase 11 — Playwright e2e smoke (login → connect → done)

- web/tests/e2e/smoke.spec.ts walks the happy path: /login → submit
  email → 'Check your email' → read magic-link from stub SMTP capture →
  /verify → /, with empty Matches → /connections → Connect → modal →
  Done → row reads Connected. Reload still Connected.
- web/tests/e2e/server_fixture.ts spawns uvicorn + next dev against a
  per-test tmpdir (HOME=…, FLATPILOT_DB_PATH=…/fp.db,
  FLATPILOT_E2E_SMTP_CAPTURE=…/smtp.jsonl, FLATPILOT_E2E_FAKE_ENGINE=1).
  Tears down on afterAll.
- web/tests/e2e/smtp_stub.ts re-exports readMagicLinkUrl, the helper
  the spec references in §10's acceptance criteria.
- Backend escape hatches:
  - notifications/email.py: when FLATPILOT_E2E_SMTP_CAPTURE is set,
    write the message to a JSONL file and skip real SMTP.
  - routes/connections.py: when FLATPILOT_E2E_FAKE_ENGINE=1, the
    connect runner writes a fake state.json with a future-expires
    cookie and returns SAVED immediately — no real Chromium spawn.
  - config.py: DEFAULT_DB_PATH respects FLATPILOT_DB_PATH override.
- All three escape hatches are guarded by env vars that production
  never sets.

Phase 12: docs (ADR 0002, README) and final acceptance pass."
```

---

## Phase 12 — Docs + final acceptance pass

**Spec sections:** §10 acceptance checklist (every item), §11 (rollout), §9 (public-exposure hard rule).

**Files:**
- Create: `docs/adr/0002-bundle-b-deployment-caveats.md`
- Modify: `README.md`

### Task 12.1 — ADR 0002 (deployment caveats)

- [ ] **Step 12.1.1: Create `docs/adr/0002-bundle-b-deployment-caveats.md`.**

```markdown
# 0002. Bundle B Deployment Caveats

## Status

Accepted, 2026-05-04. Supersedes nothing; complements ADR 0001.

## Context

Bundle B (FlatPilot-ix2m + FlatPilot-8jx + magic-link auth) ships a
working FastAPI + Next.js stack with magic-link login and a Connections
page that drives headed-Playwright login per platform. ADR 0001 fixed
the architectural choices for Phase 5; this ADR records the deployment
constraints specific to Bundle B that, if ignored, turn benign defaults
into real attack surface.

## Decisions

### 1. Bundle B is local-only by design.

The Connections page spawns a headed Chromium window on the FastAPI
host. That works only when the FastAPI host is the user's own machine.
A hosted VPS deployment cannot show a Chromium window to a user on a
different machine — there is no display attached.

Hosted multi-user deploy of the connect flow is filed as a follow-up
ADR; it will need a desktop helper or a cookie-upload mechanism. Bundle
B does not solve it.

### 2. Open signup with no rate limiting is NOT safe to expose to the
public internet.

Bundle B's `POST /api/auth/request` always returns 200 regardless of
email validity (no existence reveal — see ADR 0001 §3.3 magic-link
section). Combined with no rate limiting (also out of scope per ADR
0001) and no allowlist, exposing this server to the public internet
permits an attacker to:

- Trigger unbounded SMTP traffic by submitting random emails.
- Fill the `magic_link_tokens` table with junk (bounded by cleanup, but
  noisy).

Mitigation: do not expose this server to the public internet until
**FlatPilot-j1k** lands the email allowlist. The README, the FastAPI
startup log, and this ADR all carry the rule.

### 3. Single-worker uvicorn only.

The connect-flow registries (`_pending`, `_pending_tasks`,
`_last_result` in `src/flatpilot/server/routes/connections.py`) are
in-process. Multi-worker uvicorn breaks the connect flow because a
`/start` on worker A and a `/done` on worker B don't share state. The
server logs a warning if `--workers > 1`.

Multi-worker support is **FlatPilot-28o** (Redis pub/sub or DB-backed
coordination).

### 4. Stateless cookie sessions until expiry.

Sessions are signed cookies with a 30-day TTL. Logout clears the
cookie client-side, but a stolen cookie remains valid for the rest of
its TTL — there is no server-side revocation in Bundle B. Server-side
revocation is **FlatPilot-xzg**.

For local-only single-user deploys, this is acceptable. Hosted
deployments must land xzg before going live.

## Consequences

- Bundle B can ship and be useful to a single user running on their
  laptop.
- Hosting the same server publicly without `j1k` (allowlist), `xzg`
  (revocation), and a hosted-connect ADR is unsafe and explicitly
  forbidden by this ADR.
- Future Phase 5 PRs that touch the connect flow must respect the
  in-process-state assumption (or land `28o` first).

## Out of scope

Rate limiting, abuse protection, audit logging, security observability,
production CSP. Each gets its own ADR or follow-up.
```

### Task 12.2 — README updates

- [ ] **Step 12.2.1: Append a Bundle B section to `README.md`.**

```markdown
## Bundle B — Web UI (Phase 5 foundations)

Bundle B adds a FastAPI + Next.js Web UI on top of the CLI. It includes
magic-link login, three dashboard tabs (Matches / Applied / Responses)
with the same mutations the legacy localhost dashboard supports, and a
Connections page that drives headed-Playwright login per platform.

### Local dev

Two processes:

```
# Terminal 1: FastAPI
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000

# Terminal 2: Next.js
cd web
npm install
npm run dev
```

Open `http://localhost:3000`. Sign in with your email; the magic-link
arrives via the existing SMTP transport (configured in `profile.json`).

CLI users who want to see their existing matches in the Web UI: bind
your email once with

```
flatpilot set-email you@example.com
```

Subsequent magic-link logins with that address resolve to the seed
user (id=1) and surface your CLI data. Other email addresses get
fresh accounts.

### Public-internet exposure: HARD RULE

**Do NOT expose the Bundle B server to the public internet.** Open
signup with no rate limiting + no existence reveal makes the unprotected
server an SMTP-abuse vector (see `docs/adr/0002-bundle-b-deployment-
caveats.md`). The Connections page also assumes the FastAPI host is the
user's own machine — hosted multi-user deploy is not solved by this PR.

The follow-up beads to land before any public deploy are:
- **FlatPilot-j1k** — email allowlist on signup
- **FlatPilot-xzg** — server-side session revocation
- (separate ADR) — hosted-multi-user connect mechanism

### Tests

```
pytest                                 # full Python suite
cd web && npx playwright test          # e2e smoke
cd web && npm run lint && npx tsc --noEmit   # frontend lint/types
```

### Architecture references

- `docs/adr/0001-web-ui-architecture.md` — north star (FastAPI + Next.js + magic-link + docker-compose)
- `docs/adr/0002-bundle-b-deployment-caveats.md` — public-exposure rule
- `docs/superpowers/specs/2026-05-03-web-ui-foundations-design.md` — per-user data-model design
- `docs/superpowers/specs/2026-05-04-bundle-b-web-ui-design.md` — Bundle B design
```

If the existing `README.md` has a different structural style (e.g. headings, tone, or sections like "Quickstart"), match it. The content above is the floor.

### Task 12.3 — FastAPI startup warning for the public-exposure rule

- [ ] **Step 12.3.1: Write the failing test.**

Append to `tests/test_server_app.py`:

```python
def test_startup_logs_public_exposure_warning(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLATPILOT_DEV_AUTOGEN_SECRET", "1")
    monkeypatch.delenv("FLATPILOT_SESSION_SECRET", raising=False)
    from flatpilot.server.settings import get_settings
    get_settings.cache_clear()

    from flatpilot.server.app import create_app
    with caplog.at_level("WARNING", logger="flatpilot.server"):
        create_app()
    assert any(
        "j1k" in rec.message or "allowlist" in rec.message.lower() or "public" in rec.message.lower()
        for rec in caplog.records
    )
```

- [ ] **Step 12.3.2: Add the warning log to `create_app()`.**

In `src/flatpilot/server/app.py`, inside `create_app()` after the FastAPI instance is built:

```python
logger.warning(
    "FlatPilot Bundle B: do NOT expose this server to the public internet "
    "without first landing FlatPilot-j1k (email allowlist) and "
    "FlatPilot-xzg (session revocation). See docs/adr/0002 for the rule."
)
```

- [ ] **Step 12.3.3: Re-run, confirm pass.**

```
pytest tests/test_server_app.py -k "startup_logs_public" -xvs
```

Expected: PASS.

### Task 12.4 — Final acceptance pass

This task is a checklist that walks every acceptance criterion from spec §10. Execute each item and confirm before commit.

- [ ] **Step 12.4.1: Backend tests + linters + types.**

```bash
pytest -x
ruff check src/ tests/
mypy src/flatpilot/
```

Expected: all PASS, lint + types clean.

- [ ] **Step 12.4.2: Frontend tests + linters + types.**

```bash
cd web
npm run lint
npx tsc --noEmit
npx playwright test
cd ..
```

Expected: lint clean, types clean, e2e PASS.

- [ ] **Step 12.4.3: Manual end-to-end smoke against real SMTP and a real platform login.**

```bash
# Terminal 1: FastAPI
FLATPILOT_DEV_AUTOGEN_SECRET=1 uvicorn flatpilot.server.app:app --port 8000

# Terminal 2: Next.js
cd web && npm run dev

# Browser:
# 1. Visit http://localhost:3000 → redirects to /login.
# 2. Submit your real email → check inbox → click link.
# 3. Land on /, see top nav with email.
# 4. Visit /connections → click Connect on wg-gesucht.
# 5. Headed Chromium opens → log in by hand → click Done in modal.
# 6. Toast: 'Connected'; row updates with expires date.
# 7. From a separate terminal, run `flatpilot scrape wg-gesucht --dry-run`
#    → confirm cookies still work (legacy path is preserved for user 1).
# 8. Sign out via top-nav → /login redirect.
```

If any step fails: identify the layer; do not paper over the failure with code changes that aren't covered by a test.

- [ ] **Step 12.4.4: Walk the spec's §10 acceptance checklist** (`docs/superpowers/specs/2026-05-04-bundle-b-web-ui-design.md`). Confirm each box.

Specific items worth re-confirming:
- [ ] `GET /api/auth/verify` returns **405** (test_verify_endpoint_rejects_get).
- [ ] `test_no_get_endpoints_mutate_state` passes — every GET in `READONLY_GET_HANDLERS`.
- [ ] `test_no_unscoped_sql_in_routes_module` passes — sqlglot AST audit.
- [ ] `flatpilot login wg-gesucht` still works exactly as before (cookies at `~/.flatpilot/sessions/wg-gesucht/state.json`).
- [ ] `flatpilot dashboard` still starts the legacy `view.py + server.py` stack on port 9999 (Bundle B did not touch it).
- [ ] `flatpilot doctor` reports the seed-user-no-email hint when applicable.
- [ ] `flatpilot set-email <addr>` writes both columns idempotently and rejects duplicates.
- [ ] On a foundations-only DB, `init_db` upgrades to add `email_normalized` and create `magic_link_tokens` without data loss.

### Task 12.5 — Phase 12 commit

- [ ] **Step 12.5.1: Commit.**

```bash
git add docs/adr/0002-bundle-b-deployment-caveats.md README.md \
  src/flatpilot/server/app.py tests/test_server_app.py
git commit -m "FlatPilot-ix2m/8jx: phase 12 — ADR 0002 + README + startup warning

- docs/adr/0002-bundle-b-deployment-caveats.md formalizes the four
  Bundle B deployment constraints: local-only by design, no public
  exposure without j1k allowlist, single-worker uvicorn only,
  stateless sessions until xzg lands.
- README adds a Bundle B section: dev-mode 'how to run', flatpilot
  set-email instructions, the explicit public-exposure HARD RULE
  with pointers to j1k / xzg / hosted-connect followups.
- create_app() logs a startup WARNING repeating the hard rule. CI
  gate via test_startup_logs_public_exposure_warning makes this
  invariant load-bearing — removing the warning silently fails CI.

Bundle B branch is now ready for review. Do NOT git push without
explicit user approval."
```

### Task 12.6 — Hand off to the user (do NOT push)

- [ ] **Step 12.6.1: Verify the branch is clean and locally complete.**

```bash
git status                          # working tree clean
git log --oneline origin/main..HEAD # 12 commits, one per phase
git diff origin/main..HEAD --stat   # ~3.5–5k LOC across the surfaces in §2.3
```

- [ ] **Step 12.6.2: STOP. Report to the user.**

Tell the user:
> Bundle B is implemented locally on `feat/bundle-b-web-ui`. 12 phase commits (ix2m/8jx). Acceptance pass complete: all backend pytest passes, e2e smoke passes, lint + types clean, manual smoke walked. The branch is held local — the project's git rules require explicit approval before push. Reply "push" to run `git push -u origin feat/bundle-b-web-ui` and `gh pr create --base main --head feat/bundle-b-web-ui --fill`.

Do NOT run `git push` until the user explicitly approves.

---

## Plan footer — overall verification

After all 12 phase commits are in place, before pushing:

- [ ] Run the full backend + frontend + e2e suite one more time end-to-end.
- [ ] Confirm `git log --oneline origin/main..HEAD | wc -l` is exactly 12 (one commit per phase) — if it's not, the per-phase commit policy was broken.
- [ ] Confirm no commit message contains `Co-Authored-By: Claude` or any AI trailer (project rule, CLAUDE.md).
- [ ] Confirm commit author is `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>` on every commit:
  ```
  git log --format='%an <%ae>' origin/main..HEAD | sort -u
  # Should print exactly one line.
  ```
- [ ] Hold the branch local until the user replies "push" or equivalent.

If any of those checks fail, fix before pushing.












