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
