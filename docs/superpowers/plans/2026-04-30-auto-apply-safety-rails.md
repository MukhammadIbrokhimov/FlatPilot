# Auto-Apply Safety Rails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 4 opt-in auto-apply with four safety rails (PAUSE, daily cap, cooldown, completeness) as a single feature on the existing `docs/auto-apply-safety-rails-spec` branch.

**Architecture:** New pipeline stage `apply` between `match` and `notify`. Saved searches are filter overlays on the base profile (C1 semantic — gates auto-apply only). Caps/cooldowns are per-platform aggregate, configured in profile JSON. PAUSE is a global file-presence switch under `~/.flatpilot/`. All deterministic Python; no LLM calls.

**Tech Stack:** Python 3.11+, pydantic v2, SQLite (forward migrations), pytest, typer, rich.

**Spec:** `docs/superpowers/specs/2026-04-30-auto-apply-safety-rails-design.md` — read first.

**Commits:** Per user preference, **one commit at the very end** (Task 17). Earlier tasks accumulate work locally without committing. The branch already has the spec commit from earlier; the final commit adds the implementation + this plan in one logical unit.

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `src/flatpilot/profile.py` | modify | Add `SavedSearch`, `AutoApplySettings` models, two new `Profile` fields, unique-name validator |
| `src/flatpilot/schemas.py` | modify | Add two `COLUMNS` entries + index for cap/cooldown queries |
| `src/flatpilot/auto_apply.py` | **create** | All auto-apply logic: PAUSE, gates, overlay, pipeline stage |
| `src/flatpilot/matcher/runner.py` | modify | Iterate `[None] + saved_searches`; write `matched_saved_searches_json` |
| `src/flatpilot/apply.py` | modify | `method` + `saved_search` params on `apply_to_flat`; `_record_application` honors them |
| `src/flatpilot/pipeline.py` | modify | Wire `run_pipeline_apply` between match and notify; `--dry-run-apply` / `--skip-apply` plumbing |
| `src/flatpilot/cli.py` | modify | `pause`, `resume` commands; `run` flag plumbing |
| `src/flatpilot/wizard/init.py` | modify | y/N auto-apply prompt with re-run skip |
| `src/flatpilot/doctor.py` | modify | Five new rows (PAUSE, saved-search count, three per-platform burns) |
| `src/flatpilot/view.py` | modify | `auto`/`manual` badge; failed-row notes prominent display |
| `src/flatpilot/profile.example.json` | modify | Demonstrate `auto_apply` block + sample saved search |
| `tests/test_saved_search_schema.py` | **create** | Pydantic field validation, name regex, range validators, unique name |
| `tests/test_auto_apply_settings.py` | **create** | Defaults, `extra=forbid`, missing-platform-key behavior |
| `tests/test_overlay_profile.py` | **create** | None overlay, scalar overrides, list semantics |
| `tests/test_matcher_saved_searches.py` | **create** | `matched_saved_searches_json` written correctly; widening saved search produces match |
| `tests/test_auto_apply_gates.py` | **create** | `is_paused`, `daily_cap_remaining`, `cooldown_remaining_sec`, `completeness_ok` |
| `tests/test_apply_to_flat_method.py` | **create** | New params write `method='auto'` and `triggered_by_saved_search` |
| `tests/test_run_pipeline_apply.py` | **create** | Full apply stage: gate ordering, idempotency, dry-run, FillError handling |
| `tests/test_pause_resume_cli.py` | **create** | Idempotent touch / unlink |
| `tests/test_wizard_auto_apply.py` | **create** | y → starter saved search; N → no change; re-run with existing auto-default skips |
| `tests/test_doctor_auto_apply.py` | **create** | Five rows render under paused/capped/cooldown/fresh states |
| `tests/test_view_auto_apply.py` | **create** | Badge + failed-row notes display |

---

## Test Discipline

Every task uses TDD: write the failing test, run it, watch it fail with the specific error, write minimal implementation, run again, watch it pass. **Do not commit during tasks** — the single commit happens in Task 15.

**Test fixtures:**
- The shared `tmp_db` fixture in `tests/conftest.py` is the ONLY fixture pattern these tests use. It patches every path-binding site (`config.APP_DIR`, `database.DB_PATH`, `compose.TEMPLATES_DIR`, etc.), runs `init_db()`, and yields a connection. Do not roll your own `isolated_app` fixture — `flatpilot.database.DB_PATH` is bound by name at import time and a naive `monkeypatch.setenv("FLATPILOT_DIR", ...)` plus `importlib.reload(config)` will leave it stale, making tests flaky.
- Task 6 adds one extra `monkeypatch.setattr(auto_apply, "PAUSE_PATH", ...)` line to `tmp_db` so PAUSE-aware tests can rely on the same fixture.
- An example `Profile` is available via `Profile.load_example()`.
- Run a single test with `pytest tests/test_X.py::test_Y -v`.

---

## Task 1: SavedSearch and AutoApplySettings pydantic models

**Files:**
- Modify: `src/flatpilot/profile.py`
- Test: `tests/test_saved_search_schema.py`, `tests/test_auto_apply_settings.py`

- [ ] **Step 1: Write failing tests for `SavedSearch`**

Create `tests/test_saved_search_schema.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from flatpilot.profile import SavedSearch


def test_minimal_saved_search_loads():
    ss = SavedSearch(name="kreuzberg-2br")
    assert ss.name == "kreuzberg-2br"
    assert ss.auto_apply is False
    assert ss.platforms == []
    assert ss.rent_max_warm is None


def test_name_regex_rejects_uppercase():
    with pytest.raises(ValidationError):
        SavedSearch(name="Kreuzberg")


def test_name_regex_rejects_spaces():
    with pytest.raises(ValidationError):
        SavedSearch(name="kreuzberg 2br")


def test_name_regex_accepts_underscore_hyphen_digits():
    SavedSearch(name="my_search-1")


def test_rent_range_validator():
    with pytest.raises(ValidationError):
        SavedSearch(name="x", rent_min_warm=1500, rent_max_warm=1000)


def test_rooms_range_validator():
    with pytest.raises(ValidationError):
        SavedSearch(name="x", rooms_min=3, rooms_max=2)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        SavedSearch(name="x", unknown_field=42)


def test_overlay_fields_default_to_none():
    ss = SavedSearch(name="x")
    for field in (
        "rent_min_warm", "rent_max_warm", "rooms_min", "rooms_max",
        "district_allowlist", "radius_km", "furnished_pref",
        "min_contract_months",
    ):
        assert getattr(ss, field) is None, field


def test_platforms_defaults_to_empty_list_not_none():
    ss = SavedSearch(name="x")
    assert ss.platforms == []
```

Create `tests/test_auto_apply_settings.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from flatpilot.profile import AutoApplySettings


def test_defaults_present():
    s = AutoApplySettings()
    assert s.daily_cap_per_platform == {
        "wg-gesucht": 20, "kleinanzeigen": 20, "inberlinwohnen": 20,
    }
    assert s.cooldown_seconds_per_platform == {
        "wg-gesucht": 120, "kleinanzeigen": 120, "inberlinwohnen": 120,
    }


def test_extra_forbidden():
    with pytest.raises(ValidationError):
        AutoApplySettings(unknown=1)


def test_user_override_replaces_defaults_completely():
    s = AutoApplySettings(daily_cap_per_platform={"wg-gesucht": 50})
    assert s.daily_cap_per_platform == {"wg-gesucht": 50}
```

- [ ] **Step 2: Run tests, watch them fail**

Run: `pytest tests/test_saved_search_schema.py tests/test_auto_apply_settings.py -v`

Expected: ImportError on `SavedSearch` and `AutoApplySettings` not found.

- [ ] **Step 3: Add the models to `profile.py`**

In `src/flatpilot/profile.py`, before the `Profile` class definition:

```python
class AutoApplySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_cap_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 20,
            "kleinanzeigen": 20,
            "inberlinwohnen": 20,
        }
    )
    cooldown_seconds_per_platform: dict[str, int] = Field(
        default_factory=lambda: {
            "wg-gesucht": 120,
            "kleinanzeigen": 120,
            "inberlinwohnen": 120,
        }
    )


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
        if (
            self.rent_min_warm is not None
            and self.rent_max_warm is not None
            and self.rent_max_warm < self.rent_min_warm
        ):
            raise ValueError("rent_max_warm must be >= rent_min_warm")
        if (
            self.rooms_min is not None
            and self.rooms_max is not None
            and self.rooms_max < self.rooms_min
        ):
            raise ValueError("rooms_max must be >= rooms_min")
        return self
```

