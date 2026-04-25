"""Smoke tests for the dashboard HTML — tabs scaffolding only.

We assert structural invariants (presence of tab buttons, pane elements,
badge CSS classes) rather than full rendering. Pane bodies grow in
later M-series tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flatpilot.view import generate_html


def _insert_match(conn) -> None:
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
            "ext-view-1",
            "wg-gesucht",
            "https://www.wg-gesucht.de/listing/1.html",
            "View test flat",
            850.0,
            2.0,
            "Neukölln",
            now,
            now,
        ),
    )
    flat_id = cur.lastrowid
    conn.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, 'h1', 'match', '[]', ?)
        """,
        (flat_id, now),
    )


def test_generate_html_includes_three_tab_buttons(tmp_db):
    html = generate_html(tmp_db)
    assert 'data-tab="matches"' in html
    assert 'data-tab="applied"' in html
    assert 'data-tab="responses"' in html


def test_generate_html_includes_three_panes(tmp_db):
    html = generate_html(tmp_db)
    assert 'data-pane="matches"' in html
    assert 'data-pane="applied"' in html
    assert 'data-pane="responses"' in html


def test_generate_html_matches_pane_renders_match_card(tmp_db):
    _insert_match(tmp_db)
    html = generate_html(tmp_db)
    assert "View test flat" in html


def test_generate_html_status_badge_css_classes_present(tmp_db):
    html = generate_html(tmp_db)
    # Badges use a status-prefix convention so M3 can drop the spans in
    # without re-touching CSS.
    assert ".badge-submitted" in html
    assert ".badge-failed" in html
    assert ".badge-viewing_invited" in html
    assert ".badge-rejected" in html
    assert ".badge-no_response" in html


def test_skipped_flat_hidden_from_matches_pane(tmp_db):
    """A 'skipped' matches row for the same flat hides the 'match' row."""
    now = datetime.now(UTC).isoformat()
    cur = tmp_db.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at, requires_wbs
        ) VALUES ('ext-skip', 'wg-gesucht', 'https://x/skip',
                  'Skipped flat title', ?, ?, 0)
        """,
        (now, now),
    )
    flat_id = cur.lastrowid
    tmp_db.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, 'h1', 'match', '[]', ?)
        """,
        (flat_id, now),
    )
    tmp_db.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, 'h1', 'skipped', '[]', ?)
        """,
        (flat_id, now),
    )

    html = generate_html(tmp_db)
    matches_start = html.index('data-pane="matches"')
    matches_end = html.index('data-pane="applied"')
    matches_pane = html[matches_start:matches_end]
    assert "Skipped flat title" not in matches_pane


def test_generate_returns_path_and_writes_file(tmp_db):
    from flatpilot.view import generate

    path = generate()
    assert path.exists()
    assert "data-tab=\"matches\"" in path.read_text(encoding="utf-8")


def test_generate_html_includes_apply_and_skip_handlers(tmp_db):
    _insert_match(tmp_db)
    html = generate_html(tmp_db)
    # Both fetch() targets must be present in the inline script.
    assert "/api/applications" in html
    assert "/api/matches/" in html
    assert "data-flat-id" in html
    assert "data-match-id" in html
