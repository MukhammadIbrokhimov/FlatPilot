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
