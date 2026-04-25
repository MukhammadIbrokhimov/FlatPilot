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
    html = generate_html()
    assert 'data-tab="matches"' in html
    assert 'data-tab="applied"' in html
    assert 'data-tab="responses"' in html


def test_generate_html_includes_three_panes(tmp_db):
    html = generate_html()
    assert 'data-pane="matches"' in html
    assert 'data-pane="applied"' in html
    assert 'data-pane="responses"' in html


def test_generate_html_matches_pane_renders_match_card(tmp_db):
    _insert_match(tmp_db)
    html = generate_html()
    assert "View test flat" in html


def test_generate_html_status_badge_css_classes_present(tmp_db):
    html = generate_html()
    # Badges use a status-prefix convention so M3 can drop the spans in
    # without re-touching CSS.
    assert ".badge-submitted" in html
    assert ".badge-failed" in html
    assert ".badge-viewing_invited" in html
    assert ".badge-rejected" in html
    assert ".badge-no_response" in html


def test_generate_returns_path_and_writes_file(tmp_db):
    from flatpilot.view import generate

    path = generate()
    assert path.exists()
    assert "data-tab=\"matches\"" in path.read_text(encoding="utf-8")
