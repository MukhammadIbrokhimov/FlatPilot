"""Tests for `flatpilot run --drain` (FlatPilot-8kt continuation).

drain=True changes the cooldown branch from 'skip and return' to
'sleep and retry'. The cap branch still returns (cap won't reset
until UTC midnight). A pause file appearing mid-drain stops the loop.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

from rich.console import Console

from flatpilot.profile import (
    AutoApplySettings,
    Profile,
    SavedSearch,
    save_profile,
)


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


def _seed_flat(conn, *, external_id="e1", platform="wg-gesucht"):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title, scraped_at, first_seen_at,
            requires_wbs
        ) VALUES (?, ?, 'https://x', 'T', ?, ?, 0)
        """,
        (external_id, platform, now, now),
    )
    return cur.lastrowid


def _seed_application(conn, *, flat_id, platform, status, applied_at, notes=None):
    conn.execute(
        """
        INSERT INTO applications
            (flat_id, platform, listing_url, title, applied_at, method,
             attachments_sent_json, status, notes)
        VALUES (?, ?, 'https://x', 'T', ?, 'auto', '[]', ?, ?)
        """,
        (flat_id, platform, applied_at, status, notes),
    )


def _profile():
    return Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 20},
                cooldown_seconds_per_platform={"wg-gesucht": 120},
            ),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )


def test_drain_false_skips_on_cooldown(tmp_db):
    # Default behaviour: cooldown active → skip + return, do NOT sleep,
    # do NOT call apply_to_flat. Mirrors the pre-FlatPilot-8kt step-3 path.
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db, flat_id=flat_id, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    # Active cooldown: a recent submitted application on the same platform.
    just_now = datetime.now(UTC).isoformat()
    _seed_application(
        tmp_db, flat_id=flat_id, platform="wg-gesucht",
        status="submitted", applied_at=just_now,
    )
    # Add a second flat in 'match' state so the apply queue has the new one.
    flat_id_2 = _seed_flat(tmp_db, external_id="e2")
    _seed_match(
        tmp_db, flat_id=flat_id_2, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with (
        patch("flatpilot.auto_apply.time.sleep") as mocked_sleep,
        patch("flatpilot.auto_apply.apply_to_flat") as mocked_apply,
    ):
        run_pipeline_apply(profile, Console())  # drain defaults to False

    mocked_sleep.assert_not_called()
    mocked_apply.assert_not_called()


def test_drain_true_sleeps_through_cooldown_then_applies(tmp_db):
    from flatpilot.apply import ApplyOutcome
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile()
    save_profile(profile)
    flat_id_old = _seed_flat(tmp_db, external_id="old")
    _seed_match(
        tmp_db, flat_id=flat_id_old, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    # Active cooldown from a recent submitted application on the OLD flat.
    just_now = datetime.now(UTC).isoformat()
    _seed_application(
        tmp_db, flat_id=flat_id_old, platform="wg-gesucht",
        status="submitted", applied_at=just_now,
    )
    # Fresh flat 'matches' but is gated by the cooldown the OLD submit set.
    flat_id_new = _seed_flat(tmp_db, external_id="new")
    _seed_match(
        tmp_db, flat_id=flat_id_new, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    fake_outcome = ApplyOutcome(status="submitted", application_id=999, fill_report=None)
    with (
        patch("flatpilot.auto_apply.time.sleep") as mocked_sleep,
        patch("flatpilot.auto_apply.apply_to_flat", return_value=fake_outcome) as mocked_apply,
        patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)),
    ):
        run_pipeline_apply(profile, Console(), drain=True)

    # Drain mode slept through the cooldown at least once and then applied.
    assert mocked_sleep.call_count >= 1
    assert mocked_apply.call_count >= 1


def test_drain_true_does_not_sleep_when_cap_exhausted(tmp_db):
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    # Cap = 1, already exhausted by a submitted row.
    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 1},
                cooldown_seconds_per_platform={"wg-gesucht": 120},
            ),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, external_id="capped")
    _seed_match(
        tmp_db, flat_id=flat_id, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    _seed_application(
        tmp_db, flat_id=flat_id, platform="wg-gesucht",
        status="submitted", applied_at=datetime.now(UTC).isoformat(),
    )
    # Add another match — drain should still skip because cap is hit.
    flat_id_2 = _seed_flat(tmp_db, external_id="next")
    _seed_match(
        tmp_db, flat_id=flat_id_2, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    with (
        patch("flatpilot.auto_apply.time.sleep") as mocked_sleep,
        patch("flatpilot.auto_apply.apply_to_flat") as mocked_apply,
    ):
        run_pipeline_apply(profile, Console(), drain=True)

    mocked_sleep.assert_not_called()
    mocked_apply.assert_not_called()


def test_drain_true_stops_when_pause_file_appears_during_sleep(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH, run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, external_id="cool")
    _seed_match(
        tmp_db, flat_id=flat_id, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )
    just_now = datetime.now(UTC).isoformat()
    _seed_application(
        tmp_db, flat_id=flat_id, platform="wg-gesucht",
        status="submitted", applied_at=just_now,
    )
    flat_id_2 = _seed_flat(tmp_db, external_id="next")
    _seed_match(
        tmp_db, flat_id=flat_id_2, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    def fake_sleep(_seconds):
        # User taps `flatpilot pause` mid-drain.
        PAUSE_PATH.touch()

    with (
        patch("flatpilot.auto_apply.time.sleep", side_effect=fake_sleep),
        patch("flatpilot.auto_apply.apply_to_flat") as mocked_apply,
    ):
        run_pipeline_apply(profile, Console(), drain=True)

    # Loop must abort without applying after pause appeared.
    mocked_apply.assert_not_called()


def test_drain_true_no_cooldown_applies_immediately(tmp_db):
    # No prior application = no cooldown; drain should behave like default.
    from flatpilot.apply import ApplyOutcome
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile()
    save_profile(profile)
    flat_id = _seed_flat(tmp_db)
    _seed_match(
        tmp_db, flat_id=flat_id, profile_hash=profile_hash(profile),
        matched_saved_searches=["ss1"],
    )

    fake_outcome = ApplyOutcome(status="submitted", application_id=1, fill_report=None)
    with (
        patch("flatpilot.auto_apply.time.sleep") as mocked_sleep,
        patch("flatpilot.auto_apply.apply_to_flat", return_value=fake_outcome) as mocked_apply,
        patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)),
    ):
        run_pipeline_apply(profile, Console(), drain=True)

    mocked_sleep.assert_not_called()
    assert mocked_apply.call_count == 1


def test_drain_complete_when_all_reachable_caps_hit(tmp_db):
    # FlatPilot-rv2: drain loop's exit predicate. Two reachable platforms
    # (wg-gesucht, kleinanzeigen, both with fillers + cap>0), both at cap.
    from flatpilot.auto_apply import drain_complete

    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 1, "kleinanzeigen": 1},
                cooldown_seconds_per_platform={"wg-gesucht": 120, "kleinanzeigen": 120},
            ),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )
    fid_wg = _seed_flat(tmp_db, external_id="wg", platform="wg-gesucht")
    fid_ka = _seed_flat(tmp_db, external_id="ka", platform="kleinanzeigen")
    now = datetime.now(UTC).isoformat()
    _seed_application(
        tmp_db, flat_id=fid_wg, platform="wg-gesucht",
        status="submitted", applied_at=now,
    )
    _seed_application(
        tmp_db, flat_id=fid_ka, platform="kleinanzeigen",
        status="submitted", applied_at=now,
    )

    assert drain_complete(tmp_db, profile, empty_pass_streak=0) is True


