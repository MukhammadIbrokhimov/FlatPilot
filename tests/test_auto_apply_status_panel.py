"""Tests for the `flatpilot status` auto-apply panel and its helpers (FlatPilot-iwu)."""

from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from flatpilot.profile import AutoApplySettings, Profile, save_profile


def _seed_flat(conn, platform="wg-gesucht", external_id_suffix=""):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
        VALUES (?, ?, 'https://example.test', 'T', ?, ?, 0)
        """,
        (f"ext-{now}-{platform}-{external_id_suffix}", platform, now, now),
    )
    return cur.lastrowid


def _seed_application(
    conn, *, flat_id, platform, status, applied_at, method="auto", notes=None,
):
    conn.execute(
        """
        INSERT INTO applications
            (flat_id, platform, listing_url, title, applied_at, method,
             attachments_sent_json, status, notes)
        VALUES (?, ?, 'https://example.test', 'T', ?, ?, '[]', ?, ?)
        """,
        (flat_id, platform, applied_at, method, status, notes),
    )


def _profile_with_caps(cap=20, cooldown=120, max_failures=3):
    return Profile.load_example().model_copy(
        update={
            "auto_apply": AutoApplySettings(
                daily_cap_per_platform={"wg-gesucht": cap, "kleinanzeigen": cap},
                cooldown_seconds_per_platform={"wg-gesucht": cooldown, "kleinanzeigen": cooldown},
                max_failures_per_flat=max_failures,
            )
        }
    )


# -------- flats_over_max_failures helper --------

def test_flats_over_max_failures_zero_when_empty(tmp_db):
    from flatpilot.auto_apply import flats_over_max_failures

    profile = _profile_with_caps(max_failures=3)
    assert flats_over_max_failures(tmp_db, profile) == 0


def test_flats_over_max_failures_counts_flats_at_or_above_threshold(tmp_db):
    from flatpilot.auto_apply import flats_over_max_failures

    today = datetime.now(UTC).isoformat()
    flat_a = _seed_flat(tmp_db, external_id_suffix="A")
    flat_b = _seed_flat(tmp_db, external_id_suffix="B")
    flat_c = _seed_flat(tmp_db, external_id_suffix="C")

    # flat A: 3 real failures → over threshold
    for _ in range(3):
        _seed_application(
            tmp_db, flat_id=flat_a, platform="wg-gesucht",
            status="failed", applied_at=today,
        )
    # flat B: 2 real failures → under threshold
    for _ in range(2):
        _seed_application(
            tmp_db, flat_id=flat_b, platform="wg-gesucht",
            status="failed", applied_at=today,
        )
    # flat C: 5 auto_skipped failures → must NOT count (not real filler errors)
    for _ in range(5):
        _seed_application(
            tmp_db, flat_id=flat_c, platform="wg-gesucht",
            status="failed", applied_at=today,
            notes="auto_skipped: missing template",
        )

    profile = _profile_with_caps(max_failures=3)
    assert flats_over_max_failures(tmp_db, profile) == 1


def test_flats_over_max_failures_ignores_manual_method(tmp_db):
    from flatpilot.auto_apply import flats_over_max_failures

    today = datetime.now(UTC).isoformat()
    flat_id = _seed_flat(tmp_db)
    for _ in range(5):
        _seed_application(
            tmp_db, flat_id=flat_id, platform="wg-gesucht",
            status="failed", applied_at=today, method="manual",
        )
    profile = _profile_with_caps(max_failures=3)
    assert flats_over_max_failures(tmp_db, profile) == 0


# -------- panel rendering via CLI --------

def _run_status() -> str:
    from flatpilot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    return result.output


def test_status_panel_shows_active_when_not_paused(tmp_db):
    save_profile(_profile_with_caps())
    out = _run_status()
    assert "Auto-apply" in out
    assert "ACTIVE" in out


def test_status_panel_shows_paused_when_pause_file_present(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    save_profile(_profile_with_caps())
    PAUSE_PATH.touch()
    out = _run_status()
    assert "PAUSED" in out


def test_status_panel_shows_per_platform_usage_and_cooldown(tmp_db):
    save_profile(_profile_with_caps(cap=20, cooldown=120))
    today = datetime.now(UTC).isoformat()
    flat_id = _seed_flat(tmp_db)
    _seed_application(
        tmp_db, flat_id=flat_id, platform="wg-gesucht",
        status="submitted", applied_at=today,
    )
    out = _run_status()
    assert "wg-gesucht" in out
    assert "1 / 20" in out
    # cooldown is 120s configured; just-submitted ≈ 120s remaining
    assert "cooldown" in out


def test_status_panel_marks_platform_without_filler(tmp_db):
    # Use default AutoApplySettings — its cap dict includes inberlinwohnen,
    # which has no registered filler today. The panel must list it but
    # mark it as not auto-applying.
    save_profile(Profile.load_example())
    out = _run_status()
    assert "inberlinwohnen" in out
    assert "no filler" in out


def test_status_panel_shows_flats_over_max_failures(tmp_db):
    save_profile(_profile_with_caps(max_failures=3))
    today = datetime.now(UTC).isoformat()
    flat_id = _seed_flat(tmp_db)
    for _ in range(3):
        _seed_application(
            tmp_db, flat_id=flat_id, platform="wg-gesucht",
            status="failed", applied_at=today,
        )
    out = _run_status()
    assert "Flats over max failures" in out
    assert "1" in out


def test_status_panel_skipped_when_no_profile(tmp_db):
    out = _run_status()
    # Without a profile we can't compute caps/cooldowns; the panel must not
    # crash and should explain why it's skipped.
    assert "Auto-apply" in out
    assert "no profile" in out.lower()
