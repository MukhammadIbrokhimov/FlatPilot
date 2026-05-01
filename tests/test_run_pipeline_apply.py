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
    from flatpilot.apply import ApplyOutcome
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


def test_skips_after_max_failures_reached(tmp_db):
    from datetime import UTC, datetime
    from unittest.mock import patch

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
    # Seed 3 prior FillError-style failures for this flat (default max_failures_per_flat=3).
    for _ in range(3):
        tmp_db.execute(
            "INSERT INTO applications (flat_id, platform, listing_url, title, "
            "applied_at, method, attachments_sent_json, status, notes) "
            "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'failed', "
            "'FillError: x')",
            (flat_id, now),
        )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked, \
         patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)):
        run_pipeline_apply(profile, Console())

    mocked.assert_not_called()


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


# --- safety-rail integration: cap-exhausted + cooldown-active ---------
# The existing _seed_flat helper at line 25 hard-codes external_id='e1',
# so calling it twice trips the (platform, external_id) UNIQUE
# constraint. The new tests need 2-3 distinct flats each, so we add a
# local helper rather than mutating the existing one (which the older
# tests rely on).


def _seed_flat_with(conn, *, external_id, platform="wg-gesucht"):
    """Like _seed_flat but with caller-supplied external_id (UNIQUE)."""
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at, requires_wbs) "
        "VALUES (?, ?, 'https://x', 'T', ?, ?, 0)",
        (external_id, platform, now, now),
    )
    return cur.lastrowid


def test_daily_cap_exhausted_skips_pending_match(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import AutoApplySettings, profile_hash

    base = _profile_with_one_auto_search()
    profile = base.model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 2},
                cooldown_seconds_per_platform={"wg-gesucht": 0},
                pacing_seconds_per_platform={"wg-gesucht": 0},
            )
        }
    )
    save_profile(profile)
    today = datetime.now(UTC).isoformat()

    # Seed 2 submitted rows = cap reached. Each prior flat gets a
    # unique external_id so the UNIQUE constraint holds.
    for i in range(2):
        prior_flat = _seed_flat_with(tmp_db, external_id=f"prior-{i}")
        tmp_db.execute(
            "INSERT INTO applications "
            "(flat_id, platform, listing_url, title, applied_at, method, "
            " attachments_sent_json, status) "
            "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'submitted')",
            (prior_flat, today),
        )

    pending_flat = _seed_flat_with(tmp_db, external_id="pending")
    _seed_match(
        tmp_db,
        flat_id=pending_flat,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())

    mocked.assert_not_called()
    new_apps = tmp_db.execute(
        "SELECT COUNT(*) FROM applications WHERE flat_id = ?", (pending_flat,)
    ).fetchone()[0]
    assert new_apps == 0


def test_active_cooldown_skips_pending_match(tmp_db):
    from datetime import timedelta

    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import AutoApplySettings, profile_hash

    base = _profile_with_one_auto_search()
    profile = base.model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 100},
                cooldown_seconds_per_platform={"wg-gesucht": 120},
                pacing_seconds_per_platform={"wg-gesucht": 0},
            )
        }
    )
    save_profile(profile)

    # One submitted row 30s ago → 90s cooldown remaining > 0.
    prior_flat = _seed_flat_with(tmp_db, external_id="prior-cool")
    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    tmp_db.execute(
        "INSERT INTO applications "
        "(flat_id, platform, listing_url, title, applied_at, method, "
        " attachments_sent_json, status) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'submitted')",
        (prior_flat, recent),
    )

    pending_flat = _seed_flat_with(tmp_db, external_id="pending-cool")
    _seed_match(
        tmp_db,
        flat_id=pending_flat,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())

    mocked.assert_not_called()
