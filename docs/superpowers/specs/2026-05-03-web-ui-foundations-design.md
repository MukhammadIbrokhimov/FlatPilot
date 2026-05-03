# Web UI Foundations Design

**Beads:** FlatPilot-x0pq (O1. Architecture decision record), FlatPilot-z3me (O2. Per-user isolation in data model)

**Date:** 2026-05-03

**Status:** Design — implementation pending

---

## 1. Motivation

FlatPilot is a single-user CLI today. Phase 5 will deliver a hosted multi-user Web UI (FastAPI + Next.js, magic-link auth). Before that work starts, two foundations need to land together:

1. **Architecture decision record** — a north star that future Phase 5 PRs converge on, so each one doesn't re-litigate auth model, DB choice, or deployment topology.
2. **Per-user data model** — every per-user SQL table (`matches`, `applications`, `apply_locks`) gains a `user_id` column, plus a `users` table is introduced. The CLI continues to operate as the single seed user (`id = 1`) and its UX is unchanged.

These ship in one PR because the ADR's decisions directly drive the schema shape (e.g. flats are global, decisions are per-user). Splitting would mean re-loading the same architectural context twice.

## 2. Scope

**In scope:**

- `docs/adr/0001-web-ui-architecture.md` capturing the forward-looking decisions for Phase 5.
- `users` table with one seed row (`id = 1`).
- `user_id` column on `matches` and `applications` (added via the existing `COLUMNS` ALTER-TABLE migration).
- `apply_locks` table rebuild to change the primary key from `flat_id` to `(flat_id, user_id)`.
- `DEFAULT_USER_ID = 1` constant exported from a new `flatpilot.users` module.
- All existing SQL queries updated to scope by `user_id`.
- Composite indexes on `(user_id, decided_at)` and `(user_id, applied_at)`.
- New tests covering seed-user creation, backfill, cross-user isolation, lock isolation per user, idempotent rebuild.
- One `doctor.py` row reporting the live user count.

**Out of scope (recorded in the ADR as Phase 5 follow-ups):**

- FastAPI server, magic-link auth, signed-cookie sessions.
- Next.js frontend, the connections page (FlatPilot-8jx), the React tabs (FlatPilot-ix2m).
- Postgres adoption and SQLAlchemy/Alembic migration.
- `docker-compose.yml` for the hosted multi-container deploy (FlatPilot-u5gh).
- Per-user filesystem namespacing (`~/.flatpilot/users/<id>/{profile.json, sessions/, templates/, attachments/}`).
- Profile-file → DB conversion. Saved searches stay in `profile.json`.
- Flats partitioning. `flats` remains global; one row per real listing.
- Modifying `cli.py` entry points. Every CLI command continues to operate as the seed user with no flag plumbing.

## 3. Architecture decision record

`docs/adr/0001-web-ui-architecture.md`. ~1 page. Sections:

### 3.1 Status

Accepted, 2026-05-03.

### 3.2 Context

FlatPilot is a single-user CLI for the German rental market. It scrapes listings, matches them against a profile, and notifies the user. State lives under `~/.flatpilot/` and a SQLite DB at `~/.flatpilot/flatpilot.db`.

A hosted multi-user Web UI is on the roadmap (epic FlatPilot-0wfb). Without an architectural north star, individual Phase 5 PRs will diverge — one PR picks an auth library, the next a DB driver, the third a frontend framework, and they never align. This ADR fixes the major choices upfront.

### 3.3 Decisions

- **Backend: FastAPI.** Async, integrates with the existing `flatpilot` package as a Python module, plays well with Pydantic models already used in `profile.py` and `schemas.py`.
- **Frontend: Next.js (App Router, TypeScript).** SSR for the dashboard, file-system routing, deployed as its own container.
- **Auth: email magic-link.** 15-minute single-use tokens, signed-cookie sessions via `itsdangerous`. No passwords. Reuses the existing SMTP transport from `notifications/email.py` to send link emails.
- **Database: SQLite now → Postgres at hosted launch.** The Phase 5 server PR adopts SQLAlchemy + Alembic; pre-launch migration is a one-shot `pgloader` run. SQLAlchemy adoption is deliberately deferred — bundling it with this PR would mix a schema migration and an ORM rewrite in the same diff.
- **Deployment: docker-compose.** Five services — `web` (FastAPI), `worker` (scrapers + matcher + notifier on cron), `frontend` (Next.js), `postgres`, `caddy` (TLS reverse proxy). Worker is deliberately separate from web so a long Playwright run can't block API requests.
- **Per-user filesystem namespace (Phase 5):** `~/.flatpilot/users/<id>/{profile.json, sessions/, templates/, attachments/}`. The geocode cache (`~/.flatpilot/geocode_cache.json`) stays shared across users since geocoding addresses is platform-shared work.

