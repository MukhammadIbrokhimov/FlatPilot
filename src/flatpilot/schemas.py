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

from flatpilot.database import COLUMN_BACKFILLS, COLUMNS, SCHEMAS

USERS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    email_normalized TEXT,
    created_at TEXT NOT NULL
)
"""

USERS_EMAIL_NORMALIZED_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_normalized
    ON users(email_normalized) WHERE email_normalized IS NOT NULL
"""

SCHEMAS["users"] = USERS_CREATE_SQL
SCHEMAS["idx_users_email_normalized"] = USERS_EMAIL_NORMALIZED_INDEX_SQL

# Upgrade path: existing installs built before Bundle-B lack email_normalized.
COLUMNS.setdefault("users", {})["email_normalized"] = "TEXT"
COLUMN_BACKFILLS.setdefault("users", {})["email_normalized"] = (
    "UPDATE users SET email_normalized = LOWER(TRIM(email))"
    " WHERE email IS NOT NULL AND email_normalized IS NULL"
)

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

MAGIC_LINK_TOKENS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    jti TEXT PRIMARY KEY NOT NULL,
    email TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
)
"""

MAGIC_LINK_TOKENS_EXPIRES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_expires
    ON magic_link_tokens(expires_at)
"""

SCHEMAS["magic_link_tokens"] = MAGIC_LINK_TOKENS_CREATE_SQL
SCHEMAS["idx_magic_link_tokens_expires"] = MAGIC_LINK_TOKENS_EXPIRES_INDEX_SQL
