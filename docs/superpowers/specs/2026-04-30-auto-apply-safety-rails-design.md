# Phase 4 — Opt-in Auto-Apply with Safety Rails

**Beads:** FlatPilot-jzp2 (N1), FlatPilot-g2a4 (N2), FlatPilot-5f4i (N3),
FlatPilot-y4ws (N4), FlatPilot-l4dq (N5). Epic: FlatPilot-f2fa.

**Date:** 2026-04-30. **Status:** Approved by user; pending sign-off on
spec before writing-plans.

---

## 1. Goal

Ship the first end-to-end opt-in auto-apply path. A user defines saved
searches in their profile; whenever the pipeline finds a fresh flat that
matches a saved search with `auto_apply: true`, FlatPilot submits the
contact form, gated by four safety rails (daily cap, cooldown,
completeness, global PAUSE). One PR, ~600–800 LOC including tests.

---

## 2. Non-goals

- Per-saved-search notifications (notifications still route via base
  profile).
- Per-saved-search caps/cooldowns (per-platform aggregate only).
- Cross-machine cap enforcement.
- Within-day pacing beyond `cap × cooldown`.
- Backoff after N failed auto-applies (follow-up bead).
- Dashboard saved-search filter dropdown (column lands; UI deferred).
- Wizard support for multiple saved searches or cap tuning (single y/N
  prompt only).
- Integration tests with time-travel (covered by FlatPilot-dwao / N6).

---

## 3. User-visible surface

### 3.1 Profile schema additions

```python
class Profile(BaseModel):
    ...
    auto_apply: AutoApplySettings = Field(default_factory=AutoApplySettings)
    saved_searches: list[SavedSearch] = Field(default_factory=list)
```

```python
class AutoApplySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_cap_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 20, "kleinanzeigen": 20, "inberlinwohnen": 20,
        }
    )
    cooldown_seconds_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 120, "kleinanzeigen": 120, "inberlinwohnen": 120,
        }
    )
```

A platform key absent from `daily_cap_per_platform` means cap=0 (fail-safe:
forgetting an entry disables auto-apply for that platform).

```python
class SavedSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    auto_apply: bool = False

    rent_min_warm: int | None = Field(default=None, ge=0)
    rent_max_warm: int | None = Field(default=None, ge=0)
    rooms_min: int | None = Field(default=None, ge=1)
    rooms_max: int | None = Field(default=None, ge=1)
    district_allowlist: list[str] | None = None
    radius_km: int | None = Field(default=None, ge=0, le=500)
    furnished_pref: FurnishedPref | None = None
    min_contract_months: int | None = Field(default=None, ge=0)

    platforms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> SavedSearch:
        if self.rent_min_warm is not None and self.rent_max_warm is not None \
                and self.rent_max_warm < self.rent_min_warm:
            raise ValueError("rent_max_warm must be >= rent_min_warm")
        if self.rooms_min is not None and self.rooms_max is not None \
                and self.rooms_max < self.rooms_min:
            raise ValueError("rooms_max must be >= rooms_min")
        return self
```

Overlay rules: scalar `None` = inherit from base profile. List fields:
`None` = inherit, `[]` = override-to-empty (e.g.
`district_allowlist=[]` removes the base-profile district restriction).

`platforms` is the one exception — it's a plain `list[str]`, never None.
`platforms=[]` means "all registered platforms"; a non-empty list is an
allowlist. (Reviewer flagged the tristate as confusing; collapsed to
two states.)

`Profile` gains a top-level validator enforcing unique
`saved_searches[*].name`.

Personal/identity fields (`net_income_eur`, `status`, `household_size`,
`kids`, `pets`, `wbs`, `move_in_date`, `smoker`) are never overlaid.

### 3.2 Database additions

Forward-migration columns via the `COLUMNS` dict in
`flatpilot/database.py`:

```python
COLUMNS["matches"] = {
    "matched_saved_searches_json": "TEXT NOT NULL DEFAULT '[]'",
}
COLUMNS["applications"] = {
    "triggered_by_saved_search": "TEXT",
}
```

