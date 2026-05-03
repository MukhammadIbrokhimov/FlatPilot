"""DB writers for user-driven actions on matches and applications.

These functions are kept off the request handlers so they can be unit
tested with the ``tmp_db`` fixture without a server thread. Each
function receives an open ``sqlite3.Connection`` so callers from
multiple threads (the dashboard server) supply their own per-thread
connection via ``flatpilot.database.get_conn()``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal

from flatpilot.users import DEFAULT_USER_ID

ResponseStatus = Literal["viewing_invited", "rejected", "no_response"]


def record_skip(
    conn: sqlite3.Connection,
    *,
    match_id: int,
    profile_hash: str,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    """Insert a 'skipped' matches row for the flat referenced by ``match_id``.

    Audit-preserving: leaves the original 'match' row alone. Idempotent
    via the table's ``UNIQUE (user_id, flat_id, profile_version_hash, decision)``
    constraint.
    """
    row = conn.execute(
        "SELECT flat_id FROM matches WHERE id = ? AND user_id = ?",
        (match_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"no match with id {match_id}")
    flat_id = int(row["flat_id"])
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO matches
            (user_id, flat_id, profile_version_hash, decision,
             decision_reasons_json, decided_at)
        VALUES (?, ?, ?, 'skipped', '[]', ?)
        """,
        (user_id, flat_id, profile_hash, now),
    )


def record_response(
    conn: sqlite3.Connection,
    *,
    application_id: int,
    status: ResponseStatus,
    response_text: str,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    """Update an applications row with a landlord reply.

    Sets ``status``, ``response_text`` and ``response_received_at=now``.
    Allowed status values are constrained to the post-application
    transitions (the L4 path-set values 'submitted'/'failed' are
    rejected). Raises ``LookupError`` if the row doesn't exist and
    ``ValueError`` for an out-of-range status.
    """
    if status not in ("viewing_invited", "rejected", "no_response"):
        raise ValueError(f"unsupported response status: {status!r}")
    row = conn.execute(
        "SELECT id FROM applications WHERE id = ? AND user_id = ?",
        (application_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"no application with id {application_id}")
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        UPDATE applications
           SET status = ?,
               response_text = ?,
               response_received_at = ?
         WHERE id = ?
           AND user_id = ?
        """,
        (status, response_text, now, application_id, user_id),
    )
