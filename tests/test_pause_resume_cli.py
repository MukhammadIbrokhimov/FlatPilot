from __future__ import annotations

from typer.testing import CliRunner

from flatpilot.cli import app


def test_pause_creates_file(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    runner = CliRunner()
    result = runner.invoke(app, ["pause"])
    assert result.exit_code == 0
    assert PAUSE_PATH.exists()


def test_pause_is_idempotent(tmp_db):
    runner = CliRunner()
    runner.invoke(app, ["pause"])
    result = runner.invoke(app, ["pause"])
    assert result.exit_code == 0


def test_resume_removes_file(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    runner = CliRunner()
    runner.invoke(app, ["pause"])
    result = runner.invoke(app, ["resume"])
    assert result.exit_code == 0
    assert not PAUSE_PATH.exists()


def test_resume_is_idempotent(tmp_db):
    runner = CliRunner()
    result = runner.invoke(app, ["resume"])
    assert result.exit_code == 0
