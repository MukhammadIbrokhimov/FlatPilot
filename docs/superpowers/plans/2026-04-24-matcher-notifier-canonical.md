# Matcher + Notifier Canonical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `flats.canonical_flat_id` (populated by PR #21) through the matcher and notifier so the same apartment posted on two platforms produces one match row and one notification.

**Architecture:**
- **Matcher** (`src/flatpilot/matcher/runner.py`): restrict the unmatched-flats SELECT to canonical roots (`WHERE f.canonical_flat_id IS NULL`). Duplicates are never evaluated because they always have a non-NULL `canonical_flat_id` after PR #21's ingest-time stamping. The existing `UNIQUE(flat_id, profile_version_hash, decision)` constraint keeps reruns idempotent.
- **Notifier** (`src/flatpilot/notifications/dispatcher.py`): for each pending match, resolve its canonical id at query time (`COALESCE(f.canonical_flat_id, f.id)`). Track notified canonicals within the run and check sibling match rows' `notified_channels_json` so a second match row for the same cluster (from pre-PR-#21 legacy data) is silently deduped.
- **Deleted-canonical semantics:** "one ping per **live** canonical." When a canonical is deleted, `ON DELETE SET NULL` turns former duplicates into roots; the next matcher run picks them up and they get their own notification. This is intentional — the original listing is gone, the surviving listing is a fresh actionable opportunity. Tested explicitly.

**Tech Stack:** Python 3.11+, SQLite (WAL mode), pytest, typer, ruff.

**Spec reference:** `docs/superpowers/specs/2026-04-24-flat-dedup-design.md` §"Forward compatibility with FlatPilot-k40y" (note: we diverge from that section by using a filter rather than writing `decision='skipped'` rows — the filter avoids a UNIQUE-constraint trap where a flat locked as `skipped` could not later be re-decided as `match` when its canonical gets deleted).

**Bead:** FlatPilot-k40y (depends on closed FlatPilot-0ktm)

---

## File Structure

- **Modify** `src/flatpilot/matcher/runner.py` — one WHERE-clause change.
- **Modify** `src/flatpilot/notifications/dispatcher.py` — extend SELECT with canonical id; add run-level + sibling-row dedup.
- **Modify** `tests/test_dedup.py` — add matcher + notifier + integration tests next to the existing dedup tests (thematically grouped).

Files stay small: both production modules are under 200 LOC today and each change is surgical (~5 LOC for the matcher, ~25 LOC for the notifier).

---

## Task 0: Branch setup

Skip this task if you're already on `feat/i-bis-2-matcher-canonical`.

- [ ] **Step 1: Confirm you're on the feature branch**

Run: `git rev-parse --abbrev-ref HEAD`

Expected: `feat/i-bis-2-matcher-canonical`.

If not, create it:

```bash
git fetch origin
git checkout -b feat/i-bis-2-matcher-canonical origin/main
```

CLAUDE.md forbids committing to `main` directly.

- [ ] **Step 2: Confirm commit author**

Run: `git config user.email`

Expected: `ibrohimovmuhammad2020@gmail.com`. If not, set it locally:

```bash
git config user.email ibrohimovmuhammad2020@gmail.com
git config user.name "Mukhammad Ibrokhimov"
```

---

## Task 1: Matcher restricts to canonical roots

**Files:**
- Modify: `src/flatpilot/matcher/runner.py:52-61`
- Test: `tests/test_dedup.py` (append new tests)

- [ ] **Step 1: Write the failing test — two-platform twin yields one match**

Append to `tests/test_dedup.py`:

```python
def test_matcher_writes_one_match_per_canonical(tmp_db, monkeypatch):
    """Twin flats on two platforms → one match row, keyed on the canonical root."""
    from flatpilot.cli import _insert_flat
    from flatpilot.matcher import runner

    profile = _minimal_profile()
    monkeypatch.setattr(runner, "load_profile", lambda: profile)

    now = datetime.now(UTC).isoformat()
    _insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    _insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )

    summary = runner.run_match()

    assert summary["processed"] == 1  # only the canonical root
    rows = tmp_db.execute(
        "SELECT m.flat_id, f.platform "
        "FROM matches m JOIN flats f ON f.id = m.flat_id"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["platform"] == "wg_gesucht"  # the older, canonical row
```

