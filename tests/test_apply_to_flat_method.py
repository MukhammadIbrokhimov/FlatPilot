from __future__ import annotations

from datetime import UTC, datetime

from flatpilot.apply import _record_application
from flatpilot.profile import Profile


def _seed_flat(conn):
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title, scraped_at, first_seen_at,
            requires_wbs
        ) VALUES ('e1', 'wg-gesucht', 'https://x', 'T', ?, ?, 0)
        """,
        (now, now),
    )
    return cur.lastrowid


def test_record_application_default_is_manual(tmp_db):
    flat_id = _seed_flat(tmp_db)
    profile = Profile.load_example()
    flat = dict(tmp_db.execute("SELECT * FROM flats WHERE id=?", (flat_id,)).fetchone())

    app_id = _record_application(
        tmp_db,
        profile=profile,
        flat=flat,
        message="msg",
        attachments=[],
        status="submitted",
        notes=None,
    )

    row = tmp_db.execute(
        "SELECT method, triggered_by_saved_search FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    assert row["method"] == "manual"
    assert row["triggered_by_saved_search"] is None


def test_record_application_writes_auto_method_and_saved_search(tmp_db):
    flat_id = _seed_flat(tmp_db)
    profile = Profile.load_example()
    flat = dict(tmp_db.execute("SELECT * FROM flats WHERE id=?", (flat_id,)).fetchone())

    app_id = _record_application(
        tmp_db,
        profile=profile,
        flat=flat,
        message="msg",
        attachments=[],
        status="submitted",
        notes=None,
        method="auto",
        saved_search="kreuzberg-2br",
    )

    row = tmp_db.execute(
        "SELECT method, triggered_by_saved_search FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    assert row["method"] == "auto"
    assert row["triggered_by_saved_search"] == "kreuzberg-2br"
