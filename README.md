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
- **Auto-apply support:** WG-Gesucht and Kleinanzeigen only. inberlinwohnen.de and ImmoScout24 are **scrape + notify only** — `flatpilot apply <id>` and the auto-apply queue both skip them with a clear message. inberlinwohnen deeplinks each listing to a different landlord's site (degewo, Howoge, Gesobau, etc.), each with its own form; ImmoScout24 is intentionally RSS-only to avoid the fragile HTML path. Open those listings in your browser and apply manually.

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

**After editing saved searches:** run `flatpilot run` (which re-matches before notifying), not `flatpilot notify` standalone. Profile edits rotate the internal hash that scopes pending matches; running `flatpilot notify` directly after an edit will silently drop queued notifications. `flatpilot run` re-creates the match rows under the new hash automatically.

## Scraping behaviour

- **inberlinwohnen.de** paginates from page 1 onwards. On a fresh install, `flatpilot scrape --platform inberlinwohnen` walks the full Wohnungsfinder feed (~22 pages, ~220 listings today) so you don't miss older inventory. In steady state, the scraper stops after page 1 once every listing on it is already in the local DB — typically one page-fetch per pass. A safety cap of 30 pages bounds the worst case.
- **WG-Gesucht** and **Kleinanzeigen** read page 1 only — new listings surface there, and steady-state polling is the primary use case.

## Auto-apply

`flatpilot run` includes an auto-apply stage that submits applications to matched flats automatically. It is **opt-in per saved search** and supported on **WG-Gesucht** and **Kleinanzeigen** only (see Scope above). The engine sits behind several safety rails so a misconfigured profile cannot spam landlords.

### Enable

Set `auto_apply: true` on a saved search inside your profile (`~/.flatpilot/profile.json`):

```json
{
  "saved_searches": [
    {
      "name": "wedding-2br",
      "auto_apply": true,
      "rent_max_warm": 1400,
      "rooms_min": 2,
      "platforms": ["wg-gesucht"]
    }
  ]
}
```

A match must hit at least one saved search with `auto_apply: true` before the engine will submit. Saved searches with `auto_apply: false` (the default) still notify but never auto-submit.

### Safety rails

Configured under `auto_apply` in your profile; defaults are conservative:

| Setting | Default | What it does |
|---|---|---|
| `daily_cap_per_platform` | `20` | Max submitted applications per platform per day. Auto-apply skips the flat once the cap is hit and resumes the next day. |
| `cooldown_seconds_per_platform` | `120` | Minimum gap between submissions on the same platform. Avoids burst-submit patterns that look bot-like. |
| `pacing_seconds_per_platform` | `0` | Optional extra spacing on top of cooldown. Use `>0` if you want slower-than-cooldown pacing. |
| `max_failures_per_flat` | `3` | After this many consecutive filler failures on the same flat, FlatPilot stops retrying it. |

A flat that the filler reports as expired is excluded from auto-apply for 7 days, so a stale or removed listing doesn't poison the queue.

### Pause / resume

```bash
flatpilot pause     # creates ~/.flatpilot/PAUSE; auto-apply skips the stage entirely
flatpilot resume    # removes the file; auto-apply resumes on the next run
```

The pause file is checked at the start of every `flatpilot run`. Manual `flatpilot apply <id>` calls ignore the pause file — it gates the *automatic* stage only.

### Dry-run

```bash
flatpilot run --dry-run-apply
```

Walks the full pipeline and logs which flats would be auto-applied to, without calling any filler. Useful when you've changed saved-search rules and want to confirm the auto-apply set before going live.

### Skip the stage

```bash
flatpilot run --skip-apply       # scrape + match + notify only
```

## Configuration

- `FLATPILOT_APPLY_TIMEOUT_SEC` (default `180`) — caps the dashboard's `flatpilot apply <id>` subprocess. Bump for slow Playwright runs (large attachments, slow networks).
- `POST /api/applications` returns **HTTP 409** with the message _"Apply already in progress — retry shortly"_ when another FlatPilot process is already applying to the same flat (in-process double-click guard or cross-process race against a CLI invocation). Safe to retry.

## License

MIT — see [LICENSE](LICENSE).
