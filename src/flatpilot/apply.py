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
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

# Force the filler registry to populate before apply_to_flat runs.
# init_db() handles the schemas import internally.
import flatpilot.fillers.kleinanzeigen  # noqa: F401
import flatpilot.fillers.wg_gesucht  # noqa: F401
from flatpilot.attachments import resolve_for_platform
from flatpilot.compose import compose_anschreiben
from flatpilot.database import get_conn, init_db
from flatpilot.errors import ProfileMissingError
from flatpilot.fillers import get_filler
from flatpilot.fillers.base import FillError, FillReport
from flatpilot.profile import Profile, load_profile

logger = logging.getLogger(__name__)

DEFAULT_APPLY_TIMEOUT_SEC = 180

# Buffer added to apply_timeout_sec() before reaping stale state.
# Wide enough that we never reap a row a slow apply could legitimately
# still be holding, narrow enough that a kill -9'd holder unblocks the
# next request reasonably fast. Shared with the dashboard's in-process
# watchdog (see flatpilot.server._inflight_watchdog_threshold_sec) so
# the DB lock reaper and the in-flight slot watchdog reap on the same
# schedule.
STALE_APPLY_BUFFER_SEC = 60

# Exit code emitted by `flatpilot apply` when the cross-process
# apply_locks lock for the target flat is already held by another
# process. The dashboard's _handle_apply maps this returncode to
# HTTP 409 (Conflict) — semantically "apply already in progress,
# retry later" — instead of the generic 500. Exit 1 stays reserved
# for everything else (post-submit duplicate row, FillError,
# ProfileMissingError, AttachmentError, TemplateError). FlatPilot-wsp.
APPLY_LOCK_HELD_EXIT = 4


def apply_timeout_sec() -> int:
    """Resolve the per-call apply subprocess timeout.

    Default 180s — a reasonable upper bound for a headed Playwright
    apply (load + login + fill + submit + screenshot typically takes
    20-60s, with margin for slow networks and one CAPTCHA-equivalent
    prompt). Override via ``FLATPILOT_APPLY_TIMEOUT_SEC`` for users on
    very slow networks (raise it) or paranoid CI environments (lower
    it). Invalid values (non-int, non-positive) log a warning and fall
    back to the default — the caller has no recourse here, and a typo
    in an ergonomics env var shouldn't break apply.
    """
    raw = os.environ.get("FLATPILOT_APPLY_TIMEOUT_SEC")
    if raw is None:
        return DEFAULT_APPLY_TIMEOUT_SEC
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "FLATPILOT_APPLY_TIMEOUT_SEC=%r is not an int; using default %ds",
            raw,
            DEFAULT_APPLY_TIMEOUT_SEC,
        )
        return DEFAULT_APPLY_TIMEOUT_SEC
    if v <= 0:
        logger.warning(
            "FLATPILOT_APPLY_TIMEOUT_SEC=%d must be > 0; using default %ds",
            v,
            DEFAULT_APPLY_TIMEOUT_SEC,
        )
        return DEFAULT_APPLY_TIMEOUT_SEC
    return v


class AlreadyAppliedError(RuntimeError):
    """Raised when a flat already has a successful submitted application.

    The schema allows multiple ``applications`` rows per flat (so a failed
    submit followed by a retry both leave a trail), but two ``status='submitted'``
    rows mean we sent the landlord two messages — almost always a mistake.
    The CLI / dashboard surfaces this as a user-correctable error so a
    double-click or two open dashboards can't accidentally double-submit.
    """


class ApplyLockHeldError(AlreadyAppliedError):
    """Raised by :func:`acquire_apply_lock` when the cross-process lock
    for ``flat_id`` is already held by another process.

    Subclasses :class:`AlreadyAppliedError` so existing
    ``except AlreadyAppliedError`` and
    ``pytest.raises(AlreadyAppliedError, ...)`` callsites keep matching.
    The CLI uses this discrimination to exit ``APPLY_LOCK_HELD_EXIT``
    (4) instead of 1, which the dashboard's ``_handle_apply`` maps to
    HTTP 409 instead of 500.

    The post-submit duplicate-row path in :func:`apply_to_flat`
    intentionally keeps raising plain :class:`AlreadyAppliedError` —
    that case is a logically-completed application (not a transient
    contention), and exit 1 / HTTP 500 is the correct surface (the
    user should not retry). Promoting that raise to this subclass
    would be a behavioral regression. FlatPilot-wsp.
    """


def acquire_apply_lock(conn, flat_id: int) -> None:
    """Take a cross-process lock on ``flat_id``.

    Two FlatPilot processes — typically the CLI ``flatpilot apply 42``
    and the dashboard's ``POST /api/applications`` subprocess — racing
    on the same flat must not both reach ``filler.fill(submit=True)``,
    or the landlord receives two messages. The ``apply_locks`` table
    has ``flat_id`` as PRIMARY KEY: ``INSERT`` from the second caller
    raises ``sqlite3.IntegrityError`` and we surface it as
    ``AlreadyAppliedError`` with a message distinct from the existing
    "already has a submitted application" path.

    Stale rows (acquired_at older than
    ``apply_timeout_sec() + STALE_APPLY_BUFFER_SEC``) are reaped before
    the INSERT so a process crash (kill -9) doesn't permanently block
    future applies for that flat. The buffer means we never reap a row
    that could legitimately still be held by a slow apply. Reaping is
    bounded to the target flat — siblings are left alone so parallel
    acquires for different flats don't trip on each other.
    """
    threshold_ts = (
        datetime.now(UTC)
        - timedelta(seconds=apply_timeout_sec() + STALE_APPLY_BUFFER_SEC)
    ).isoformat()
    conn.execute(
        "DELETE FROM apply_locks WHERE flat_id = ? AND acquired_at < ?",
        (flat_id, threshold_ts),
    )
    try:
        conn.execute(
            "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
            (flat_id, datetime.now(UTC).isoformat(), os.getpid()),
        )
    except sqlite3.IntegrityError as exc:
        existing = conn.execute(
            "SELECT pid, acquired_at FROM apply_locks WHERE flat_id = ?",
            (flat_id,),
        ).fetchone()
        if existing is not None:
            msg = (
                f"flat {flat_id} apply already in progress "
                f"(pid={existing['pid']}, since {existing['acquired_at']})"
            )
        else:
            # Race window: holder released between our INSERT failing
            # and our SELECT. Bubble up rather than auto-retry inside an
            # exception handler — a fresh user click is the explicit
            # recovery path.
            msg = f"flat {flat_id} apply already in progress (lock contention; please retry)"
        raise ApplyLockHeldError(msg) from exc


def release_apply_lock(conn, flat_id: int) -> None:
    """Release the cross-process lock for ``flat_id``.

    No-op if no row exists (e.g. the caller never successfully acquired,
    or a stale-row sweep already reaped it). Always safe in a finally.
    """
    conn.execute("DELETE FROM apply_locks WHERE flat_id = ?", (flat_id,))


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

    existing = conn.execute(
        "SELECT id FROM applications WHERE flat_id = ? AND status = 'submitted' LIMIT 1",
        (flat_id,),
    ).fetchone()
    if existing is not None:
        raise AlreadyAppliedError(
            f"flat {flat_id} already has a submitted application "
            f"(application_id={existing['id']}); refusing to double-submit"
        )

    acquire_apply_lock(conn, flat_id)
    try:
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
    finally:
        release_apply_lock(conn, flat_id)


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
