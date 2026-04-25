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

import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Eagerly populate registries the request handlers will need.
import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.schemas  # noqa: F401
from flatpilot.view import generate_html

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the dashboard and its mutation endpoints."""

    # Quieter access log — match the project's logging style.
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "":
            html = generate_html()
            self._send(HTTPStatus.OK, html, content_type="text/html; charset=utf-8")
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    # POST endpoints land in Tasks 6 / 7 / 9.
    def do_POST(self) -> None:
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

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
    """
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