- [ ] **Step 4: Run tests, watch them pass**

Run: `pytest tests/test_saved_search_schema.py tests/test_auto_apply_settings.py -v`

Expected: all tests pass.

---

## Task 2: Wire AutoApplySettings + saved_searches into Profile, with unique-name validator

**Files:**
- Modify: `src/flatpilot/profile.py`
- Test: `tests/test_saved_search_schema.py` (extend)

- [ ] **Step 1: Write failing tests for Profile-level integration**

Append to `tests/test_saved_search_schema.py`:

```python
from flatpilot.profile import Profile


def test_profile_loads_with_default_auto_apply_block():
    p = Profile.load_example()
    assert p.auto_apply.daily_cap_per_platform["wg-gesucht"] == 20
    assert p.saved_searches == []


def test_profile_accepts_saved_searches(tmp_path):
    profile_dict = Profile.load_example().model_dump(mode="json")
    profile_dict["saved_searches"] = [
        {"name": "ss1", "auto_apply": True, "rent_max_warm": 1200},
        {"name": "ss2", "auto_apply": False},
    ]
    p = Profile.model_validate(profile_dict)
    assert len(p.saved_searches) == 2
    assert p.saved_searches[0].auto_apply is True
    assert p.saved_searches[1].rent_max_warm is None


def test_profile_rejects_duplicate_saved_search_names():
    profile_dict = Profile.load_example().model_dump(mode="json")
    profile_dict["saved_searches"] = [
        {"name": "dup", "auto_apply": True},
        {"name": "dup", "auto_apply": False},
    ]
    with pytest.raises(ValidationError, match="duplicate"):
        Profile.model_validate(profile_dict)


def test_profile_hash_changes_when_saved_searches_added():
    from flatpilot.profile import profile_hash

    base = Profile.load_example()
    h_before = profile_hash(base)

    with_ss = base.model_copy(
        update={"saved_searches": [SavedSearch(name="x", auto_apply=True)]}
    )
    h_after = profile_hash(with_ss)
    assert h_before != h_after
```

- [ ] **Step 2: Run tests, watch them fail**

Expected: AttributeError on `auto_apply` / `saved_searches`, then ValidationError mismatch on the dup test.

- [ ] **Step 3: Add fields to Profile + unique-name validator**

In `src/flatpilot/profile.py`, inside the `Profile` class, add (after the `attachments` field):

```python
    auto_apply: AutoApplySettings = Field(default_factory=AutoApplySettings)
    saved_searches: list[SavedSearch] = Field(default_factory=list)
```

Add a top-level validator next to the existing `_ranges_are_ordered`:

```python
    @model_validator(mode="after")
    def _saved_search_names_unique(self) -> Profile:
        names = [ss.name for ss in self.saved_searches]
        if len(names) != len(set(names)):
            raise ValueError(
                f"duplicate saved-search names: {names}"
            )
        return self
```

- [ ] **Step 4: Run tests, watch them pass**

Run: `pytest tests/test_saved_search_schema.py -v`

Expected: all tests pass.

---

## Task 3: DB column migrations + cap/cooldown index

**Files:**
- Modify: `src/flatpilot/schemas.py`, `src/flatpilot/database.py` (verify only)
- Test: `tests/test_auto_apply_settings.py` (extend with one schema test)

- [ ] **Step 1: Write failing test for new columns**

Append to `tests/test_auto_apply_settings.py` (use the `tmp_db` fixture — the broken `monkeypatch.setenv("FLATPILOT_DIR", …)` pattern does NOT redirect because `database.DB_PATH` is bound at import time):

```python
def test_matches_table_has_matched_saved_searches_json(tmp_db):
    cols = {row["name"] for row in tmp_db.execute("PRAGMA table_info(matches)")}
    assert "matched_saved_searches_json" in cols


def test_applications_table_has_triggered_by_saved_search(tmp_db):
    cols = {row["name"] for row in tmp_db.execute("PRAGMA table_info(applications)")}
    assert "triggered_by_saved_search" in cols


def test_applications_index_present(tmp_db):
    indices = {
        row["name"]
        for row in tmp_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_applications_method_applied_at" in indices
```

- [ ] **Step 2: Run tests, watch them fail**

Run: `pytest tests/test_auto_apply_settings.py::test_matches_table_has_matched_saved_searches_json -v`

Expected: assertion failure — column not present.

- [ ] **Step 3: Add migrations to `schemas.py`**

In `src/flatpilot/schemas.py`:

1. At the top, add `COLUMNS` to the existing `SCHEMAS` import:

```python
from flatpilot.database import COLUMNS, SCHEMAS
```

2. After the existing `SCHEMAS["apply_locks"] = ...` line, add:

```python
COLUMNS["matches"] = {
    "matched_saved_searches_json": "TEXT NOT NULL DEFAULT '[]'",
}
COLUMNS["applications"] = {
    "triggered_by_saved_search": "TEXT",
}
```

For the index, add to the same file:

```python
APPLICATIONS_METHOD_APPLIED_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_applications_method_applied_at
    ON applications(method, applied_at)
"""

SCHEMAS["idx_applications_method_applied_at"] = APPLICATIONS_METHOD_APPLIED_AT_INDEX_SQL
```

(The existing `init_db` loop runs every value in `SCHEMAS` as `conn.execute(create_sql)` — `CREATE INDEX IF NOT EXISTS` is fine in that loop.)

- [ ] **Step 4: Run tests, watch them pass**

Run: `pytest tests/test_auto_apply_settings.py -v`

Expected: all three new tests pass.

---

## Task 4: Filter overlay helper

**Files:**
- Create: `src/flatpilot/auto_apply.py`
- Test: `tests/test_overlay_profile.py`

> Spec §4.4 lists `effective_filters(profile, saved_search) -> dict` in the public surface. We implement `overlay_profile(profile, saved_search) -> Profile` instead — same role, but returns a `Profile` so the existing filter functions (which take `(flat, profile)`) keep working unchanged. Treat this as the spec's `effective_filters`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_overlay_profile.py`:

```python
from __future__ import annotations

from flatpilot.auto_apply import overlay_profile
from flatpilot.profile import Profile, SavedSearch


def _base():
    return Profile.load_example()


def test_none_overlay_returns_base_profile():
    base = _base()
    result = overlay_profile(base, None)
    assert result.rent_max_warm == base.rent_max_warm
    assert result.district_allowlist == base.district_allowlist


def test_scalar_overrides_apply():
    base = _base()
    ss = SavedSearch(name="x", rent_max_warm=999, rooms_min=1)
    result = overlay_profile(base, ss)
    assert result.rent_max_warm == 999
    assert result.rooms_min == 1
    # Non-overridden fields keep base values
    assert result.rent_min_warm == base.rent_min_warm


def test_district_list_none_inherits():
    base = _base().model_copy(update={"district_allowlist": ["Mitte"]})
    ss = SavedSearch(name="x")  # district_allowlist=None
    result = overlay_profile(base, ss)
    assert result.district_allowlist == ["Mitte"]


def test_district_list_empty_overrides_to_empty():
    base = _base().model_copy(update={"district_allowlist": ["Mitte"]})
    ss = SavedSearch(name="x", district_allowlist=[])
    result = overlay_profile(base, ss)
    assert result.district_allowlist == []


def test_overlay_does_not_mutate_inputs():
    base = _base()
    base_rent = base.rent_max_warm
    ss = SavedSearch(name="x", rent_max_warm=base_rent + 500)
    _ = overlay_profile(base, ss)
    assert base.rent_max_warm == base_rent  # base untouched
```

- [ ] **Step 2: Run tests, watch them fail**

Expected: ImportError on `flatpilot.auto_apply`.

- [ ] **Step 3: Create `auto_apply.py` with `overlay_profile`**

Create `src/flatpilot/auto_apply.py`:

