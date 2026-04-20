# Project Instructions for AI Agents — FlatPilot

This file provides instructions and context for AI coding agents working on FlatPilot.

## Project

FlatPilot is a flat-hunting agent for the German rental market. It scrapes listings, matches them against a user profile with deterministic filters (no LLM scoring), and notifies the user via Telegram / email.

FlatPilot is a **clean-room, independent project** under the MIT license. Do not copy code from any other project (including any job-hunting agent that may have inspired the high-level architecture). When in doubt, rewrite from scratch.

## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists.
- Run `bd prime` for detailed command reference and session close protocol.
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files.
- Every commit message references the Beads task ID it addresses (e.g. `bd-42: add WG-Gesucht scraper`).

## Tech Stack

- **Language:** Python 3.11+
- **CLI:** typer + rich
- **Storage:** SQLite (WAL mode, thread-local connections, forward-migration helper)
- **Scraping:** Playwright (Chromium), BeautifulSoup, httpx
- **Geocoding:** Nominatim via geopy (politeness: 1 req/s, cached on disk)
- **Notifications:** Telegram (HTTPS API) + SMTP email
- **Validation:** pydantic
- **Tests:** pytest (focus on matcher logic and scraper parsers with fixture HTML)
- **Web UI (Phase 5+):** FastAPI backend + React/Next.js frontend

## Architecture Overview

```
flatpilot/
├── config.py          # Paths (~/.flatpilot/), env loading
├── database.py        # SQLite schema, migrations, connection helpers
├── cli.py             # typer app: init, run, scrape, match, notify, status, dashboard, doctor
├── wizard/
│   └── init.py        # First-time setup wizard
├── scrapers/
│   ├── base.py        # Scraper protocol
│   └── wg_gesucht.py  # Phase 1 scraper
├── matcher/
│   ├── filters.py     # Deterministic hard filters (rent, rooms, WBS, distance, etc.)
│   └── distance.py    # Haversine + Nominatim geocoding with cache
├── notifications/
│   ├── telegram.py
│   ├── email.py
│   └── dispatcher.py
├── view.py            # HTML dashboard generator
└── pipeline.py        # Orchestrator: scrape → match → notify
```

## Conventions & Patterns

- **No LLM calls in Phase 1.** The matcher is pure Python, deterministic. Keep it ≤150 LOC.
- **All user state lives under `~/.flatpilot/`** — never write into the repo directory at runtime.
- **Scraper cookies/sessions:** `~/.flatpilot/sessions/<platform>/`.
- **Geocode cache:** `~/.flatpilot/geocode_cache.json` with 180-day TTL.
- **Anschreiben templates (Phase 2+):** `~/.flatpilot/templates/`.
- **Attachments (Phase 2+):** `~/.flatpilot/attachments/`.

## Git & Pull-Request Rules

- **Commit author identity:** `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. Any other email on the machine is NOT for git commits here. Set it locally with `git config user.email ibrohimovmuhammad2020@gmail.com`.
- **NEVER add AI co-author or tool trailers to commits.** No `Co-Authored-By: Claude …`, no `🤖 Generated with Claude Code`, nothing AI-branded. Commits are authored by the human only.
- **NEVER push directly to `main`.** All changes go through a PR.
- For every change: branch → commit → push branch → `gh pr create`. Return the PR URL and stop.
- Do not force-push shared branches. Force-push on a personal feature branch that nobody else has pulled is allowed when fixing the author of a freshly-pushed commit.

## Session Completion

When ending a work session:

1. **File remaining work** in Beads (`bd create`).
2. **Run quality gates** (pytest, ruff) if code changed.
3. **Close finished issues** (`bd close <id>`).
4. **Push the feature branch and open the PR** (never push `main`):
   ```bash
   git fetch origin
   git rebase origin/main
   git push -u origin <feature-branch>
   gh pr create --base main --head <feature-branch> --fill
   ```
5. **Verify** `gh pr view` shows the PR and hand off the PR URL.

Work is not complete until the PR is open.

## Build & Test

_Added once Phase 1 lands._

```bash
# pip install -e '.[dev]'
# playwright install chromium
# pytest
# ruff check
```
