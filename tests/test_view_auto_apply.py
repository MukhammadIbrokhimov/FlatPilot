from __future__ import annotations

import itertools

from flatpilot.profile import Profile, save_profile
from flatpilot.view import generate_html

_counter = itertools.count(1)


def _seed_application(conn, *, method, status, notes=None):
    ext_id = f"e{next(_counter)}"
    conn.execute(
        "INSERT INTO flats (external_id, platform, listing_url, title, "
        "scraped_at, first_seen_at, requires_wbs) "
        f"VALUES ('{ext_id}', 'wg-gesucht', 'https://x', 'T', "
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


def test_saved_search_filter_dropdown_present(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="auto", status="submitted")

    html = generate_html(tmp_db)
    assert 'id="saved-search-filter"' in html
    assert '<option value="">All</option>' in html
    assert '<option value="manual">Manual only</option>' in html


def test_saved_search_filter_lists_unique_names(tmp_db):
    save_profile(Profile.load_example())
    # Seed two auto rows with different saved searches and one manual row.
    _seed_application(tmp_db, method="auto", status="submitted")
    tmp_db.execute(
        "UPDATE applications SET triggered_by_saved_search = 'kreuzberg-2br' "
        "WHERE id = (SELECT MAX(id) FROM applications)"
    )
    _seed_application(tmp_db, method="auto", status="submitted")
    tmp_db.execute(
        "UPDATE applications SET triggered_by_saved_search = 'spandau-cheap' "
        "WHERE id = (SELECT MAX(id) FROM applications)"
    )
    _seed_application(tmp_db, method="manual", status="submitted")

    html = generate_html(tmp_db)
    assert '<option value="kreuzberg-2br">kreuzberg-2br</option>' in html
    assert '<option value="spandau-cheap">spandau-cheap</option>' in html


def test_application_row_has_data_saved_search_attribute(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="auto", status="submitted")
    tmp_db.execute(
        "UPDATE applications SET triggered_by_saved_search = 'foo' "
        "WHERE id = (SELECT MAX(id) FROM applications)"
    )

    html = generate_html(tmp_db)
    assert 'data-saved-search="foo"' in html


def test_application_row_manual_has_data_saved_search_manual(tmp_db):
    save_profile(Profile.load_example())
    _seed_application(tmp_db, method="manual", status="submitted")

    html = generate_html(tmp_db)
    assert 'data-saved-search="manual"' in html
