"""User identity primitives.

Phase 1 (CLI) and the post-this-PR schema both use a single seed user
(`id=1`). Phase 5 (Web UI) will populate the table from magic-link
signups. `DEFAULT_USER_ID` is the constant every query and INSERT in
the CLI path threads through.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

DEFAULT_USER_ID = 1


def ensure_default_user(conn: sqlite3.Connection) -> None:
    """Insert the seed user (id=1, email=NULL) if absent. Idempotent."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, created_at) VALUES (1, NULL, ?)",
        (now,),
    )
