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
from flatpilot.errors import ProfileMissingError
from flatpilot.matcher.filters import evaluate
from flatpilot.profile import load_profile, profile_hash
from flatpilot.users import DEFAULT_USER_ID

logger = logging.getLogger(__name__)


class MatchSummary(TypedDict):
    processed: int
    match: int
    reject: int
    profile_hash: str


def run_match(user_id: int = DEFAULT_USER_ID) -> MatchSummary:
    profile = load_profile()
    if profile is None:
        raise ProfileMissingError(
            "No profile at ~/.flatpilot/profile.json — run `flatpilot init` first."
        )

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)
    now = datetime.now(UTC).isoformat()

    # Only evaluate canonical roots. PR #21 stamps canonical_flat_id on
    # every scrape insert; duplicates always have a non-NULL link, so
    # restricting to IS NULL means each cluster gets exactly one match
    # row (keyed on the oldest row in the cluster).
    rows = conn.execute(
        """
        SELECT f.*
        FROM flats f
        LEFT JOIN matches m
            ON m.flat_id = f.id
            AND m.profile_version_hash = ?
            AND m.user_id = ?
        WHERE m.id IS NULL
          AND f.canonical_flat_id IS NULL
        """,
        (phash, user_id),
    ).fetchall()

    from flatpilot.auto_apply import overlay_profile

    counts = {"match": 0, "reject": 0}
    for row in rows:
        flat = dict(row)

        base_reasons = evaluate(flat, profile)
        matched_saved: list[str] = []
        for ss in profile.saved_searches:
            if not evaluate(flat, overlay_profile(profile, ss)):
                matched_saved.append(ss.name)

        decision = "match" if not base_reasons or matched_saved else "reject"

        logger.debug(
            "matcher: platform=%s external_id=%s decision=%s reasons=%s saved=%s",
            flat.get("platform"),
            flat.get("external_id"),
            decision,
            base_reasons,
            matched_saved,
        )

        conn.execute(
            """
            INSERT OR IGNORE INTO matches
                (user_id, flat_id, profile_version_hash, decision,
                 decision_reasons_json, decided_at, matched_saved_searches_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                flat["id"],
                phash,
                decision,
                json.dumps(base_reasons),
                now,
                json.dumps(matched_saved),
            ),
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
