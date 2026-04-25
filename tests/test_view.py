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


def _insert_application(conn, *, status: str, applied_at: str, title: str = "Applied flat") -> int:
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES (?, 'wg-gesucht', 'https://x/' || ?, ?,
                  '2026-04-25', '2026-04-25')
        """,
        (f"ext-{title}", title, title),
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            applied_at, method, message_sent, attachments_sent_json,
            status
        ) VALUES (?, 'wg-gesucht', 'https://x/listing', ?, ?, 'manual',
                  'msg', '[]', ?)
        """,
        (flat_id, title, applied_at, status),
    )
    return int(cur.lastrowid)


def test_applied_pane_renders_rows_in_applied_at_desc(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-20T10:00:00+00:00", title="Older")  # noqa: E501
    _insert_application(tmp_db, status="viewing_invited", applied_at="2026-04-25T10:00:00+00:00", title="Newest")  # noqa: E501
    html = generate_html(tmp_db)

    pane_start = html.index('data-pane="applied"')
    pane_end = html.index('data-pane="responses"')
    pane = html[pane_start:pane_end]

    # Newest must appear before older in the pane.
    assert pane.index("Newest") < pane.index("Older")


def test_applied_pane_renders_status_badges(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-25T10:00:00+00:00", title="A1")  # noqa: E501
    _insert_application(tmp_db, status="failed", applied_at="2026-04-24T10:00:00+00:00", title="A2")
    html = generate_html(tmp_db)

    pane_start = html.index('data-pane="applied"')
    pane_end = html.index('data-pane="responses"')
    pane = html[pane_start:pane_end]
    assert "badge-submitted" in pane
    assert "badge-failed" in pane


def test_applied_pane_renders_status_filter(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-25T10:00:00+00:00")
    html = generate_html(tmp_db)
    assert 'id="f-app-status"' in html
    # All five allowed status values plus "any" must be selectable.
    for value in ("any", "submitted", "failed", "viewing_invited", "rejected", "no_response"):
        assert f'value="{value}"' in html
