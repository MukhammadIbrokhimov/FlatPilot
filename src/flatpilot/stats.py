"""Aggregate counters over the flats and matches tables.

Used by ``flatpilot status`` and the HTML dashboard. Kept deliberately
small — anything richer should live closer to its caller.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from flatpilot.database import get_conn
from flatpilot.users import DEFAULT_USER_ID


class Stats(TypedDict):
    total_flats: int
    new_last_24h: int
    matched: int
    notified: int
    rejected_by_reason: dict[str, int]
    last_scrape_at: str | None
    notifications_by_channel: dict[str, int]


def get_stats(user_id: int = DEFAULT_USER_ID) -> Stats:
    conn = get_conn()

    total_flats = conn.execute("SELECT COUNT(*) FROM flats").fetchone()[0]

    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    new_last_24h = conn.execute(
        "SELECT COUNT(*) FROM flats WHERE first_seen_at >= ?",
        (since,),
    ).fetchone()[0]

    matched = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE decision = 'match' AND user_id = ?",
        (user_id,),
    ).fetchone()[0]

    notified = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE notified_at IS NOT NULL AND user_id = ?",
        (user_id,),
    ).fetchone()[0]

    last_scrape_at = conn.execute("SELECT MAX(scraped_at) FROM flats").fetchone()[0]

    rejected_by_reason: dict[str, int] = {}
    for (reasons_json,) in conn.execute(
        "SELECT decision_reasons_json FROM matches WHERE decision = 'reject' AND user_id = ?",
        (user_id,),
    ):
        try:
            reasons = json.loads(reasons_json)
        except (TypeError, json.JSONDecodeError):
            continue
        for reason in reasons:
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1

    notifications_by_channel: dict[str, int] = {}
    for (channels_json,) in conn.execute(
        "SELECT notified_channels_json FROM matches "
        "WHERE notified_at IS NOT NULL "
        "AND notified_channels_json IS NOT NULL "
        "AND user_id = ?",
        (user_id,),
    ):
        try:
            channels = json.loads(channels_json)
        except (TypeError, json.JSONDecodeError):
            continue
        for channel in channels:
            notifications_by_channel[channel] = notifications_by_channel.get(channel, 0) + 1

    return {
        "total_flats": total_flats,
        "new_last_24h": new_last_24h,
        "matched": matched,
        "notified": notified,
        "rejected_by_reason": rejected_by_reason,
        "last_scrape_at": last_scrape_at,
        "notifications_by_channel": notifications_by_channel,
    }
