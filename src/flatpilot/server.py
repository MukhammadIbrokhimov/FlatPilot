"""Localhost HTTP server backing ``flatpilot dashboard``.

Replaces the static file Phase-1 dashboard. Serves the same HTML at
``GET /`` and exposes three POST endpoints (added in M2 / M4):

* ``POST /api/matches/<match_id>/skip`` — mark a match skipped.
* ``POST /api/applications`` (body: ``{"flat_id": int}``) — spawn
  ``flatpilot apply <flat_id>`` as a subprocess.
* ``POST /api/applications/<application_id>/response`` — record a
  pasted-in landlord reply on an applications row.

**Security boundary.** The server binds ``127.0.0.1`` — localhost only.
There is no auth, no CSRF token, no allowed-origin check. Anyone with
shell access to the host can drive it; that is the expected single-user
operator threat model. Do NOT add half-baked auth here without a
discussion — Phase 5 will replace this with a proper FastAPI service
behind email magic-link auth.

Threading model. ``ThreadingHTTPServer`` spawns one thread per
request. ``flatpilot.database.get_conn()`` caches a sqlite connection
per thread under WAL — multiple concurrent reads + the occasional
small write co-exist safely. The Apply endpoint shells out so the
server thread doesn't block on Playwright.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# Eagerly populate registries the request handlers will need.
import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.schemas  # noqa: F401
from flatpilot.applications import record_response, record_skip
from flatpilot.apply import (
    APPLY_LOCK_HELD_EXIT,
    STALE_APPLY_BUFFER_SEC,
    apply_timeout_sec,
)
from flatpilot.database import get_conn, init_db
from flatpilot.profile import load_profile, profile_hash
from flatpilot.view import generate_html

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765

_SKIP_RE = re.compile(r"^/api/matches/(\d+)/skip$")
_RESPONSE_RE = re.compile(r"^/api/applications/(\d+)/response$")
_APPLY_PATH = "/api/applications"


def _spawn_apply(flat_id: int) -> dict:
    """Run ``flatpilot apply <flat_id>`` as a subprocess.

    Captures stdout/stderr; returns a small dict the handler can ship to
    the browser. Stdout is tail-trimmed to ~2 KB so a verbose Playwright
    log doesn't bloat the JSON response.

    Bounded by ``apply_timeout_sec()`` (default 180s, override via
    ``FLATPILOT_APPLY_TIMEOUT_SEC``): a hung child (e.g. Playwright stuck
    on a CAPTCHA wait) is killed and surfaced as ``ok=False`` with the
    captured-so-far output, so the dashboard thread is freed.

    Patched in tests so we don't actually invoke the CLI.
    """
    timeout_sec = apply_timeout_sec()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "flatpilot", "apply", str(flat_id)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        captured = (exc.stdout or "") + (exc.stderr or "")  # type: ignore[operator]
        tail_body = captured[-2000:].strip()  # type: ignore[str-bytes-safe]
        prefix = f"timed out after {timeout_sec}s"
        tail = f"{prefix}\n{tail_body}".strip() if tail_body else prefix  # type: ignore[str-bytes-safe]
        return {
            "ok": False,
            "returncode": None,
            "stdout_tail": tail,
        }
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = combined[-2000:].strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": tail,
    }


# Per-flat concurrency control for the Apply path.
#
# The dashboard's POST /api/applications endpoint shells out to
# `flatpilot apply <flat_id>` via _spawn_apply. Two near-simultaneous
# POSTs for the same flat (e.g. a double-click) used to fork two
# subprocesses; both passed apply_to_flat's status='submitted' check and
# the landlord received two messages.
#
# The lock here serializes the two server threads BEFORE either
# subprocess is spawned. Second concurrent caller for the same flat_id
# returns 409 immediately. The lock is per-flat (not global) so applies
# to DIFFERENT flats still run in parallel.
#
# Slots are tracked as flat_id -> monotonic acquire time so the
# watchdog can reap orphaned slots on the next acquire. APPLY's
# subprocess timeout already bounds _spawn_apply, so the dict can't
# actually leak today; the watchdog is defense-in-depth against a
# future refactor that removes the subprocess timeout or replaces
# _spawn_apply with a genuinely-blocking in-process call.
#
# Cross-process race (CLI + dashboard on the same flat) is handled by
# the apply_locks SQLite table — see flatpilot.apply.acquire_apply_lock.
_inflight_lock = threading.Lock()
_inflight_flats: dict[int, float] = {}

# Watchdog threshold mirrors the apply_locks stale-row reaper in
# flatpilot.apply.acquire_apply_lock — both layers share
# STALE_APPLY_BUFFER_SEC so a future tuning honors both at once.
def _inflight_watchdog_threshold_sec() -> float:
    return apply_timeout_sec() + STALE_APPLY_BUFFER_SEC


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the dashboard and its mutation endpoints."""

    # Quieter access log — match the project's logging style.
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", ""):
            html = generate_html()
            self._send(HTTPStatus.OK, html, content_type="text/html; charset=utf-8")
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        skip_match = _SKIP_RE.match(path)
        if skip_match:
            self._handle_skip(int(skip_match.group(1)))
            return
        response_match = _RESPONSE_RE.match(path)
        if response_match:
            self._handle_response(int(response_match.group(1)))
            return
        if path == _APPLY_PATH:
            self._handle_apply()
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    def _handle_apply(self) -> None:
        body = self._read_json_body()
        if body is None:
            return  # _read_json_body already responded.
        flat_id = body.get("flat_id")
        # bool is a subclass of int — guard explicitly so {"flat_id": true}
        # doesn't slip through and spawn `flatpilot apply True`.
        if not isinstance(flat_id, int) or isinstance(flat_id, bool):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "request body must be {'flat_id': <int>}"},
            )
            return

        # Claim an in-flight slot for this flat. If another request is
        # already applying to it (and the slot is fresh), fail fast with
        # 409 — don't queue, the caller will see the (eventually)
        # submitted row on the next dashboard refresh.
        #
        # Sweep stale slots first (held longer than apply_timeout +
        # buffer) so a future code path that genuinely hangs doesn't
        # block all subsequent applies for that flat until restart.
        now = time.monotonic()
        with _inflight_lock:
            threshold = _inflight_watchdog_threshold_sec()
            stale = [
                (fid, now - acquired_at)
                for fid, acquired_at in _inflight_flats.items()
                if now - acquired_at > threshold
            ]
            for fid, age in stale:
                logger.warning(
                    "apply: watchdog clearing stale in-flight slot for "
                    "flat_id=%d (held %.1fs, threshold %.1fs)",
                    fid,
                    age,
                    threshold,
                )
                _inflight_flats.pop(fid, None)
            if flat_id in _inflight_flats:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {
                        "ok": False,
                        "error": (
                            f"apply already in progress for flat {flat_id}; "
                            "wait for it to finish"
                        ),
                    },
                )
                return
            _inflight_flats[flat_id] = now
        try:
            result = _spawn_apply(flat_id)
        finally:
            with _inflight_lock:
                _inflight_flats.pop(flat_id, None)
        # Map subprocess returncode to HTTP status.
        #
        # APPLY_LOCK_HELD_EXIT (4) means acquire_apply_lock raised
        # ApplyLockHeldError because another FlatPilot process holds the
        # cross-process lock for this flat — semantically "in progress,
        # retry later" → 409 Conflict, mirroring the in-process
        # _inflight_flats fast-path 409 above.
        #
        # The explicit returncode==4 branch is load-bearing: lock-held
        # exits with ok=False, so the previous `OK if ok else 500`
        # ternary mapped it to 500. Collapsing back to a binary ok/!ok
        # shape silently regresses 409 → 500. None (subprocess timeout)
        # and 1 (filler error, ProfileMissing, post-submit duplicate
        # row, ...) both fall through to the 500 branch.
        # FlatPilot-wsp.
        if result["returncode"] == APPLY_LOCK_HELD_EXIT:
            status = HTTPStatus.CONFLICT
        elif result["ok"]:
            status = HTTPStatus.OK
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status, result)

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "empty body, expected JSON"}
            )
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"invalid JSON: {exc}"},
            )
            return None
        if not isinstance(data, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON body must be an object"})
            return None
        return data

    def _handle_skip(self, match_id: int) -> None:
        profile = load_profile()
        if profile is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "no profile — run `flatpilot init` first"},
            )
            return
        conn = get_conn()
        try:
            record_skip(conn, match_id=match_id, profile_hash=profile_hash(profile))
        except LookupError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "match_id": match_id})

    def _handle_response(self, application_id: int) -> None:
        body = self._read_json_body()
        if body is None:
            return
        status_value = body.get("status")
        response_text = body.get("response_text", "")
        if not isinstance(status_value, str) or not isinstance(response_text, str):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "body must be {'status': str, 'response_text': str}"},
            )
            return
        conn = get_conn()
        try:
            record_response(
                conn,
                application_id=application_id,
                status=status_value,  # type: ignore[arg-type]
                response_text=response_text,
            )
        except LookupError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "application_id": application_id})

    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        payload = json.dumps(body)
        self._send(status, payload, content_type="application/json; charset=utf-8")

    def _send(
        self,
        status: HTTPStatus,
        body: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> tuple[ThreadingHTTPServer, int]:
    """Bind and return the server (without starting its loop).

    Caller runs ``server.serve_forever()`` (blocks) and ``server.shutdown()``
    + ``server.server_close()`` for teardown. Returns the actually-bound
    port — useful when ``port=0`` was requested.

    init_db() runs once here at startup so per-request handlers can
    assume the schema exists. Failing fast at bind time also means a
    broken SQLite path surfaces before the first user click.
    """
    init_db()
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if port == DEFAULT_PORT:
            # Default port busy; fall back to ephemeral so dev can iterate.
            logger.warning(
                "port %d is in use (%s) — falling back to ephemeral port",
                port,
                exc,
            )
            server = ThreadingHTTPServer((host, 0), DashboardHandler)
        else:
            raise
    bound_port = server.server_address[1]
    return server, bound_port