Also add the profile helper at the top of `tests/test_dedup.py` (after imports). The fields below are verified against `src/flatpilot/profile.py` as of 2026-04-24:

```python
def _minimal_profile(*, telegram_enabled: bool = False):
    """Return a Profile that accepts any reasonably-priced 1+ room flat.

    Home coords are left unset so the distance filter is skipped — keeps
    these tests from needing a Nominatim mock.
    """
    from flatpilot.profile import Profile

    return Profile.model_validate(
        {
            "city": "Berlin",
            "radius_km": 50,
            "rent_min_warm": 0,
            "rent_max_warm": 2000,
            "rooms_min": 1,
            "rooms_max": 10,
            "household_size": 1,
            "kids": 0,
            "status": "student",
            "net_income_eur": 1500,
            "move_in_date": "2026-01-01",
            "notifications": {"telegram": {"enabled": telegram_enabled}},
        }
    )
```

- [ ] **Step 2: Run the test — confirm it fails**

Run: `pytest tests/test_dedup.py::test_matcher_writes_one_match_per_canonical -v`

Expected: FAIL with assertion error on `len(rows) == 1` (the current matcher writes 2 rows, one per scrape).

- [ ] **Step 3: Implement the matcher change**

Modify `src/flatpilot/matcher/runner.py:52-61`. Replace:

```python
    rows = conn.execute(
        """
        SELECT f.*
        FROM flats f
        LEFT JOIN matches m
            ON m.flat_id = f.id AND m.profile_version_hash = ?
        WHERE m.id IS NULL
        """,
        (phash,),
    ).fetchall()
```

with:

```python
    # Only evaluate canonical roots. PR #21 stamps canonical_flat_id on
    # every scrape insert; duplicates always have a non-NULL link, so
    # restricting to IS NULL means each cluster gets exactly one match
    # row (keyed on the oldest row in the cluster).
    rows = conn.execute(
        """
        SELECT f.*
        FROM flats f
        LEFT JOIN matches m
            ON m.flat_id = f.id AND m.profile_version_hash = ?
        WHERE m.id IS NULL
          AND f.canonical_flat_id IS NULL
        """,
        (phash,),
    ).fetchall()
```

- [ ] **Step 4: Run the test — confirm it passes**

Run: `pytest tests/test_dedup.py::test_matcher_writes_one_match_per_canonical -v`

Expected: PASS.

- [ ] **Step 5: Run the full suite — confirm no regressions**

Run: `pytest -q`

Expected: all prior tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/matcher/runner.py tests/test_dedup.py
git commit -m "FlatPilot-k40y: matcher evaluates only canonical roots"
```

---

## Task 2: Notifier canonical-aware dedup

**Files:**
- Modify: `src/flatpilot/notifications/dispatcher.py:134-172`
- Test: `tests/test_dedup.py` (append new tests)

This task exists even though Task 1 already makes steady-state matches canonical-keyed, because:
1. Legacy match rows (from any pre-PR-#21 matcher run) may still exist, keyed on non-root flats.
2. A future `dedup --rebuild` could retroactively link flats that previously had independent match rows, leaving two match rows pointing to the same cluster.

The notifier must send one notification per live canonical per channel, regardless of how many match rows reference that cluster.

- [ ] **Step 1: Write the failing test — two match rows, same canonical, one send**

Append to `tests/test_dedup.py`:

```python
def test_notifier_dedups_by_canonical(tmp_db, monkeypatch):
    """Two match rows for the same canonical cluster → one dispatch call."""
    from flatpilot.cli import _insert_flat
    from flatpilot.notifications import dispatcher

    now = datetime.now(UTC).isoformat()
    _insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    _insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )

    profile = _minimal_profile(telegram_enabled=True)
    phash = "test-hash"

    # Simulate legacy state: two match rows, one per flat_id, both under the same profile hash.
    for flat_id in (1, 2):
        tmp_db.execute(
            "INSERT INTO matches (flat_id, profile_version_hash, decision, "
            "decision_reasons_json, decided_at) VALUES (?, ?, 'match', '[]', ?)",
            (flat_id, phash, now),
        )
    monkeypatch.setattr(dispatcher, "profile_hash", lambda _p: phash)

    calls: list[tuple[str, int]] = []

    def fake_send(channel, flat, _profile):
        calls.append((channel, flat["id"]))

    monkeypatch.setattr(dispatcher, "_send", fake_send)

    summary = dispatcher.dispatch_pending(profile)

    assert summary["sent"] == {"telegram": 1}
    assert len(calls) == 1
    assert calls[0] == ("telegram", 1)  # the canonical root wins
