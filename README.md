# FlatPilot

**Flat-hunting agent for the German rental market.** Scrapes listings, matches them against your profile with deterministic filters, and pings you (Telegram / email) the moment a flat fits — so you're first in the landlord's inbox.

FlatPilot is a clean-room, independent project under the MIT license. It is **not** affiliated with or derived from the source code of any other flat- or job-hunting agent.

---

## Status

Under active development. Phase 1 MVP is in progress — see the [Beads](https://github.com/steveyegge/beads) issue tracker (`bd ready`, `bd list`) for what's in-flight.

## Scope

- **Country:** Germany only (MVP).
- **City:** any German city the user configures, plus a radius in km.
- **Platforms (phased):**
  - Phase 1 — WG-Gesucht (Wohnungen / full flats)
  - Phase 2 — Kleinanzeigen
  - Phase 3 — inberlinwohnen.de (Berlin municipal, WBS-heavy)
  - Phase 4 — ImmoScout24 (via RSS feeds from saved searches — no scraping)
  - Phase 5+ — Immowelt, Immonet, other municipal sites

## Install

Two paths. Pick whichever you prefer — they share state via `~/.flatpilot/` on the host.

### Local (Python 3.11+)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
flatpilot --help
```

### Docker

Bypasses the Python and Playwright-browser install steps. Requires Docker and Docker Compose.

```bash
cp .env.example .env            # add Telegram / SMTP creds later
docker compose build            # ~1–2 min on first build
docker compose run --rm flatpilot --help
docker compose run --rm flatpilot init       # interactive wizard
docker compose run --rm flatpilot run        # one scrape + match + notify pass
```

State lives in `~/.flatpilot/` on the host by default (shared with a local install). Override via `FLATPILOT_DATA_DIR` in `.env`.

### Logging in to a platform (host-only)

`flatpilot login <platform>` is the one command that must run on the host — it opens a headed Chromium window so you can log in by hand (2FA / captcha included). Docker on macOS/Windows has no display forwarding, so attempting this inside the container is guarded with a clear error.

FlatPilot never stores your password: only the cookies your own browser receives. Those cookies persist to `~/.flatpilot/sessions/<platform>/state.json` and every subsequent `scrape` / `run` call (Docker or host) reuses them via the bind mount.

Use the [Local install](#local-python-311) block above to get Python 3.11+ and Playwright on the host, then:

```bash
flatpilot login wg-gesucht  # opens the browser, press Enter here once you see your dashboard
```

Re-run any time cookies expire.

## Usage (planned)

```bash
flatpilot init                        # one-time setup wizard (profile, rent, rooms, WBS, city, radius)
flatpilot doctor                      # verify install
flatpilot login wg-gesucht            # host-only; opens headed browser for cookie capture
flatpilot run                         # single scrape + match + notify pass
flatpilot run --watch --interval 120  # keep polling every 2 min
flatpilot dashboard                   # open HTML dashboard of matches
flatpilot status                      # DB counts and last-run info
```

## Scraping behaviour

- **inberlinwohnen.de** paginates from page 1 onwards. On a fresh install, `flatpilot scrape --platform inberlinwohnen` walks the full Wohnungsfinder feed (~22 pages, ~220 listings today) so you don't miss older inventory. In steady state, the scraper stops after page 1 once every listing on it is already in the local DB — typically one page-fetch per pass. A safety cap of 30 pages bounds the worst case.
- **WG-Gesucht** and **Kleinanzeigen** read page 1 only — new listings surface there, and steady-state polling is the primary use case.

## Configuration

- `FLATPILOT_APPLY_TIMEOUT_SEC` (default `180`) — caps the dashboard's `flatpilot apply <id>` subprocess. Bump for slow Playwright runs (large attachments, slow networks).
- `POST /api/applications` returns **HTTP 409** with the message _"Apply already in progress — retry shortly"_ when another FlatPilot process is already applying to the same flat (in-process double-click guard or cross-process race against a CLI invocation). Safe to retry.

## License

MIT — see [LICENSE](LICENSE).
