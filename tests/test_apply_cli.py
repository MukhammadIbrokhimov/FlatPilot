"""Tests for ``flatpilot apply`` CLI subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from flatpilot.apply import ApplyOutcome
from flatpilot.cli import app
from flatpilot.errors import ProfileMissingError
from flatpilot.fillers.base import FillReport, NotAuthenticatedError


def _stub_outcome(status: str) -> ApplyOutcome:
    report = FillReport(
        platform="wg-gesucht",
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        contact_url="https://www.wg-gesucht.de/listing/123.html",
        fields_filled={"message": "Hallo."},
        message_sent="Hallo.",
        attachments_sent=[],
        screenshot_path=Path("/tmp/shot.png"),
        submitted=status == "submitted",
        started_at="t0",
        finished_at="t1",
    )
    return ApplyOutcome(
        status=status,  # type: ignore[arg-type]
        application_id=42 if status == "submitted" else None,
        fill_report=report,
    )


def test_apply_dry_run_exit_zero_and_prints_preview():
    runner = CliRunner()
    outcome = _stub_outcome("dry_run")
    with patch("flatpilot.cli.apply_to_flat", return_value=outcome) as orchestrator:
        result = runner.invoke(app, ["apply", "5", "--dry-run"])

    assert result.exit_code == 0, result.output
    orchestrator.assert_called_once()
    call_kwargs = orchestrator.call_args.kwargs
    assert call_kwargs["dry_run"] is True
    assert "preview" in result.output.lower() or "dry-run" in result.output.lower()
    assert "Hallo." in result.output


def test_apply_live_exit_zero_and_prints_application_id():
    runner = CliRunner()
    outcome = _stub_outcome("submitted")
    with patch("flatpilot.cli.apply_to_flat", return_value=outcome) as orchestrator:
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 0, result.output
    assert orchestrator.call_args.kwargs["dry_run"] is False
    assert "42" in result.output  # application_id


def test_apply_filler_error_exit_one():
    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=NotAuthenticatedError("session expired"),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 1
    assert "session expired" in result.output


def test_apply_lookup_error_exit_two():
    runner = CliRunner()
    with patch("flatpilot.cli.apply_to_flat", side_effect=LookupError("no flat with id 999")):
        result = runner.invoke(app, ["apply", "999"])

    assert result.exit_code == 2
    assert "no flat with id 999" in result.output


def test_apply_no_profile_exit_one():
    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=ProfileMissingError("No profile at ..."),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 1
    assert "No profile" in result.output


def test_apply_lock_held_error_exits_apply_lock_held_exit():
    """Lock contention exits APPLY_LOCK_HELD_EXIT (4), not 1.

    The dashboard's _handle_apply translates this returncode to HTTP 409.
    Direct CLI users see a yellow message + exit 4 they can branch on.
    """
    from flatpilot.apply import APPLY_LOCK_HELD_EXIT, ApplyLockHeldError

    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=ApplyLockHeldError(
            "flat 5 apply already in progress (pid=12345, since 2026-04-29T10:00:00+00:00)"
        ),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == APPLY_LOCK_HELD_EXIT == 4, result.output
    assert "apply already in progress" in result.output


def test_apply_plain_already_applied_error_still_exits_one():
    """Regression: the post-submit duplicate-row path keeps exit 1.

    Two AlreadyAppliedError causes have intentionally different exit
    codes (and HTTP statuses): the lock-contention case is transient
    (retry-able, 409); the duplicate-row case is terminal (do-not-retry,
    500). Don't conflate them.
    """
    from flatpilot.apply import AlreadyAppliedError

    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=AlreadyAppliedError(
            "flat 5 already has a submitted application (application_id=42); "
            "refusing to double-submit"
        ),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 1, result.output
    assert "already has a submitted application" in result.output