### 3.4 Consequences

- Future PRs slot cleanly into the named decisions; no further north-star debate needed for the listed concerns.
- The CLI keeps working unchanged today and after the Postgres cutover (host-only commands like `flatpilot login` will still target the user-1 filesystem namespace once Phase 5 lands).
- Cost: Phase 5 PRs commit to FastAPI/Next.js/Postgres/Caddy. Switching later would invalidate the ADR; that's the explicit price of choosing a direction.

### 3.5 Alternatives considered and rejected

- **Plain `http.server` + vanilla React:** rejected; the existing `server.py` already calls out FastAPI as its replacement and Next.js gives us routing/SSR for free.
- **Password auth:** rejected as higher attack surface (password resets, hashing, rotation) for a hobbyist deployment.
- **SQLAlchemy adoption in this PR:** rejected for diff-size reasons; deferred to the FastAPI PR where every query is rewritten anyway.
- **Per-user `flats` table:** rejected; one ad on a real platform is one ad. Scrapers stay user-unaware, scrape work and the geocode cache are shared, freshness comes from scrape frequency not table partitioning.

### 3.6 Out of scope for this ADR

Endpoint shapes, frontend component structure, ops/observability, rate limiting, abuse protection. Each gets its own ADR (0002, 0003, ...) when the work starts.

## 4. Schema

### 4.1 New `users` table

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    created_at TEXT NOT NULL
);
```

`email` is nullable because the seed CLI user has no email address to bind. Phase 5 magic-link signup populates it. The `UNIQUE` constraint still applies to non-NULL values (SQLite treats `NULL`s as distinct in unique indexes), so multiple no-email users could coexist in principle — fine, since only the seed user is no-email and there is only one of it.

### 4.2 Seed user

`init_db()` runs:

```sql
INSERT OR IGNORE INTO users (id, email, created_at)
VALUES (1, NULL, ?)
```

with the current UTC timestamp. Idempotent on every startup.

### 4.3 `user_id` columns

Added via the existing `COLUMNS` ALTER-TABLE migration mechanism in `database.py`:

| Table | New column |
|---|---|
| `matches` | `user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)` |
| `applications` | `user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)` |

The `DEFAULT 1` makes SQLite backfill every existing row to the seed user during the ALTER TABLE.

### 4.4 `apply_locks` rebuild

`ALTER TABLE` cannot change a primary key in SQLite; the table must be recreated. The migration helper runs the following sequence inside one transaction, idempotent on already-rebuilt schemas:

```sql
CREATE TABLE apply_locks_new (
    flat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    acquired_at TEXT NOT NULL,
    pid INTEGER NOT NULL,
    PRIMARY KEY (flat_id, user_id)
);
INSERT INTO apply_locks_new (flat_id, user_id, acquired_at, pid)
    SELECT flat_id, 1, acquired_at, pid FROM apply_locks;
DROP TABLE apply_locks;
ALTER TABLE apply_locks_new RENAME TO apply_locks;
```

Idempotency check: if `PRAGMA table_info(apply_locks)` already lists a `user_id` column, the helper is a no-op.

### 4.5 `flats` and saved searches — unchanged

Per the ADR, `flats` stays global (no `user_id`). Saved searches remain in `profile.json`. Both can be migrated in later PRs without re-touching this PR's work.

### 4.6 New indexes

```sql
CREATE INDEX IF NOT EXISTS idx_matches_user_decided
    ON matches(user_id, decided_at);
CREATE INDEX IF NOT EXISTS idx_applications_user_applied
    ON applications(user_id, applied_at);
```

These cover the dashboard and auto-apply cap-counting query patterns, which all filter by user and order/range over a timestamp.

## 5. Code touch points

### 5.1 New module `src/flatpilot/users.py`

```python
DEFAULT_USER_ID = 1

def ensure_default_user(conn) -> None:
    """Insert the seed user (id=1) if absent. Idempotent."""