```python
"""Phase 4 — opt-in auto-apply with safety rails.

Saved searches overlay the base profile to gate auto-apply. PAUSE,
daily cap, cooldown, and completeness gates run before each filler call.
The pipeline stage `run_pipeline_apply` is the only auto-apply trigger.
"""

from __future__ import annotations

from flatpilot.profile import Profile, SavedSearch


_OVERLAY_FIELDS_SCALAR = (
    "rent_min_warm",
    "rent_max_warm",
    "rooms_min",
    "rooms_max",
    "radius_km",
    "furnished_pref",
    "min_contract_months",
)


def overlay_profile(profile: Profile, saved_search: SavedSearch | None) -> Profile:
    """Return a Profile with saved_search fields overlaid on profile.

    None overlay returns profile unchanged. Scalar fields use saved_search
    value when not None; list fields use saved_search value when not None
    (an empty list overrides). Personal/identity fields are never touched.
    """
    if saved_search is None:
        return profile
    updates: dict[str, object] = {}
    for field in _OVERLAY_FIELDS_SCALAR:
        v = getattr(saved_search, field)
        if v is not None:
            updates[field] = v
    if saved_search.district_allowlist is not None:
        updates["district_allowlist"] = saved_search.district_allowlist
    return profile.model_copy(update=updates)
```

- [ ] **Step 4: Run tests, watch them pass**

Run: `pytest tests/test_overlay_profile.py -v`

Expected: all tests pass.

---

## Task 5: Matcher iterates over [None] + saved_searches

**Files:**
- Modify: `src/flatpilot/matcher/runner.py`
- Test: `tests/test_matcher_saved_searches.py`

> The existing `run_match` query at `runner.py:53-62` already does `LEFT JOIN matches m ON ... WHERE m.id IS NULL` — flats that have a match row under the current `profile_version_hash` are excluded from `rows`. So an `INSERT OR IGNORE` on the new write is correct: editing a saved search changes `profile.model_dump_json()`, hence `profile_hash` changes, hence the next pass evaluates each flat again under the new hash and writes a fresh row. No retro-update of older rows is needed.

- [ ] **Step 1: Write failing tests**

Create `tests/test_matcher_saved_searches.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

from flatpilot.matcher.runner import run_match
from flatpilot.profile import Profile, SavedSearch, save_profile


def _seed_flat(conn, **overrides):
    now = datetime.now(UTC).isoformat()
    row = {
        "external_id": "ext1",
        "platform": "wg-gesucht",
        "listing_url": "https://example.test/1",
        "title": "Test flat",
        "rent_warm_eur": 1200,
        "rooms": 2,
        "size_sqm": 50,
        "address": None,
        "district": None,
        "lat": None,
        "lng": None,
        "online_since": None,
        "available_from": None,
        "requires_wbs": 0,
        "wbs_size_category": None,
        "wbs_income_category": None,
        "furnished": None,
        "deposit_eur": None,
        "min_contract_months": None,
        "pets_allowed": None,
        "description": None,
        "scraped_at": now,
        "first_seen_at": now,
        "canonical_flat_id": None,
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row.keys())
    cur = conn.execute(
        f"INSERT INTO flats ({cols}) VALUES ({placeholders})", row
    )
    return cur.lastrowid


def test_match_with_no_saved_searches_writes_empty_json(tmp_db):
    profile = Profile.load_example()
    save_profile(profile)
    flat_id = _seed_flat(
        tmp_db,
        rent_warm_eur=profile.rent_max_warm,
        rooms=profile.rooms_min,
    )

    summary = run_match()
    assert summary["match"] == 1

    row = tmp_db.execute(
        "SELECT matched_saved_searches_json FROM matches WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert json.loads(row["matched_saved_searches_json"]) == []


def test_saved_search_widening_produces_match(tmp_db):
    base = Profile.load_example()
    profile = base.model_copy(
        update={
            "rent_max_warm": 1500,
            "saved_searches": [
                SavedSearch(name="luxury", auto_apply=True, rent_max_warm=2500)
            ],
        }
    )
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, rent_warm_eur=2000, rooms=profile.rooms_min)

    summary = run_match()
    assert summary["match"] == 1

    row = tmp_db.execute(
        "SELECT decision, matched_saved_searches_json FROM matches WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert row["decision"] == "match"
    assert json.loads(row["matched_saved_searches_json"]) == ["luxury"]


def test_full_reject_when_neither_base_nor_saved_search_matches(tmp_db):
    base = Profile.load_example()
    profile = base.model_copy(
        update={
            "rent_max_warm": 1500,
            "saved_searches": [
                SavedSearch(name="strict", auto_apply=True, rent_max_warm=1000),
            ],
        }
    )
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, rent_warm_eur=2000, rooms=profile.rooms_min)

    summary = run_match()
    assert summary["reject"] == 1

    row = tmp_db.execute(
        "SELECT decision, decision_reasons_json, matched_saved_searches_json "
        "FROM matches WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert row["decision"] == "reject"
    assert json.loads(row["matched_saved_searches_json"]) == []
    # Reasons come from base profile only
    assert "rent_too_high" in json.loads(row["decision_reasons_json"])
```

- [ ] **Step 2: Run tests, watch them fail**

Expected: tests run but `matched_saved_searches_json` is empty everywhere or the widening test reports `decision='reject'`.

- [ ] **Step 3: Update `matcher/runner.py`**

Replace the body of `run_match()` (everything after `phash = profile_hash(profile)` and `now = datetime.now(UTC).isoformat()`) so the loop iterates `[None] + profile.saved_searches`. Replace:

```python
    counts = {"match": 0, "reject": 0}
    for row in rows:
        flat = dict(row)
        reasons = evaluate(flat, profile)
        decision = "reject" if reasons else "match"
        conn.execute(
            """
            INSERT OR IGNORE INTO matches
                (flat_id, profile_version_hash, decision, decision_reasons_json, decided_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (flat["id"], phash, decision, json.dumps(reasons), now),
        )
        counts[decision] += 1
```

with:

```python
    from flatpilot.auto_apply import overlay_profile

    counts = {"match": 0, "reject": 0}
    for row in rows:
        flat = dict(row)

        base_reasons = evaluate(flat, profile)
        matched_saved: list[str] = []
        for ss in profile.saved_searches:
            if not evaluate(flat, overlay_profile(profile, ss)):
                matched_saved.append(ss.name)

        if not base_reasons or matched_saved:
            decision = "match"
        else:
            decision = "reject"

        conn.execute(
            """
            INSERT OR IGNORE INTO matches
                (flat_id, profile_version_hash, decision, decision_reasons_json,
                 decided_at, matched_saved_searches_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                flat["id"],
                phash,
                decision,
                json.dumps(base_reasons),
                now,
                json.dumps(matched_saved),
            ),
        )
        counts[decision] += 1
```

- [ ] **Step 4: Run tests, watch them pass**

Run: `pytest tests/test_matcher_saved_searches.py tests/test_overlay_profile.py -v`

Expected: all pass. Also re-run the existing matcher tests to ensure no regression: `pytest tests/test_matcher_runner.py -v` (or whatever the existing matcher test file is named — `grep -l "run_match" tests/`).

---

## Task 6: PAUSE primitive + extend `tmp_db` to patch `PAUSE_PATH`

**Files:**
- Modify: `src/flatpilot/auto_apply.py`, `tests/conftest.py`
- Test: `tests/test_auto_apply_gates.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auto_apply_gates.py` with PAUSE tests:

```python
from __future__ import annotations

from flatpilot.auto_apply import PAUSE_PATH, is_paused


def test_is_paused_false_when_no_file(tmp_db):
    assert is_paused() is False


def test_is_paused_true_when_file_exists(tmp_db):
    PAUSE_PATH.touch()
    assert is_paused() is True
```

- [ ] **Step 2: Run, watch fail (ImportError on PAUSE_PATH / is_paused).**

Run: `pytest tests/test_auto_apply_gates.py -v`

- [ ] **Step 3: Add PAUSE_PATH and `is_paused` to `auto_apply.py`**

In `src/flatpilot/auto_apply.py`, add at top after imports:

```python
from flatpilot.config import APP_DIR

PAUSE_PATH = APP_DIR / "PAUSE"


def is_paused() -> bool:
    return PAUSE_PATH.exists()
```

- [ ] **Step 4: Extend `tmp_db` to patch `auto_apply.PAUSE_PATH`**

In `tests/conftest.py`, inside the `tmp_db` fixture body, after the existing block that patches `_profile.PROFILE_PATH` (line 49) and before the `database.close_conn()` line, add:

```python
    # auto_apply.PAUSE_PATH is computed from APP_DIR at import time, so
    # patch the bound name explicitly. Lazy import keeps this fixture
    # importable on a tree that hasn't introduced auto_apply.py yet.
    from flatpilot import auto_apply as _auto_apply

    monkeypatch.setattr(_auto_apply, "PAUSE_PATH", app_dir / "PAUSE")
```

- [ ] **Step 5: Run, watch pass.**

---

## Task 7: Daily cap and cooldown gate helpers

**Files:**
- Modify: `src/flatpilot/auto_apply.py`
- Test: `tests/test_auto_apply_gates.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_auto_apply_gates.py`:

```python
from datetime import UTC, datetime, timedelta

from flatpilot.profile import AutoApplySettings, Profile


def _seed_flat(conn, platform="wg-gesucht"):
    """Insert a flat (FK target for applications). Returns its id."""
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
        VALUES (?, ?, 'https://example.test', 'T', ?, ?, 0)
        """,
        (f"ext-{now}-{platform}", platform, now, now),
    )
    return cur.lastrowid


def _seed_application(
    conn, *, platform, status, applied_at, method="auto", notes=None,
):
    flat_id = _seed_flat(conn, platform=platform)
    conn.execute(
        """
        INSERT INTO applications
            (flat_id, platform, listing_url, title, applied_at, method,
             attachments_sent_json, status, notes)
        VALUES (?, ?, 'https://example.test', 'T', ?, ?, '[]', ?, ?)
        """,
        (flat_id, platform, applied_at, method, status, notes),
    )


def _profile_with_caps(cap=20, cooldown=120):
    base = Profile.load_example()
    return base.model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": cap, "kleinanzeigen": cap},
                cooldown_seconds_per_platform={"wg-gesucht": cooldown, "kleinanzeigen": cooldown},
            )
        }
    )


def test_daily_cap_remaining_full_when_no_rows(tmp_db):
    from flatpilot.auto_apply import daily_cap_remaining

    profile = _profile_with_caps(cap=20)
    assert daily_cap_remaining(tmp_db, profile, "wg-gesucht") == 20


def test_daily_cap_remaining_decrements_for_submitted_only(tmp_db):
    from flatpilot.auto_apply import daily_cap_remaining

    today = datetime.now(UTC).isoformat()
    _seed_application(tmp_db, platform="wg-gesucht", status="submitted", applied_at=today)
    _seed_application(tmp_db, platform="wg-gesucht", status="failed", applied_at=today)
    _seed_application(
        tmp_db, platform="wg-gesucht", status="failed", applied_at=today,
        notes="auto_skipped: missing template",
    )
    profile = _profile_with_caps(cap=20)
    # Only the 'submitted' row burns cap.
    assert daily_cap_remaining(tmp_db, profile, "wg-gesucht") == 19


def test_daily_cap_remaining_excludes_yesterday(tmp_db):
    from flatpilot.auto_apply import daily_cap_remaining

    yesterday = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    _seed_application(tmp_db, platform="wg-gesucht", status="submitted", applied_at=yesterday)
    profile = _profile_with_caps(cap=20)
    assert daily_cap_remaining(tmp_db, profile, "wg-gesucht") == 20


def test_daily_cap_remaining_zero_when_platform_missing(tmp_db):
    from flatpilot.auto_apply import daily_cap_remaining

    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(daily_cap_per_platform={"only-other": 5})
        }
    )
    assert daily_cap_remaining(tmp_db, profile, "wg-gesucht") == 0


def test_cooldown_zero_when_no_rows(tmp_db):
    from flatpilot.auto_apply import cooldown_remaining_sec

    profile = _profile_with_caps(cooldown=120)
    assert cooldown_remaining_sec(tmp_db, profile, "wg-gesucht") == 0.0


def test_cooldown_counts_submitted_and_real_failures(tmp_db):
    from flatpilot.auto_apply import cooldown_remaining_sec

    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    _seed_application(tmp_db, platform="wg-gesucht", status="failed", applied_at=recent)
    profile = _profile_with_caps(cooldown=120)
    remaining = cooldown_remaining_sec(tmp_db, profile, "wg-gesucht")
    assert 80 < remaining < 95


def test_cooldown_ignores_auto_skipped_rows(tmp_db):
    from flatpilot.auto_apply import cooldown_remaining_sec

    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    _seed_application(
        tmp_db, platform="wg-gesucht", status="failed", applied_at=recent,
        notes="auto_skipped: missing template",
    )
    profile = _profile_with_caps(cooldown=120)
    assert cooldown_remaining_sec(tmp_db, profile, "wg-gesucht") == 0.0
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Add gate helpers to `auto_apply.py`**

```python
import sqlite3
from datetime import UTC, datetime, timedelta

from flatpilot.profile import Profile


def daily_cap_remaining(
    conn: sqlite3.Connection, profile: Profile, platform: str
) -> int:
    cap = profile.auto_apply.daily_cap_per_platform.get(platform, 0)
    if cap <= 0:
        return 0
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    used = conn.execute(
        "SELECT COUNT(*) FROM applications "
        "WHERE platform = ? AND method = 'auto' "
        "AND status = 'submitted' AND applied_at >= ?",
        (platform, today_start),
    ).fetchone()[0]
    return max(0, cap - used)


def cooldown_remaining_sec(
    conn: sqlite3.Connection, profile: Profile, platform: str
) -> float:
    cooldown = profile.auto_apply.cooldown_seconds_per_platform.get(platform, 0)
    if cooldown <= 0:
        return 0.0
    row = conn.execute(
        "SELECT MAX(applied_at) AS last FROM applications "
        "WHERE platform = ? AND method = 'auto' "
        "AND ( status = 'submitted' "
        "      OR (status = 'failed' "
        "          AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')))",
        (platform,),
    ).fetchone()
    last = row["last"] if row is not None else None
    if last is None:
        return 0.0
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
    return max(0.0, cooldown - elapsed)
```

- [ ] **Step 4: Run, watch pass.**

---

## Task 8: Completeness gate

**Files:**
- Modify: `src/flatpilot/auto_apply.py`
- Test: `tests/test_auto_apply_gates.py` (extend)

- [ ] **Step 1: Write failing tests**

Append:

```python
def test_completeness_passes_for_complete_setup(tmp_db, monkeypatch):
    """A profile + flat that compose+attachments resolve cleanly should pass."""
    from flatpilot.auto_apply import completeness_ok

    monkeypatch.setattr(
        "flatpilot.auto_apply.compose_anschreiben",
        lambda *a, **kw: "Hello landlord",
    )
    monkeypatch.setattr(
        "flatpilot.auto_apply.resolve_for_platform",
        lambda *a, **kw: [],
    )
    profile = Profile.load_example()
    flat = {"platform": "wg-gesucht", "id": 1}

    ok, reason = completeness_ok(profile, flat)
    assert ok is True
    assert reason is None


def test_completeness_fails_on_template_error(tmp_db, monkeypatch):
    from flatpilot.auto_apply import completeness_ok
    from flatpilot.compose import TemplateError

    def boom(*a, **kw):
        raise TemplateError("missing template")

    monkeypatch.setattr("flatpilot.auto_apply.compose_anschreiben", boom)
    monkeypatch.setattr(
        "flatpilot.auto_apply.resolve_for_platform", lambda *a, **kw: []
    )

    profile = Profile.load_example()
    flat = {"platform": "wg-gesucht", "id": 1}
    ok, reason = completeness_ok(profile, flat)
    assert ok is False
    assert reason is not None
    assert "template" in reason


def test_completeness_fails_on_unregistered_platform(tmp_db):
    from flatpilot.auto_apply import completeness_ok

    profile = Profile.load_example()
    flat = {"platform": "totally-unknown-platform", "id": 1}
    ok, reason = completeness_ok(profile, flat)
    assert ok is False
    assert "filler" in reason.lower()
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Add `completeness_ok` to `auto_apply.py`**

Add at the bottom of `auto_apply.py`:

```python
# Force filler registry population.
import flatpilot.fillers.kleinanzeigen  # noqa: F401
import flatpilot.fillers.wg_gesucht  # noqa: F401

from flatpilot.attachments import AttachmentError, resolve_for_platform
from flatpilot.compose import TemplateError, compose_anschreiben
from flatpilot.fillers import get_filler


def completeness_ok(profile: Profile, flat: dict) -> tuple[bool, str | None]:
    platform = str(flat["platform"])
    try:
        get_filler(platform)
    except KeyError:
        return False, f"filler not registered for platform {platform!r}"
    try:
        compose_anschreiben(profile, platform, flat)
    except TemplateError as exc:
        return False, f"template: {exc}"
    try:
        resolve_for_platform(profile, platform)
    except AttachmentError as exc:
        return False, f"attachment: {exc}"
    return True, None
```

