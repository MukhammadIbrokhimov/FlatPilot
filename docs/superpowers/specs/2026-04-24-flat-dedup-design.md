# Cross-platform flat dedup — design

- **Bead:** FlatPilot-0ktm (I-bis-1. Address normalization + fuzzy match)
- **Epic:** FlatPilot-12bj (I-bis. Cross-platform dedup, Phase 2)
- **Date:** 2026-04-24

## Problem

The same physical apartment is often posted on both WG-Gesucht and
Kleinanzeigen. Each listing lands as a separate row in `flats`, so
the matcher evaluates it twice and the notifier fires twice. The user
sees two Telegram pings for one flat.

The `flats` schema already has a `canonical_flat_id` self-reference
column (added in C1), but no code populates it. This bead closes that
gap. The matcher and notifier are *not* changed here — that work is
FlatPilot-k40y, which reads `canonical_flat_id` to collapse
duplicates.

## Scope

- A deterministic fuzzy-match key over `(address, rent_warm_eur, size_sqm)`.
- A hook into scrape ingest that stamps `canonical_flat_id` when a new
  row has a twin already in the DB.
- A `flatpilot dedup --rebuild` subcommand that recomputes
  `canonical_flat_id` across every existing row (needed because the
  DB already contains WG-Gesucht rows from before Kleinanzeigen
  shipped, and because rule changes require re-clustering).
- Tests covering the normalizer, the match key, and chain-follow.

## Non-goals

- Changing `matcher/runner.py` or `notifications/dispatcher.py` to
  collapse duplicates — that's FlatPilot-k40y.
- LLM-based similarity. Matcher stays deterministic (Phase 1 rule).
- Dedup across different platforms within the same platform account
  (not a real case — already covered by `UNIQUE(platform,
  external_id)`).

## Design

### Module layout

A new module `src/flatpilot/matcher/dedup.py` with three public
functions:

```python
def normalize_address(raw: str | None) -> str | None: ...
def find_canonical(conn: sqlite3.Connection, flat: dict) -> int | None: ...
def assign_canonical(conn: sqlite3.Connection, flat_id: int) -> None: ...
```

`dedup.py` is pure Python + SQLite — no network, no Playwright, no
profile. This keeps it easy to unit-test with an in-memory DB.

### `normalize_address`

A pure function. German-rental-listing-specific rules only — no
general address parsing library. Rules applied in order:

1. Trim and lowercase.
2. Strip 5-digit postcodes (`10435` etc.) — Kleinanzeigen often
   prefixes the address with them; WG-Gesucht usually doesn't.
3. Strip a trailing `, berlin` (or leading `berlin, `). Other cities
   aren't handled yet — FlatPilot is Berlin-first, and adding more
   cities can ride along with the scraper that needs them.
4. Unify the Straße family: `straße`, `strasse`, `str.` → `str`.
5. Strip `.` and `,`.
6. Collapse `<digits>` followed by whitespace + single letter
   (e.g. `42 a`) into `<digits><letter>` (`42a`), so both spellings
   of the same house-number cluster.
7. Collapse all consecutive whitespace to a single space.

Returns the normalized string, or `None` if the input was `None` or
empty after trimming. Unicode is preserved otherwise (umlauts stay).

Examples:

| Raw                                        | Normalized                 |
|--------------------------------------------|----------------------------|
| `"Greifswalder Straße 42"`                | `"greifswalder str 42"`    |
| `"greifswalder strasse 42"`               | `"greifswalder str 42"`    |
| `"Greifswalder Str. 42"`                  | `"greifswalder str 42"`    |
| `"  Greifswalder  Strasse, 42 "`          | `"greifswalder str 42"`    |
| `"10435 Berlin, Greifswalder Str. 42"`    | `"greifswalder str 42"`    |
| `"Greifswalder Str. 42 a"`                | `"greifswalder str 42a"`   |
| `"Greifswalder Str. 42A"`                 | `"greifswalder str 42a"`   |
| `None` / `""` / `"   "`                    | `None`                     |

Deliberately **not** normalized (preserved distinctions):

- `42` vs `42a` — different buildings must not cluster.
- `42/2` (Austrian-style staircase) — left as-is; we haven't seen it
  in Berlin listings yet.
