from __future__ import annotations

from io import StringIO

from rich.console import Console

from flatpilot.doctor import run as run_doctor
from flatpilot.profile import Profile, SavedSearch, save_profile


def _doctor_output() -> str:
    buf = StringIO()
    run_doctor(Console(file=buf, force_terminal=False, width=200))
    return buf.getvalue()


def test_doctor_shows_pause_row(tmp_db):
    save_profile(Profile.load_example())
    text = _doctor_output()
    assert "PAUSE switch" in text
    assert "not paused" in text


def test_doctor_shows_paused_when_file_exists(tmp_db):
    from flatpilot.auto_apply import PAUSE_PATH

    save_profile(Profile.load_example())
    PAUSE_PATH.touch()
    text = _doctor_output()
    assert "PAUSED" in text


def test_doctor_shows_saved_search_count(tmp_db):
    profile = Profile.load_example().model_copy(
        update={
            "saved_searches": [
                SavedSearch(name="ss1", auto_apply=True),
                SavedSearch(name="ss2", auto_apply=False),
            ]
        }
    )
    save_profile(profile)
    text = _doctor_output()
    assert "saved searches" in text
    assert "1 active" in text


def test_doctor_shows_per_platform_burn(tmp_db):
    save_profile(Profile.load_example())
    text = _doctor_output()
    assert "wg-gesucht" in text
    assert "0/20" in text