(`flatpilot.fillers.get_filler` raises `KeyError` for unknown platforms — verified at `src/flatpilot/fillers/__init__.py:49`.)

- [ ] **Step 4: Run, watch pass.**

---

## Task 9: `apply_to_flat` accepts `method` and `saved_search`

**Files:**
- Modify: `src/flatpilot/apply.py`
- Test: `tests/test_apply_to_flat_method.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_apply_to_flat_method.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from flatpilot.apply import _record_application
from flatpilot.profile import Profile


def _seed_flat(conn):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title, scraped_at, first_seen_at,
            requires_wbs
        ) VALUES ('e1', 'wg-gesucht', 'https://x', 'T', ?, ?, 0)
        """,
        (now, now),
    )
    return cur.lastrowid


def test_record_application_default_is_manual(tmp_db):
    flat_id = _seed_flat(tmp_db)
    profile = Profile.load_example()
    flat = dict(tmp_db.execute("SELECT * FROM flats WHERE id=?", (flat_id,)).fetchone())

    app_id = _record_application(
        tmp_db,
        profile=profile,
        flat=flat,
        message="msg",
        attachments=[],
        status="submitted",
        notes=None,
    )

    row = tmp_db.execute(
        "SELECT method, triggered_by_saved_search FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    assert row["method"] == "manual"
    assert row["triggered_by_saved_search"] is None


def test_record_application_writes_auto_method_and_saved_search(tmp_db):
    flat_id = _seed_flat(tmp_db)
    profile = Profile.load_example()
    flat = dict(tmp_db.execute("SELECT * FROM flats WHERE id=?", (flat_id,)).fetchone())

    app_id = _record_application(
        tmp_db,
        profile=profile,
        flat=flat,
        message="msg",
        attachments=[],
        status="submitted",
        notes=None,
        method="auto",
        saved_search="kreuzberg-2br",
    )

    row = tmp_db.execute(
        "SELECT method, triggered_by_saved_search FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    assert row["method"] == "auto"
    assert row["triggered_by_saved_search"] == "kreuzberg-2br"
```

- [ ] **Step 2: Run, watch fail (TypeError on unexpected kwargs).**

- [ ] **Step 3: Modify `apply.py`**

In `_record_application`, change the signature and body. Replace the existing function with:

```python
def _record_application(
    conn,
    *,
    profile: Profile,
    flat: dict,
    message: str,
    attachments: list[Path],
    status: str,
    notes: str | None,
    method: Literal["manual", "auto"] = "manual",
    saved_search: str | None = None,
) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            rent_warm_eur, rooms, size_sqm, district,
            applied_at, method,
            message_sent, attachments_sent_json,
            status, notes, triggered_by_saved_search
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            flat["id"],
            flat["platform"],
            flat["listing_url"],
            flat["title"],
            flat.get("rent_warm_eur"),
            flat.get("rooms"),
            flat.get("size_sqm"),
            flat.get("district"),
            now,
            method,
            message,
            json.dumps([str(p) for p in attachments]),
            status,
            notes,
            saved_search,
        ),
    )
    return int(cur.lastrowid)
```

Then update the public `apply_to_flat` signature to thread the new kwargs through to every `_record_application` call site (there are 2: the success path and the FillError path):

```python
def apply_to_flat(
    flat_id: int,
    *,
    dry_run: bool = False,
    screenshot_dir: Path | None = None,
    method: Literal["manual", "auto"] = "manual",
    saved_search: str | None = None,
) -> ApplyOutcome:
    ...
```

In both `_record_application` invocations inside `apply_to_flat`, add `method=method, saved_search=saved_search`.

- [ ] **Step 4: Run tests, watch pass.**

Run: `pytest tests/test_apply_to_flat_method.py -v` and the existing apply tests: `pytest tests/test_apply.py -v` to verify no regression on the manual path.

---

## Task 10: Pipeline stage `run_pipeline_apply`

**Files:**
- Modify: `src/flatpilot/auto_apply.py`
- Test: `tests/test_run_pipeline_apply.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_run_pipeline_apply.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

from rich.console import Console

from flatpilot.profile import Profile, SavedSearch, save_profile


def _seed_match(conn, *, flat_id, profile_hash, matched_saved_searches):
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO matches
            (flat_id, profile_version_hash, decision, decision_reasons_json,
             decided_at, matched_saved_searches_json)
        VALUES (?, ?, 'match', '[]', ?, ?)
        """,
        (flat_id, profile_hash, now, json.dumps(matched_saved_searches)),
    )


def _seed_flat(conn, platform="wg-gesucht"):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title, scraped_at, first_seen_at,
            requires_wbs
        ) VALUES ('e1', ?, 'https://x', 'T', ?, ?, 0)
        """,
        (platform, now, now),
    )
    return cur.lastrowid


def _profile_with_one_auto_search():
    from flatpilot.profile import AutoApplySettings

    base = Profile.load_example()
    return base.model_copy(
        update={
            "auto_apply": AutoApplySettings(),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )


def test_pause_short_circuits_stage(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH, run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db,
        flat_id=flat_id,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    PAUSE_PATH.touch()

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())

    mocked.assert_not_called()


def test_iterates_candidate_and_calls_apply_to_flat(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.apply import ApplyOutcome
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db,
        flat_id=flat_id,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked, \
         patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)):
        mocked.return_value = ApplyOutcome(
            status="submitted", application_id=1, fill_report=None
        )
        run_pipeline_apply(profile, Console())

    mocked.assert_called_once()
    kwargs = mocked.call_args.kwargs
    assert kwargs["method"] == "auto"
    assert kwargs["saved_search"] == "ss1"


def test_skips_when_already_submitted(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db,
        flat_id=flat_id,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    now = datetime.now(UTC).isoformat()
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'submitted')",
        (flat_id, now),
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())
    mocked.assert_not_called()


def test_dry_run_does_not_call_apply(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db,
        flat_id=flat_id,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked, \
         patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)):
        run_pipeline_apply(profile, Console(), dry_run=True)

    mocked.assert_not_called()


def test_completeness_failure_writes_skip_row(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db,
        flat_id=flat_id,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch(
        "flatpilot.auto_apply.completeness_ok",
        return_value=(False, "template: missing"),
    ), patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())
    mocked.assert_not_called()

    row = tmp_db.execute(
        "SELECT method, status, notes, triggered_by_saved_search "
        "FROM applications WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert row["method"] == "auto"
    assert row["status"] == "failed"
    assert row["notes"].startswith("auto_skipped:")
    assert row["triggered_by_saved_search"] == "ss1"
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Implement `run_pipeline_apply` in `auto_apply.py`**

Add at the bottom:

```python
import json
import logging

from flatpilot.apply import apply_to_flat
from flatpilot.database import get_conn, init_db
from flatpilot.fillers.base import FillError
from flatpilot.profile import profile_hash

logger = logging.getLogger(__name__)


def run_pipeline_apply(profile, console, *, dry_run: bool = False) -> None:
    if is_paused():
        console.print("[yellow]auto-apply: PAUSED (~/.flatpilot/PAUSE present)[/yellow]")
        return

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)

    rows = conn.execute(
        """
        SELECT m.id AS match_id,
               m.matched_saved_searches_json,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        WHERE m.decision = 'match'
          AND m.profile_version_hash = ?
          AND m.matched_saved_searches_json != '[]'
          AND NOT EXISTS (
            SELECT 1 FROM applications a
            WHERE a.flat_id = m.flat_id
              AND a.method = 'auto'
              AND a.status = 'submitted'
          )
        ORDER BY m.decided_at ASC
        """,
        (phash,),
    ).fetchall()

    saved_search_by_name = {ss.name: ss for ss in profile.saved_searches}

    for row in rows:
        flat = dict(row)
        platform = str(flat["platform"])
        candidate_names = json.loads(flat["matched_saved_searches_json"])

        if not _try_flat(
            conn=conn,
            console=console,
            profile=profile,
            flat=flat,
            platform=platform,
            candidate_names=candidate_names,
            saved_search_by_name=saved_search_by_name,
            dry_run=dry_run,
        ):
            continue


