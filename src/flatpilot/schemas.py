"""Table definitions registered into :mod:`flatpilot.database`.

Importing this module has the side effect of populating
:data:`flatpilot.database.SCHEMAS` (and :data:`COLUMNS`, for any forward
migrations). :func:`flatpilot.database.init_db` imports this module lazily so
downstream callers only need to call ``init_db()``.

SQLite quirks worth remembering:
- There is no BOOLEAN type. Booleans are stored as INTEGER 0/1.
- Dates and timestamps are stored as TEXT in ISO-8601 form (UTC).
- ``INTEGER PRIMARY KEY`` aliases ``rowid`` — no extra storage.
"""

from __future__ import annotations

from flatpilot.database import SCHEMAS


FLATS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS flats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    listing_url TEXT NOT NULL,
    title TEXT NOT NULL,
    rent_warm_eur REAL,
    rent_cold_eur REAL,
    extra_costs_eur REAL,
    rooms REAL,
    size_sqm REAL,
    address TEXT,
    district TEXT,
    lat REAL,
    lng REAL,
    online_since TEXT,
    available_from TEXT,
    requires_wbs INTEGER NOT NULL DEFAULT 0,
    wbs_size_category INTEGER,
    wbs_income_category INTEGER,
    furnished INTEGER,
    deposit_eur INTEGER,
    min_contract_months INTEGER,
    pets_allowed INTEGER,
    description TEXT,
    scraped_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    canonical_flat_id INTEGER REFERENCES flats(id) ON DELETE SET NULL,
    UNIQUE (platform, external_id)
)
"""

SCHEMAS["flats"] = FLATS_CREATE_SQL


MATCHES_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    profile_version_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('match', 'reject', 'skipped')),
    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
    decided_at TEXT NOT NULL,
    notified_at TEXT,
    notified_channels_json TEXT,
    UNIQUE (flat_id, profile_version_hash, decision)
)
"""

SCHEMAS["matches"] = MATCHES_CREATE_SQL
