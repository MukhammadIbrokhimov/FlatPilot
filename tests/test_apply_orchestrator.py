"""Unit tests for ``apply_to_flat`` — the L4 orchestrator.

The filler is monkey-patched so no browser starts. We assert: which DB
rows are written, with which column values, in which scenarios.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from flatpilot.apply import ApplyOutcome, apply_to_flat
from flatpilot.fillers.base import (
    FillReport,
    NotAuthenticatedError,
)
from flatpilot.profile import Attachments, Profile, save_profile


def _profile_for_test(tmp_path: Path) -> Profile:
    """Use Profile.load_example() then narrow attachments to a real file."""
    pdf = tmp_path / ".flatpilot" / "attachments" / "schufa.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    profile = Profile.load_example().model_copy(
        update={
            "city": "Berlin",
            "attachments": Attachments(default=["schufa.pdf"], per_platform={}),
        }
    )
    save_profile(profile)
    return profile


def _insert_flat(conn, *, platform: str = "wg-gesucht", external_id: str = "ext-1") -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            rent_warm_eur, rooms, district,
            scraped_at, first_seen_at, requires_wbs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            external_id,
            platform,
            "https://www.wg-gesucht.de/wohnungen-in-berlin.123.html",
            "Bright 2-room Friedrichshain",
            900.0,
            2.0,
            "Friedrichshain",
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def _write_template(tmp_path: Path) -> None:
    tpl_dir = tmp_path / ".flatpilot" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "wg-gesucht.md").write_text(
        "Hallo, ich bin interessiert an $title.\n",
        encoding="utf-8",
    )


def _stub_filler(monkeypatch, *, submitted: bool = True, raises: Exception | None = None):
    captured: dict = {}

    def fake_fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None):
        captured.update(
            {
                "listing_url": listing_url,
                "message": message,
                "attachments": attachments,
                "submit": submit,
                "screenshot_dir": screenshot_dir,
            }
        )
        if raises is not None:
            raise raises
        return FillReport(
            platform="wg-gesucht",
            listing_url=listing_url,
            contact_url=listing_url,
            fields_filled={"message": message},
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=screenshot_dir / "shot.png" if screenshot_dir else None,
            submitted=submitted,
            started_at="2026-04-25T10:00:00+00:00",
            finished_at="2026-04-25T10:00:05+00:00",
        )

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill",
        fake_fill,
        raising=True,
    )
    return captured


def test_apply_dry_run_writes_no_row(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    captured = _stub_filler(monkeypatch, submitted=False)

    outcome = apply_to_flat(flat_id, dry_run=True)

    assert isinstance(outcome, ApplyOutcome)
    assert outcome.status == "dry_run"
    assert outcome.application_id is None
    assert outcome.fill_report is not None
    assert outcome.fill_report.submitted is False
    assert captured["submit"] is False
    assert "interessiert an Bright 2-room Friedrichshain" in captured["message"]
    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_live_writes_submitted_row(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, submitted=True)

    outcome = apply_to_flat(flat_id, dry_run=False)

    assert outcome.status == "submitted"
    assert outcome.application_id is not None

    row = tmp_db.execute(
        "SELECT * FROM applications WHERE id = ?", (outcome.application_id,)
    ).fetchone()
    assert row["status"] == "submitted"
    assert row["method"] == "manual"
    assert row["platform"] == "wg-gesucht"
    assert row["flat_id"] == flat_id
    assert row["title"] == "Bright 2-room Friedrichshain"
    assert "Bright 2-room" in row["message_sent"]
    assert "schufa.pdf" in row["attachments_sent_json"]


def test_apply_refuses_double_submit_when_submitted_row_exists(tmp_db, tmp_path, monkeypatch):
    from flatpilot.apply import AlreadyAppliedError

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, submitted=True)

    # First apply lands a submitted row.
    apply_to_flat(flat_id, dry_run=False)

    # Second apply must refuse and not write a second row.
    with pytest.raises(AlreadyAppliedError, match=f"flat {flat_id} already has"):
        apply_to_flat(flat_id, dry_run=False)

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM applications WHERE flat_id = ? AND status = 'submitted'",
        (flat_id,),
    ).fetchone()[0]
    assert cnt == 1


def test_apply_dry_run_after_submitted_row_still_allowed(tmp_db, tmp_path, monkeypatch):
    """Dry-run after a real submit is fine — doesn't write a row anyway."""
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, submitted=True)

    apply_to_flat(flat_id, dry_run=False)
    # Dry-run path skips the idempotency check.
    outcome = apply_to_flat(flat_id, dry_run=True)
    assert outcome.status == "dry_run"


