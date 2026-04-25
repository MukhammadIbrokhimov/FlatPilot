"""Tests for the dashboard HTTP server.

Spins up the server on an ephemeral port in a background thread for
each test; exercises endpoints with ``urllib.request``.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from unittest.mock import patch

import pytest


@contextmanager
def _running_server(tmp_db):
    from flatpilot.server import serve

    server, port = serve(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_get_root_serves_dashboard_html(tmp_db):
    with _running_server(tmp_db) as port, urllib.request.urlopen(
        f"http://127.0.0.1:{port}/"
    ) as resp:
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.getheader("Content-Type", "").startswith("text/html")
        assert 'data-tab="matches"' in body
        assert 'data-tab="applied"' in body
        assert 'data-tab="responses"' in body


def test_get_unknown_path_returns_404(tmp_db):
    with _running_server(tmp_db) as port:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/nope")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 404


def _post(url: str, body: bytes = b"") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _seed_match_with_profile(conn):
    """Insert a flat + match and write a profile so endpoints have one.

    Relies on the ``tmp_db`` fixture having monkey-patched
    ``flatpilot.profile.PROFILE_PATH`` to a temp location, so
    ``save_profile`` doesn't touch the user's real ``~/.flatpilot``.
    """
    from flatpilot.profile import Profile, profile_hash, save_profile

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    save_profile(profile)
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES ('e1', 'wg-gesucht', 'https://x/1', 'T1',
                  '2026-04-25', '2026-04-25')
        """
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, ?, 'match', '[]', '2026-04-25T00:00:00+00:00')
        """,
        (flat_id, profile_hash(profile)),
    )
    return flat_id, int(cur.lastrowid)


def test_post_skip_marks_match_skipped(tmp_db):
    flat_id, match_id = _seed_match_with_profile(tmp_db)
    with _running_server(tmp_db) as port:
        status, body = _post(f"http://127.0.0.1:{port}/api/matches/{match_id}/skip")

    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM matches WHERE flat_id = ? AND decision = 'skipped'",
        (flat_id,),
    ).fetchone()[0]
    assert cnt == 1


def test_post_skip_unknown_match_id_returns_404(tmp_db):
    _seed_match_with_profile(tmp_db)  # ensure profile exists
    with _running_server(tmp_db) as port:
        status, body = _post(f"http://127.0.0.1:{port}/api/matches/999/skip")

    assert status == 404
    payload = json.loads(body)
    assert "no match with id 999" in payload["error"]


def test_post_apply_spawns_subprocess_and_returns_result(tmp_db):
    _seed_match_with_profile(tmp_db)
    fake_result = {"ok": True, "stdout_tail": "submitted · application_id=7", "returncode": 0}

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", return_value=fake_result) as spawn,
    ):
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications",
            body=json.dumps({"flat_id": 1}).encode("utf-8"),
        )

    assert status == 200
    spawn.assert_called_once_with(1)
    payload = json.loads(body)
    assert payload["ok"] is True
    assert "application_id=7" in payload["stdout_tail"]


def test_post_apply_subprocess_failure_returns_500(tmp_db):
    _seed_match_with_profile(tmp_db)
    fake_result = {
        "ok": False,
        "stdout_tail": "NotAuthenticatedError: session expired",
        "returncode": 1,
    }

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", return_value=fake_result),
    ):
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications",
            body=json.dumps({"flat_id": 1}).encode("utf-8"),
        )

    assert status == 500
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "session expired" in payload["stdout_tail"]


def test_post_apply_invalid_body_returns_400(tmp_db):
    _seed_match_with_profile(tmp_db)
    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications",
            body=b"not-json",
        )

    assert status == 400
    payload = json.loads(body)
    assert "flat_id" in payload["error"] or "json" in payload["error"].lower()


def _seed_application(conn) -> int:
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES ('e2', 'wg-gesucht', 'https://x/2', 'T2',
                  '2026-04-25', '2026-04-25')
        """
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            applied_at, method, message_sent, attachments_sent_json, status
        ) VALUES (?, 'wg-gesucht', 'https://x/2', 'T2',
                  '2026-04-25T10:00:00+00:00', 'manual', 'msg', '[]', 'submitted')
        """,
        (flat_id,),
    )
    return int(cur.lastrowid)


def test_post_response_updates_row(tmp_db):
    _seed_match_with_profile(tmp_db)  # ensures profile exists
    app_id = _seed_application(tmp_db)
    payload = {"status": "viewing_invited", "response_text": "Komm am Samstag"}

    with _running_server(tmp_db) as port:
        status, body = _post(
                f"http://127.0.0.1:{port}/api/applications/{app_id}/response",
            body=json.dumps(payload).encode("utf-8"),
        )

    assert status == 200, body
    data = json.loads(body)
    assert data["ok"] is True

    row = tmp_db.execute(
        "SELECT status, response_text FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    assert row["status"] == "viewing_invited"
    assert "Komm am Samstag" in row["response_text"]


def test_post_response_invalid_status_returns_400(tmp_db):
    _seed_match_with_profile(tmp_db)
    app_id = _seed_application(tmp_db)
    payload = {"status": "submitted", "response_text": ""}

    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications/{app_id}/response",
            body=json.dumps(payload).encode("utf-8"),
        )

    assert status == 400
    data = json.loads(body)
    assert "unsupported response status" in data["error"]


def test_post_response_unknown_id_returns_404(tmp_db):
    _seed_match_with_profile(tmp_db)
    payload = {"status": "rejected", "response_text": ""}

    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications/9999/response",
            body=json.dumps(payload).encode("utf-8"),
        )

    assert status == 404
    data = json.loads(body)
    assert "no application with id 9999" in data["error"]
