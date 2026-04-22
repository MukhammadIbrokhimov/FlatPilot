"""Drive the matcher over unmatched flats and write match rows.

``run_match`` reads flats that have not yet been decided under the current
profile hash, evaluates them through the filter chain, and writes one
``matches`` row per ``(flat_id, profile_version_hash, decision)`` triple.
``INSERT OR IGNORE`` plus the uniqueness constraint on that triple keeps
reruns idempotent.

The profile hash is a SHA-256 prefix of ``profile.model_dump_json()`` — any
substantive config change produces a new hash, so a user who tightens
rent bands or flips the WBS flag will see all flats re-evaluated on the
next ``flatpilot match``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TypedDict

from flatpilot.database import get_conn, init_db
from flatpilot.matcher.filters import evaluate
from flatpilot.profile import load_profile, profile_hash

logger = logging.getLogger(__name__)


class MatchSummary(TypedDict):
    processed: int
    match: int
    reject: int
    profile_hash: str


class ProfileMissingError(RuntimeError):
    """Raised when ``flatpilot match`` runs before ``flatpilot init``."""


def run_match() -> MatchSummary:
    profile = load_profile()
    if profile is None:
        raise ProfileMissingError(
            "No profile at ~/.flatpilot/profile.json — run `flatpilot init` first."
        )

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)
    now = datetime.now(UTC).isoformat()

    rows = conn.execute(
        """
        SELECT f.*
        FROM flats f
        LEFT JOIN matches m
            ON m.flat_id = f.id AND m.profile_version_hash = ?
        WHERE m.id IS NULL
        """,
        (phash,),
    ).fetchall()

    counts = {"match": 0, "reject": 0}
    for row in rows:
        flat = dict(row)
        reasons = evaluate(flat, profile)
        decision = "reject" if reasons else "match"
        conn.execute(
            """
            INSERT OR IGNORE INTO matches
                (flat_id, profile_version_hash, decision, decision_reasons_json, decided_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (flat["id"], phash, decision, json.dumps(reasons), now),
        )
        counts[decision] += 1

    logger.info(
        "matcher: processed %d flats under profile %s — %d match, %d reject",
        len(rows),
        phash,
        counts["match"],
        counts["reject"],
    )
    return {
        "processed": len(rows),
        "match": counts["match"],
        "reject": counts["reject"],
        "profile_hash": phash,
    }