```

- [ ] **Step 2: Run the test — confirm it fails**

Run: `pytest tests/test_dedup.py::test_notifier_dedups_by_canonical -v`

Expected: FAIL — current notifier sends twice (one per match row). The assertion `len(calls) == 1` fails with `len(calls) == 2`.

- [ ] **Step 3: Implement the notifier change**

Modify `src/flatpilot/notifications/dispatcher.py`. Replace the SELECT + loop at lines 134-172 with:

```python
    rows = conn.execute(
        """
        SELECT m.id AS match_id,
               m.notified_channels_json,
               COALESCE(f.canonical_flat_id, f.id) AS canonical_id,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        WHERE m.decision = 'match' AND m.profile_version_hash = ?
        ORDER BY canonical_id, f.id
        """,
        (phash,),
    ).fetchall()

    # Dedup across sibling match rows that reference the same canonical
    # cluster. Earned by two scenarios the matcher's root-only filter
    # can't cover: legacy match rows written before PR #21, and any
    # row churn from a future `dedup --rebuild` that re-clusters already
    # matched flats. For each (canonical_id, channel), we send at most
    # once; later siblings inherit the notified stamp without firing.
    sent_canonicals: dict[int, set[str]] = {}
    sent: dict[str, int] = {}
    failed: dict[str, int] = {}
    processed = 0

    for row in rows:
        flat = dict(row)
        match_id = flat.pop("match_id")
        canonical_id = flat.pop("canonical_id")
        notified = _parse_channels(flat.pop("notified_channels_json", None))
        already_for_canonical = sent_canonicals.setdefault(canonical_id, set())
        effective = notified | already_for_canonical
        pending = [c for c in channels if c not in effective]
        if not pending:
            continue

        processed += 1
        for channel in pending:
            try:
                _send(channel, flat, profile)
            except (telegram_adapter.TelegramError, email_adapter.EmailError) as exc:
                logger.warning("match %d channel %s failed: %s", match_id, channel, exc)
                failed[channel] = failed.get(channel, 0) + 1
                continue
            notified.add(channel)
            already_for_canonical.add(channel)
            sent[channel] = sent.get(channel, 0) + 1

        if notified:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE matches SET notified_channels_json = ?, notified_at = ? WHERE id = ?",
                (json.dumps(sorted(notified)), now, match_id),
            )
```

Key changes vs. current code:
- SELECT adds `COALESCE(f.canonical_flat_id, f.id) AS canonical_id` and an `ORDER BY canonical_id, f.id` so the canonical root (lowest id) is processed first within each cluster.
- `sent_canonicals` tracks which channels already fired for each canonical in this run.
- `effective = notified | already_for_canonical` ensures a sibling inherits the dedup decision.
- On success, both `notified` (persisted) and `already_for_canonical` (run-local) are updated.

- [ ] **Step 4: Run the test — confirm it passes**

Run: `pytest tests/test_dedup.py::test_notifier_dedups_by_canonical -v`

Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`