def test_apply_live_filler_failure_writes_failed_row_and_raises(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, raises=NotAuthenticatedError("session expired"))

    with pytest.raises(NotAuthenticatedError):
        apply_to_flat(flat_id, dry_run=False)

    row = tmp_db.execute(
        "SELECT status, notes FROM applications WHERE flat_id = ?", (flat_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "session expired" in row["notes"]


def test_apply_listing_expired_records_auto_skipped_listing_expired_note(
    tmp_db, tmp_path, monkeypatch
):
    # FlatPilot-tgw: when the filler raises ListingExpiredError, the
    # orchestrator must record the row with notes prefixed by
    # 'auto_skipped: listing_expired'. This makes the row invisible to
    # cooldown_remaining_sec and failures_for_flat (existing
    # 'auto_skipped:%' exclusions), and visible to run_pipeline_apply's
    # listing-expired SELECT exclusion. The exception still re-raises so
    # the CLI / dashboard exit path is unchanged.
    from flatpilot.fillers.base import ListingExpiredError

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, raises=ListingExpiredError("redirected to category"))

    with pytest.raises(ListingExpiredError):
        apply_to_flat(flat_id, dry_run=False)

    row = tmp_db.execute(
        "SELECT status, notes FROM applications WHERE flat_id = ?", (flat_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["notes"].startswith("auto_skipped: listing_expired")
    assert "redirected to category" in row["notes"]


def test_apply_unknown_flat_id_raises_lookup_error_no_row(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    _stub_filler(monkeypatch)

    with pytest.raises(LookupError, match="no flat with id 999"):
        apply_to_flat(999, dry_run=False)

    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_missing_template_raises_no_row(tmp_db, tmp_path, monkeypatch):
    from flatpilot.compose import TemplateMissingError

    _profile_for_test(tmp_path)
    # No template written.
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch)

    with pytest.raises(TemplateMissingError):
        apply_to_flat(flat_id, dry_run=False)

    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_missing_attachment_raises_no_row(tmp_db, tmp_path, monkeypatch):
    from flatpilot.attachments import AttachmentError

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch)

    # Override profile to reference an attachment that doesn't exist.
    profile = Profile.load_example().model_copy(
        update={
            "city": "Berlin",
            "attachments": Attachments(default=["does-not-exist.pdf"], per_platform={}),
        }
    )
    save_profile(profile)

    with pytest.raises(AttachmentError):
        apply_to_flat(flat_id, dry_run=False)

    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_no_profile_raises(tmp_db, tmp_path, monkeypatch):
    from flatpilot.errors import ProfileMissingError

    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch)

    with pytest.raises(ProfileMissingError):
        apply_to_flat(flat_id, dry_run=False)


def test_double_submit_raises_plain_already_applied_not_subclass(tmp_db, tmp_path, monkeypatch):
    """Regression pin: the post-submit duplicate-row path raises PLAIN
    AlreadyAppliedError, NOT the ApplyLockHeldError subclass.

    The two raise sites in apply.py have semantically different meanings
    that drive different HTTP statuses:

    * acquire_apply_lock contention      → ApplyLockHeldError → exit 4 → HTTP 409
    * post-submit duplicate-row check    → AlreadyAppliedError → exit 1 → HTTP 500

    Promoting the duplicate-row raise to the subclass would silently flip
    its dashboard mapping to 409 ("retry later"), which is wrong — a
    completed application should NOT be retried.
    """
    from flatpilot.apply import (
        AlreadyAppliedError,
        ApplyLockHeldError,
        apply_to_flat,
    )

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, submitted=True)

    # First apply lands the submitted row.
    apply_to_flat(flat_id, dry_run=False)

    # Second apply must raise the parent class, NOT the subclass.
    with pytest.raises(AlreadyAppliedError) as exc_info:
        apply_to_flat(flat_id, dry_run=False)

    assert not isinstance(exc_info.value, ApplyLockHeldError), (
        "post-submit duplicate-row path must raise plain AlreadyAppliedError; "
        "promoting it to ApplyLockHeldError would flip HTTP 500 → 409 silently"
    )
    assert f"flat {flat_id} already has" in str(exc_info.value)
