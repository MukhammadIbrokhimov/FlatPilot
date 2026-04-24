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

1. Trim and collapse internal whitespace.
2. Lowercase.
3. Unify the Straße family: `straße`, `strasse`, `str.` (standalone
   token or word-final) all collapse to `str`.
4. Strip `.` and `,`.
5. Collapse consecutive spaces to one.

Returns the normalized string, or `None` if the input was `None` or
empty after trimming. Unicode is preserved otherwise (umlauts stay).

Examples:

| Raw                               | Normalized               |
|-----------------------------------|--------------------------|
| `"Greifswalder Straße 42"`       | `"greifswalder str 42"`  |
| `"greifswalder strasse 42"`      | `"greifswalder str 42"`  |
| `"Greifswalder Str. 42"`         | `"greifswalder str 42"`  |
| `"  Greifswalder  Strasse, 42 "` | `"greifswalder str 42"`  |
| `None` / `""` / `"   "`          | `None`                   |

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

### `flatpilot dedup` CLI command

```
Usage: flatpilot dedup [OPTIONS]

Options:
  --rebuild   Recompute canonical_flat_id for every flat in the DB.
```

Default (no flag) is a no-op that prints cluster stats (total flats,
canonical-set count, largest cluster size). With `--rebuild`:

1. `UPDATE flats SET canonical_flat_id = NULL` (single statement).
2. Iterate all flat ids in ascending order.
3. Call `assign_canonical` on each.
4. Print the before/after cluster stats.

Ascending `id` order matters: it guarantees the oldest row in a
cluster is always processed first and becomes canonical, so later
members link to it.

### Tests

New file `tests/test_dedup.py`. Uses an in-memory SQLite DB seeded
via `init_db` on a connection pointed at `:memory:`.

Test groups:

1. **`normalize_address`** — each normalization rule in isolation,
   plus the full-pipeline table above.
2. **Match rule boundaries** — rent at 49/50/51 EUR delta; size at
   2.9/3.0/3.1 qm delta; same platform never matches; different
   normalized address never matches.
3. **Missing fields** — address, rent, or size None → no link.
4. **Chain follow** — insert A, B (linked to A), C that matches B
   but not A (e.g. rent drifted by 70). C should still link to A
   via B's `canonical_flat_id`.
5. **`--rebuild`** — insert two twins, then tamper with the link
   (`UPDATE flats SET canonical_flat_id = NULL`), run rebuild, check
   the link is restored.

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
