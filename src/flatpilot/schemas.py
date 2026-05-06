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

from flatpilot.database import COLUMNS, SCHEMAS

USERS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    created_at TEXT NOT NULL
)
"""

SCHEMAS["users"] = USERS_CREATE_SQL

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
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    profile_version_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('match', 'reject', 'skipped')),
    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
    decided_at TEXT NOT NULL,
    notified_at TEXT,
    notified_channels_json TEXT,
    matched_saved_searches_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (user_id, flat_id, profile_version_hash, decision)
)
"""

SCHEMAS["matches"] = MATCHES_CREATE_SQL


# Each row is one outgoing contact attempt for a flat. Multiple rows per
# flat are allowed (e.g. a failed submit followed by a retry) — idempotency
# belongs in the apply command (L4), not the schema. The flat columns are
# denormalised on purpose: a landlord may take the listing down days after
# we contacted them, but we still want to see what we actually sent.
APPLICATIONS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    listing_url TEXT NOT NULL,
    title TEXT NOT NULL,
    rent_warm_eur REAL,
    rooms REAL,
    size_sqm REAL,
    district TEXT,
    applied_at TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('manual', 'auto')),
    message_sent TEXT,
    attachments_sent_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('submitted', 'failed', 'viewing_invited', 'rejected', 'no_response')
    ),
    response_received_at TEXT,
    response_text TEXT,
    notes TEXT,
    triggered_by_saved_search TEXT
)
"""

SCHEMAS["applications"] = APPLICATIONS_CREATE_SQL


# A row exists for the duration of an in-flight apply for ``flat_id``.
# Cross-process correctness layer: two FlatPilot processes (CLI +
# dashboard) racing on the same flat both attempt INSERT — the
# PRIMARY KEY makes exactly one win, the loser raises
# AlreadyAppliedError. Stale rows older than ``apply_timeout_sec() +
# 60`` are reaped on next acquire to recover from process crashes
# (kill -9, OS panic) so the slot doesn't block forever.
APPLY_LOCKS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS apply_locks (
    flat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
    acquired_at TEXT NOT NULL,
    pid INTEGER NOT NULL,
    PRIMARY KEY (flat_id, user_id)
)
"""

SCHEMAS["apply_locks"] = APPLY_LOCKS_CREATE_SQL

# Forward-migration columns (FlatPilot-a9l): legacy DBs created before the
# saved-searches feature predate these CREATE TABLE columns, and the
# user_id rebuild migration's INSERT SELECT reads from them. ensure_columns
# must run before _rebuild_user_scoped_tables so the source tables have
# them before the rebuild copies rows.
COLUMNS["matches"] = {
    "matched_saved_searches_json": "TEXT NOT NULL DEFAULT '[]'",
}
COLUMNS["applications"] = {
    "triggered_by_saved_search": "TEXT",
}

APPLICATIONS_METHOD_APPLIED_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_applications_method_applied_at
    ON applications(method, applied_at)
"""

SCHEMAS["idx_applications_method_applied_at"] = APPLICATIONS_METHOD_APPLIED_AT_INDEX_SQL

MATCHES_USER_DECIDED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_matches_user_decided
    ON matches(user_id, decided_at)
"""

SCHEMAS["idx_matches_user_decided"] = MATCHES_USER_DECIDED_INDEX_SQL


APPLICATIONS_USER_APPLIED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_applications_user_applied
    ON applications(user_id, applied_at)
"""

SCHEMAS["idx_applications_user_applied"] = APPLICATIONS_USER_APPLIED_INDEX_SQL
