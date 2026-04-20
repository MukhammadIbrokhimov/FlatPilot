# Project Instructions for AI Agents — FlatPilot

This file provides instructions and context for AI coding agents working on FlatPilot.

## Project

FlatPilot is a flat-hunting agent for the German rental market. It scrapes listings, matches them against a user profile with deterministic filters (no LLM scoring), and notifies the user via Telegram / email.

FlatPilot is a **clean-room, independent project** under the MIT license. Do not copy code from any other project (including any job-hunting agent that may have inspired the high-level architecture). When in doubt, rewrite from scratch.

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

## Build & Test

_Added once Phase 1 lands._

```bash
# pip install -e '.[dev]'
# playwright install chromium
# pytest
# ruff check
```

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Git & Pull-Request Rules (OVERRIDE — takes precedence over the Session-Completion block above)

The `git push` referenced in the auto-injected block above means **push the feature branch**, never `main`. Changes reach `main` only through a merged pull request.

- **Commit author identity:** `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`. Any other email on the machine is NOT for git commits here. Set it locally with `git config user.email ibrohimovmuhammad2020@gmail.com` and `git config user.name "Mukhammad Ibrokhimov"` before the first commit in a fresh clone.
- **NEVER add AI co-author or tool trailers to commits.** No `Co-Authored-By: Claude …`, no `🤖 Generated with Claude Code`, nothing AI-branded. Commits are authored by the human only.
- **NEVER push directly to `main`.** All changes go through a PR.
- **Standard workflow for every change:**
  1. `git fetch origin && git checkout -b <type>/<short-slug> origin/main` (types: `feat`, `fix`, `chore`, `refactor`, `docs`).
  2. Commit on the branch (no AI trailer, right email).
  3. `git push -u origin <branch>`.
  4. `gh pr create --base main --head <branch> --fill` with Summary and Test Plan.
  5. Return the PR URL and stop. The human reviews and merges.
- Do not force-push shared branches. Force-push is allowed **only** on a personal feature branch that nobody else has pulled, e.g. when fixing the author of a freshly-pushed commit (`git push --force-with-lease`).
- Every commit message should reference the Beads task ID it addresses (e.g. `bd-42: add WG-Gesucht scraper`).
- `bd dolt push` listed in the auto-injected block is a beads-specific sync command and is orthogonal to the GitHub PR workflow; skip it if no dolt remote is configured.