Plus a covering index on `applications` for the cap/cooldown queries
(reviewer flagged: those queries would table-scan otherwise):

```python
CREATE INDEX IF NOT EXISTS idx_applications_method_applied_at
    ON applications(method, applied_at)
```

The index is added in `flatpilot/schemas.py` alongside the table SQL.

### 3.3 New CLI commands

```text
flatpilot pause                # touch ~/.flatpilot/PAUSE; idempotent
flatpilot resume               # rm -f ~/.flatpilot/PAUSE; idempotent
flatpilot run --dry-run-apply  # log what would have been auto-applied
flatpilot run --skip-apply     # run scrape/match/notify; skip apply stage
```

With default `saved_searches: []`, the apply stage is a no-op.

### 3.4 Doctor changes

Five new rows on `flatpilot doctor`:

```text
Auto-apply: PAUSE switch       OK         not paused
Auto-apply: saved searches     OK         2 active (kreuzberg-2br, spandau-cheap)
Auto-apply: wg-gesucht         OK         17/20 used today, ready in 0s
Auto-apply: kleinanzeigen      OK          8/20 used today, ready in 47s
Auto-apply: inberlinwohnen     OK          3/20 used today, ready in 0s
```

PAUSE active flips the first row to yellow `PAUSED`. Auto-apply rows
never affect doctor's exit code.

### 3.5 Dashboard changes

Applied tab gains an `auto`/`manual` badge per row. For
`status='failed'` auto rows, the `notes` field is rendered prominently
so the user sees the reason inline. Saved-search filter dropdown is
deferred.

---

## 4. Architecture

### 4.1 Pipeline stages

```
scrape → match → apply → notify
```

`apply` is a new stage between match and notify and the only auto-apply
trigger. `flatpilot apply <flat_id>` and the dashboard's
`POST /api/applications` continue to call `apply_to_flat` directly with
`method='manual'`, bypassing all gates. The matcher stays pure (CLAUDE.md
mandates ≤150 LOC, deterministic, no side effects).

### 4.2 Data flow

```
matcher writes:
  matches(flat_id, profile_hash, decision, decision_reasons_json,
          decided_at, matched_saved_searches_json)

apply stage queries:
  SELECT m.*, f.* FROM matches m
  JOIN flats f ON f.id = m.flat_id
  WHERE m.decision = 'match'
    AND m.profile_version_hash = ?       -- current profile hash
    AND m.matched_saved_searches_json != '[]'
    AND NOT EXISTS (
      SELECT 1 FROM applications a
      WHERE a.flat_id = m.flat_id
        AND a.method = 'auto'
        AND a.status = 'submitted'
    )
  ORDER BY m.decided_at ASC

For each candidate row:
  1. PAUSE active → log "auto-apply: PAUSED"; return from stage
     (PAUSE is global; do not advance to next flat).
  2. For each saved-search name in matched_saved_searches_json,
     iterating in profile order:
     a. saved_search.auto_apply == false              → next saved search
     b. flat.platform not in saved_search.platforms
        (when platforms is non-empty)                 → next saved search
     c. daily cap reached on flat.platform            → next FLAT
        (cap is platform-aggregate; another saved
         search would hit the same cap)
     d. cooldown active on flat.platform              → next FLAT
     e. completeness_ok fails                         → write applications
        row (status='failed', method='auto',
        notes='auto_skipped: <reason>',
        triggered_by_saved_search=name); next FLAT
     f. all gates pass → apply_to_flat(method='auto', saved_search=name);
        on success the row is written internally;
        on FillError the failed row is written internally;
        next flat.

A flat is auto-applied at most once per stage invocation. The first
auto_apply=true saved search whose per-search gates pass fires and is
recorded in triggered_by_saved_search.
```

### 4.3 Saved-search semantics (matcher side)

The matcher evaluates each flat against `[None] + profile.saved_searches`,
where `None` is "base profile, no overlay." Decision logic:

- `decision = 'match'` if any filter set accepts.
- `decision = 'reject'` if all filter sets reject.
- `decision_reasons_json` carries base profile's reject reasons (`[]` if
  base accepted). Saved-search-only reject reasons are not surfaced —
  known limitation flagged by reviewer; the dashboard's rejection pane
  stays semantically clean.
