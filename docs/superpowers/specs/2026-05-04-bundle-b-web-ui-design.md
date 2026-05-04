# Bundle B — Web UI Phase 5 Foundations Design

**Beads:** FlatPilot-ix2m (O3. React/Next.js tabs), FlatPilot-8jx (O. 'Connected accounts' page with per-platform Connect buttons), plus magic-link auth (extends ADR 0001)

**Date:** 2026-05-04

**Status:** Design — implementation pending

**Related:**
- ADR `docs/adr/0001-web-ui-architecture.md` (the north star this PR executes against)
- Foundations spec `docs/superpowers/specs/2026-05-03-web-ui-foundations-design.md` (the per-user data model this PR builds on)
- Epic `FlatPilot-2p3` — Web UI fully self-sufficient (CLI/UI feature parity); this PR is its first implementation step
- Children of `2p3`: `FlatPilot-h1i` (per-user pipeline), `FlatPilot-biv` (frontend unit tests), `FlatPilot-xzg` (session revocation), `FlatPilot-j1k` (signup allowlist), `FlatPilot-28o` (multi-worker connect)

---

## 1. Motivation

The foundations PR (FlatPilot-x0pq + FlatPilot-z3me) shipped a per-user data model — `users` table, `user_id` column on every per-user table — but the CLI continues to operate as the single seed user. There is no Web UI yet. ADR 0001 fixed the major Phase 5 architectural choices (FastAPI, Next.js, magic-link auth, SQLite-now/Postgres-later, docker-compose deploy).

This PR delivers the first three Phase 5 user-facing pieces in one bundle:

1. **A FastAPI server** that replaces the role of today's localhost dashboard (`src/flatpilot/server.py` + `src/flatpilot/view.py`).
2. **A Next.js frontend** with the three existing dashboard tabs (Matches / Applied / Responses) ported with full mutation parity, plus a Connections page for per-platform headed-Playwright login.
3. **Magic-link authentication** end-to-end: token issue / verify / signed-cookie sessions / open-signup / no-existence-reveal. Email binding for the existing seed user via a new CLI command and wizard step.

These three ship together because they're tightly coupled — auth gates every endpoint, the connections page exists *because* per-user sessions need their own login flow, and the tabs *are* the auth-protected UI. Splitting them would mean three PRs each shipping a half-finished surface.

This PR does **not** make the Web UI self-sufficient for users who never touch the CLI. Closing that gap is epic `FlatPilot-2p3` — it requires per-user matcher pipeline, profile editor, saved-searches CRUD, templates/attachments UI, notifications config, and onboarding flow. Bundle B is the foundation those pieces will be built on.

## 2. Scope

### 2.1 In scope

