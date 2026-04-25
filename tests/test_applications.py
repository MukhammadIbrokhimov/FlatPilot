"""Tests for the applications.py DB writers (skip + response)."""

from __future__ import annotations

import pytest


def _seed_match(conn) -> tuple[int, int]:
    """Insert one flat + one matches row; return (flat_id, match_id)."""
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES ('e1', 'wg-gesucht', 'https://x/1', 'T1', '2026-04-25', '2026-04-25')
        """
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, 'phash-1', 'match', '[]', '2026-04-25T00:00:00+00:00')
        """,
        (flat_id,),
    )
    return flat_id, int(cur.lastrowid)


def test_record_skip_inserts_skipped_row(tmp_db):
    from flatpilot.applications import record_skip

    flat_id, match_id = _seed_match(tmp_db)

    record_skip(tmp_db, match_id=match_id, profile_hash="phash-1")

    rows = tmp_db.execute(
        "SELECT decision FROM matches WHERE flat_id = ? ORDER BY id",
        (flat_id,),
    ).fetchall()
    decisions = [r["decision"] for r in rows]
    assert decisions == ["match", "skipped"]


def test_record_skip_is_idempotent(tmp_db):
    from flatpilot.applications import record_skip

    _flat_id, match_id = _seed_match(tmp_db)
    record_skip(tmp_db, match_id=match_id, profile_hash="phash-1")
    record_skip(tmp_db, match_id=match_id, profile_hash="phash-1")

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM matches WHERE decision = 'skipped'"
    ).fetchone()[0]
    assert cnt == 1


def test_record_skip_unknown_match_id_raises(tmp_db):
    from flatpilot.applications import record_skip

    with pytest.raises(LookupError, match="no match with id 999"):
        record_skip(tmp_db, match_id=999, profile_hash="phash-1")


def test_record_response_updates_row(tmp_db):
    from flatpilot.applications import record_response

    flat_id, _ = _seed_match(tmp_db)
    cur = tmp_db.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            applied_at, method, message_sent, attachments_sent_json, status
        ) VALUES (?, 'wg-gesucht', 'https://x/1', 'T1',
                  '2026-04-25T10:00:00+00:00', 'manual', 'msg', '[]', 'submitted')
        """,
        (flat_id,),
    )
    app_id = int(cur.lastrowid)

    record_response(
        tmp_db,
        application_id=app_id,
        status="viewing_invited",
        response_text="Komm gern am Samstag um 15 Uhr",
    )

    row = tmp_db.execute(
        "SELECT status, response_text, response_received_at FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    assert row["status"] == "viewing_invited"
    assert "Komm gern" in row["response_text"]
    assert row["response_received_at"] is not None


def test_record_response_unknown_id_raises(tmp_db):
    from flatpilot.applications import record_response

    with pytest.raises(LookupError, match="no application with id 999"):
        record_response(
            tmp_db, application_id=999, status="rejected", response_text=""
        )


def test_record_response_invalid_status_raises(tmp_db):
    from flatpilot.applications import record_response

    with pytest.raises(ValueError, match="unsupported response status"):
        record_response(
            tmp_db, application_id=1, status="submitted", response_text=""  # type: ignore[arg-type]
        )