Expected: all tests pass. In particular the existing `_mark_stale_matches_notified` behavior must still work — it operates on `notified_at`, which we still stamp.

- [ ] **Step 6: Commit**

```bash
git add src/flatpilot/notifications/dispatcher.py tests/test_dedup.py
git commit -m "FlatPilot-k40y: notifier dedups by canonical across sibling matches"
```

---

## Task 3: Integration tests — multi-platform + deleted-canonical

**Files:**
- Test: `tests/test_dedup.py` (append new tests)

Mirror PR #21's coverage: three-platform cluster and deleted-canonical survivor. PR #21 tested the dedup layer; this task tests the matcher+notifier reading through it.

- [ ] **Step 1: Write the three-platform matcher+notifier test**

Append to `tests/test_dedup.py`:

```python
def test_three_platform_cluster_produces_one_match_and_one_notification(tmp_db, monkeypatch):
    """All three platforms → one match row, one notification per channel."""
    from flatpilot.cli import _insert_flat
    from flatpilot.matcher import runner
    from flatpilot.notifications import dispatcher

    profile_obj = _minimal_profile(telegram_enabled=True)
    monkeypatch.setattr(runner, "load_profile", lambda: profile_obj)

    now = datetime.now(UTC).isoformat()
    for ext, plat, rent in (
        ("wg-1", "wg_gesucht", 800.0),
        ("ka-1", "kleinanzeigen", 810.0),
        ("is-1", "immoscout", 820.0),
    ):
        _insert_flat(
            tmp_db,
            {
                "external_id": ext,
                "listing_url": f"https://example.com/{ext}",
                "title": "A",
                "rent_warm_eur": rent,
                "size_sqm": 50.0,
                "rooms": 2.0,
                "address": "Greifswalder Str. 42",
            },
            plat,
            now,
        )

    runner.run_match()
    assert tmp_db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        dispatcher, "_send", lambda c, f, _p: calls.append((c, f["id"]))
    )
    summary = dispatcher.dispatch_pending(profile_obj)
    assert summary["sent"] == {"telegram": 1}
    assert len(calls) == 1
```

- [ ] **Step 2: Write the deleted-canonical test**

Append to `tests/test_dedup.py`:

```python
def test_deleted_canonical_releases_survivor_for_fresh_matching(tmp_db, monkeypatch):
    """When the canonical is deleted, the surviving duplicate becomes a root
    and gets its own match — "one ping per *live* canonical" semantics."""
    from flatpilot.cli import _insert_flat
    from flatpilot.matcher import runner

    profile = _minimal_profile()
    monkeypatch.setattr(runner, "load_profile", lambda: profile)

    now = datetime.now(UTC).isoformat()
    _insert_flat(
        tmp_db,
        {
            "external_id": "wg-1",
            "listing_url": "https://wg-gesucht.de/1",
            "title": "A",
            "rent_warm_eur": 800.0,
            "size_sqm": 50.0,
            "address": "Greifswalder Str. 42",
        },
        "wg_gesucht",
        now,
    )
    _insert_flat(
        tmp_db,
        {
            "external_id": "ka-1",
            "listing_url": "https://kleinanzeigen.de/1",
            "title": "B",
            "rent_warm_eur": 810.0,
            "size_sqm": 51.0,
            "address": "10435 Berlin, Greifswalder Straße 42",
        },
        "kleinanzeigen",
        now,
    )

    # First pass: matcher writes one match for the root (flat id 1).
    runner.run_match()
    assert tmp_db.execute(
        "SELECT flat_id FROM matches"
    ).fetchone()["flat_id"] == 1

    # Delete the canonical — ON DELETE SET NULL releases the duplicate.
    tmp_db.execute("DELETE FROM flats WHERE id = 1")
    row = tmp_db.execute(
        "SELECT canonical_flat_id FROM flats WHERE id = 2"
    ).fetchone()
    assert row["canonical_flat_id"] is None

    # Second pass: the survivor is now a root and gets its own match.
    runner.run_match()
    rows = tmp_db.execute(
        "SELECT flat_id FROM matches ORDER BY flat_id"
    ).fetchall()
    assert [r["flat_id"] for r in rows] == [2]  # old row cascaded away
```

