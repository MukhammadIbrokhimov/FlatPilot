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
- `user_id` column added to `matches`, `applications`, and `apply_locks` via a one-shot table-rebuild migration (SQLite cannot ALTER-add a `REFERENCES` column with a non-NULL default; see §4.3 for the full rationale).
- `matches.UNIQUE` constraint widened from `(flat_id, profile_version_hash, decision)` to `(user_id, flat_id, profile_version_hash, decision)` as part of the same rebuild.
- `apply_locks` PRIMARY KEY widened from `flat_id` to `(flat_id, user_id)` as part of the same rebuild.
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
- **Auth: email magic-link.** 15-minute single-use tokens, signed-cookie sessions via `itsdangerous`. No passwords. Reuses the existing SMTP transport from `notifications/email.py` to send link emails initially. If hosted launch shows that SMTP through a hobbyist relay can't deliver the link within ~5–10 s end-to-end (the latency budget magic-link UX requires), the auth PR swaps in a transactional API like Postmark or Resend without changing the rest of the auth design.
- **Database: SQLite now → Postgres at hosted launch.** The Phase 5 server PR adopts SQLAlchemy + Alembic; pre-launch migration is a one-shot `pgloader` run. SQLAlchemy adoption is deliberately deferred — bundling it with this PR would mix a schema migration and an ORM rewrite in the same diff.
- **Deployment: docker-compose**, five services:
    - `web` — FastAPI process serving the API.
    - `worker` — scrapers, matcher, and notifier on a cron loop. Deliberately separate from `web` so a long Playwright run can't block API requests.
    - `frontend` — Next.js.
    - `postgres` — primary store post-cutover.
    - `caddy` — TLS reverse proxy in front of `web` and `frontend`.
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

### 4.3 Why every user-scoped table needs a rebuild, not an ALTER

The natural plan would be `ALTER TABLE matches ADD COLUMN user_id ... REFERENCES users(id) NOT NULL DEFAULT 1`, but SQLite forbids this: `ALTER TABLE ADD COLUMN` rejects any `REFERENCES` column with a non-NULL default (hard rule, not version-dependent). On top of that, `matches` carries `UNIQUE (flat_id, profile_version_hash, decision)` which must widen to include `user_id` — otherwise two users sharing a `profile_version_hash` (the default-profile case after `flatpilot init`) would silently lose match rows to `INSERT OR IGNORE` in `matcher/runner.py`. SQLite cannot ALTER a UNIQUE constraint either.

Both forces resolve the same way: **rebuild `matches`, `applications`, and `apply_locks` via the standard CREATE-COPY-DROP-RENAME pattern.** The rebuild is the one place SQLite *does* allow a `NOT NULL DEFAULT 1 REFERENCES users(id)` column, and it's also the only way to widen a UNIQUE/PRIMARY-KEY constraint. The cost is one transaction's worth of I/O on each user-scoped table, paid once per install on the first post-upgrade `init_db`.

### 4.4 Rebuild SQL for each user-scoped table

All three rebuilds live in a single helper `_rebuild_user_scoped_tables(conn)` in `database.py`, executed inside one `BEGIN IMMEDIATE … COMMIT` transaction. Each rebuild starts with a `DROP TABLE IF EXISTS <name>_new` to recover from a half-finished previous attempt (crash between `DROP` and `RENAME` outside the transaction). The helper is gated on a per-table column probe: if `PRAGMA table_info(<name>)` already lists a `user_id` column, that table's rebuild block is skipped.

**`matches`** — adds `user_id`, widens UNIQUE to include it:

```sql
DROP TABLE IF EXISTS matches_new;
CREATE TABLE matches_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    profile_version_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('match', 'reject', 'skipped')),
    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
    decided_at TEXT NOT NULL,
    notified_at TEXT,
    notified_channels_json TEXT,
    matched_saved_searches_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (user_id, flat_id, profile_version_hash, decision)
);
INSERT INTO matches_new (
    id, user_id, flat_id, profile_version_hash, decision, decision_reasons_json,
    decided_at, notified_at, notified_channels_json, matched_saved_searches_json
)
SELECT
    id, 1, flat_id, profile_version_hash, decision, decision_reasons_json,
    decided_at, notified_at, notified_channels_json, matched_saved_searches_json
FROM matches;
DROP TABLE matches;
ALTER TABLE matches_new RENAME TO matches;
```

