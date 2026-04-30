from __future__ import annotations

from datetime import UTC, datetime, timedelta

import flatpilot.auto_apply as _aa
from flatpilot.auto_apply import is_paused
from flatpilot.profile import AutoApplySettings, Profile


def test_is_paused_false_when_no_file(tmp_db):
    assert is_paused() is False


def test_is_paused_true_when_file_exists(tmp_db):
    _aa.PAUSE_PATH.touch()
    assert is_paused() is True


def _seed_flat(conn, platform="wg-gesucht"):
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


def test_completeness_passes_for_complete_setup(tmp_db, monkeypatch):
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


def test_failures_for_flat_zero_when_no_failures(tmp_db):
    from flatpilot.auto_apply import failures_for_flat

    flat_id = _seed_flat(tmp_db)
    assert failures_for_flat(tmp_db, flat_id) == 0


def test_failures_for_flat_counts_only_real_failures(tmp_db):
    from datetime import UTC, datetime

    from flatpilot.auto_apply import failures_for_flat

    flat_id = _seed_flat(tmp_db)
    now = datetime.now(UTC).isoformat()
    # 2 real failures, 1 auto_skipped (must not count), 1 submitted (must not count)
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'failed', 'FillError: foo')",
        (flat_id, now),
    )
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'failed', 'FillError: bar')",
        (flat_id, now),
    )
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'failed', "
        "'auto_skipped: missing template')",
        (flat_id, now),
    )
    tmp_db.execute(
        "INSERT INTO applications (flat_id, platform, listing_url, title, "
        "applied_at, method, attachments_sent_json, status) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', ?, 'auto', '[]', 'submitted')",
        (flat_id, now),
    )
    assert failures_for_flat(tmp_db, flat_id) == 2


def test_pacing_extends_wait_beyond_cooldown(tmp_db):
    from datetime import UTC, datetime, timedelta

    from flatpilot.auto_apply import cooldown_remaining_sec
    from flatpilot.profile import AutoApplySettings, Profile

    base = Profile.load_example()
    profile = base.model_copy(
        update={
            "auto_apply": AutoApplySettings(
                cooldown_seconds_per_platform={"wg-gesucht": 60},
                pacing_seconds_per_platform={"wg-gesucht": 4320},
            )
        }
    )
    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    _seed_application(tmp_db, platform="wg-gesucht", status="submitted", applied_at=recent)
    remaining = cooldown_remaining_sec(tmp_db, profile, "wg-gesucht")
    # pacing (4320) wins over cooldown (60); ~30s elapsed → ~4290s remaining
    assert 4280 < remaining < 4300


def test_pacing_zero_does_not_change_cooldown_behavior(tmp_db):
    from datetime import UTC, datetime, timedelta

    from flatpilot.auto_apply import cooldown_remaining_sec

    profile = _profile_with_caps(cooldown=120)
    recent = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    _seed_application(tmp_db, platform="wg-gesucht", status="submitted", applied_at=recent)
    remaining = cooldown_remaining_sec(tmp_db, profile, "wg-gesucht")
    # Pacing default=0 → cooldown alone (120) gates → ~90s remaining
    assert 80 < remaining < 95