def _try_flat(
    *, conn, console, profile, flat, platform, candidate_names,
    saved_search_by_name, dry_run,
) -> bool:
    flat_id = int(flat["id"])

    for name in candidate_names:
        ss = saved_search_by_name.get(name)
        if ss is None:
            continue
        if not ss.auto_apply:
            continue
        if ss.platforms and platform not in ss.platforms:
            continue

        cap = daily_cap_remaining(conn, profile, platform)
        if cap <= 0:
            console.print(
                f"[dim]auto-apply: cap reached on {platform}; skipping flat {flat_id}[/dim]"
            )
            return False

        wait = cooldown_remaining_sec(conn, profile, platform)
        if wait > 0:
            console.print(
                f"[dim]auto-apply: cooldown {wait:.0f}s on {platform}; "
                f"skipping flat {flat_id}[/dim]"
            )
            return False

        ok, reason = completeness_ok(profile, flat)
        if not ok:
            from flatpilot.attachments import resolve_for_platform as _resolve
            attachments = []
            try:
                attachments = _resolve(profile, platform)
            except Exception:
                attachments = []
            from flatpilot.apply import _record_application

            _record_application(
                conn,
                profile=profile,
                flat=flat,
                message="",
                attachments=attachments,
                status="failed",
                notes=f"auto_skipped: {reason}",
                method="auto",
                saved_search=name,
            )
            console.print(
                f"[yellow]auto-apply: skipped flat {flat_id} ({reason})[/yellow]"
            )
            return False

        if dry_run:
            console.print(
                f"[cyan]auto-apply (dry-run): would apply flat {flat_id} "
                f"via saved-search '{name}'[/cyan]"
            )
            return True

        try:
            apply_to_flat(flat_id, method="auto", saved_search=name)
            console.print(
                f"[green]auto-apply: submitted flat {flat_id} "
                f"via saved-search '{name}'[/green]"
            )
            return True
        except FillError as exc:
            console.print(
                f"[red]auto-apply: filler failed for flat {flat_id}: {exc}[/red]"
            )
            return True

    return False
```

- [ ] **Step 4: Run, watch pass.**

Run: `pytest tests/test_run_pipeline_apply.py -v`

---

## Task 11: Pipeline integration + run-stage flags

**Files:**
- Modify: `src/flatpilot/pipeline.py`, `src/flatpilot/cli.py`
- Test: extend `tests/test_run_pipeline_apply.py` with one integration test

- [ ] **Step 1: Write failing test**

Append to `tests/test_run_pipeline_apply.py`:

```python
def test_run_pipeline_once_now_includes_apply_stage(tmp_db, monkeypatch):
    from flatpilot.pipeline import run_pipeline_once

    profile = _profile_with_one_auto_search()
    save_profile(profile)

    called = {"apply": False}

    def fake_apply_stage(profile, console, **kw):
        called["apply"] = True

    monkeypatch.setattr("flatpilot.pipeline.run_pipeline_apply", fake_apply_stage)
    monkeypatch.setattr(
        "flatpilot.pipeline.run_pipeline_scrape", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "flatpilot.pipeline.run_pipeline_match", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "flatpilot.pipeline.run_pipeline_notify", lambda *a, **k: None
    )

    run_pipeline_once(profile, Console())
    assert called["apply"] is True


def test_run_pipeline_once_skip_apply_does_not_call_apply_stage(tmp_db, monkeypatch):
    from flatpilot.pipeline import run_pipeline_once

    profile = _profile_with_one_auto_search()
    save_profile(profile)

    called = {"apply": False}

    def fake_apply_stage(profile, console, **kw):
        called["apply"] = True

    monkeypatch.setattr("flatpilot.pipeline.run_pipeline_apply", fake_apply_stage)
    monkeypatch.setattr(
        "flatpilot.pipeline.run_pipeline_scrape", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "flatpilot.pipeline.run_pipeline_match", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "flatpilot.pipeline.run_pipeline_notify", lambda *a, **k: None
    )

    run_pipeline_once(profile, Console(), skip_apply=True)
    assert called["apply"] is False
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Update `pipeline.py`**

Add at the top with other imports:

```python
from flatpilot.auto_apply import run_pipeline_apply as run_pipeline_apply_impl
```

Add a thin wrapper near the existing stage wrappers:

```python
def run_pipeline_apply(profile: Profile, console, *, dry_run: bool = False) -> None:
    run_pipeline_apply_impl(profile, console, dry_run=dry_run)
```

Modify `run_pipeline_once` to call `run_pipeline_apply` between `match` and `notify`. Update its signature to accept `skip_apply` and `dry_run_apply` flags:

```python
def run_pipeline_once(
    profile: Profile,
    console,
    *,
    skip_apply: bool = False,
    dry_run_apply: bool = False,
) -> int:
    failures = 0

    console.rule("scrape")
    try:
        run_pipeline_scrape(profile, console)
    except Exception as exc:
        console.print(f"[red]scrape failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    console.rule("match")
    try:
        run_pipeline_match(console)
    except Exception as exc:
        console.print(f"[red]match failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    if not skip_apply:
        console.rule("apply")
        try:
            run_pipeline_apply(profile, console, dry_run=dry_run_apply)
        except Exception as exc:
            console.print(f"[red]apply failed: {exc.__class__.__name__}: {exc}[/red]")
            failures += 1

    console.rule("notify")
    try:
        run_pipeline_notify(profile, console)
    except Exception as exc:
        console.print(f"[red]notify failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    return failures
```

- [ ] **Step 4: Wire flags into `cli.py`**

The existing `run` command (cli.py:108-184) is NOT a placeholder. It already has `--watch`, `--interval`, signal handlers, a watch loop, profile loading, and TWO call sites of `run_pipeline_once` (the non-watch path at line 136, and the watch-loop path at line 161). Both call sites must forward the new flags. **ADD** parameters; do not replace the body.

Concrete edits:

1. Extend the `run` signature (keep existing `watch` and `interval`):

```python
@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Loop until SIGINT / SIGTERM."),
    interval: int = typer.Option(
        120, "--interval", help="Seconds between passes when --watch is set (default 120)."
    ),
    skip_apply: bool = typer.Option(
        False,
        "--skip-apply",
        help="Run scrape/match/notify only. No auto-apply stage.",
    ),
    dry_run_apply: bool = typer.Option(
        False,
        "--dry-run-apply",
        help="Log what auto-apply would do without calling fillers.",
    ),
) -> None:
    """One scrape + match + apply + notify pass (add --watch to loop)."""
```

2. Update the docstring (`"""One scrape + match + notify pass (add --watch to loop)."""` → `"""One scrape + match + apply + notify pass (add --watch to loop)."""`).

3. Update the non-watch call (cli.py:136):

```python
    if not watch:
        failures = run_pipeline_once(
            profile, console,
            skip_apply=skip_apply,
            dry_run_apply=dry_run_apply,
        )
        if failures:
            raise typer.Exit(1)
        return
```

4. Update the watch-loop call (cli.py:161):

```python
            try:
                total_failures += run_pipeline_once(
                    profile, console,
                    skip_apply=skip_apply,
                    dry_run_apply=dry_run_apply,
                )
            except Exception as exc:
```

Leave the rest of the watch loop, signal handling, and exit logic exactly as it is.

- [ ] **Step 5: Run, watch pass.**

Run: `pytest tests/test_run_pipeline_apply.py -v`

---

## Task 12: `flatpilot pause` and `flatpilot resume`

**Files:**
- Modify: `src/flatpilot/cli.py`
- Test: `tests/test_pause_resume_cli.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pause_resume_cli.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from flatpilot.cli import app


def test_pause_creates_file(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    runner = CliRunner()
    result = runner.invoke(app, ["pause"])
    assert result.exit_code == 0
    assert PAUSE_PATH.exists()


def test_pause_is_idempotent(tmp_db):
    runner = CliRunner()
    runner.invoke(app, ["pause"])
    result = runner.invoke(app, ["pause"])
    assert result.exit_code == 0


def test_resume_removes_file(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    runner = CliRunner()
    runner.invoke(app, ["pause"])
    result = runner.invoke(app, ["resume"])
    assert result.exit_code == 0
    assert not PAUSE_PATH.exists()


def test_resume_is_idempotent(tmp_db):
    runner = CliRunner()
    result = runner.invoke(app, ["resume"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Add commands in `cli.py`**

```python
@app.command()
def pause() -> None:
    """Halt auto-apply by creating ~/.flatpilot/PAUSE."""
    from flatpilot.auto_apply import PAUSE_PATH
    from flatpilot.config import ensure_dirs

    ensure_dirs()
    PAUSE_PATH.touch()
    rprint(f"[yellow]auto-apply paused[/yellow] · {PAUSE_PATH}")