def test_drain_not_complete_when_a_platform_has_remaining_cap(tmp_db):
    from flatpilot.auto_apply import drain_complete

    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 5, "kleinanzeigen": 1},
            ),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )
    fid_ka = _seed_flat(tmp_db, external_id="ka", platform="kleinanzeigen")
    _seed_application(
        tmp_db, flat_id=fid_ka, platform="kleinanzeigen", status="submitted",
        applied_at=datetime.now(UTC).isoformat(),
    )
    # wg-gesucht has 5 cap remaining; not done.
    assert drain_complete(tmp_db, profile, empty_pass_streak=0) is False


def test_drain_complete_ignores_unreachable_platforms(tmp_db):
    # inberlinwohnen has cap=20 in defaults but no filler registered, so
    # the drain loop must not wait on it. Reachable wg-gesucht is at cap.
    from flatpilot.auto_apply import drain_complete

    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": 1, "inberlinwohnen": 20},
            ),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )
    fid = _seed_flat(tmp_db, external_id="wg", platform="wg-gesucht")
    _seed_application(
        tmp_db, flat_id=fid, platform="wg-gesucht", status="submitted",
        applied_at=datetime.now(UTC).isoformat(),
    )
    assert drain_complete(tmp_db, profile, empty_pass_streak=0) is True


def test_drain_complete_when_empty_streak_threshold_reached(tmp_db):
    # Two passes in a row with zero submits → bail even if cap not hit.
    from flatpilot.auto_apply import drain_complete

    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(daily_cap_per_platform={"wg-gesucht": 20}),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )
    assert drain_complete(tmp_db, profile, empty_pass_streak=2) is True
    assert drain_complete(tmp_db, profile, empty_pass_streak=1) is False