- `matched_saved_searches_json` carries names of saved searches that
  accepted the flat (excluding base).

`profile_hash` already covers new fields (it hashes
`profile.model_dump_json()`), so editing saved searches re-keys match
rows automatically.

Notifications fire on `decision='match'`, unchanged.

### 4.4 New module: `flatpilot/auto_apply.py`

~250 LOC. Public surface:

```python
PAUSE_PATH: Path = APP_DIR / "PAUSE"

def is_paused() -> bool: ...

def daily_cap_remaining(conn, profile, platform) -> int:
    """Counts only status='submitted' AND method='auto' rows since UTC midnight."""

def cooldown_remaining_sec(conn, profile, platform) -> float:
    """MAX(applied_at) over (status='submitted' OR
    (status='failed' AND notes NOT LIKE 'auto_skipped:%'))
    AND method='auto'. Failures throttle (real platform contact);
    auto_skipped rows do not (never reached the platform)."""

def completeness_ok(profile, flat) -> tuple[bool, str | None]:
    """Checks: filler is registered for flat['platform'], compose_anschreiben
    succeeds, resolve_for_platform succeeds. Returns (False, reason) on
    first failure. Profile must be loaded; ProfileMissingError is the
    caller's concern."""

def effective_filters(profile, saved_search) -> dict: ...

def run_pipeline_apply(profile, console, *, dry_run: bool = False) -> None:
    """Idempotent: re-running on the same DB state is a no-op for
    already-submitted flats."""
```

Cookie/session expiry is not part of completeness — it surfaces as
FillError during submit, gets the existing `status='failed'` row
treatment, and retries next pass.

### 4.5 `apply_to_flat` signature change

```python
def apply_to_flat(
    flat_id: int,
    *,
    dry_run: bool = False,
    screenshot_dir: Path | None = None,
    method: Literal["manual", "auto"] = "manual",
    saved_search: str | None = None,
) -> ApplyOutcome: ...
```

`_record_application` writes `method` and
`triggered_by_saved_search=saved_search`. Existing callers unaffected.

### 4.6 Wizard changes

A single new prompt at the end of `wizard/init.py`. **Re-run handling:**
if a saved search named `auto-default` already exists in the profile
being edited, the wizard skips the prompt entirely (the user has either
already opted in or has customized that slot).

```text
Enable auto-apply? [y/N]: y
A starter saved search 'auto-default' has been created with
auto_apply=true. It mirrors your profile filters exactly. Edit
~/.flatpilot/profile.json to add more saved searches, or run
`flatpilot pause` to disable temporarily.
```

On `y`: append a `SavedSearch(name='auto-default', auto_apply=True)`
with all overlay fields `None` and `platforms=[]` (= all platforms).
On `N`/default: no saved search created.

### 4.7 Idempotency and retry semantics

| Outcome | Row written? | Retried next pass? |
|---|---|---|
| Submit succeeds | applications, submitted, auto | No |
| Submit fails (FillError) | applications, failed, auto | Yes |
| Completeness fails | applications, failed, auto, `notes='auto_skipped:...'` | Yes |
| PAUSE active | None | Yes |
| Daily cap reached | None | Yes (UTC midnight) |
| Cooldown active | None | Yes (after expiry) |

Permanent skips retry on purpose: user fixes the missing attachment, the
next pass auto-resolves.

---

## 5. Failure modes and edge cases

- **Empty `saved_searches`**: query returns zero rows; stage exits in
  <10ms. No behavior change for existing users.
- **PAUSE present + auto_apply=true everywhere**: stage logs PAUSED and
  returns. Manual `flatpilot apply` keeps working (E1 from
  brainstorming).
- **`platforms=["wg-gesucht"]` on a saved search**: only flats from that
  platform are auto-apply candidates for this search. Notifications
  still fire on its matches across all platforms.
- **Two auto_apply=true saved searches both match a flat**: profile
  order wins; `triggered_by_saved_search` records the winner.
