from __future__ import annotations

from flatpilot.profile import Profile, save_profile
from flatpilot.view import generate_html


def _seed_application(conn, *, method, status, notes=None):
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at, requires_wbs) "
        "VALUES ('e', 'wg-gesucht', 'https://x', 'T', "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 0)"
    )
    flat_id = conn.execute("SELECT MAX(id) AS i FROM flats").fetchone()["i"]
    conn.execute(
        "INSERT INTO applications "
        "(flat_id, platform, listing_url, title, applied_at, method, "
        " attachments_sent_json, status, notes) "
        "VALUES (?, 'wg-gesucht', 'https://x', 'T', "
        "'2026-04-30T12:00:00+00:00', ?, '[]', ?, ?)",
        (flat_id, method, status, notes),
    )


def test_badge_renders_for_auto(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="auto", status="submitted")

    html = generate_html(tmp_db)
    assert "badge--auto" in html


def test_badge_renders_for_manual(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="manual", status="submitted")

    html = generate_html(tmp_db)
    assert "badge--manual" in html


def test_failed_auto_row_shows_notes(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(
        tmp_db, method="auto", status="failed",
        notes="auto_skipped: missing template",
    )

    html = generate_html(tmp_db)
    assert "auto_skipped: missing template" in html
