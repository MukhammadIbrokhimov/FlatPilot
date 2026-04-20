# AGENTS.md — FlatPilot

Instructions for AI coding agents (Claude, Cursor, Aider, Codex, Copilot, Gemini). Keep in sync with `CLAUDE.md`.

## Project

FlatPilot is a flat-hunting agent for the German rental market. Clean-room, MIT-licensed, independent project. See `README.md` for scope and `CLAUDE.md` for full details.

## Quick Rules

- Use **Beads** (`bd`) for all task tracking. Run `bd prime` first. Do not use TodoWrite / TaskCreate / markdown TODO lists.
- Reference the Beads task ID in every commit message (e.g. `bd-42: add WG-Gesucht scraper`).
- **Clean-room policy:** do not copy code from any other project. If you recognize a pattern from elsewhere, rewrite it in your own words. No verbatim imports, no transliterations.
- **No LLM calls in Phase 1.** The matcher is pure Python, deterministic, ≤150 LOC.
- All user runtime state lives under `~/.flatpilot/`. Never write into the repo at runtime.

## Tech

Python 3.11+, typer, rich, SQLite (WAL), Playwright, BeautifulSoup, pydantic, pytest.

## Workflow

1. `bd ready` — find available work.
2. `bd show <id>` — read acceptance criteria.
3. `bd update <id> --claim` — claim it.
4. Implement with tests. Commit referencing the Beads ID.
5. `bd close <id>` — complete.
6. `git push` before ending the session.
