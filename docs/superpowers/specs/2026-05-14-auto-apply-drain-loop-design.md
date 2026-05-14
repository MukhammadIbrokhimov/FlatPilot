# Auto-apply `--drain` loops until all reachable platforms hit cap

**Bead:** FlatPilot-rv2
**Date:** 2026-05-14
**Status:** approved

## Problem

`flatpilot run --drain` today exhausts the matched-flat queue, then exits. In
practice that means 3–5 submits per pass (current queue depth) rather than the
configured 20/platform daily cap. Users have to re-run repeatedly to fill the
cap; per-flat errors also bubble up only as log lines mid-run, leaving no
end-of-session view of what broke.

## Goals

1. One invocation (`flatpilot run --drain`) repeatedly scrapes → matches →
   applies, sleeping cooldowns, until every reachable platform's daily cap
   is met.
2. Per-flat errors continue to skip cleanly (already implemented — no change).
3. On any exit path — caps reached, empty-pass streak, SIGINT, SIGTERM — print
   a grouped, deduped failure summary so the user can fix the underlying
   filler bug.

## Non-goals

- Changing `flatpilot run` (no flags) one-pass behavior.
- Changing `--watch` semantics; `--watch --drain` keeps "loop forever, drain
  each pass".
- Writing the summary to a file or sending it via Telegram — terminal only.
- Changing `cooldown_seconds_per_platform`, `daily_cap_per_platform`, or
  any profile defaults.

## Design

### Loop shape

```
flatpilot run --drain
  ├─ pass 1: scrape → match → apply (cooldown-sleeps inside apply)
  ├─ drain_complete? ── yes → print summary, exit 0
  ├─                    no  → sleep --interval
  ├─ pass 2: scrape → match → apply
  ├─ drain_complete? ...
  └─ (SIGINT / SIGTERM at any point → finish current `_try_flat`,
       print summary, exit 130)
```

### Exit predicate

```python
def drain_complete(
    conn: sqlite3.Connection,
    profile: Profile,
    empty_pass_streak: int,
    user_id: int = DEFAULT_USER_ID,
) -> bool:
    reachable = [
        p for p, cap in profile.auto_apply.daily_cap_per_platform.items()
        if cap > 0 and _has_filler(p)
    ]
    if not reachable:
        return True
    if all(daily_cap_remaining(conn, profile, p, user_id=user_id) <= 0
           for p in reachable):
        return True
    if empty_pass_streak >= 2:
        return True
    return False
```

`_has_filler(platform)` wraps `flatpilot.fillers.get_filler` and returns False
on `LookupError`. This excludes `inberlinwohnen` (no filler) and any
`cap = 0` platform from blocking loop termination.

### Empty-pass streak

`empty_pass_streak` is bumped when a pass produces zero new `submitted`
applications AND the matched-but-not-yet-applied queue size is unchanged
from the previous pass. Any successful submit resets the streak to 0.

The "queue unchanged" half catches the case where a slow scraper night
adds matched flats but every one fails the filler — we don't want to spin
forever on a broken filler.

### Failure summary

Source of truth: `applications` table. At every exit path, run:

```sql
SELECT platform, flat_id, url, notes, applied_at
FROM applications
WHERE method = 'auto'
  AND status = 'failed'
  AND user_id = ?
  AND applied_at >= ?   -- run start ISO8601
  AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')
ORDER BY platform, applied_at
```

`auto_skipped` rows (no filler, expired listing) are excluded — those are
expected, not bugs. Deduplicate on `(flat_id, error_class)` where
`error_class` is the leading token of `notes` before the first colon
(e.g. `kleinanzeigen: neither success...` → `kleinanzeigen`).
Screenshot path is reconstructed from convention
(`~/.flatpilot/screenshots/<platform>/<url-slug>-<ts>.png`) — already
visible in the existing `failure screenshot saved to` log line, so the
summary just points to the directory rather than guessing exact filenames.

Render with `rich.table.Table`, one section per platform:

```
─── auto-apply: 5 submitted, 2 distinct failures ───

wg-gesucht (1)
  flat 1234  selector_missing
    https://wg-gesucht.de/...
    screenshots: ~/.flatpilot/screenshots/wg-gesucht/

kleinanzeigen (1)
  flat 1057  submit_timeout
    https://kleinanzeigen.de/...
    screenshots: ~/.flatpilot/screenshots/kleinanzeigen/
```

If zero failures occurred, print a one-line success message instead.

### Signal handling

`cli.run()` already wraps `--watch` in SIGINT/SIGTERM handlers. The new
`--drain` loop reuses the same shape: a `stop` flag flipped by the
handler, checked at loop top and after every cooldown sleep inside
`_try_flat` (which already re-checks `is_paused()` after long sleeps —
the same hook point fits SIGINT). The summary always prints in a
`finally` block so unhandled exceptions don't swallow it.

### File-level changes

| File | Change |
|---|---|
| `src/flatpilot/auto_apply.py` | Add `drain_complete()` and `summarize_failures()` helpers. `run_pipeline_apply` returns the queue-size delta so the CLI can compute `empty_pass_streak`. |
| `src/flatpilot/cli.py` (`run`) | Wrap the `--drain` branch in a loop with signal handling and a `finally` block that prints the summary. `--watch` path uses the same summary helper at clean shutdown. |
| `tests/test_auto_apply_drain.py` | New cases: drain stops at cap, drain stops after 2 empty passes, drain ignores cap-0 and filler-less platforms, summary dedup. |
| `tests/test_apply_cli.py` | Existing CLI tests stay green; add one for `--drain` printing summary on SIGINT-like early exit (use a profile with empty queue + monkey-patched signal). |

No schema migrations, no profile changes, no new dependencies.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Loop spins forever on slow-listing nights | `empty_pass_streak >= 2` exits cleanly. |
| Caps incorrectly counted as "reachable" when filler crashes consistently | `_has_filler` only checks registration, not health. Filler-level health is out of scope — `max_failures_per_flat=3` keeps a single bad flat from blocking the queue. |
| Summary grows unbounded on bad nights | Dedup on `(flat_id, error_class)`. A flat with 3 retries shows once. |
| Old one-pass `--drain` callers (cron, scripts) suddenly loop | Documented behavior change in PR description + CLI `--help`. The user explicitly chose this over a new flag. |

## Testing

- `pytest tests/test_auto_apply_drain.py tests/test_apply_cli.py`
- Manual: `flatpilot run --drain` in a sandbox where the matched queue has
  one flat per platform and `daily_cap_per_platform={x: 1}` → expect one
  pass, summary printed, exit 0.
- Manual: same as above but Ctrl-C mid-cooldown → expect summary printed,
  exit 130.