- `ß` → `ss` outside the Straße token (e.g. hypothetical
  `Schloßstr`) — not worth the regex until we see it in the wild.

### `find_canonical`

Given an already-inserted `flat` row (as a dict containing at least
`id`, `platform`, `address`, `rent_warm_eur`, `size_sqm`), return
the canonical flat id to link this row to, or `None` if the row is
itself canonical (or cannot be safely deduped).

Returns `None` (no dedup) when any of these is true:
- `normalize_address(flat["address"])` is `None`.
- `flat["rent_warm_eur"]` is `None`.
- `flat["size_sqm"]` is `None`.

Otherwise, query for a twin:

```sql
SELECT id, canonical_flat_id
  FROM flats
 WHERE id < :self_id                -- only look backwards (see note below)
   AND platform != :platform        -- same-platform is already unique
   AND rent_warm_eur IS NOT NULL
   AND size_sqm IS NOT NULL
   AND address IS NOT NULL
   AND ABS(rent_warm_eur - :rent) <= 50
   AND ABS(size_sqm - :size)      <= 3
 ORDER BY id ASC
```

Filter the candidates in Python by `normalize_address` equality
(SQLite's LIKE/LOWER won't handle the Straße rule), pick the first
match (lowest `id`). Return the match's `canonical_flat_id` if set,
otherwise its `id`. This is the chain-follow that keeps clusters
from fragmenting.

**Why `id < :self_id` and not `id != :self_id`:** At ingest time this
makes no difference — the newly inserted row has the highest id, so
every other row is already older. But `--rebuild` walks rows in
ascending order, and without the `<` bound the oldest row could
match a younger twin and wrongly flip the canonical convention. The
strict-less-than clause makes both paths correct.

Rationale for doing the normalization in Python instead of SQL: the
rule set is small and the candidate set per flat is already narrowed
by rent/size bands, so there's at most a handful of rows to check.
No index on normalized address is needed for Phase 2 volume.

### `assign_canonical`

```python
def assign_canonical(conn, flat_id: int) -> None:
    row = conn.execute("SELECT * FROM flats WHERE id = ?", (flat_id,)).fetchone()
    if row is None:
        return
    canonical = find_canonical(conn, dict(row))
    if canonical is not None and canonical != flat_id:
        conn.execute(
            "UPDATE flats SET canonical_flat_id = ? WHERE id = ?",
            (canonical, flat_id),
        )
```

The `canonical != flat_id` guard is defensive — `find_canonical`
already excludes `self_id`, but re-checking here keeps the invariant
"canonical row has NULL canonical_flat_id" easy to verify.

### Hook into scrape ingest

`_insert_flat` in `cli.py` currently does:

```python
cursor = conn.execute(sql, row)
return cursor.rowcount > 0
```

Change it to:

```python
cursor = conn.execute(sql, row)
if cursor.rowcount > 0:
    from flatpilot.matcher.dedup import assign_canonical
    assign_canonical(conn, cursor.lastrowid)
    return True
return False
```

One extra SELECT per new row is cheap — scrape volume is tens per
run, not thousands. Import is deferred to avoid pulling the matcher
at module import time.

**Transactionality.** `get_conn` sets `isolation_level=None`
(SQLite autocommit under WAL), so the INSERT commits before
`assign_canonical` runs. If the follow-up UPDATE fails for any
reason, the row stays in the DB without a canonical link — benign,
and `dedup --rebuild` restores it. Explicit transactions aren't
worth the complexity here.

### `flatpilot dedup --rebuild` CLI command

```
Usage: flatpilot dedup --rebuild

  Recompute canonical_flat_id for every flat in the DB.
```

`--rebuild` is the only mode. No no-arg variant — stats without a
clear single format aren't worth specifying, and adding them later
is a separate bead if actually wanted.

Steps:

1. `UPDATE flats SET canonical_flat_id = NULL` (single statement).
2. Iterate all flat ids in ascending order.
3. Call `assign_canonical` on each.
4. Print a one-liner: `rebuilt N flats → K clusters`.

