"""Tests for `flatpilot reclassify-submits` (FlatPilot-8kt cleanup)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from flatpilot.reclassify import (
    _RECLASSIFIED_PREFIX,
    apply_reclassification,
    find_candidates,
)


def _seed_flat(conn, *, listing_url: str, title: str, platform: str = "wg-gesucht") -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats
            (external_id, platform, listing_url, title,
             scraped_at, first_seen_at, requires_wbs)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (f"ext-{listing_url}", platform, listing_url, title, now, now),
    )
    return int(cur.lastrowid)


def _seed_app(
    conn, *, flat_id: int, status: str, notes: str | None,
    applied_at: str | None = None, platform: str = "wg-gesucht",
    method: str = "auto",
) -> int:
    if applied_at is None:
        applied_at = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO applications
            (flat_id, platform, listing_url, title, applied_at, method,
             attachments_sent_json, status, notes)
        VALUES (?, ?, 'https://example.test', 'T', ?, ?, '[]', ?, ?)
        """,
        (flat_id, platform, applied_at, method, status, notes),
    )
    return int(cur.lastrowid)


def _seed_silent_success_pair(
    conn, *, listing_url: str = "https://example.test/foo.html",
    title: str = "Test flat",
    failed_notes: str = "wg-gesucht: submit did not navigate away from the form URL (...)",
) -> tuple[int, int]:
    """Return (failed_app_id, listing_expired_app_id) for one flat."""
    flat_id = _seed_flat(conn, listing_url=listing_url, title=title)
    earlier = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    later = datetime.now(UTC).isoformat()
    failed_id = _seed_app(
        conn, flat_id=flat_id, status="failed", notes=failed_notes,
        applied_at=earlier,
    )
    expired_id = _seed_app(
        conn, flat_id=flat_id, status="failed",
        notes=(
            f"auto_skipped: listing_expired "
            f"(wg-gesucht: no contact CTA matching ... at {listing_url})"
        ),
        applied_at=later,
    )
    return failed_id, expired_id


# -------- find_candidates --------

def test_find_candidates_empty_when_no_apps(tmp_db):
    assert find_candidates(tmp_db) == []


def test_finds_silent_success_with_listing_expired_followup(tmp_db):
    failed_id, _ = _seed_silent_success_pair(tmp_db)
    candidates = find_candidates(tmp_db)
    assert [c.application_id for c in candidates] == [failed_id]
    assert candidates[0].platform == "wg-gesucht"


def test_finds_post_fix_error_message_too(tmp_db):
    # After PR #49, the error string changed; both shapes must match.
    failed_id, _ = _seed_silent_success_pair(
        tmp_db,
        failed_notes="wg-gesucht: submit verification failed at https://... — messenger form ...",
    )
    assert [c.application_id for c in find_candidates(tmp_db)] == [failed_id]


def test_ignores_failed_without_listing_expired_followup(tmp_db):
    # Failed silent-success-style row but no listing_expired follow-up =
    # we don't have proof the submit went through. Don't reclassify.
    flat_id = _seed_flat(tmp_db, listing_url="https://example.test/x.html", title="T")
    _seed_app(
        tmp_db, flat_id=flat_id, status="failed",
        notes="wg-gesucht: submit did not navigate away from the form URL (...)",
    )
    assert find_candidates(tmp_db) == []


def test_ignores_failed_with_unrelated_notes(tmp_db):
    # Genuine selector-missing failures must NOT be reclassified.
    flat_id = _seed_flat(tmp_db, listing_url="https://example.test/y.html", title="T")
    earlier = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    later = datetime.now(UTC).isoformat()
    _seed_app(
        tmp_db, flat_id=flat_id, status="failed",
        notes="wg-gesucht: no submit button matching ... at form URL",
        applied_at=earlier,
    )
    _seed_app(
        tmp_db, flat_id=flat_id, status="failed",
        notes="auto_skipped: listing_expired (...)",
        applied_at=later,
    )
    assert find_candidates(tmp_db) == []


def test_ignores_already_submitted(tmp_db):
    # If the row is already status='submitted' it doesn't match. Idempotency.
    failed_id, _ = _seed_silent_success_pair(tmp_db)
    candidates = find_candidates(tmp_db)
    apply_reclassification(tmp_db, candidates)
    # Second pass should find nothing.
    assert find_candidates(tmp_db) == []
    # Verify the row is now 'submitted' with the audit prefix.
    row = tmp_db.execute(
        "SELECT status, notes FROM applications WHERE id = ?", (failed_id,)
    ).fetchone()
    assert row["status"] == "submitted"
    assert row["notes"].startswith(_RECLASSIFIED_PREFIX)


def test_only_failed_method_auto(tmp_db):
    # Manual (method='manual') applies must not be touched even if their
    # notes match — only auto-apply silent successes are in scope.
    flat_id = _seed_flat(tmp_db, listing_url="https://example.test/z.html", title="T")
    earlier = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    later = datetime.now(UTC).isoformat()
    _seed_app(
        tmp_db, flat_id=flat_id, status="failed", method="manual",
        notes="wg-gesucht: submit did not navigate away from the form URL (...)",
        applied_at=earlier,
    )
    _seed_app(
        tmp_db, flat_id=flat_id, status="failed", method="auto",
        notes="auto_skipped: listing_expired (...)",
        applied_at=later,
    )
    assert find_candidates(tmp_db) == []


# -------- apply_reclassification --------

def test_apply_reclassification_updates_status_and_audits_notes(tmp_db):
    failed_id, _ = _seed_silent_success_pair(
        tmp_db,
        failed_notes="wg-gesucht: submit did not navigate away from form URL (X)",
    )
    candidates = find_candidates(tmp_db)
    n = apply_reclassification(tmp_db, candidates)
    assert n == 1
    row = tmp_db.execute(
        "SELECT status, notes FROM applications WHERE id = ?", (failed_id,)
    ).fetchone()
    assert row["status"] == "submitted"
    assert row["notes"].startswith(_RECLASSIFIED_PREFIX)
    # Original notes preserved after the prefix.
    assert "submit did not navigate" in row["notes"]


def test_apply_reclassification_zero_when_empty(tmp_db):
    assert apply_reclassification(tmp_db, []) == 0


# -------- CLI smoke tests --------

def _run_reclassify(*args: str) -> str:
    from flatpilot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["reclassify-submits", *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_cli_dry_run_does_not_mutate(tmp_db):
    failed_id, _ = _seed_silent_success_pair(tmp_db)
    out = _run_reclassify()
    assert "Dry run" in out
    assert "1 candidate" in out
    # Row unchanged.
    row = tmp_db.execute(
        "SELECT status FROM applications WHERE id = ?", (failed_id,)
    ).fetchone()
    assert row["status"] == "failed"


def test_cli_apply_mutates(tmp_db):
    failed_id, _ = _seed_silent_success_pair(tmp_db)
    out = _run_reclassify("--apply")
    assert "Reclassified 1" in out
    row = tmp_db.execute(
        "SELECT status FROM applications WHERE id = ?", (failed_id,)
    ).fetchone()
    assert row["status"] == "submitted"


def test_cli_no_candidates_message(tmp_db):
    out = _run_reclassify()
    assert "nothing to do" in out.lower()