- **Saved-search name collides on save**: pydantic top-level validator
  raises `ValidationError`; surfaced by `doctor._safe_load_profile`.
- **DST / cap window**: `applied_at >= start_of_today_utc.isoformat()`.
  UTC, not local — consistent across timezone changes.
- **Cap counts only `status='submitted'`** (real successes burn quota).
  Cooldown counts `submitted` plus `failed` rows that aren't
  auto-skipped (FillError throttles real platform contact).
  `auto_skipped` rows never touched the platform and burn neither.
- **Flapping FillError on the same flat**: cooldown gates retry rate per
  platform (every 120s by default), not per flat. `apply_locks`
  prevents parallel attempts. N-strikes-out backoff is §8.
- **Profile changes mid-run**: matcher writes new rows under a new
  `profile_hash`; apply stage filters on `profile_version_hash =
  current_hash`, so stale matches are not auto-applied under fresh
  filters.
- **Re-running `flatpilot init`**: wizard skips the auto-apply prompt
  if `auto-default` saved search exists (§4.6).

---

## 6. Testing

Unit tests in this PR. Integration with time-travel = FlatPilot-dwao
(N6, separate bead).

- `test_saved_search_schema.py` — field validation, name regex, range
  validators, unique-name validator, overlay defaults round-trip.
- `test_auto_apply_settings.py` — defaults, `extra=forbid` rejects
  unknown keys, missing platform key returns 0 from gates.
- `test_effective_filters.py` — `None` overlay returns base profile,
  scalar overrides apply, list `None` inherits, list `[]`
  override-to-empty.
- `test_matcher_saved_searches.py` — `matched_saved_searches_json` is
  written correctly; base-rejected + saved-matched flat resolves to
  `decision='match'`.
- `test_auto_apply_gates.py` — `is_paused`, `daily_cap_remaining`,
  `cooldown_remaining_sec`, `completeness_ok` (including
  filler-registry check) against seeded DB and fixture profile.
- `test_run_pipeline_apply.py` — full stage with `apply_to_flat`
  monkey-patched. Verifies gate ordering, idempotency, dry-run writes
  no rows, skip-apply skips the stage, `triggered_by_saved_search`
  populated.
- `test_apply_to_flat_method.py` — new params write
  `method='auto'` and `triggered_by_saved_search` to the row.
- `test_pause_resume_cli.py` — idempotent touch / unlink.
- `test_doctor_auto_apply.py` — five new rows render under paused,
  capped, cooldown-active, fresh states.

---

## 7. File-by-file change summary

```
src/flatpilot/profile.py            + SavedSearch, AutoApplySettings,
                                     Profile.{auto_apply, saved_searches},
                                     unique-name validator
src/flatpilot/schemas.py            + COLUMNS["matches"], COLUMNS["applications"];
                                     idx_applications_method_applied_at
src/flatpilot/auto_apply.py         NEW ~250 LOC
src/flatpilot/matcher/runner.py     evaluate against [None] + saved_searches;
                                     write matched_saved_searches_json
src/flatpilot/matcher/filters.py    accept dict-of-filters input (overlay)
src/flatpilot/apply.py              + method, saved_search params;
                                     _record_application uses them
src/flatpilot/pipeline.py           + run_pipeline_apply between match & notify;
                                     --dry-run-apply / --skip-apply plumbing
src/flatpilot/cli.py                + pause, resume, run flag plumbing
src/flatpilot/wizard/init.py        + final auto-apply y/N prompt with
                                     re-run skip
src/flatpilot/doctor.py             + 5 new rows
src/flatpilot/view.py               + auto/manual badge; failed-row notes
src/flatpilot/profile.example.json  + auto_apply, saved_searches example
tests/                              + 9 new test modules (per §6)
```

---

## 8. Out-of-scope follow-ups

- Dashboard saved-search filter dropdown.
- Wizard support for multiple saved searches and cap tuning.
- Per-saved-search notification routing.
- Backoff after N failed auto-applies on the same flat.
- Within-day pacing.
- N6 integration tests (FlatPilot-dwao).
