"""Sanity-check that the tmp_db fixture creates an isolated DB."""

from __future__ import annotations

from pathlib import Path


def test_tmp_db_has_flats_table(tmp_db):
    row = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='flats'"
    ).fetchone()
    assert row is not None


def test_tmp_db_is_fresh_per_test_a(tmp_db):
    tmp_db.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at) VALUES ('x', 'wg_gesucht', 'u', 't', 'now', 'now')"
    )
    assert tmp_db.execute("SELECT COUNT(*) FROM flats").fetchone()[0] == 1


def test_tmp_db_is_fresh_per_test_b(tmp_db):
    assert tmp_db.execute("SELECT COUNT(*) FROM flats").fetchone()[0] == 0


def test_tmp_db_does_not_touch_real_flatpilot_dir(tmp_db, tmp_path):
    """Paranoia check: the fixture must point APP_DIR inside tmp_path."""
    from flatpilot import config

    assert config.APP_DIR != Path.home() / ".flatpilot"  # noqa: SIM300
    assert (
        tmp_path in config.APP_DIR.parents
        or config.APP_DIR == tmp_path / ".flatpilot"  # noqa: SIM300
    )
    assert config.DB_PATH.exists()
