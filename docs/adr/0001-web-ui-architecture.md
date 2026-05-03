# 0001. Web UI Architecture

## Status

Accepted, 2026-05-03.

## Context

FlatPilot is a single-user CLI for the German rental market. It scrapes listings, matches them against a profile, and notifies the user. State lives under `~/.flatpilot/` and a SQLite DB at `~/.flatpilot/flatpilot.db`.

A hosted multi-user Web UI is on the roadmap (epic FlatPilot-0wfb). Without an architectural north star, individual Phase 5 PRs will diverge — one PR picks an auth library, the next a DB driver, the third a frontend framework, and they never align. This ADR fixes the major choices upfront.

## Decisions

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

## Consequences

- Future PRs slot cleanly into the named decisions; no further north-star debate needed for the listed concerns.
- The CLI keeps working unchanged today and after the Postgres cutover (host-only commands like `flatpilot login` will still target the user-1 filesystem namespace once Phase 5 lands).
- Cost: Phase 5 PRs commit to FastAPI/Next.js/Postgres/Caddy. Switching later would invalidate the ADR; that's the explicit price of choosing a direction.

## Alternatives considered and rejected

- **Plain `http.server` + vanilla React:** rejected; the existing `server.py` already calls out FastAPI as its replacement and Next.js gives us routing/SSR for free.
- **Password auth:** rejected as higher attack surface (password resets, hashing, rotation) for a hobbyist deployment.
- **SQLAlchemy adoption in this PR:** rejected for diff-size reasons; deferred to the FastAPI PR where every query is rewritten anyway.
- **Per-user `flats` table:** rejected; one ad on a real platform is one ad. Scrapers stay user-unaware, scrape work and the geocode cache are shared, freshness comes from scrape frequency not table partitioning.

## Out of scope for this ADR

Endpoint shapes, frontend component structure, ops/observability, rate limiting, abuse protection. Each gets its own ADR (0002, 0003, ...) when the work starts.