- **FastAPI server** at `src/flatpilot/server/` (package, replaces single-file `server.py`'s role). Modules:
  - `app.py` — `FastAPI()` instance, middleware, route registration.
  - `deps.py` — `get_current_user`, `get_db` dependencies.
  - `auth.py` — magic-link sign / verify, session-cookie sign / verify.
  - `email_links.py` — wraps the existing `notifications/email.py` SMTP transport to send magic-link emails.
  - `routes/auth.py`, `routes/matches.py`, `routes/applications.py`, `routes/connections.py`.
  - `schemas.py` — Pydantic request / response models.
- **Next.js frontend** at `web/` (App Router, TypeScript strict, Tailwind, shadcn/ui). Pages:
  - `/login` (public) — email input, "Send magic link", confirmation state.
  - `/verify` (public) — consumes `?t=<token>`, redirects to `/`.
  - `/` (protected) — Matches / Applied / Responses tabs.
  - `/connections` (protected) — per-platform Connect / Reconnect.
- **Magic-link auth**: 15-min single-use signed tokens via `itsdangerous`, signed-cookie sessions (`fp_session`, HttpOnly, SameSite=Lax, 30d), open signup with no existence reveal, email binding via `flatpilot set-email <addr>` and a wizard prompt.
- **Tab parity**: the three existing dashboard mutations (`POST /api/matches/{id}/skip`, `POST /api/applications`, `POST /api/applications/{id}/response`) ported to FastAPI endpoints, wired into Next.js components.
- **Connections page**: per-platform Connect button spawns a headed Playwright Chromium window via the refactored login engine; user clicks Done in the Web UI to capture cookies; status reflects `state.json` presence and cookie expiry.
- **Login engine refactor**: `src/flatpilot/sessions/login_engine.py` exposing `run_login_session(...)` driven by an `Awaitable[None]` completion signal. CLI passes a stdin-driven coroutine; FastAPI passes `asyncio.Event.wait()`. Per-platform metadata (login URL, `is_authenticated` cookie heuristic) lives in `src/flatpilot/sessions/platforms.py`.
- **Schema**: one new column (`users.email_normalized`, indexed unique-when-not-NULL) and one new table (`magic_link_tokens`) for single-use enforcement.
- **CLI additions**: `flatpilot set-email <addr>` command; wizard prompt during `flatpilot init`; `flatpilot doctor` row flagging unbound seed users.
- **Tests**: backend pytest covering auth flow, every endpoint, isolation per `user_id`, login-engine state machine, the new CLI command path. One Playwright e2e smoke test in `web/tests/e2e/`.

### 2.2 Out of scope (deferred to follow-ups, all linked under epic `FlatPilot-2p3`)

- **Per-user matcher / scrape / notify pipeline.** Pipeline still runs only as `DEFAULT_USER_ID = 1`. Web UI users with `id >= 2` see empty Matches/Applied/Responses tabs. Tracked: `FlatPilot-h1i`.
- **Profile editing UI, saved-searches CRUD UI, templates UI, attachments UI, notifications config UI, onboarding flow.** All part of `FlatPilot-2p3`.
- **Per-user filesystem namespace fully realized** for profile.json / templates / attachments. Bundle B *partially* implements it for sessions only (`session_storage_path` resolves per-user paths for `id >= 2`).
- **Hosted / multi-machine deploy.** `Connect` spawns Playwright on the FastAPI host machine. Works for `localhost` deployment; hosted-VPS deploy needs a different connect mechanism. Not addressed in this PR.
- **Docker-compose / Postgres / SQLAlchemy / Caddy.** Tracked: `FlatPilot-u5gh`.
- **Allowlist signup, rate limiting, abuse protection.** Tracked: `FlatPilot-j1k`.
- **Server-side session revocation.** Cookie-only sessions in this PR. Tracked: `FlatPilot-xzg`.
- **Multi-worker uvicorn support for the connect flow.** Single-worker only in this PR. Tracked: `FlatPilot-28o`.
- **Vitest unit tests for frontend components.** Single Playwright e2e smoke test only. Tracked: `FlatPilot-biv`.
- **Retiring the old localhost dashboard** (`src/flatpilot/server.py`, `src/flatpilot/view.py`, `flatpilot dashboard` CLI). Stays alongside the new server; dual-maintenance for one PR cycle is the explicit price.

### 2.3 Estimated size

~3.5–5k LOC of net new code, broken down approximately:

| Surface | LOC |
|---|---|
| FastAPI server (`server/app.py`, `deps.py`, `auth.py`, `email_links.py`, route modules, Pydantic schemas) | ~700 |
| Login engine refactor (`sessions/login_engine.py`, `platforms.py`, `paths.py`) | ~300 |
| CLI additions (`set-email`, wizard hook, doctor row) | ~150 |
| Backend tests (the ~30 tests in §8.1) | ~700 |
| Next.js scaffolding (`package.json`, `next.config.js`, `tailwind.config.ts`, `middleware.ts`, layouts) | ~250 |
| shadcn/ui components (checked in, not a runtime dep) | ~500 |
| Feature components (`MatchCard`, `ApplicationRow`, `ResponseForm`, `ConnectionRow`, hooks, API client) | ~700 |
| Pages (`/login`, `/verify`, `/`, `/connections`) | ~400 |
| e2e smoke + SMTP stub fixture | ~200 |
| Schema migration / database touches | ~100 |
| Docs (README updates, ADR 0002) | ~100 |

Reviewable as one PR but at the upper end. If the first implementation pass produces something materially over 5k LOC, splitting becomes worth a discussion before merging.

## 3. Architecture

### 3.1 Process topology (dev mode)

```
┌─────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│  Next.js dev    │  HTTP │  FastAPI / uvicorn│ SQL   │  ~/.flatpilot/   │
│  :3000          │ ────► │  :8000           │ ────► │  flatpilot.db    │
│  (web/)         │       │  (server/)       │       └──────────────────┘
└─────────────────┘       │                  │       ┌──────────────────┐
        ▲                 │                  │ spawn │  Playwright      │
        │ cookie          │                  │ ────► │  (headed)        │
        │ fp_session      │                  │       └──────────────────┘
        │                 │                  │       ┌──────────────────┐
        └─── magic-link ──│                  │ SMTP  │  user inbox      │
                          │                  │ ────► │                  │
                          └──────────────────┘       └──────────────────┘
```

Two processes in dev: `uvicorn flatpilot.server.app:app --reload` on `:8000` and `next dev` on `:3000`. `next.config.js` rewrites `/api/*` → `http://localhost:8000/api/*` so cookies are shared across the proxy from the browser's perspective (everything appears on `localhost:3000`).

### 3.2 Auth flow

```
1. /login → user types email → POST /api/auth/request {email}
2. Server: jti = uuid4().hex
           token = serializer.dumps({jti, email})       (itsdangerous)
           INSERT magic_link_tokens (jti, email, issued_at, expires_at)
           email_links.send_magic_link(email, link=http://localhost:3000/verify?t=<token>)
           respond 200 {"ok": true}    ← always, regardless of email validity
3. User clicks link → /verify?t=<token> → POST /api/auth/verify {token}
4. Server: payload = serializer.loads(token, max_age=900)        # signature + expiry
           row = SELECT used_at, expires_at FROM magic_link_tokens WHERE jti = ?
           if row.used_at OR NOW > row.expires_at: 400
           UPDATE magic_link_tokens SET used_at = NOW WHERE jti = ?
           user = SELECT * FROM users WHERE email_normalized = LOWER(TRIM(email))
           if user is None:
               INSERT users (email, email_normalized, created_at) VALUES (...) RETURNING id
               user.id = <new id>
           Set-Cookie: fp_session=<sign(user.id)>; HttpOnly; SameSite=Lax; Max-Age=2592000
           respond 200 {"user_id": user.id}
5. Browser redirects to /; subsequent /api/* requests carry the cookie.
6. get_current_user dependency reads cookie, verifies signature, returns user_id.
   Every protected route depends on it → 401 if cookie missing/invalid.
7. DELETE /api/auth/session → Set-Cookie: fp_session=; Max-Age=0
```

Sessions are stateless signed cookies. Revocation in this PR = "wait for the cookie to expire." Server-side revocation is `FlatPilot-xzg`.

### 3.3 Connect flow

```
1. /connections → user clicks Connect on platform
   → POST /api/connections/<platform>/start
2. FastAPI:
   key = (user_id, platform)
   if key in _pending: 409
   event = asyncio.Event()
   _pending[key] = event
   asyncio.create_task(
       run_login_session(
           platform,
           storage_state_path=session_storage_path(user_id, platform),
           completion_signal=event.wait(),
           timeout_sec=300.0,
       ).then(lambda _: _pending.pop(key, None))
   )
   respond 202 {"status": "in_progress"}
3. Headed Chromium window opens on the host machine (= the user's laptop, local-only).
   User logs in inside that window.
4. User clicks Done in the modal → POST /api/connections/<platform>/done
5. FastAPI handler:
   - lookup _pending[(user_id, platform)]; 404 if absent
   - event.set()
   - await the runner task with a 30s timeout
   - the runner returns LoginResult (SAVED / ABANDONED / TIMED_OUT / CANCELLED)
   - respond 200 {"result": "saved" | "abandoned" | "timed_out"}
6. login_engine, woken by event.set(), inspects context.cookies():
   - If authenticated: context.storage_state(path=storage_state_path) → returns SAVED
   - If not:                                                          → returns ABANDONED
7. UI receives a definitive verdict in the /done response body. Modal closes;
   toast renders confirmation (saved) or error ("Login wasn't completed — try
   again, and make sure you see your dashboard before clicking Done."). No
   post-Done polling — the answer is synchronous.
```

`GET /api/connections` polling is used only on the *initial* page load and to refresh stale rows; it is no longer the channel for "did Done succeed?". This eliminates the race where the file-on-disk lags the registry pop and the UI can't tell SAVED from ABANDONED.

The `_pending: dict[(int, str), asyncio.Event]` is in-process state. Single-worker uvicorn only. Multi-worker support is `FlatPilot-28o`.

### 3.4 Data flow per request

Every authenticated route follows the same shape:

```python
@router.get("/api/matches")
async def list_matches(user: User = Depends(get_current_user)) -> MatchesOut:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT … FROM matches m JOIN flats f ON m.flat_id = f.id "
            "WHERE m.user_id = ? AND m.decision = 'match' "
            "ORDER BY m.decided_at DESC LIMIT 200",
            (user.id,),
        ).fetchall()
    return MatchesOut(matches=[MatchOut.from_row(r) for r in rows])
```

`get_current_user` is the only place `user_id` enters; every query parameter-binds it. The static SQL audit (test `test_no_unscoped_sql_in_routes_module`) makes this a runnable invariant.

### 3.5 CSRF posture

This PR does not add CSRF tokens. Mitigation rests on three properties that must hold together:

1. **`SameSite=Lax` on `fp_session`.** Cross-site forms cannot submit POST requests carrying the cookie. Browsers refuse to attach `Lax` cookies to cross-site state-changing requests.
2. **No `GET` endpoints mutate state.** Every endpoint that touches the database is `POST` / `DELETE`. CSRF via `<img src=…>` or link prefetchers cannot trigger writes.
3. **`/verify` is a SPA page that POSTs the token from the browser, not a server-side GET handler that consumes it.** This is **load-bearing**: corporate email scanners and link prefetchers (Outlook, Mimecast, etc.) follow GETs on every link in incoming mail. If `/verify` consumed tokens via GET, those scanners would burn the token before the user clicked, breaking single-use silently. The Next.js page at `/verify` only renders; on mount, the in-browser script does `POST /api/auth/verify {token}`. The FastAPI server has no GET handler at `/api/auth/verify`.

Acceptance criterion: a `GET` to `/api/auth/verify` returns 405 (Method Not Allowed). A `POST` is the only path that consumes tokens.

If any of these three properties is broken in a future PR (e.g. someone adds a GET handler "for convenience"), the CSRF guarantees collapse. The relevant tests assert all three.

### 3.6 File layout

```
src/flatpilot/server/
├── __init__.py
├── app.py
├── deps.py
├── auth.py
├── email_links.py
├── routes/
│   ├── __init__.py
│   ├── auth.py
│   ├── matches.py
│   ├── applications.py
│   └── connections.py
└── schemas.py

src/flatpilot/sessions/
├── __init__.py
├── login_engine.py
├── platforms.py
└── paths.py

web/
├── package.json
├── next.config.js
├── tailwind.config.ts
├── tsconfig.json
├── src/
│   ├── middleware.ts
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── (authed)/layout.tsx
│   │   ├── page.tsx
│   │   ├── connections/page.tsx
│   │   ├── login/page.tsx
│   │   └── verify/page.tsx
│   ├── components/
│   │   ├── ui/                     # shadcn output
│   │   ├── MatchCard.tsx
│   │   ├── ApplicationRow.tsx
│   │   ├── ResponseForm.tsx
│   │   └── ConnectionRow.tsx
│   └── lib/
│       ├── api.ts
│       └── auth.ts
└── tests/e2e/smoke.spec.ts
```

## 4. Schema

### 4.1 New column `users.email_normalized`

```sql
ALTER TABLE users ADD COLUMN email_normalized TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_normalized
    ON users(email_normalized) WHERE email_normalized IS NOT NULL;
```

`ALTER`-able (no `REFERENCES`, no `NOT NULL DEFAULT`) so no rebuild dance. The partial unique index allows multiple `NULL`s and enforces uniqueness on real emails. `email_normalized` = `LOWER(TRIM(email))` computed in Python (Unicode-aware) before every write — never via SQLite's `LOWER()`, which is byte-level. Every write path sets both columns; every lookup uses `email_normalized`.

**Backfill on `init_db`** (idempotent):
```sql
UPDATE users SET email_normalized = LOWER(TRIM(email))
WHERE email IS NOT NULL AND email_normalized IS NULL
```

**Dual UNIQUE constraint behavior (intentional).** The foundations PR shipped `email TEXT UNIQUE`. Bundle B keeps that constraint and adds the partial unique index on `email_normalized`. Both stay in place. SQLite's `email UNIQUE` is byte-level, so `Foo@x.com` and `foo@x.com` are distinct to it; the partial unique index on `email_normalized` is strictly stricter, so it always blocks first or simultaneously. Concretely:

| Existing row | Insert attempt | Blocked by |
|---|---|---|
| `email='foo@x.com'` | `email='Foo@x.com'` | `email_normalized` (both normalize to `foo@x.com`) |
| `email='foo@x.com'` | `email='foo@x.com'` | both fire; `email_normalized` semantically primary |
| `email='Foo@x.com'` | `email='bar@x.com'` | neither (different emails); insert succeeds |

`email UNIQUE` is therefore a redundant defense — kept because dropping it would require another foundations-style table rebuild for zero behavioral gain. Tests cover both constraints firing on conflicting inserts.

### 4.2 New table `magic_link_tokens`

```sql
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    jti TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_expires
    ON magic_link_tokens(expires_at);
```

One row per `/api/auth/request`. `used_at` set on first successful verify; subsequent verifies of the same token reject. Cleanup on every server startup:

```sql
DELETE FROM magic_link_tokens WHERE expires_at < datetime('now', '-1 day')
```

**The `expires_at` column is for cleanup, not validation.** Token expiry is enforced by the signed token itself via `serializer.loads(token, max_age=900)` (the `itsdangerous` library raises if the signature is older than `max_age`). The row's `expires_at` is informational — it lets us prune rows safely after the signed token would already have rejected on its own. Future implementers should not add belt-and-braces "and `expires_at >= NOW()`" checks to the verify path; they're redundant and create false-positive rejection windows on clock skew.

### 4.3 Schema registration

Both go into `src/flatpilot/schemas.py`:
- `MAGIC_LINK_TOKENS_CREATE_SQL` registered in `SCHEMAS["magic_link_tokens"]`.
- The two indexes registered alongside.
- `email_normalized` ADD COLUMN goes into `database.py`'s existing `ensure_columns()` mechanism, *not* into `SCHEMAS` — it's a forward ALTER on existing installs. The `users` `CREATE TABLE` string in `SCHEMAS` is updated to declare the column inline so fresh installs go straight to the new shape.

(The foundations PR §5.3 deliberately removed two `COLUMNS[...]` ALTER entries in favor of inline declarations. Bundle B's reintroduction of the pattern for `email_normalized` is also deliberate: this is a one-time forward migration on existing installs, exactly the case `ensure_columns()` was designed for. Future Phase 5 PRs that add ALTER-able columns should reuse this pathway rather than rebuild `users`.)

### 4.4 Untouched

- `matches`, `applications`, `apply_locks` — schema unchanged. Foundations PR already widened them. New endpoints just SELECT/INSERT against them with `user_id` parameter-bound from the cookie.
- `flats` — still global, no `user_id`.
- No `sessions` table. Cookie-only.
- No `connections` table. Connection state derives from filesystem (`state.json` presence + cookie `expires` field).

## 5. API surface

All routes under `src/flatpilot/server/routes/`. Auth-required routes use `Depends(get_current_user)` (401 with empty body when cookie missing/invalid). JSON in/out, Pydantic models in `server/schemas.py`.

### 5.1 Auth (`routes/auth.py`)

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| POST | `/api/auth/request` | public | `{"email": str}` | `{"ok": true}` | Always 200, regardless of email validity. Inserts `magic_link_tokens` row, sends email via `email_links`. |
| POST | `/api/auth/verify` | public | `{"token": str}` | `{"user_id": int}` + `Set-Cookie: fp_session` | Verifies signature + expiry + single-use. Lookup-or-create user. Issues HMAC-signed cookie (HttpOnly, SameSite=Lax, 30d). |
| DELETE | `/api/auth/session` | required | — | `204` + cookie cleared | Logout. |
| GET | `/api/auth/me` | required | — | `{"user_id": int, "email": str \| null}` | Frontend bootstrap. |

### 5.2 Matches (`routes/matches.py`)

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| GET | `/api/matches` | required | — | `{"matches": MatchOut[]}` | `WHERE user_id = ? AND decision = 'match' ORDER BY decided_at DESC LIMIT 200`. Joins `flats`. |
| POST | `/api/matches/{id}/skip` | required | — | `204` | `WHERE id = ? AND user_id = ?`. 404 if not the caller's. |

`MatchOut`: `{id, flat_id, title, district, rent_warm_eur, rooms, size_sqm, listing_url, decided_at, matched_saved_searches[]}`.

### 5.3 Applications (`routes/applications.py`)

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| GET | `/api/applications` | required | — | `{"applications": ApplicationOut[]}` | `WHERE user_id = ? ORDER BY applied_at DESC LIMIT 200`. |
| POST | `/api/applications` | required | `{"flat_id": int}` | `{"application_id": int, "status": str}` | Calls `apply.py` engine via `run_in_executor` (no subprocess hop — FastAPI is already long-running). `user_id` threaded through. |
| POST | `/api/applications/{id}/response` | required | `{"status": str, "response_text": str}` | `204` | Mirrors the existing paste-reply endpoint, scoped by `user_id`. |

`ApplicationOut`: `{id, flat_id, platform, listing_url, title, rent_warm_eur, rooms, size_sqm, district, applied_at, method, status, response_text, response_received_at, notes, triggered_by_saved_search}`.

### 5.4 Connections (`routes/connections.py`)

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| GET | `/api/connections` | required | — | `{"connections": ConnectionOut[]}` | One entry per platform. Status from filesystem: `connected` if `state.json` exists with non-stale cookies, `expired` if all cookies stale, `disconnected` if no file, `in_progress` if `(user_id, platform) in _pending`. |
| POST | `/api/connections/{platform}/start` | required | — | `202 {"status": "in_progress"}` | Validates platform; 404 unknown. 409 if already in progress for `(user_id, platform)`. Creates `asyncio.Event`, registers, spawns `login_engine` task. |
| POST | `/api/connections/{platform}/done` | required | — | `200 {"result": "saved" \| "abandoned" \| "timed_out"}` | Synchronous-with-timeout. Looks up registered Event (404 if absent), calls `.set()`, awaits the runner task with a 30s timeout, returns the engine's `LoginResult` so the frontend can render a definitive outcome without polling. |

`ConnectionOut`: `{platform, status: "connected" | "expired" | "disconnected" | "in_progress", expires_at: str \| null}`.

### 5.5 Errors

All error responses share `{"error": str, "detail": str | null}`. Codes: `400` (validation), `401` (unauth), `404` (not found OR not owned — no distinction by design), `409` (already in progress), `500` (logged with traceback, generic body).

### 5.6 Middleware

```python
app = FastAPI(title="FlatPilot")
app.add_middleware(SessionCookieMiddleware, secret=SETTINGS.session_secret)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router, prefix="/api/auth")
app.include_router(matches_router, prefix="/api/matches")
app.include_router(applications_router, prefix="/api/applications")
app.include_router(connections_router, prefix="/api/connections")
```

CORS is dev-only; production-equivalent (Caddy front-of-everything) is `FlatPilot-u5gh`.

## 6. Frontend

### 6.1 Auth strategy

- **Middleware** (`web/src/middleware.ts`): cheap presence check on `fp_session` cookie. Absent → redirect to `/login`. Public routes: `/login`, `/verify`. UX optimization only; real enforcement is FastAPI-side.
- **Client bootstrap**: protected layout calls `GET /api/auth/me` on first render. 401 → `window.location.assign('/login')`. 200 → stash `{user_id, email}` in React context.
- **API client** (`web/src/lib/api.ts`): single `fetch` wrapper with `credentials: 'include'`, 401 redirect, typed error throwing.

### 6.2 `/login` (public)

Single email input + submit. `POST /api/auth/request {email}` → swap to "Check your email" confirmation. Resend button after 30s.

### 6.3 `/verify` (public)

On mount: `POST /api/auth/verify {token}` from `?t=<token>`. Success → redirect `/`. Error → small message + "Back to login".

### 6.4 `/` — tabs (protected)

Top nav: wordmark + email + Connections link + Logout. shadcn `<Tabs>` with three values:

- **Matches tab**: `GET /api/matches` → `MatchCard` per row. Apply / Skip / Copy URL buttons. Empty state copy (no CLI mention, no internal references, no time-bound language): *"No matches yet. Profile and saved-search setup will appear here once available."* The CLI seed user (with bound email) sees their existing CLI-generated matches here naturally; non-seed users see the empty state until `FlatPilot-h1i` lands.
- **Applied tab**: `GET /api/applications` → `ApplicationRow` per row, status badges.
- **Responses tab**: same data filtered to applications without `response_received_at`. `ResponseForm` per row.

State management: per-tab `useMatches() / useApplications()` hooks built on `fetch` + `useState` + `useEffect`. No SWR / React Query in this PR.

### 6.5 `/connections` (protected)

`GET /api/connections` → `ConnectionRow` per platform. Connect button → `POST /api/connections/{platform}/start` → modal:

```
Sign in to WG-Gesucht

We've opened a browser window for you. Log in to WG-Gesucht
in that window. When you see your dashboard, click Done.

                       [Cancel]    [Done]
```

Done → `POST /api/connections/{platform}/done`. Synchronous: response body is `{result: "saved" | "abandoned" | "timed_out"}`. Modal renders accordingly:
- `saved` → modal closes, success toast, row updates from the response or a one-shot `GET /api/connections` refetch.
- `abandoned` → error toast ("Login wasn't completed — make sure you see your dashboard before clicking Done.") and the modal stays open so the user can retry.
- `timed_out` → "Server didn't get an answer in time. The browser window may still be open — finish logging in and click Done again."

Cancel button uses the same endpoint (the engine returns `ABANDONED` when no auth cookies were captured). The browser-window-still-open backstop: if the user closes the headed Chromium directly without clicking Done, the engine's 5-minute `timeout_sec` fires and the next `GET /api/connections` shows the `(user, platform)` entry no longer `in_progress`.

No post-Done polling. The initial page load fetches `GET /api/connections` once to render the table; subsequent updates come from explicit user actions or the `/done` response.

### 6.6 Shared

- `web/src/app/layout.tsx` — root, `<Toaster>` from shadcn.
- `web/src/app/(authed)/layout.tsx` — protected nested layout, fetches `/api/auth/me`, renders top nav, provides `UserContext`.
- `web/src/components/ui/` — shadcn output (Button, Input, Form, Tabs, Card, Dialog, Toast, Badge).
- `web/src/lib/auth.ts` — `useUser()` reads from context.

### 6.7 Not in this PR

Profile editor, saved-searches CRUD, settings page, dashboard/stats overview, mobile-specific layout, dark mode toggle, internationalization. All deferred under `FlatPilot-2p3`.

## 7. Login engine refactor

### 7.1 `src/flatpilot/sessions/login_engine.py`

```python
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
    capture storage_state to storage_state_path, close the browser."""
```

Behavior:
1. Resolve `platform` against `PLATFORMS` registry (`UnknownPlatform` if missing).
2. Launch Chromium **headed** (`headless=False`).
3. Open fresh context, navigate to login URL.
4. `await asyncio.wait({completion_signal_task, timeout_task}, return_when=FIRST_COMPLETED)`.
5. On signal: inspect `context.cookies()` against `PLATFORMS[platform].is_authenticated`.
   - Authenticated → `await context.storage_state(path=...)` → `SAVED`.
   - Not authenticated → `ABANDONED`. **No file write** (don't overwrite a working `state.json` with a broken one).
6. On timeout → `TIMED_OUT`. Existing `state.json` untouched.
7. On cancellation → cleanup, re-raise `CancelledError`.
8. `finally`: always `browser.close()` + `playwright.stop()`.

### 7.2 CLI shim

`flatpilot login <platform>` becomes a thin wrapper:

```python
async def cli_login(platform: str) -> None:
    storage_path = session_storage_path(DEFAULT_USER_ID, platform)

    async def stdin_signal() -> None:
        await asyncio.to_thread(input, "Press Enter when you see your dashboard… ")

    result = await run_login_session(
        platform,
        storage_state_path=storage_path,
        completion_signal=stdin_signal(),
    )
    print({
        LoginResult.SAVED: "saved.",
        LoginResult.ABANDONED: "no cookies captured.",
        LoginResult.TIMED_OUT: "timed out.",
        LoginResult.CANCELLED: "cancelled.",
    }[result])
```

CLI surface and UX unchanged.

### 7.3 Platform registry — `src/flatpilot/sessions/platforms.py`

```python
@dataclass(frozen=True)
class PlatformLogin:
    name: str
    login_url: str
    is_authenticated: Callable[[Sequence[Cookie]], bool]

PLATFORMS: dict[str, PlatformLogin] = {
    "wg-gesucht":    PlatformLogin(...),
    "kleinanzeigen": PlatformLogin(...),
    "immoscout24":   PlatformLogin(...),
}
```

`is_authenticated` cookie heuristics are lifted from existing scraper auth-detection code; no new platform-specific knowledge introduced in this PR.

### 7.4 Storage path — `src/flatpilot/sessions/paths.py`

```python
def session_storage_path(user_id: int, platform: str) -> Path:
    if user_id == DEFAULT_USER_ID:
        return Path.home() / ".flatpilot" / "sessions" / platform / "state.json"
    return (
        Path.home() / ".flatpilot" / "users" / str(user_id) /
        "sessions" / platform / "state.json"
    )
```

User 1 stays at the legacy path so existing scrapers keep reading their cookies unchanged. User N ≥ 2 lives under the per-user namespace. Directories created on demand.

### 7.5 FastAPI integration

In `src/flatpilot/server/routes/connections.py`:

```python
_pending: dict[tuple[int, str], asyncio.Event] = {}
_pending_tasks: dict[tuple[int, str], asyncio.Task[LoginResult]] = {}

@router.post("/{platform}/start", status_code=202)
async def start(platform: str, user: User = Depends(get_current_user)):
    if platform not in PLATFORMS:
        raise HTTPException(404, detail="unknown_platform")
    key = (user.id, platform)
    if key in _pending:
        raise HTTPException(409, detail="already_in_progress")
    event = asyncio.Event()
    _pending[key] = event

    async def runner() -> LoginResult:
        try:
            return await run_login_session(
                platform,
                storage_state_path=session_storage_path(user.id, platform),
                completion_signal=event.wait(),
                timeout_sec=300.0,
            )
        finally:
            _pending.pop(key, None)
            _pending_tasks.pop(key, None)

    _pending_tasks[key] = asyncio.create_task(runner())
    return {"status": "in_progress"}

@router.post("/{platform}/done")
async def done(
    platform: str,
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    key = (user.id, platform)
    event = _pending.get(key)
    if event is None:
        raise HTTPException(404, detail="no_session_in_progress")
    event.set()
    runner_task = _pending_tasks.get(key)  # parallel registry of the create_task handles
    try:
        result = await asyncio.wait_for(runner_task, timeout=30.0)
    except asyncio.TimeoutError:
        result = LoginResult.TIMED_OUT
    return {"result": result.name.lower()}
```

(The `_pending_tasks` parallel registry holds the `asyncio.Task` handle returned by `create_task` in `/start`, so `/done` can `await` the same runner with a timeout. Both `_pending` and `_pending_tasks` are cleaned up in the runner's `finally`.)

Cancel button uses the same `done` endpoint — engine returns `ABANDONED` when no auth cookie is present.

### 7.6 Untouched

- Scraper code paths — keep loading `state.json` from the legacy path for user 1.
- `polite_session()` and headless-True default for scrape runs.
- Anti-bot heuristics, rate limits, cookie expiry handling.
- Per-platform login URLs / cookie names — relocated to the registry, values unchanged.

### 7.7 Behavior change worth flagging

The CLI shim now drives the login flow through `asyncio` (it didn't before — the original `flatpilot login` was synchronous, blocking on `input()`). The user-visible UX is preserved: the prompt text is the same, Enter advances, Ctrl-C aborts. But the underlying control-flow change means:
- Signal handling now runs through asyncio's signal handler installation (`loop.add_signal_handler` style). Ctrl-C during the `input()` call cleanly cancels the running task and triggers the `finally` browser-close.
- An existing test that relied on the synchronous control flow (e.g. asserting that `flatpilot login` blocks the test thread until input arrives) needs updating.
- No CLI flag, return code, or stdout/stderr text changes. Scripts that pipe into `flatpilot login` continue to work because `asyncio.to_thread(input, ...)` reads from the same stdin.

The acceptance criterion "Existing `flatpilot login wg-gesucht` works unchanged" remains accurate from the user's perspective; the test suite verifies the same external contract.

## 8. Tests

### 8.1 Backend (pytest)

**`tests/test_server_auth.py`**:

| Test | Verifies |
|---|---|
| `test_request_returns_ok_for_unknown_email` | 200 + `{"ok": true}`; no user row created. |
| `test_request_creates_token_row` | Row in `magic_link_tokens` with `used_at IS NULL`. |
| `test_request_sends_email_via_smtp` | `email_links.send_magic_link` called with link URL containing the signed token. |
| `test_verify_creates_user_for_new_email` | First verify creates user with `email_normalized` populated, sets cookie. |
| `test_verify_logs_in_existing_user` | Second verify with same email finds existing user, no new row. |
| `test_verify_email_lookup_is_case_insensitive` | `Foo@Example.com` registers; `foo@example.com` verifies same user. |
| `test_verify_rejects_reused_token` | Second verify of same token → 400. `used_at` set on first call. |
| `test_verify_rejects_expired_token` | `expires_at` in the past → 400. |
| `test_verify_rejects_tampered_token` | One byte mutation → signature fails. |
| `test_session_cookie_is_signed` | Manually crafted `fp_session=1` rejected on protected routes. |
| `test_logout_clears_cookie_and_401s_subsequent_requests` | After DELETE, `GET /api/auth/me` → 401. |
| `test_protected_route_returns_401_without_cookie` | All protected routes 401 without cookie. |
| `test_get_me_returns_user_id_and_email` | 200 with the cookie-bound user's id and email. |

**`tests/test_server_routes.py`**:

| Test | Verifies |
|---|---|
| `test_get_matches_scoped_to_user` | User 1's matches invisible to user 2 and vice versa. |
| `test_skip_match_404s_for_other_users_match` | 404 (not 403). User 2's row unchanged. |
| `test_get_applications_scoped_to_user` | Same isolation. |
| `test_post_application_uses_callers_user_id` | Inserted row's `user_id` matches caller. |
| `test_post_application_response_404s_for_other_users_application` | 404 leak prevention. |
| `test_get_connections_returns_status_per_platform` | `state.json` present → `connected` for that platform, `disconnected` for others. |
| `test_get_connections_reports_expired_when_cookies_stale` | All cookies' `expires` < now → `expired`. |
| `test_no_unscoped_sql_in_routes_module` | Real SQL parse via `sqlglot`: walk every string literal in `server/routes/*.py`, attempt `sqlglot.parse_one(s)`, skip strings that don't parse as SQL, for parsed SQL touching `matches` / `applications` / `apply_locks` assert that the AST contains a `WHERE` (or join `ON`) referencing `user_id`. Regex-based audits are too brittle (multi-line strings, joins, subqueries) and were considered and rejected. |
| `test_users_dual_unique_constraint` | Insert `email='Foo@x.com'`; second insert with `email='foo@x.com'` is rejected by `email_normalized UNIQUE` (both normalize to `foo@x.com`). Then directly insert (bypassing the normalize step) two rows differing only in case — `email UNIQUE` lets that through (byte-level), but the application-level `set_email` path computes `email_normalized` and trips the partial unique index. Both layers tested. |
| `test_verify_endpoint_rejects_get` | `GET /api/auth/verify?t=...` returns 405 (Method Not Allowed). Only `POST` consumes tokens — the CSRF property from §3.5 is a runnable invariant. |
| `test_no_get_endpoints_mutate_state` | Static check: walk `app.routes`, assert no `GET` route's handler writes to the database. (Implementation: the route handlers' `__module__` + name are checked against an allowlist of mutating helpers; failure surfaces the offending route.) |

**`tests/test_login_engine.py`** (mocked Playwright, no real browser):

| Test | Verifies |
|---|---|
| `test_run_login_session_saves_on_signal` | Auth cookie present → `storage_state(path=...)` called → `SAVED`. |
| `test_run_login_session_abandoned_when_no_auth_cookie` | No auth cookie → no `storage_state` call → `ABANDONED`. |
| `test_run_login_session_times_out` | No signal fired → `TIMED_OUT` after `timeout_sec`. |
| `test_run_login_session_closes_browser_on_cancel` | Task cancelled → `browser.close()` awaited. |
| `test_session_storage_path_legacy_for_user_1` | Returns `~/.flatpilot/sessions/<platform>/state.json`. |
| `test_session_storage_path_namespaced_for_user_2` | Returns `~/.flatpilot/users/2/sessions/<platform>/state.json`. |
| `test_unknown_platform_raises` | `UnknownPlatform`. |

**`tests/test_server_connections.py`**:

| Test | Verifies |
|---|---|
| `test_start_returns_202_and_registers_event` | 202; `_pending[(uid, platform)]` exists; `run_login_session` called with right args (mocked). |
| `test_start_returns_409_if_already_in_progress` | Second start for same `(user, platform)` → 409. |
| `test_done_returns_saved_when_engine_writes_state` | After `/start` + `/done`, response is `200 {"result": "saved"}` (engine mocked to return `SAVED`). |
| `test_done_returns_abandoned_when_engine_finds_no_auth_cookie` | Engine mocked to return `ABANDONED` → response is `200 {"result": "abandoned"}`. |
| `test_done_returns_timed_out_when_runner_exceeds_30s` | Runner blocked past timeout → response is `200 {"result": "timed_out"}`. The `_pending` registry is also cleaned up. |
| `test_done_404s_when_no_start` | 404 if no prior start. |
| `test_done_does_not_affect_other_users_pending` | User 2's done for same platform doesn't touch user 1's event. |

**`tests/test_set_email_cli.py`**:

| Test | Verifies |
|---|---|
| `test_set_email_writes_both_columns` | `flatpilot set-email me@example.com` updates both `email` and `email_normalized` for `users.id=1`. |
| `test_set_email_normalizes_case_and_whitespace` | `Me@Example.com␠␠` → stored email is the input, normalized is `me@example.com`. |
| `test_set_email_rejects_duplicate_normalized` | Clear error when another user has the same `email_normalized`. |
| `test_doctor_flags_seed_user_without_email` | `flatpilot doctor` reports the upgrade hint. |

**`tests/test_wizard_email.py`**:

| Test | Verifies |
|---|---|
| `test_init_wizard_prompts_for_email_and_writes_user_row` | Wizard collects email, writes both columns. |
| `test_init_wizard_skip_email_leaves_seed_unbound` | Skipping leaves `email IS NULL`; doctor flags it. |

### 8.2 Frontend (Playwright e2e — single smoke test)

**`web/tests/e2e/smoke.spec.ts`**, ~50 LOC:

```
test("happy path: login → verify → see app → connect → done → connected")
  1. Navigate to /login
  2. Type "test@example.com" + click "Send magic link"
  3. Assert "Check your email" copy appears
  4. Read magic-link URL from stub SMTP capture (test fixture)
  5. Navigate to that URL — page redirects to /
  6. Assert top nav shows "test@example.com"
  7. Assert Matches tab shows the empty state
  8. Click [Connections]
  9. Click [Connect →] on WG-Gesucht
  10. Modal appears
  11. Stub the spawned login_engine to mark session as saved (don't open real Chromium)
  12. Click [Done] — modal closes, row shows "Connected"
  13. Refresh — row still shows "Connected"
```

Test fixture starts uvicorn + next dev on random ports, tears down after. No vitest unit tests; tracked as `FlatPilot-biv`.

### 8.3 Coverage target

Backend: ≥95% on `src/flatpilot/server/`, `src/flatpilot/sessions/login_engine.py`, the new CLI command path. Frontend: e2e smoke as the only gate; no line-coverage target.

### 8.4 Not tested

- Real platform login (manual smoke).
- Cross-browser (Chromium-only via Playwright).
- Visual snapshots.
- Performance / load.
- Concurrent connect under multi-worker uvicorn (`FlatPilot-28o`).

## 9. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Stateless cookie sessions can't be revoked until they expire (30d). Stolen cookie remains valid. | Medium | Documented limitation. Logout clears cookie client-side. Server-side revocation = `FlatPilot-xzg`. Acceptable for local-only deploy. |
| `_pending` dict breaks under multi-worker uvicorn. | Medium | Single-worker assertion at startup (warning if `--workers > 1`). Multi-worker support = `FlatPilot-28o`. |
| **Server restart mid-connect orphans Chromium and the modal.** A `--reload` cycle (or any process bounce) loses `_pending` and the running login task; the headed Chromium process stays alive on the host with no parent listening; the user's next `POST /done` returns 404; the modal stays open with no recovery path. | Medium | (a) On FastAPI startup, scan for Chromium processes whose parent is `playwright`'s launcher and not the current PID, and best-effort `kill` them. (b) Document `--reload` as dev-only-while-not-connecting in the README. (c) Frontend's `GET /api/connections` after a browser refresh reflects the post-restart truth (no in-progress entry), so a stale modal can be dismissed by reloading the page. The orphaned-process scan is best-effort, not load-bearing — if it misses one, the user can `kill` manually. Filed as deferred-hardening if a follow-up bead emerges. |
| Magic-link emails take >5–10 s through hobbyist SMTP, breaking ADR's UX latency budget. | Medium | If real-world latency falls outside budget, swap SMTP for Postmark/Resend per ADR — auth design unchanged. Separate small PR. |
| Open signup + no rate limit + no existence reveal = SMTP abuse vector. | Medium | Local-only deploy makes it theoretical. **Hard rule: do not expose this PR's server to the public internet without `FlatPilot-j1k`.** Documented in README and ADR addendum. |
| Headed Playwright on FastAPI process could hang an event loop slot during long login. | Low | Login runs as a `create_task`, not awaited inline. Handler returns 202 immediately. Playwright I/O is async-friendly anyway. |
| Race on `(user_id, platform)` if user double-clicks Connect. | Low | Handler is `async`; check + insert happen synchronously between awaits. Documented; if assumption changes, an `asyncio.Lock` per key is added. |
| `magic_link_tokens` grows unbounded if cleanup forgotten. | Low | Cleanup on every server startup. Test asserts pruning. Worst case is hundreds of rows on a hobbyist deploy. |
| `ABANDONED` result confuses the user (UI shows "still disconnected"). | Low | Error toast: "Login wasn't completed — try again, and make sure you see your dashboard before clicking Done." Engine logs cookie set for `flatpilot doctor` follow-up. |
| Refactoring `flatpilot login` regresses an existing CLI workflow. | Low | CLI shim preserves stdin behavior verbatim. Existing CLI tests pass unchanged. Manual smoke before merge. |
| Next.js dev proxy doesn't match production. | Low | Production = Caddy → FastAPI direct (`FlatPilot-u5gh`). Dev rewrites are clearly marked. Functional shape (cookies on one origin) matches prod. |
| Web UI users `id ≥ 2` see empty tabs (matcher runs only as user 1). | **Known / intended** | Empty-state copy in Matches tab is honest about it. Closing the gap = `FlatPilot-h1i` and the rest of `FlatPilot-2p3`. |

## 10. Acceptance criteria

- [ ] `uvicorn flatpilot.server.app:app` starts. `GET /api/auth/me` returns 401 without cookie, `{user_id, email}` with one.
- [ ] `npm run dev` in `web/` starts Next. `/login` renders. `/` redirects to `/login` when unauthenticated.
- [ ] End-to-end auth: type email at `/login` → receive magic-link email through existing SMTP → click link → land on `/` → top nav shows email. Cookie persists across refresh.
- [ ] Magic-link single-use enforced: clicking the same link twice fails the second time.
- [ ] Existing `flatpilot login wg-gesucht` works unchanged; cookies still land at `~/.flatpilot/sessions/wg-gesucht/state.json`. Existing scrapers continue reading that path.
- [ ] `flatpilot set-email <addr>` writes both `email` and `email_normalized` for `users.id=1`. Idempotent. Clear error on duplicate.
- [ ] Wizard email prompt: `flatpilot init` on a fresh install offers an email step; storing populates both columns.
- [ ] `flatpilot doctor` reports unbound seed user with the upgrade hint when `users.id=1` has `email IS NULL`.
- [ ] Web UI tabs render parity data with the legacy localhost dashboard for the seed user (post-email-binding). Skip / Apply / Paste-reply mutations work end-to-end.
- [ ] Cross-user isolation: a fresh non-seed user sees empty tabs even when seed user has matches/applications.
- [ ] Connections page: clicking Connect on `wg-gesucht` opens a real headed Chromium via the engine. After hand-login + Done click, row flips to "Connected" with `expires_at`. Cookies land at the right per-user path.
- [ ] Connect cancel path: closing the browser leaves no broken `state.json`.
- [ ] **Connect synchronous result**: `POST /api/connections/{platform}/done` returns `200 {"result": "saved" | "abandoned" | "timed_out"}` — frontend never has to poll to learn the post-Done outcome. The 30s server-side timeout is enforced.
- [ ] **Server-restart orphan scan**: FastAPI startup logs and best-effort kills any leftover Playwright-spawned Chromium processes from a prior run.
- [ ] All new pytest files pass; existing test suite passes; `ruff check` clean; `mypy` clean. e2e smoke passes against a freshly-spun-up dev stack.
- [ ] Coverage ≥95% on `src/flatpilot/server/`, `src/flatpilot/sessions/login_engine.py`, the new CLI path.
- [ ] Schema migration: `init_db` on a foundations-PR-shaped DB brings up `email_normalized` and `magic_link_tokens` without data loss. Idempotent.
- [ ] **Dual UNIQUE behavior verified**: `email UNIQUE` + partial unique index on `email_normalized` are both in place; tests cover both layers firing on case-conflicting inserts.
- [ ] **CSRF posture verified**: `GET /api/auth/verify` returns 405; no `GET` route in `app.routes` writes to the database (asserted by `test_no_get_endpoints_mutate_state`).
- [ ] Old localhost dashboard untouched: `flatpilot dashboard` continues to start the legacy stack.
- [ ] Static SQL audit (`test_no_unscoped_sql_in_routes_module`) passes — using `sqlglot`-backed AST inspection, not regex.
- [ ] **`sqlglot` added to dev dependencies** in `pyproject.toml` for the audit test.
- [ ] **e2e SMTP stub fixture** built: `web/tests/e2e/smtp_stub.ts` (or equivalent) intercepts the magic-link email and exposes a `getMagicLinkUrl()` helper used by `smoke.spec.ts` step 4. The stub is local to tests; production SMTP is unchanged.
- [ ] README and a new ADR `docs/adr/0002-bundle-b-deployment-caveats.md` carry the **hard rule against public-internet exposure without `FlatPilot-j1k`**. (New ADR rather than appending to 0001 because 0001 is "Accepted" and pinned-as-of-foundations; deployment caveats specific to Bundle B's local-only constraint belong in their own ADR.)

## 11. Migration / rollout

On the next `init_db` after this PR lands:

1. `CREATE TABLE` for every entry in `SCHEMAS` (idempotent). Picks up updated `users` shape with `email_normalized` for fresh installs and the new `magic_link_tokens` table.
2. `ensure_columns()` ALTER-adds `email_normalized` to existing `users` tables that don't have it.
3. Backfill query: `UPDATE users SET email_normalized = LOWER(TRIM(email)) WHERE email IS NOT NULL AND email_normalized IS NULL`. Idempotent.
4. Cleanup query for `magic_link_tokens` runs once on startup (no-op on fresh installs).

On every server startup (post-migration):

5. Magic-link token cleanup: `DELETE FROM magic_link_tokens WHERE expires_at < datetime('now', '-1 day')`.

CLI continues to function unchanged for existing seed users with `email IS NULL`. They're prompted to run `flatpilot set-email` only if they want to use the Web UI.

**Public-exposure hard rule (restated for visibility — also in §10).** Bundle B's open-signup + no-rate-limit posture (chosen because the deployment audience is local-only) is **NOT safe to expose to the public internet** until `FlatPilot-j1k` lands the email allowlist. The README, the ADR `docs/adr/0002-bundle-b-deployment-caveats.md`, and the FastAPI app's startup log message all say so. Any operator running this on a VPS without first landing `j1k` is operating outside what this PR designed for.

## 12. Decision log (chosen during brainstorming)

| # | Decision | Pick |
|---|---|---|
| 1 | Auth scope | B — full magic-link in this PR |
| 2 | Repo layout | A — `src/flatpilot/server/` package + `web/` at repo root |
| 3 | Tabs scope | B — parity with mutations (port skip / apply / paste-reply) |
| 4a | Connect plumbing | B — refactor login engine + Done button |
| 4b | Hosted vs local | i — local-only (server-spawned Playwright assumes user is on host machine) |
| 5 | Docker-compose | A — out of scope (`FlatPilot-u5gh`) |
| 6a | Signup policy | i — open, no existence reveal |
| 6b | Seed user binding | iii — explicit binding via `flatpilot set-email` + wizard prompt |
| 7 | Frontend tooling | A — Tailwind + shadcn/ui |
| 8 | Frontend tests | e2e smoke only (`FlatPilot-biv` for unit tests) |
| 9 | Self-sufficient Web UI | A — fix empty-state copy, file `FlatPilot-2p3` epic for the full vision |
