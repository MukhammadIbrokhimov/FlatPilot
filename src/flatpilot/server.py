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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# Eagerly populate registries the request handlers will need.
import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.schemas  # noqa: F401
from flatpilot.applications import record_response, record_skip
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

    Patched in tests so we don't actually invoke the CLI.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "flatpilot", "apply", str(flat_id)],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = combined[-2000:].strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": tail,
    }


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
        result = _spawn_apply(flat_id)
        status = HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR
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
