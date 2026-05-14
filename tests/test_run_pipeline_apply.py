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


def test_skips_flat_with_recent_listing_expired_row(tmp_db):
    # FlatPilot-tgw: a flat that was already classified as expired within
    # the last 7 days should not be retried — running the filler again
    # would just navigate, hit the same redirect/missing-CTA, and burn a
    # browser session. The auto-apply SELECT excludes such flats.
    from datetime import UTC, datetime, timedelta
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
    recent = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'failed', "
        "'auto_skipped: listing_expired (redirected)')",
        (flat_id, recent),
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked, \
         patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)):
        run_pipeline_apply(profile, Console())
    # If the SELECT exclusion is missing, _try_flat would proceed to
    # apply_to_flat once because completeness is mocked OK, cooldown is
    # 0 (auto_skipped rows excluded), and failures-for-flat is 0
    # (auto_skipped rows excluded). assert_not_called proves the SELECT
    # itself dropped the flat from the result set.
    mocked.assert_not_called()


def test_skips_flat_with_existing_filler_not_registered_row(tmp_db):
    # FlatPilot-289: when a flat's platform has no registered filler
    # (today: inberlinwohnen, immoscout24), completeness_ok writes one
    # 'auto_skipped: filler not registered' row. The queue-selector SQL
    # must then exclude the flat on subsequent passes so the table does
    # not accumulate one such row per pass per matched flat. No TTL on
    # this exclusion — the state is permanent until a filler is added.
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, platform="inberlinwohnen")
    _seed_match(
        tmp_db,
        flat_id=flat_id,
        profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    earlier = datetime.now(UTC).isoformat()
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status, notes) "
        "VALUES (?, 'inberlinwohnen', 'https://x', 'T', ?, 'auto', '[]', "
        "'failed', "
        "'auto_skipped: filler not registered for platform "
        "''inberlinwohnen''')",
        (flat_id, earlier),
    )

    before = tmp_db.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()["n"]

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked:
        run_pipeline_apply(profile, Console())

    after = tmp_db.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()["n"]

    mocked.assert_not_called()
    # No new auto_skipped row added on this pass — the SELECT excluded
    # the flat outright. Without the exclusion, _try_flat would have
    # written a second 'filler not registered' row via completeness_ok.
    assert after == before


def test_retries_flat_with_old_listing_expired_row(tmp_db):
    # FlatPilot-tgw: TTL on the listing_expired exclusion ensures a real
    # selector regression heals once the selectors are fixed. After 7
    # days, the flat is eligible again — the filler will reclassify if
    # the listing is genuinely gone, or apply normally if the prior
    # 'expired' verdict was caused by a since-fixed selector regression.
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

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
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'failed', "
        "'auto_skipped: listing_expired (redirected)')",
        (flat_id, old),
    )

    with patch("flatpilot.auto_apply.apply_to_flat") as mocked, \
         patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)):
        mocked.return_value = ApplyOutcome(
            status="submitted", application_id=1, fill_report=None
        )
        run_pipeline_apply(profile, Console())

    mocked.assert_called_once()


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


def test_unhandled_exception_on_one_flat_does_not_stop_the_queue(tmp_db):
    # An unexpected exception inside _try_flat (e.g. Playwright crash,
    # anti-bot challenge, AttributeError in a filler edge case) must not
    # abandon every remaining flat in the queue. The pipeline catches it
    # per-flat, logs, and moves on. Drain mode depends on this.
    from flatpilot.apply import ApplyOutcome
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)

    # Three flats in queue; the first will blow up, the next two must
    # still be attempted.
    flat_ids: list[int] = []
    for i in range(3):
        cur = tmp_db.execute(
            "INSERT INTO flats (external_id, platform, listing_url, title, "
            "scraped_at, first_seen_at, requires_wbs) "
            "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, ?, 0)",
            (f"crash-q-{i}", datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
        flat_id = int(cur.lastrowid or 0)
        flat_ids.append(flat_id)
        _seed_match(
            tmp_db, flat_id=flat_id, profile_hash=profile_hash(profile),
            matched_saved_searches=["ss1"],
        )

    apply_calls: list[int] = []

    def apply_side_effect(flat_id, **_kwargs):
        if flat_id == flat_ids[0]:
            raise RuntimeError("simulated Playwright crash on first flat")
        apply_calls.append(flat_id)
        return ApplyOutcome(
            status="submitted", application_id=len(apply_calls), fill_report=None,
        )

    with (
        patch("flatpilot.auto_apply.apply_to_flat", side_effect=apply_side_effect),
        patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)),
        # Bypass cooldown so flats 2 and 3 are reachable in one pass.
        patch("flatpilot.auto_apply.cooldown_remaining_sec", return_value=0.0),
    ):
        run_pipeline_apply(profile, Console())

    assert apply_calls == flat_ids[1:]


def test_keyboard_interrupt_propagates_through_error_isolation(tmp_db):
    # The error-isolation must NOT swallow KeyboardInterrupt / SystemExit —
    # otherwise Ctrl-C during a drain would never bubble out.
    import pytest

    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile_with_one_auto_search()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db, flat_id=flat_id, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with (
        patch("flatpilot.auto_apply.apply_to_flat", side_effect=KeyboardInterrupt),
        patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)),
        pytest.raises(KeyboardInterrupt),
    ):
        run_pipeline_apply(profile, Console())
