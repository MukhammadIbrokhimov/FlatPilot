"""Reclassify silent-success false-fail rows in the applications table.

Background (FlatPilot-8kt): WG-Gesucht's post-submit UX changed to render
a success card in-place on the form URL rather than redirecting. The
filler's URL-based detection mis-classified these as
``SubmitVerificationError`` for a window of pipeline runs, so the
``applications`` table holds rows with ``status='failed'`` for messages
that actually reached the landlord.

This module identifies those rows and re-classifies them as
``status='submitted'`` so the daily-cap counter, dashboard, and
auto-apply queue exclusions reflect reality.

A row is treated as a silent-success false-fail iff:

1. ``method='auto'`` and ``status='failed'``.
2. ``notes`` starts with one of the wg-gesucht silent-success error
   strings emitted before/after the FlatPilot-8kt fix.
3. There exists a *later* row for the same ``flat_id`` with notes
   starting with ``auto_skipped: listing_expired`` — proof that the
   listing's contact CTA has been hidden, which on wg-gesucht only
   happens after the user successfully contacts the listing.

Idempotency: after reclassification ``status='submitted'`` is the
filter that excludes the row, so a second invocation finds no
candidates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from flatpilot.users import DEFAULT_USER_ID

# Notes prefixes the wg-gesucht filler has emitted for silent-success
# rows. Pre-FlatPilot-8kt fix used the first; post-fix uses the second.
# Both shapes need re-classifying.
_SILENT_SUCCESS_PATTERNS: tuple[str, ...] = (
    "wg-gesucht: submit did not navigate%",
    "wg-gesucht: submit verification failed%",
)

_RECLASSIFIED_PREFIX = (
    "reclassified by flatpilot reclassify-submits (FlatPilot-8kt): "
)


@dataclass(frozen=True)
class Candidate:
    application_id: int
    flat_id: int
    platform: str
    applied_at: str
    notes: str | None
    listing_url: str
    title: str


def find_candidates(
    conn: sqlite3.Connection,
    *,
    user_id: int = DEFAULT_USER_ID,
) -> list[Candidate]:
    """Return rows that look like silent-success false-fails."""

    note_clauses = " OR ".join(["a1.notes LIKE ?"] * len(_SILENT_SUCCESS_PATTERNS))
    sql = f"""
        SELECT
            a1.id           AS application_id,
            a1.flat_id      AS flat_id,
            a1.platform     AS platform,
            a1.applied_at   AS applied_at,
            a1.notes        AS notes,
            f.listing_url   AS listing_url,
            f.title         AS title
        FROM applications AS a1
        JOIN flats AS f ON f.id = a1.flat_id
        WHERE a1.user_id = ?
          AND a1.method = 'auto'
          AND a1.status = 'failed'
          AND ({note_clauses})
          AND EXISTS (
            SELECT 1 FROM applications AS a2
            WHERE a2.flat_id = a1.flat_id
              AND a2.user_id = a1.user_id
              AND a2.id > a1.id
              AND a2.notes LIKE 'auto_skipped: listing_expired%'
          )
        ORDER BY a1.id
    """
    rows = conn.execute(sql, (user_id, *_SILENT_SUCCESS_PATTERNS)).fetchall()
    return [
        Candidate(
            application_id=int(r["application_id"]),
            flat_id=int(r["flat_id"]),
            platform=str(r["platform"]),
            applied_at=str(r["applied_at"]),
            notes=(None if r["notes"] is None else str(r["notes"])),
            listing_url=str(r["listing_url"]),
            title=str(r["title"]),
        )
        for r in rows
    ]


def apply_reclassification(
    conn: sqlite3.Connection,
    candidates: list[Candidate],
) -> int:
    """Update each candidate row to ``status='submitted'`` and prefix its
    notes with the audit marker. Returns the number of rows updated."""

    if not candidates:
        return 0
    cur = conn.cursor()
    for c in candidates:
        new_notes = _RECLASSIFIED_PREFIX + (c.notes or "")
        cur.execute(
            "UPDATE applications SET status = 'submitted', notes = ? WHERE id = ?",
            (new_notes, c.application_id),
        )
    conn.commit()
    return len(candidates)