@app.command()
def resume() -> None:
    """Resume auto-apply by removing ~/.flatpilot/PAUSE."""
    from flatpilot.auto_apply import PAUSE_PATH

    PAUSE_PATH.unlink(missing_ok=True)
    rprint("[green]auto-apply resumed[/green]")
```

- [ ] **Step 4: Run, watch pass.**

---

## Task 13: Wizard auto-apply prompt with re-run skip

**Files:**
- Modify: `src/flatpilot/wizard/init.py`
- Test: `tests/test_wizard_auto_apply.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_wizard_auto_apply.py`:

```python
from __future__ import annotations

import pytest

from flatpilot.profile import Profile, SavedSearch
from flatpilot.wizard.init import _maybe_add_auto_apply


def test_no_existing_yes_appends_starter():
    base = Profile.load_example()
    out = _maybe_add_auto_apply(base, answer=True)
    assert len(out.saved_searches) == 1
    assert out.saved_searches[0].name == "auto-default"
    assert out.saved_searches[0].auto_apply is True


def test_no_existing_no_returns_unchanged():
    base = Profile.load_example()
    out = _maybe_add_auto_apply(base, answer=False)
    assert out.saved_searches == []


def test_existing_auto_default_short_circuits():
    base = Profile.load_example().model_copy(
        update={
            "saved_searches": [
                SavedSearch(name="auto-default", auto_apply=True, rent_max_warm=999)
            ]
        }
    )
    # Even on yes, do not append a duplicate or overwrite custom fields.
    out = _maybe_add_auto_apply(base, answer=True)
    assert len(out.saved_searches) == 1
    assert out.saved_searches[0].rent_max_warm == 999
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Add `_maybe_add_auto_apply` to `wizard/init.py`**

In `src/flatpilot/wizard/init.py`:

```python
from flatpilot.profile import SavedSearch


def _maybe_add_auto_apply(profile: Profile, *, answer: bool) -> Profile:
    if any(ss.name == "auto-default" for ss in profile.saved_searches):
        return profile
    if not answer:
        return profile
    new = list(profile.saved_searches)
    new.append(SavedSearch(name="auto-default", auto_apply=True))
    return profile.model_copy(update={"saved_searches": new})
```

In the `run()` function, before the final `Confirm.ask("Save profile?", ...)`, add the prompt:

```python
    out.rule("Auto-apply (Phase 4)")
    has_default = any(ss.name == "auto-default" for ss in profile.saved_searches)
    if not has_default:
        enable = Confirm.ask(
            "Enable auto-apply with a starter saved search? "
            "(Use `flatpilot pause` to disable temporarily.)",
            default=False,
        )
        profile = _maybe_add_auto_apply(profile, answer=enable)
```

(The wizard's `payload` dict is converted to a Profile via `Profile(**payload)` first; the `_maybe_add_auto_apply` call uses that Profile object.)

- [ ] **Step 4: Run, watch pass.**

---

## Task 14: Doctor rows for PAUSE, saved-search count, per-platform burn

**Files:**
- Modify: `src/flatpilot/doctor.py`
- Test: `tests/test_doctor_auto_apply.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_doctor_auto_apply.py`:

```python
from __future__ import annotations

from io import StringIO

from rich.console import Console

from flatpilot.doctor import run as run_doctor
from flatpilot.profile import Profile, SavedSearch, save_profile


def _doctor_output() -> str:
    buf = StringIO()
    run_doctor(Console(file=buf, force_terminal=False, width=200))
    return buf.getvalue()


def test_doctor_shows_pause_row(tmp_db):
    save_profile(Profile.load_example())
    text = _doctor_output()
    assert "PAUSE switch" in text
    assert "not paused" in text


def test_doctor_shows_paused_when_file_exists(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    save_profile(Profile.load_example())
    PAUSE_PATH.touch()
    text = _doctor_output()
    assert "PAUSED" in text


def test_doctor_shows_saved_search_count(tmp_db):
    profile = Profile.load_example().model_copy(
        update={
            "saved_searches": [
                SavedSearch(name="ss1", auto_apply=True),
                SavedSearch(name="ss2", auto_apply=False),
            ]
        }
    )
    save_profile(profile)
    text = _doctor_output()
    assert "saved searches" in text
    assert "1 active" in text  # only ss1 has auto_apply=true


def test_doctor_shows_per_platform_burn(tmp_db):
    save_profile(Profile.load_example())
    text = _doctor_output()
    assert "wg-gesucht" in text
    assert "0/20" in text
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Add doctor checks**

In `src/flatpilot/doctor.py`, add new check functions:

```python
def _check_pause() -> tuple[str, str]:
    from flatpilot.auto_apply import PAUSE_PATH

    if PAUSE_PATH.exists():
        return "optional", "PAUSED — auto-apply halted (run `flatpilot resume` to re-enable)"
    return "OK", "not paused"


def _check_saved_searches() -> tuple[str, str]:
    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile"
    active = [ss.name for ss in profile.saved_searches if ss.auto_apply]
    if not active:
        return "OK", "0 active"
    return "OK", f"{len(active)} active ({', '.join(active)})"


def _check_platform_burn(platform: str) -> tuple[str, str]:
    from datetime import UTC, datetime

    from flatpilot.auto_apply import cooldown_remaining_sec, daily_cap_remaining
    from flatpilot.database import get_conn, init_db

    profile, err = _safe_load_profile()
    if err is not None:
        return "optional", err
    if profile is None:
        return "optional", "no profile"
    cap = profile.auto_apply.daily_cap_per_platform.get(platform, 0)
    if cap == 0:
        return "optional", "no cap configured (auto-apply disabled for platform)"
    init_db()
    conn = get_conn()
    remaining = daily_cap_remaining(conn, profile, platform)
    used = cap - remaining
    wait = cooldown_remaining_sec(conn, profile, platform)
    return "OK", f"{used}/{cap} used today, ready in {wait:.0f}s"
```

Then extend `CHECKS`:

```python
CHECKS: list[tuple[str, CheckFn]] = [
    ("Python >= 3.11", _check_python),
    ("App directory", _check_app_dir),
    ("Playwright Chromium", _check_playwright),
    ("Telegram creds", _check_telegram),
    ("SMTP creds", _check_smtp),
    ("Auto-apply: PAUSE switch", _check_pause),
    ("Auto-apply: saved searches", _check_saved_searches),
]
```

And add per-platform burn rows after the per-platform cookie loop in `run()`:

```python
    # Per-platform burn rows (auto-apply cap usage). Always optional;
    # never affect exit code.
    for filler_cls in sorted(all_fillers(), key=lambda c: c.platform):
        platform = filler_cls.platform
        status, detail = _check_platform_burn(platform)
        style = _STYLES[status]
        table.add_row(
            f"Auto-apply: {platform}",
            f"[{style}]{status}[/{style}]",
            detail,
        )
```

- [ ] **Step 4: Run, watch pass.**

---

## Task 15: Dashboard badge + failed-row notes

**Files:**
- Modify: `src/flatpilot/view.py`
- Test: `tests/test_view_auto_apply.py`

The dashboard is rendered by `generate_html(conn=None) -> str` (view.py:44). Each applications row is rendered by `_application_row(app: dict) -> str` (view.py:264). The task is to:

1. Inside `_application_row`, emit a `<span class="badge badge--auto">auto</span>` or `badge--manual` derived from `app["method"]`.
2. For rows where `status='failed' AND method='auto'`, render `app["notes"]` inline (HTML-escaped) instead of leaving it buried as a `data-*` attribute.
3. Add CSS for the new classes to the inline stylesheet block.

- [ ] **Step 1: Write failing tests**

Create `tests/test_view_auto_apply.py`:

```python
from __future__ import annotations

from flatpilot.profile import Profile, save_profile
from flatpilot.view import generate_html


def _seed_application(conn, *, method, status, notes=None):
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at, requires_wbs) "
        "VALUES ('e', 'wg-gesucht', 'https://x', 'T', "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0)"
    )
    flat_id = conn.execute("SELECT MAX(id) AS i FROM flats").fetchone()["i"]
    conn.execute(
        "INSERT INTO applications "
        "(flat_id, platform, listing_url, title, applied_at, method, "
        " attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', "
        "'2026-04-30T12:00:00+00:00', ?, '[]', ?, ?)",
        (flat_id, method, status, notes),
    )