**`applications`** — adds `user_id`, no UNIQUE constraint to widen:

```sql
DROP TABLE IF EXISTS applications_new;
CREATE TABLE applications_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    listing_url TEXT NOT NULL,
    title TEXT NOT NULL,
    rent_warm_eur REAL,
    rooms REAL,
    size_sqm REAL,
    district TEXT,
    applied_at TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('manual', 'auto')),
    message_sent TEXT,
    attachments_sent_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('submitted', 'failed', 'viewing_invited', 'rejected', 'no_response')
    ),
    response_received_at TEXT,
    response_text TEXT,
    notes TEXT,
    triggered_by_saved_search TEXT
);
INSERT INTO applications_new (
    id, user_id, flat_id, platform, listing_url, title, rent_warm_eur, rooms,
    size_sqm, district, applied_at, method, message_sent, attachments_sent_json,
    status, response_received_at, response_text, notes, triggered_by_saved_search
)
SELECT
    id, 1, flat_id, platform, listing_url, title, rent_warm_eur, rooms,
    size_sqm, district, applied_at, method, message_sent, attachments_sent_json,
    status, response_received_at, response_text, notes, triggered_by_saved_search
FROM applications;
DROP TABLE applications;
ALTER TABLE applications_new RENAME TO applications;
```

**`apply_locks`** — adds `user_id`, widens PRIMARY KEY:

```sql
DROP TABLE IF EXISTS apply_locks_new;
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

The denormalised columns and CHECK constraints are preserved verbatim from `schemas.py`. The post-rebuild `SCHEMAS` registrations in `schemas.py` must match exactly so `init_db()` on a fresh DB produces the same shape.

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

1. `CREATE TABLE` for every entry in `SCHEMAS`. This picks up the new `users` table and registers the post-rebuild shapes of `matches` / `applications` / `apply_locks` for fresh installs (which never enter the rebuild path because the `user_id` column is already present).
2. `ensure_default_user(conn)` — inserts seed user `(id=1, email=NULL, created_at=now)`. Must run before any rebuild that copies into a table with `REFERENCES users(id)`, otherwise the per-row FK check fails on the `INSERT INTO ..._new SELECT 1, ...` step.
3. `_rebuild_user_scoped_tables(conn)` — the per-table rebuild helper described in §4.4. Wraps the three rebuilds in one `BEGIN IMMEDIATE` transaction; each rebuild block is gated on a `PRAGMA table_info` probe so already-migrated tables are skipped.
4. `ensure_columns()` — runs after the rebuilds so any future ALTER-able columns layer on top of the new shape.
5. The two new composite indexes from §4.6 are registered in `SCHEMAS` and created by step 1.

### 5.3 `schemas.py`

- Register `users` table in `SCHEMAS`.
- Update the `matches`, `applications`, and `apply_locks` `CREATE TABLE` strings in `SCHEMAS` to the **post-rebuild** shapes (with `user_id` and the widened UNIQUE / PRIMARY KEY). Fresh installs go straight to the new shape; existing installs hit the rebuild path in `database.py`.
- Register the two new indexes (`idx_matches_user_decided`, `idx_applications_user_applied`) in `SCHEMAS`.
- Drop the obsolete `COLUMNS["matches"] = {"matched_saved_searches_json": ...}` and `COLUMNS["applications"] = {"triggered_by_saved_search": ...}` entries — those columns are now declared inline in the rebuilt `CREATE TABLE` strings, so the ALTER-TABLE forward migration is no longer needed for them.
- The rebuild helper itself lives in `database.py`, not `schemas.py`, because it's a one-shot procedural migration rather than a declarative schema entry.

### 5.4 Query updates

Every `INSERT` into `matches` / `applications` / `apply_locks` adds `user_id`. Every `SELECT` / `UPDATE` / `DELETE` that operates on user-scoped data filters by `user_id = ?`, parameter-bound, never string-formatted. The check that nothing was missed is mechanical: `grep -rn "FROM matches\|UPDATE matches\|INTO matches\|FROM applications\|UPDATE applications\|INTO applications\|FROM apply_locks\|UPDATE apply_locks\|INTO apply_locks\|DELETE FROM apply_locks" src/flatpilot/` and confirm every hit either filters/inserts a `user_id` or is in code that legitimately operates across users (none exist in this PR's scope).

| File | Specific queries to update |
|---|---|
| `applications.py` | line 27 (`SELECT flat_id FROM matches WHERE id = ?` — gate by user_id so a guessed match id can't side-step scoping); line 62 (`SELECT id FROM applications WHERE id = ?` — same); every `INSERT` into `applications` |
| `apply.py` | line 159 (stale-lock reaper `DELETE FROM apply_locks WHERE flat_id = ? AND acquired_at < ?` — must scope by `user_id` so user 1's reap doesn't touch user 2's lock); line 169 (`SELECT pid, acquired_at FROM apply_locks WHERE flat_id = ?`); line 192 (lock release `DELETE FROM apply_locks WHERE flat_id = ?`); line 263 (already-applied guard `SELECT id FROM applications WHERE flat_id = ? AND status = 'submitted'` — must scope, otherwise user 1 sees user 2's submission and refuses, defeating per-user lock isolation); the lock-acquire INSERT |
| `matcher/runner.py` | match-row INSERT (must include `user_id`) and the existing-match dedup SELECT |
| `view.py` | every dashboard SELECT against `matches` / `applications` |
| `server.py` | localhost dashboard SELECTs / UPDATEs |
| `auto_apply.py` | per-platform cap-counting SELECT |
| `stats.py` | lines 38, 42, 49, 60 — counter SELECTs against `matches` (otherwise `flatpilot status` aggregates across users) |
| `notifications/dispatcher.py` | line 263 (`UPDATE matches` in `_mark_stale_matches_notified`); line 307 (`SELECT … FROM matches m JOIN flats` for pending notifications); line 366 (`UPDATE matches SET notified_channels_json = ?, notified_at = ? WHERE id = ?` — needs to gate by user_id even though the match id is unique, to defend against id leakage in tests/bugs) |
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
| `test_backfill_existing_rows` | Pre-populate `matches` / `applications` / `apply_locks` under the pre-migration schema (no `user_id`), run `init_db`, assert every row's `user_id` is `1` and row counts match |
| `test_cross_user_match_isolation_in_view` | Insert matches for both `user_id=1` and `user_id=2`; assert the dashboard `view.py` query returns only the user-1 row |
| `test_cross_user_isolation_in_dispatcher` | Insert pending matches for `user_id=1` and `user_id=2`; run the notification dispatcher under `DEFAULT_USER_ID=1`; assert the user-2 match's `notified_at` is still NULL afterwards (i.e. the dispatcher didn't fire on or stamp another user's row) |
| `test_cross_user_isolation_in_stats` | Insert decided matches under both users; assert `stats.get_stats()` (or equivalent) counts only the user-1 rows |
| `test_already_applied_guard_per_user` | Insert a `status='submitted'` `applications` row for `user_id=2` on flat 99; assert user 1 can still acquire `apply.py`'s already-applied gate against flat 99 (i.e. the guard does not see user 2's row) |
| `test_apply_lock_per_user` | User 1 acquires lock on flat 99; user 2 independently acquires a lock on flat 99 (allowed by the `(flat_id, user_id)` composite PK); user 1 cannot double-lock flat 99 |
| `test_stale_lock_reaper_isolation` | User 2 holds a stale lock on flat 99 (older than `apply_timeout_sec() + 60`); user 1 calls `_acquire_lock(flat=99)` which triggers the stale-lock reaper. Assert: user 1 successfully acquires `(flat=99, user=1)`, and user 2's stale lock is **not** deleted (the reaper only touches the calling user's own rows). |
| `test_rebuild_user_scoped_tables_idempotent` | Run `init_db` twice; second run no-ops (no rebuild executed because all three tables already have `user_id`); row counts and contents preserved across runs |
| `test_users_table_unique_email_rejects_duplicates` | Two users with the same non-NULL email — second insert raises `IntegrityError` |
| `test_users_table_allows_multiple_no_email` | Two users with `email=NULL` coexist (SQLite treats NULLs as distinct in unique indexes) |
| `test_matches_unique_constraint_widened` | Insert a match row for user 1 with profile_hash X / flat 7 / decision 'match'; insert the same combination for user 2 — both succeed (the widened UNIQUE allows it). Insert the same combination for user 1 again — `INSERT OR IGNORE` no-ops as expected. |

Coverage target matches the rest of the codebase (≥95% on changed modules).

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Backfill migration corrupts a live single-user DB | All three rebuilds run inside a single `BEGIN IMMEDIATE` transaction; per-table column probes skip already-migrated tables; `DROP TABLE IF EXISTS <name>_new` at the start of each rebuild block recovers from a half-finished previous attempt. Covered by `test_backfill_existing_rows` and `test_rebuild_user_scoped_tables_idempotent`. |
| Forgotten query somewhere reads or writes unscoped data | The mechanical grep audit in §5.4; cross-user isolation tests for `view.py`, dispatcher, stats, and `apply.py`'s already-applied guard; stale-lock reaper isolation test |
| FK on `user_id` fails because the seed user is created after the rebuild's `INSERT … SELECT 1, …` step | `init_db` ordering enforced in §5.2: `CREATE TABLE users` (step 1) → `ensure_default_user` (step 2) → `_rebuild_user_scoped_tables` (step 3). The backfill test covers it explicitly. |
| Existing single-user installs upgrade in place and lose data | Each rebuild's `INSERT INTO ..._new SELECT ... FROM ...` copies every row before `DROP TABLE`; transaction rollback on failure leaves the original table intact. Tested by `test_backfill_existing_rows`. |
| `matches.UNIQUE` constraint widening forgotten | The post-rebuild `SCHEMAS` `CREATE TABLE` for `matches` is the single source of truth and the rebuild SQL must mirror it. `test_matches_unique_constraint_widened` asserts the runtime behavior. |

## 8. Migration order (one-shot, on next `init_db`)

1. `CREATE TABLE users IF NOT EXISTS` (and any other entry in `SCHEMAS`, including the post-rebuild shapes for fresh installs).
2. `INSERT OR IGNORE` seed user `(id=1, email=NULL, created_at=now)`.
3. Open `BEGIN IMMEDIATE`. For each of `matches`, `applications`, `apply_locks`: if `PRAGMA table_info(<name>)` already lists a `user_id` column, skip; otherwise execute the rebuild block from §4.4 (`DROP TABLE IF EXISTS <name>_new` → `CREATE TABLE <name>_new` → `INSERT INTO <name>_new SELECT ..., 1, ...` → `DROP TABLE <name>` → `ALTER TABLE <name>_new RENAME TO <name>`). `COMMIT`.
4. `CREATE INDEX IF NOT EXISTS` the two new composite indexes (already in `SCHEMAS`, so step 1 covers fresh installs; this is a no-op on re-runs).

After this completes, every existing row is owned by user 1 and the schema is ready for Phase 5 to layer real users on top.

## 9. Acceptance criteria

- [ ] `docs/adr/0001-web-ui-architecture.md` lands with the §3 content.
- [ ] `init_db` on a fresh DB produces the new schema with seed user.
- [ ] `init_db` on a pre-migration DB (existing single-user data) backfills every row to `user_id = 1` without data loss.
- [ ] `flatpilot run` / `flatpilot dashboard` / `flatpilot apply` / `flatpilot doctor` all work unchanged from the user's perspective.
- [ ] `tests/test_user_scoping.py` passes; existing test suite passes; `ruff check` and `mypy` clean.
- [ ] No call site of `conn.execute` against `matches` / `applications` / `apply_locks` is missing a `user_id` clause (verified by the §5.4 grep audit).
- [ ] `matches.UNIQUE` constraint includes `user_id`; verified by `test_matches_unique_constraint_widened`.
- [ ] `apply_locks.PRIMARY KEY` is `(flat_id, user_id)`; verified by `test_apply_lock_per_user`.
- [ ] Rebuild helper is idempotent and crash-safe (`DROP TABLE IF EXISTS <name>_new` guard plus `BEGIN IMMEDIATE` transaction); verified by `test_rebuild_user_scoped_tables_idempotent`.
