from __future__ import annotations

import pytest
from pydantic import ValidationError

from flatpilot.profile import AutoApplySettings


def test_defaults_present():
    s = AutoApplySettings()
    assert s.daily_cap_per_platform == {
        "wg-gesucht": 20, "kleinanzeigen": 20, "inberlinwohnen": 20,
    }
    assert s.cooldown_seconds_per_platform == {
        "wg-gesucht": 120, "kleinanzeigen": 120, "inberlinwohnen": 120,
    }


def test_extra_forbidden():
    with pytest.raises(ValidationError):
        AutoApplySettings(unknown=1)


def test_user_override_replaces_defaults_completely():
    s = AutoApplySettings(daily_cap_per_platform={"wg-gesucht": 50})
    assert s.daily_cap_per_platform == {"wg-gesucht": 50}


def test_matches_table_has_matched_saved_searches_json(tmp_db):
    cols = {row["name"] for row in tmp_db.execute("PRAGMA table_info(matches)")}
    assert "matched_saved_searches_json" in cols


def test_applications_table_has_triggered_by_saved_search(tmp_db):
    cols = {row["name"] for row in tmp_db.execute("PRAGMA table_info(applications)")}
    assert "triggered_by_saved_search" in cols


def test_applications_index_present(tmp_db):
    indices = {
        row["name"]
        for row in tmp_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_applications_method_applied_at" in indices
