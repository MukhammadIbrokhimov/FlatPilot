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


def test_init_db_runs_once_at_serve_startup_not_per_request(tmp_db):
    """init_db must be called once when serve() binds, not per POST request.

    Pre-fix _handle_skip and _handle_response each called init_db, so a
    sequence of N writing requests called init_db N times. The hoist
    moves the call into serve() startup so it runs exactly once.
    """
    from unittest.mock import patch

    _seed_match_with_profile(tmp_db)
    app_id = _seed_application(tmp_db)

    # Patch the bound name `flatpilot.server.init_db` (server.py imports it
    # `from flatpilot.database import ..., init_db`, so this is the reference
    # the per-request handlers and serve() both reach).
    with patch("flatpilot.server.init_db") as mock_init_db:
        # serve() is what we expect to call init_db once at startup.
        from flatpilot.server import serve

        server, port = serve(host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # One skip POST + one response POST — both used to call init_db.
            match_id = tmp_db.execute(
                "SELECT id FROM matches LIMIT 1"
            ).fetchone()[0]
            _post(f"http://127.0.0.1:{port}/api/matches/{match_id}/skip")
            _post(
                f"http://127.0.0.1:{port}/api/applications/{app_id}/response",
                body=json.dumps(
                    {"status": "rejected", "response_text": ""}
                ).encode("utf-8"),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    # serve() should have called init_db exactly once during startup.
    # Per-request handlers must no longer call it.
    assert mock_init_db.call_count == 1, (
        f"expected init_db to be called once at serve() startup, "
        f"got {mock_init_db.call_count} call(s)"
    )


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


def test_spawn_apply_returns_structured_error_on_subprocess_timeout(tmp_db):
    """A hung 'flatpilot apply' subprocess must surface as ok=False, not raise.

    Pre-fix _spawn_apply called subprocess.run with no timeout — a stuck
    Playwright would hang the entire dashboard server thread. This test
    monkeypatches subprocess.run to raise TimeoutExpired and asserts the
    function returns the structured error shape the dashboard handler
    expects.
    """
    import subprocess
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    fake_stdout = "starting apply for flat 42\nlogged in, opening listing\n"
    fake_stderr = ""

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else kwargs.get("args", []),
            timeout=kwargs.get("timeout", 180),
            output=fake_stdout,
            stderr=fake_stderr,
        )

    with patch("flatpilot.server.subprocess.run", side_effect=raise_timeout):
        result = _spawn_apply(42)

    assert result["ok"] is False
    assert result["returncode"] is None
    assert "timed out" in result["stdout_tail"].lower()
    # Captured-before-timeout output should still surface so the user
    # sees how far the apply got.
    assert "logged in" in result["stdout_tail"]


def test_spawn_apply_passes_timeout_to_subprocess_run():
    """_spawn_apply must pass a finite timeout= keyword to subprocess.run.

    Guards against the function silently going back to no-timeout. We
    record the kwargs subprocess.run is called with and assert a
    positive numeric timeout was set. No DB needed — the function only
    talks to subprocess.run.
    """
    from unittest.mock import patch

    from flatpilot.server import _spawn_apply

    captured_kwargs: dict = {}

    def fake_run(*args, **kwargs):
        captured_kwargs.update(kwargs)
        class _Done:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Done()

    with patch("flatpilot.server.subprocess.run", side_effect=fake_run):
        result = _spawn_apply(42)

    assert result["ok"] is True
    timeout = captured_kwargs.get("timeout")
    assert isinstance(timeout, (int, float)) and timeout > 0, (
        f"expected positive numeric timeout=, got {timeout!r}"
    )


def test_post_apply_rejects_concurrent_request_for_same_flat(tmp_db):
    """Two near-simultaneous applies for the same flat: one wins, one 409s.

    Pre-fix both server threads called _spawn_apply, both subprocesses
    passed apply_to_flat's status='submitted' check (because neither
    had written yet), and the landlord received two messages. The fix
    is a module-level lock + set[int] of in-flight flat_ids in
    server.py: second concurrent POST for the same flat returns 409
    immediately without spawning anything.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    _seed_match_with_profile(tmp_db)

    in_spawn = threading.Event()
    release = threading.Event()
    spawn_calls: list[int] = []

    def slow_spawn(flat_id):
        spawn_calls.append(flat_id)
        in_spawn.set()
        # Block until the test releases — gives the second request a
        # chance to collide with this one.
        if not release.wait(timeout=5):
            raise AssertionError(
                "test never released the first spawn — "
                "deadlock or test ordering bug"
            )
        return {"ok": True, "stdout_tail": f"applied {flat_id}", "returncode": 0}

    body = json.dumps({"flat_id": 1}).encode("utf-8")

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", side_effect=slow_spawn),
    ):
        url = f"http://127.0.0.1:{port}/api/applications"
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(_post, url, body)
            try:
                # Wait until the first request is locked-in inside _spawn_apply
                # before firing the second — without this, the second might
                # arrive before the first has claimed the in-flight slot,
                # making the test order-dependent.
                assert in_spawn.wait(timeout=2), (
                    "first request never entered _spawn_apply"
                )
                f2 = ex.submit(_post, url, body)
                # The second should be rejected fast with 409 — without
                # the lock, f2 also enters slow_spawn and blocks until
                # release, so f2.result(timeout=2) raises TimeoutError
                # (the expected pre-fix failure mode).
                r2 = f2.result(timeout=2)
            finally:
                release.set()
            r1 = f1.result(timeout=10)

    assert r2[0] == 409, (
        f"expected 409 for concurrent apply, got {r2[0]}: {r2[1]!r}"
    )
    assert r1[0] == 200, (
        f"expected 200 for first apply, got {r1[0]}: {r1[1]!r}"
    )
    # The second request must NOT have spawned — the lock rejects before
    # _spawn_apply is even called.
    assert spawn_calls == [1], (
        f"expected exactly one spawn (the winner), got {spawn_calls}"
    )


def test_post_apply_releases_slot_after_completion_so_retry_succeeds(tmp_db):
    """After the first apply finishes, a fresh apply for the same flat goes through.

    Guards against a finally-clause regression that would leak slots in
    the in-flight set, blocking all future applies for that flat.
    """
    from unittest.mock import patch

    _seed_match_with_profile(tmp_db)

    fake_result = {"ok": True, "stdout_tail": "ok", "returncode": 0}

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", return_value=fake_result),
    ):
        url = f"http://127.0.0.1:{port}/api/applications"
        body = json.dumps({"flat_id": 1}).encode("utf-8")
        first = _post(url, body)
        second = _post(url, body)

    assert first[0] == 200
    assert second[0] == 200, (
        f"expected sequential applies to both succeed (slot released), "
        f"got {second[0]}: {second[1]!r}"
    )


def test_post_apply_releases_slot_when_spawn_raises(tmp_db):
    """If _spawn_apply itself raises, the in-flight slot must still be released.

    Otherwise a one-time bug or kill -9 would permanently block applies
    to that flat until the server restarts. We assert on
    ``flatpilot.server._inflight_flats`` directly because
    ``BaseHTTPRequestHandler`` does NOT translate handler exceptions into
    a 5xx — the connection drops mid-response and any HTTP-level
    assertion would observe ``RemoteDisconnected``/``URLError``, masking
    the actual finally-release we care about.
    """
    from unittest.mock import patch

    import flatpilot.server as server_mod

    _seed_match_with_profile(tmp_db)

    def crashing_spawn(flat_id):
        raise RuntimeError("simulated spawn crash")

    with (
        _running_server(tmp_db) as port,
        patch("flatpilot.server._spawn_apply", side_effect=crashing_spawn),
    ):
        url = f"http://127.0.0.1:{port}/api/applications"
        body = json.dumps({"flat_id": 1}).encode("utf-8")
        # The crash inside _handle_apply causes the request thread to
        # propagate the exception and BaseHTTPRequestHandler closes the
        # socket without a structured response. We don't care which
        # connection error urllib surfaces — we care that _inflight_flats
        # is empty afterwards.
        import contextlib

        with contextlib.suppress(Exception):
            _post(url, body)

    assert 1 not in server_mod._inflight_flats, (
        f"_inflight_flats was not cleaned up after spawn crash: "
        f"{server_mod._inflight_flats!r}"
    )
