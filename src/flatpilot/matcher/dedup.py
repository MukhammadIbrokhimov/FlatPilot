"""Cross-platform flat deduplication.

Populates ``flats.canonical_flat_id`` using a deterministic fuzzy key over
``(normalized_address, rent_warm_eur, size_sqm)``. See the design spec at
``docs/superpowers/specs/2026-04-24-flat-dedup-design.md`` for the full
rule set and the rationale behind each clause.
"""

from __future__ import annotations

import re
import sqlite3

# Matches the Straße family as a whole word. The trailing ``\.?`` catches
# the abbreviated ``Str.`` and ``str.`` forms; ``straße`` and ``strasse``
# match without the dot.
_STRASSE_RE = re.compile(r"\b(?:straße|strasse|str\.?)\b")

# Collapse "42 a" / "42 A" into "42a" so both spellings of the same
# house number cluster. Only applies to a single trailing letter —
# keeps "42 Berlin" unchanged.
_HOUSE_NUMBER_SPACE_RE = re.compile(r"(\d+)\s+([a-z])\b")

# A 5-digit postcode surrounded by word boundaries.
_POSTCODE_RE = re.compile(r"\b\d{5}\b")

# ", berlin" tail or "berlin, " prefix. Other cities are out of scope
# for this bead — FlatPilot is Berlin-first.
_BERLIN_SUFFIX_RE = re.compile(r",\s*berlin\b")
_BERLIN_PREFIX_RE = re.compile(r"\bberlin\s*,\s*")


def normalize_address(raw: str | None) -> str | None:
    """Return the canonical form of a German rental-listing address.

    Returns ``None`` for ``None`` / empty / whitespace-only input. See
    the design spec for the rule set and examples.
    """
    if raw is None:
        return None
    s = raw.strip().lower()
    if not s:
        return None

    s = _POSTCODE_RE.sub("", s)
    s = _BERLIN_PREFIX_RE.sub("", s)
    s = _BERLIN_SUFFIX_RE.sub("", s)
    s = _STRASSE_RE.sub("str", s)
    s = s.replace(".", "").replace(",", "")
    s = _HOUSE_NUMBER_SPACE_RE.sub(r"\1\2", s)
    s = re.sub(r"\s+", " ", s).strip()

    return s or None


def find_canonical(conn: sqlite3.Connection, flat: dict) -> int | None:
    """Return the canonical flat id this row should link to, or ``None``.

    Returns ``None`` when the row cannot be safely deduped (missing
    address/rent/size) or when no existing older row matches on the
    fuzzy key.
    """
    normalized = normalize_address(flat.get("address"))
    rent = flat.get("rent_warm_eur")
    size = flat.get("size_sqm")
    if normalized is None or rent is None or size is None:
        return None

    rows = conn.execute(
        """
        SELECT id, canonical_flat_id, address
          FROM flats
         WHERE id < :self_id
           AND platform != :platform
           AND rent_warm_eur IS NOT NULL
           AND size_sqm IS NOT NULL
           AND address IS NOT NULL
           AND ABS(rent_warm_eur - :rent) <= 50
           AND ABS(size_sqm - :size)      <= 3
         ORDER BY id ASC
        """,
        {
            "self_id": flat["id"],
            "platform": flat["platform"],
            "rent": rent,
            "size": size,
        },
    ).fetchall()

    for row in rows:
        if normalize_address(row["address"]) == normalized:
            # Explicit None check — ``canonical_flat_id or row["id"]``
            # would misroute if a row ever had id=0. SQLite AUTOINCREMENT
            # starts at 1, but the explicit form documents the intent.
            return (
                row["canonical_flat_id"]
                if row["canonical_flat_id"] is not None
                else row["id"]
            )
    return None


def assign_canonical(conn: sqlite3.Connection, flat_id: int) -> None:
    """Look up a twin for the row with ``flat_id`` and stamp the link.

    Safe to call on any flat id — a missing row, a row that cannot be
    deduped (missing fields), or the oldest member of a new cluster
    all result in a no-op.
    """
    row = conn.execute("SELECT * FROM flats WHERE id = ?", (flat_id,)).fetchone()
    if row is None:
        return
    canonical = find_canonical(conn, dict(row))
    if canonical is None or canonical == flat_id:
        return
    conn.execute(
        "UPDATE flats SET canonical_flat_id = ? WHERE id = ?",
        (canonical, flat_id),
    )