def test_drain_complete_when_no_reachable_platforms(tmp_db):
    # Every configured platform is either cap=0 or has no filler.
    from flatpilot.auto_apply import drain_complete

    profile = Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                # cap=0 disables auto-apply on wg-gesucht; inberlinwohnen
                # has no filler. Nothing for drain to do.
                daily_cap_per_platform={"wg-gesucht": 0, "inberlinwohnen": 20},
            ),
            "saved_searches": [SavedSearch(name="ss1", auto_apply=True)],
        }
    )
    assert drain_complete(tmp_db, profile, empty_pass_streak=0) is True


def test_collect_failures_dedups_retries_per_flat(tmp_db):
    # Same flat fails twice with the same error class → one record.
    from flatpilot.auto_apply import collect_failures_since

    fid = _seed_flat(tmp_db, external_id="bad", platform="kleinanzeigen")
    base = datetime.now(UTC).isoformat()
    _seed_application(
        tmp_db, flat_id=fid, platform="kleinanzeigen", status="failed",
        applied_at=base,
        notes="kleinanzeigen: neither success nor error indicator appeared",
    )
    _seed_application(
        tmp_db, flat_id=fid, platform="kleinanzeigen", status="failed",
        applied_at=base,
        notes="kleinanzeigen: neither success nor error indicator appeared",
    )
    failures = collect_failures_since(tmp_db, "1970-01-01T00:00:00+00:00")
    assert len(failures) == 1
    assert failures[0]["flat_id"] == fid
    assert failures[0]["platform"] == "kleinanzeigen"


def test_collect_failures_excludes_auto_skipped_rows(tmp_db):
    # auto_skipped: rows are not filler bugs and must not appear in the summary.
    from flatpilot.auto_apply import collect_failures_since

    fid = _seed_flat(tmp_db, external_id="skip", platform="inberlinwohnen")
    _seed_application(
        tmp_db, flat_id=fid, platform="inberlinwohnen", status="failed",
        applied_at=datetime.now(UTC).isoformat(),
        notes="auto_skipped: filler not registered for platform 'inberlinwohnen'",
    )
    assert collect_failures_since(tmp_db, "1970-01-01T00:00:00+00:00") == []


def test_collect_failures_respects_since_iso(tmp_db):
    from flatpilot.auto_apply import collect_failures_since

    fid = _seed_flat(tmp_db, external_id="old", platform="wg-gesucht")
    _seed_application(
        tmp_db, flat_id=fid, platform="wg-gesucht", status="failed",
        applied_at="2020-01-01T00:00:00+00:00",
        notes="wg-gesucht: selector_missing",
    )
    # Cutoff is after the row → excluded.
    assert collect_failures_since(tmp_db, "2025-01-01T00:00:00+00:00") == []


def test_drain_processes_multiple_flats_in_one_run(tmp_db):
    # Drain's whole point: one flatpilot run invocation submits to several
    # flats by sleeping through cooldowns between them.
    from flatpilot.apply import ApplyOutcome
    from flatpilot.auto_apply import run_pipeline_apply
    from flatpilot.profile import profile_hash

    profile = _profile()
    save_profile(profile)
    n_flats = 3
    flat_ids: list[int] = []
    for i in range(n_flats):
        fid = _seed_flat(tmp_db, external_id=f"flat-{i}")
        flat_ids.append(fid)
        _seed_match(
            tmp_db, flat_id=fid, profile_hash=profile_hash(profile),
            matched_saved_searches=["ss1"],
        )

    apply_calls: list[int] = []

    def apply_side_effect(flat_id, **_kwargs):
        apply_calls.append(flat_id)
        # Each successful apply writes a 'submitted' row, which sets the
        # cooldown for subsequent flats. Simulate that here so the next
        # iteration's cooldown_remaining_sec returns >0.
        _seed_application(
            tmp_db, flat_id=flat_id, platform="wg-gesucht",
            status="submitted", applied_at=datetime.now(UTC).isoformat(),
        )
        return ApplyOutcome(status="submitted", application_id=len(apply_calls), fill_report=None)

    with (
        patch("flatpilot.auto_apply.time.sleep") as mocked_sleep,
        patch("flatpilot.auto_apply.apply_to_flat", side_effect=apply_side_effect),
        patch("flatpilot.auto_apply.completeness_ok", return_value=(True, None)),
    ):
        run_pipeline_apply(profile, Console(), drain=True)

    # All n flats applied; sleep was invoked between them (n-1 times at minimum).
    assert apply_calls == flat_ids
    assert mocked_sleep.call_count >= n_flats - 1
