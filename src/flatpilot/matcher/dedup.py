"""Cross-platform flat deduplication.

Populates ``flats.canonical_flat_id`` using a deterministic fuzzy key over
``(normalized_address, rent_warm_eur, size_sqm)``. See the design spec at
``docs/superpowers/specs/2026-04-24-flat-dedup-design.md`` for the full
rule set and the rationale behind each clause.
"""

from __future__ import annotations

import re
import sqlite3  # noqa: F401 — used by follow-up task, imported now to avoid noise commit

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