- [ ] **Step 3: Run the new tests**

Run: `pytest tests/test_dedup.py::test_three_platform_cluster_produces_one_match_and_one_notification tests/test_dedup.py::test_deleted_canonical_releases_survivor_for_fresh_matching -v`

Expected: both PASS. These exercise code from Tasks 1 and 2 — no new implementation required.

- [ ] **Step 4: Run the full suite + lint**

```bash
pytest -q
ruff check src tests
```

Expected: all tests pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_dedup.py
git commit -m "FlatPilot-k40y: integration tests for three-platform + deleted-canonical"
```

---

## Task 4: Close the bead, push the branch, open the PR

- [ ] **Step 1: Close the bead**

```bash
bd close FlatPilot-k40y
```

Expected: bead transitions to closed. If the whole I-bis epic is now complete, also close FlatPilot-12bj:

```bash
bd list --status=open | grep -i "i-bis"
# if only the epic FlatPilot-12bj remains:
bd close FlatPilot-12bj --reason="All I-bis tasks complete — dedup stamped on scrape (0ktm), matcher+notifier read canonical (k40y)"
```

- [ ] **Step 2: Commit the bead state change**

```bash
git add .beads/
git commit -m "FlatPilot-k40y: close bead after PR opened"
```

(Write this commit *after* the PR is opened if your beads workflow prefers the close-after-merge pattern; check the previous merge's history with `git log --oneline -10` if unsure.)

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/i-bis-2-matcher-canonical
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --base main --head feat/i-bis-2-matcher-canonical --title "FlatPilot-k40y: matcher + notifier keyed on canonical flat" --body "$(cat <<'EOF'
## Summary
- Matcher now evaluates only canonical roots (`WHERE canonical_flat_id IS NULL`) — one match row per cluster.
- Notifier resolves each match to its canonical id and dedupes sibling rows inside a run, covering both legacy match rows and any future `dedup --rebuild` churn.
- "One ping per live canonical" semantics: if a canonical listing is deleted, the surviving duplicate becomes a root and is matched fresh on the next run.

## Test Plan
- [x] `pytest tests/test_dedup.py -v` — new tests cover two-platform twin, three-platform cluster, legacy-sibling dedup, and deleted-canonical survivor
- [x] `pytest -q` — full suite passes
- [x] `ruff check src tests` — clean

Closes FlatPilot-k40y. Completes the I-bis cross-platform dedup epic (FlatPilot-12bj).
EOF
)"
```

- [ ] **Step 5: Report the PR URL**

Print the PR URL from step 4's output. Stop. Do not merge — the human reviews and merges.

---

## Self-Review Checklist

**Spec coverage:**
- [x] Matcher writes one match per canonical → Task 1
- [x] Notifier keyed on canonical → Task 2
- [x] Cross-platform duplicates trigger only one ping → Tasks 1 + 2 (steady state) + Task 2 (legacy defense)
- [x] Deleted-canonical semantics documented and tested → Task 3

**Placeholders:** None. Every code block is complete.

**Type consistency:**
- `canonical_id` is used consistently (not `canonical_flat_id`) as the SELECT alias in the notifier query.
- `sent_canonicals: dict[int, set[str]]` — annotation is consistent with its usage as `set[channel]` keyed by canonical flat id.
- `Profile` construction in `_minimal_profile()` must match the current pydantic model — the plan flags this with a one-line verification step before the test is committed.

**Divergence from earlier spec documented:** Yes — the top of the plan notes that we use a WHERE filter rather than the `decision='skipped'` approach proposed in `docs/superpowers/specs/2026-04-24-flat-dedup-design.md` §"Forward compatibility with FlatPilot-k40y", with the reason (UNIQUE-constraint trap on deleted canonical).
