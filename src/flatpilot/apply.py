"""L4 — orchestrate the apply flow for a single flat.

Loads the user profile, looks up the flat by primary key, renders the
platform-specific Anschreiben template, resolves the per-platform
attachments, calls the filler, and (in live mode) writes one row to
``applications``. The CLI command in :mod:`flatpilot.cli` and the
dashboard server's ``POST /api/applications`` endpoint both call into
this function so the user-visible behaviour is identical regardless of
entry point.

Failure modes split into two camps:

* **Pre-conditions** — no profile, flat not found, template missing,
  attachment missing — are user-correctable. We raise the underlying
  exception WITHOUT writing a row so a fix-and-retry doesn't leave a
  ``status='failed'`` placeholder behind.
* **Filler errors** — the contact form was reachable but something on
  the platform side prevented submission. We DO write a row
  (``status='failed'`` with the exception message in ``notes``) and
  re-raise, so the user has an audit trail.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# Force the filler registry to populate before apply_to_flat runs.
# init_db() handles the schemas import internally.
import flatpilot.fillers.wg_gesucht  # noqa: F401
from flatpilot.attachments import resolve_for_platform
from flatpilot.compose import compose_anschreiben
from flatpilot.database import get_conn, init_db
from flatpilot.fillers import get_filler
from flatpilot.fillers.base import FillError, FillReport
from flatpilot.profile import Profile, load_profile

logger = logging.getLogger(__name__)


class ProfileMissingError(RuntimeError):
    """Raised when ``apply_to_flat`` runs before ``flatpilot init``."""


ApplyStatus = Literal["submitted", "dry_run"]


@dataclass
class ApplyOutcome:
    """What happened during a single :func:`apply_to_flat` call.

    - ``status='dry_run'`` — preview only, no row written.
    - ``status='submitted'`` — filler submitted successfully, row written.

    Filler failures don't produce an ``ApplyOutcome``: a row is written with
    ``status='failed'`` (persisted to the ``applications`` table) and the
    underlying :class:`FillError` is re-raised to the caller.
    """

    status: ApplyStatus
    application_id: int | None
    fill_report: FillReport | None
    error: str | None = None


def apply_to_flat(
    flat_id: int,
    *,
    dry_run: bool = False,
    screenshot_dir: Path | None = None,
) -> ApplyOutcome:
    profile = load_profile()
    if profile is None:
        raise ProfileMissingError(
            "No profile at ~/.flatpilot/profile.json — run `flatpilot init` first."
        )

    init_db()
    conn = get_conn()

    flat_row = conn.execute(
        "SELECT * FROM flats WHERE id = ?", (flat_id,)
    ).fetchone()
    if flat_row is None:
        raise LookupError(f"no flat with id {flat_id}")
    flat = dict(flat_row)

    platform = str(flat["platform"])

    # These can all fail before we touch the browser. Surface as raises;
    # do not write a row.
    message = compose_anschreiben(profile, platform, flat)
    attachments = resolve_for_platform(profile, platform)
    filler_cls = get_filler(platform)
    filler = filler_cls()

    if dry_run:
        report = filler.fill(
            listing_url=str(flat["listing_url"]),
            message=message,
            attachments=attachments,
            submit=False,
            screenshot_dir=screenshot_dir,
        )
        return ApplyOutcome(
            status="dry_run",
            application_id=None,
            fill_report=report,
        )

    try:
        report = filler.fill(
            listing_url=str(flat["listing_url"]),
            message=message,
            attachments=attachments,
            submit=True,
            screenshot_dir=screenshot_dir,
        )
    except FillError as exc:
        application_id = _record_application(
            conn,
            profile=profile,
            flat=flat,
            message=message,
            attachments=attachments,
            status="failed",
            notes=str(exc),
        )
        logger.warning(
            "apply: flat_id=%d failed: %s (application_id=%d)",
            flat_id,
            exc,
            application_id,
        )
        # Re-raise so the CLI / server caller can handle it (exit code,
        # 5xx response, etc.). The row write above is the durable trail.
        raise
    else:
        application_id = _record_application(
            conn,
            profile=profile,
            flat=flat,
            message=report.message_sent,
            attachments=report.attachments_sent,
            status="submitted",
            notes=None,
        )
        logger.info(
            "apply: flat_id=%d submitted (application_id=%d)",
            flat_id,
            application_id,
        )
        return ApplyOutcome(
            status="submitted",
            application_id=application_id,
            fill_report=report,
        )


def _record_application(
    conn,
    *,
    profile: Profile,
    flat: dict,
    message: str,
    attachments: list[Path],
    status: str,
    notes: str | None,
) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            rent_warm_eur, rooms, size_sqm, district,
            applied_at, method,
            message_sent, attachments_sent_json,
            status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?)
        """,
        (
            flat["id"],
            flat["platform"],
            flat["listing_url"],
            flat["title"],
            flat.get("rent_warm_eur"),
            flat.get("rooms"),
            flat.get("size_sqm"),
            flat.get("district"),
            now,
            message,
            json.dumps([str(p) for p in attachments]),
            status,
            notes,
        ),
    )
    return int(cur.lastrowid)