def test_badge_renders_for_auto(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="auto", status="submitted")

    html = generate_html(tmp_db)
    assert "badge--auto" in html


def test_badge_renders_for_manual(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="manual", status="submitted")

    html = generate_html(tmp_db)
    assert "badge--manual" in html


def test_failed_auto_row_shows_notes(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(
        tmp_db, method="auto", status="failed",
        notes="auto_skipped: missing template",
    )

    html = generate_html(tmp_db)
    assert "auto_skipped: missing template" in html
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Update `view.py`**

Add two helpers near `_application_row`:

```python
def _badge_html(method: str | None) -> str:
    if method == "auto":
        return '<span class="badge badge--auto">auto</span>'
    return '<span class="badge badge--manual">manual</span>'


def _notes_html(app: dict[str, Any]) -> str:
    notes = app.get("notes")
    if notes and app.get("method") == "auto" and app.get("status") == "failed":
        return f'<div class="application-row__notes">{escape(str(notes))}</div>'
    return ""
```

Inside `_application_row`, locate the existing markup that renders the row's title / metadata block. Insert `_badge_html(app.get("method"))` next to the platform / status text, and append `_notes_html(app)` immediately after the row's status line.

Find the inline `<style>` block in `generate_html` (the dashboard ships its CSS inline). Add:

```css
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.75em; font-weight: 600; margin-left: 6px; }
.badge--auto { background: #e0f2fe; color: #0369a1; }
.badge--manual { background: #f3f4f6; color: #374151; }
.application-row__notes { font-style: italic; color: #b45309; margin-top: 4px; font-size: 0.9em; }
```

(If the inline style block uses a different format / style, match its conventions; the rule names above must remain so the tests pass.)

- [ ] **Step 4: Run, watch pass.**

---

## Task 16: profile.example.json update

**Files:**
- Modify: `src/flatpilot/profile.example.json`
- Test: existing `Profile.load_example()` calls already cover round-tripping; one explicit test below.

- [ ] **Step 1: Write failing test**

Append to `tests/test_saved_search_schema.py`:

```python
def test_example_profile_demonstrates_auto_apply_shape():
    p = Profile.load_example()
    # auto_apply block exists with at least the three bundled platforms
    assert "wg-gesucht" in p.auto_apply.daily_cap_per_platform
    # Saved searches list is exposed (even if empty by default)
    assert isinstance(p.saved_searches, list)
```

- [ ] **Step 2: Run, watch pass already** (the load_example test should already pass once Profile has the new fields). If it fails, the cause is JSON not loading — proceed to step 3.

- [ ] **Step 3: Update `profile.example.json`**

Replace the file content with:

```json
{
  "country": "DE",
  "city": "Frankfurt am Main",
  "radius_km": 25,
  "district_allowlist": [],
  "home_lat": null,
  "home_lng": null,
  "rent_min_warm": 1000,
  "rent_max_warm": 1500,
  "rooms_min": 2,
  "rooms_max": 3,
  "household_size": 2,
  "kids": 0,
  "pets": [],
  "status": "employed",
  "net_income_eur": 3000,
  "move_in_date": "2026-06-01",
  "smoker": false,
  "furnished_pref": "any",
  "min_contract_months": null,
  "wbs": {
    "status": "none"
  },
  "notifications": {
    "telegram": {
      "enabled": false,
      "bot_token_env": "TELEGRAM_BOT_TOKEN",
      "chat_id": ""
    },
    "email": {
      "enabled": false,
      "smtp_env": "SMTP"
    }
  },
  "attachments": {
    "default": [],
    "per_platform": {}
  },
  "auto_apply": {
    "daily_cap_per_platform": {
      "wg-gesucht": 20,
      "kleinanzeigen": 20,
      "inberlinwohnen": 20
    },
    "cooldown_seconds_per_platform": {
      "wg-gesucht": 120,
      "kleinanzeigen": 120,
      "inberlinwohnen": 120
    }
  },
  "saved_searches": []
}
```

- [ ] **Step 4: Run, watch pass.**

---

## Task 17: Final verification + single commit

- [ ] **Step 1: Run the full test suite**

```bash
pytest -v
```

Expected: all tests pass — both the new ones and every pre-existing test.

- [ ] **Step 2: Run linters**

```bash
ruff check src/ tests/
```

Expected: zero new lint errors. (Pre-existing errors in untouched modules are tracked under FlatPilot-4wk.)

- [ ] **Step 3: Smoke-test the CLI surfaces**

```bash
python -m flatpilot doctor
python -m flatpilot pause
python -m flatpilot doctor    # PAUSE row should now show PAUSED
python -m flatpilot resume
python -m flatpilot doctor    # PAUSE row should be back to "not paused"
```

- [ ] **Step 4: Stage and commit (single commit for the entire feature)**

```bash
git add -A
git status
```

Verify only the files listed in §"File Structure" above are staged. Then:

```bash
git commit -m "$(cat <<'EOF'
FlatPilot-f2fa: implement Phase 4 auto-apply with safety rails

Bundles N1-N5 (FlatPilot-jzp2/g2a4/5f4i/y4ws/l4dq) into one feature:

- SavedSearch + AutoApplySettings models on Profile (overlay semantics,
  per-platform daily caps and cooldowns, unique-name validator)
- new applications/matches columns + idx_applications_method_applied_at
- flatpilot/auto_apply.py with PAUSE, daily_cap_remaining,
  cooldown_remaining_sec, completeness_ok, overlay_profile,
  run_pipeline_apply
- matcher iterates [None] + saved_searches and writes
  matched_saved_searches_json
- apply_to_flat takes method + saved_search; _record_application
  honors them
- pipeline gains apply stage between match and notify, with
  --skip-apply and --dry-run-apply flags
- flatpilot pause / flatpilot resume CLI commands
- wizard offers a y/N starter-saved-search prompt with re-run skip
- doctor adds PAUSE, saved-search count, per-platform burn rows
- dashboard renders auto/manual badge and surfaces notes on failed
  auto rows
- profile.example.json demonstrates the new auto_apply block
- tests across all of the above
EOF
)"
```

- [ ] **Step 5: Confirm branch state**

```bash
git log --oneline -3
git status
```

Expected output: the new commit on top of the spec commit. Status clean. Do **not** push — the user runs the push themselves at PR time.

---

## Self-Review Checklist (post-write)

**Spec coverage** (every requirement in §3–§7 of the spec maps to a task):

| Spec §              | Implemented in tasks |
|---------------------|----------------------|
| 3.1 Schema additions | 1, 2 |
| 3.2 DB additions     | 3 |
| 3.3 New CLI commands | 11 (run flags), 12 (pause/resume) |
| 3.4 Doctor changes   | 14 |
| 3.5 Dashboard changes| 15 |
| 4.1 Pipeline stages  | 11 |
| 4.2 Data flow / gates| 6, 7, 8, 10 |
| 4.3 Matcher overlay  | 4, 5 |
| 4.4 auto_apply.py    | 4, 6, 7, 8, 10 |
| 4.5 apply_to_flat    | 9 |
| 4.6 Wizard           | 13 |
| 4.7 Idempotency      | 10 (query) + 9 (record_application) |
| 5 Edge cases         | covered by tests in 5, 7, 10 |
| 6 Testing            | 1–16 |
| 7 File-by-file       | matches §"File Structure" above |

**Type consistency:** `daily_cap_remaining(conn, profile, platform)` — same signature in tasks 7, 10, 14. `apply_to_flat` keyword args — same in tasks 9, 10. `overlay_profile(profile, saved_search)` returns `Profile` — used in tasks 4, 5.

**Placeholders:** none. Every code block contains real code; every command has expected output.

---

## Out-of-scope (per spec §8, NOT in this plan)

- Dashboard saved-search filter dropdown.
- Wizard support for multiple saved searches.
- Per-saved-search notification routing.
- N-strikes-out backoff after repeated FillError.
- Within-day pacing.
- N6 integration tests with time-travel (separate bead FlatPilot-dwao).
