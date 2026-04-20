# AGENTS.md — FlatPilot

Instructions for AI coding agents (Claude, Cursor, Aider, Codex, Copilot, Gemini). Keep in sync with `CLAUDE.md`.

## Project

FlatPilot is a flat-hunting agent for the German rental market. Clean-room, MIT-licensed, independent project. See `README.md` for scope and `CLAUDE.md` for full details.

## Standing rules

- **Clean-room policy:** do not copy code from any other project. If you recognize a pattern from elsewhere, rewrite it in your own words.
- **No LLM calls in Phase 1.** The matcher is pure Python, deterministic, ≤150 LOC.
- **All user runtime state lives under `~/.flatpilot/`.** Never write into the repo at runtime.
- **Use Beads (`bd`) for all task tracking.** Do not use TodoWrite / TaskCreate / markdown TODO lists.
- **Commit author:** `Mukhammad Ibrokhimov <ibrohimovmuhammad2020@gmail.com>`.
- **No AI trailers** in commits (no `Co-Authored-By: Claude …`, no `🤖 Generated with Claude Code`).
- **No direct pushes to `main`.** Every change goes through a PR: branch → commit → push → `gh pr create`. The `git push` step in the auto-injected Beads Session-Completion section below means **push the feature branch** — not `main`.
- Reference the Beads task ID in every commit message.

## Tech

Python 3.11+, typer, rich, SQLite (WAL), Playwright, BeautifulSoup, pydantic, pytest.

## Workflow

1. `bd ready` — find available work.
2. `bd show <id>` — read acceptance criteria.
3. `bd update <id> --claim` — claim it.
4. Create a feature branch: `git fetch origin && git checkout -b <type>/<slug> origin/main`.
5. Implement with tests. Commit referencing the Beads ID.
6. `bd close <id>` — complete.
7. `git push -u origin <branch>` and `gh pr create --base main --head <branch> --fill`.
8. Return the PR URL and stop.

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

**Note on the Session-Completion block above:** "git push" in step 4 means **push the feature branch** — never `main`. Direct pushes to `main` are prohibited. All changes reach `main` only via merged pull requests. The `bd dolt push` step is optional and only relevant if a Dolt remote is configured.