Ascending `id` order matters: it guarantees the oldest row in a
cluster is always processed first and becomes canonical, so later
members link to it.

### Tests

New file `tests/test_dedup.py`. Uses an in-memory SQLite DB seeded
via `init_db` on a connection pointed at `:memory:`.

Test groups:

1. **`normalize_address`** — each normalization rule in isolation,
   plus the full-pipeline table above. Includes a case with a
   postcode prefix and a case with a `42 a` → `42a` house number.
2. **Match rule boundaries** — rent at 49/50/51 EUR delta; size at
   2.9/3.0/3.1 qm delta; same platform never matches; different
   normalized address never matches.
3. **Missing fields** — address, rent, or size None → no link.
4. **Chain follow** — insert A, B (linked to A), C that matches B
   but not A (e.g. rent drifted by 70). C should still link to A
   via B's `canonical_flat_id`.
5. **Three-platform cluster** — insert three rows on three platforms
   (WG-Gesucht, Kleinanzeigen, and a dummy third platform to
   pre-empt ImmoScout24) that all share the same normalized key. All
   three must end up with canonical = oldest.
6. **Deleted canonical** — insert A, B (linked to A). `DELETE FROM
   flats WHERE id = A`. B's `canonical_flat_id` becomes NULL (via
   `ON DELETE SET NULL`), so B is now self-canonical. A new row C
   that matches B should link to B, not NULL.
7. **`--rebuild` restores tampered link** — insert two twins, then
   `UPDATE flats SET canonical_flat_id = NULL`, run rebuild, check
   the link is restored.
8. **`--rebuild` is idempotent** — run it twice in a row, final
   state must equal state after the first run.
9. **Ingest hook writes the link** — call `_insert_flat` directly
   with a twin row and assert the link ends up on the new row (not
   just unit-testing `assign_canonical` in isolation).

All tests use `flatpilot.database.get_conn` pointed at a tempfile DB
via a pytest fixture that swaps `DB_PATH`. No writes to
`~/.flatpilot`.

## Forward compatibility with FlatPilot-k40y

`k40y` will change two SELECTs:

- `matcher/runner.py`: when picking unmatched flats, group by
  `COALESCE(canonical_flat_id, id)` and evaluate only the canonical
  member. Non-canonical rows get a matches row with
  `decision = 'skipped'` so they don't show up in `match` runs again.
- `notifications/dispatcher.py`: same `COALESCE` grouping in the
  pending-matches SELECT, plus a notified-channels dedup across
  cluster members.

This bead does nothing to those files. The schema already supports
both changes, and the link this bead populates is exactly what k40y
will read.

## Risks & open questions

- **False positives near the tolerance boundary.** Two genuinely
  different flats in the same building (same Straße, same rough
  rent, same size) would cluster. Mitigation: over-clustering only
  suppresses a notification; it doesn't lose data. The user can
  inspect all listing URLs on the canonical flat.
- **Chain drift.** A long chain A→B→C where C is far from A in
  rent/size is theoretically possible. In practice rent/size bands
  are narrow and a chain of length >2 is unlikely. If it becomes a
  problem, `--rebuild` will always converge to the stable state
  because it processes in ascending id order.
- **House-number sensitivity.** `"Greifswalder Str 42"` and
  `"Greifswalder Str 42a"` normalize differently (the `a` stays).
  That's intentional for now — flats in different buildings with
  the same street name must not cluster.
- **Deleted canonical.** `ON DELETE SET NULL` on `canonical_flat_id`
  means that deleting a canonical row leaves every cluster member
  with a NULL link (each becomes self-canonical). New matches on any
  surviving member work correctly via the `id < :self_id` rule.
  `dedup --rebuild` re-roots the cluster at the oldest survivor.
- **Non-canonical chain follow.** `find_canonical` trusts that
  `match.canonical_flat_id` (when set) points at a true canonical
  row. Under normal ingest this invariant holds because every link
  is written through `assign_canonical`, which always chain-follows.
  A manual `UPDATE` that breaks the invariant would be repaired by
  `dedup --rebuild`. We don't defensively re-resolve at lookup time
  — the extra SELECT isn't worth it for a case that can't arise
  under normal operation.
