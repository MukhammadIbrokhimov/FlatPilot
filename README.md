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

_Instructions land here once Phase 1 lands. Requires Python 3.11+ and Playwright Chromium._

## Usage (planned)

```bash
flatpilot init                        # one-time setup wizard (profile, rent, rooms, WBS, city, radius)
flatpilot doctor                      # verify install
flatpilot run                         # single scrape + match + notify pass
flatpilot run --watch --interval 120  # keep polling every 2 min
flatpilot dashboard                   # open HTML dashboard of matches
flatpilot status                      # DB counts and last-run info
```

## License

MIT — see [LICENSE](LICENSE).