```

### 5.2 `database.py`

`init_db()` ordering:

1. `CREATE TABLE` for every entry in `SCHEMAS` (this picks up the new `users` table).
2. `ensure_default_user(conn)` — must run before the FK-using ALTER TABLEs.
3. `ensure_columns()` — adds `user_id` columns to `matches` / `applications`.
4. `_rebuild_apply_locks_for_user_scope(conn)` — the table-rebuild migration.

### 5.3 `schemas.py`

- Register `users` table in `SCHEMAS`.
- Add `matches.user_id` and `applications.user_id` to `COLUMNS`.
- Register the two new indexes in `SCHEMAS`.
- The `apply_locks` rebuild lives in `database.py`, not `schemas.py`, because it's a one-shot helper rather than a declarative schema entry.

### 5.4 Query updates

Every `INSERT` into `matches` / `applications` / `apply_locks` adds `user_id`. Every `SELECT` / `UPDATE` / `DELETE` that operates on user-scoped data filters by `user_id = ?`, parameter-bound, never string-formatted.

| File | Lines/queries to update |
|---|---|
| `applications.py` | every INSERT / SELECT / UPDATE on `applications` |
| `apply.py` | lock acquire INSERT, stale-lock reaper SELECT/DELETE |
| `matcher/runner.py` | match-row INSERT and existing-match dedup SELECT |
| `view.py` | dashboard SELECTs against `matches` / `applications` |
| `server.py` | localhost dashboard SELECTs / UPDATEs |
| `auto_apply.py` | per-platform cap-counting SELECT |
| `pipeline.py` | passes `DEFAULT_USER_ID` to callees that need it |

`cli.py` does not change. Every entry point implicitly operates on the seed user via `DEFAULT_USER_ID`.

### 5.5 `doctor.py`

One new check appended to the existing rows:

```
users:                1 row
```

Counts `users` rows. Reports `error: 0 users — run flatpilot init` if zero (impossible after `init_db`, but worth a guard).

## 6. Test strategy

New file `tests/test_user_scoping.py`. Existing tests stay untouched and must pass — they all run as the seed user, transparent to new code.

| Test | Verifies |
|---|---|
| `test_seed_user_exists_after_init_db` | `users` has row `id=1` after first `init_db`; second `init_db` doesn't duplicate |
| `test_backfill_existing_rows` | Insert legacy rows under the pre-migration schema, run `init_db`, assert every row's `user_id` is `1` |
| `test_cross_user_match_isolation` | Insert match for `user_id=2` and one for `user_id=1`; query under `DEFAULT_USER_ID=1` returns only the user-1 row |
| `test_apply_lock_per_user` | User 1 locks flat 99; user 2 also locks flat 99 (allowed by composite PK); user 1 cannot double-lock flat 99 |
| `test_rebuild_apply_locks_idempotent` | Run `init_db` twice; second run no-ops, table contents preserved |
| `test_users_table_unique_email_when_set` | Two no-email users coexist; two users with same non-NULL email rejected |

Coverage target matches the rest of the codebase (≥95% on changed modules).

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Backfill migration corrupts a live single-user DB | `apply_locks` rebuild runs in one transaction; idempotency check before rebuild; covered by `test_rebuild_apply_locks_idempotent` |
| Forgotten query somewhere reads or writes unscoped data | grep audit of every `conn.execute` callsite touching user-scoped tables; reviewer pass; cross-user isolation tests would catch it for `matches` |
| FK on `user_id` fails because the seed user is created after the ALTER TABLE | `init_db` ordering enforced: `users` CREATE → seed INSERT → `ensure_columns` → apply-locks rebuild. Backfill test covers it. |
| Existing single-user installs upgrade in place and lose data | The ALTER TABLE `DEFAULT 1` preserves all rows; the apply-locks rebuild copies all rows. Tested by `test_backfill_existing_rows` |

## 8. Migration order (one-shot, on next `init_db`)

1. `CREATE TABLE users IF NOT EXISTS`.
2. `INSERT OR IGNORE` seed user `(id=1, email=NULL, created_at=now)`.
3. `ALTER TABLE matches ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)`.
4. `ALTER TABLE applications ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id)`.
5. If `apply_locks` lacks a `user_id` column: rebuild via the SQL block in §4.4.
6. `CREATE INDEX IF NOT EXISTS` the two new composite indexes.

After this completes, every existing row is owned by user 1 and the schema is ready for Phase 5 to layer real users on top.

## 9. Acceptance criteria

- [ ] `docs/adr/0001-web-ui-architecture.md` lands with the §3 content.
- [ ] `init_db` on a fresh DB produces the new schema with seed user.
- [ ] `init_db` on a pre-migration DB (existing single-user data) backfills every row to `user_id = 1` without data loss.
- [ ] `flatpilot run` / `flatpilot dashboard` / `flatpilot apply` / `flatpilot doctor` all work unchanged from the user's perspective.
- [ ] `tests/test_user_scoping.py` passes; existing test suite passes; `ruff check` and `mypy` clean.
- [ ] No call site of `conn.execute` against `matches` / `applications` / `apply_locks` is missing a `user_id` clause.
