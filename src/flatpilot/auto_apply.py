"""Auto-apply primitives + pipeline stage."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime

import flatpilot.fillers.kleinanzeigen  # noqa: F401
import flatpilot.fillers.wg_gesucht  # noqa: F401
from flatpilot.apply import _record_application, apply_to_flat
from flatpilot.attachments import AttachmentError, resolve_for_platform
from flatpilot.compose import TemplateError, compose_anschreiben
from flatpilot.config import APP_DIR
from flatpilot.database import get_conn, init_db
from flatpilot.fillers import get_filler
from flatpilot.fillers.base import FillError
from flatpilot.profile import Profile, SavedSearch, profile_hash

logger = logging.getLogger(__name__)

_OVERLAY_FIELDS_SCALAR = (
    "rent_min_warm",
    "rent_max_warm",
    "rooms_min",
    "rooms_max",
    "radius_km",
    "furnished_pref",
    "min_contract_months",
)

PAUSE_PATH = APP_DIR / "PAUSE"


def overlay_profile(profile: Profile, saved_search: SavedSearch | None) -> Profile:
    if saved_search is None:
        return profile
    updates: dict[str, object] = {}
    for field in _OVERLAY_FIELDS_SCALAR:
        v = getattr(saved_search, field)
        if v is not None:
            updates[field] = v
    if saved_search.district_allowlist is not None:
        updates["district_allowlist"] = saved_search.district_allowlist
    return profile.model_copy(update=updates)


def is_paused() -> bool:
    return PAUSE_PATH.exists()


def daily_cap_remaining(
    conn: sqlite3.Connection, profile: Profile, platform: str
) -> int:
    cap = profile.auto_apply.daily_cap_per_platform.get(platform, 0)
    if cap <= 0:
        return 0
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    used = conn.execute(
        "SELECT COUNT(*) FROM applications "
        "WHERE platform = ? AND method = 'auto' "
        "AND status = 'submitted' AND applied_at >= ?",
        (platform, today_start),
    ).fetchone()[0]
    return max(0, cap - used)


def cooldown_remaining_sec(
    conn: sqlite3.Connection, profile: Profile, platform: str
) -> float:
    cooldown = profile.auto_apply.cooldown_seconds_per_platform.get(platform, 0)
    if cooldown <= 0:
        return 0.0
    row = conn.execute(
        "SELECT MAX(applied_at) AS last FROM applications "
        "WHERE platform = ? AND method = 'auto' "
        "AND ( status = 'submitted' "
        "      OR (status = 'failed' "
        "          AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')))",
        (platform,),
    ).fetchone()
    last = row["last"] if row is not None else None
    if last is None:
        return 0.0
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
    return max(0.0, cooldown - elapsed)


def completeness_ok(profile: Profile, flat: dict) -> tuple[bool, str | None]:
    platform = str(flat["platform"])
    try:
        get_filler(platform)
    except KeyError:
        return False, f"filler not registered for platform {platform!r}"
    try:
        compose_anschreiben(profile, platform, flat)
    except TemplateError as exc:
        return False, f"template: {exc}"
    try:
        resolve_for_platform(profile, platform)
    except AttachmentError as exc:
        return False, f"attachment: {exc}"
    return True, None


def run_pipeline_apply(profile, console, *, dry_run: bool = False) -> None:
    if is_paused():
        console.print("[yellow]auto-apply: PAUSED (~/.flatpilot/PAUSE present)[/yellow]")
        return

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)

    rows = conn.execute(
        """
        SELECT m.id AS match_id,
               m.matched_saved_searches_json,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        WHERE m.decision = 'match'
          AND m.profile_version_hash = ?
          AND m.matched_saved_searches_json != '[]'
          AND NOT EXISTS (
            SELECT 1 FROM applications a
            WHERE a.flat_id = m.flat_id
              AND a.method = 'auto'
              AND a.status = 'submitted'
          )
        ORDER BY m.decided_at ASC
        """,
        (phash,),
    ).fetchall()

    saved_search_by_name = {ss.name: ss for ss in profile.saved_searches}

    for row in rows:
        flat = dict(row)
        platform = str(flat["platform"])
        candidate_names = json.loads(flat["matched_saved_searches_json"])

        _try_flat(
            conn=conn,
            console=console,
            profile=profile,
            flat=flat,
            platform=platform,
            candidate_names=candidate_names,
            saved_search_by_name=saved_search_by_name,
            dry_run=dry_run,
        )


def _try_flat(
    *, conn, console, profile, flat, platform, candidate_names,
    saved_search_by_name, dry_run,
) -> None:
    flat_id = int(flat["id"])

    for name in candidate_names:
        ss = saved_search_by_name.get(name)
        if ss is None:
            continue
        if not ss.auto_apply:
            continue
        if ss.platforms and platform not in ss.platforms:
            continue

        cap = daily_cap_remaining(conn, profile, platform)
        if cap <= 0:
            console.print(
                f"[dim]auto-apply: cap reached on {platform}; skipping flat {flat_id}[/dim]"
            )
            return

        wait = cooldown_remaining_sec(conn, profile, platform)
        if wait > 0:
            console.print(
                f"[dim]auto-apply: cooldown {wait:.0f}s on {platform}; "
                f"skipping flat {flat_id}[/dim]"
            )
            return

        ok, reason = completeness_ok(profile, flat)
        if not ok:
            attachments: list = []
            try:
                attachments = resolve_for_platform(profile, platform)
            except Exception:
                attachments = []

            _record_application(
                conn,
                profile=profile,
                flat=flat,
                message="",
                attachments=attachments,
                status="failed",
                notes=f"auto_skipped: {reason}",
                method="auto",
                saved_search=name,
            )
            console.print(
                f"[yellow]auto-apply: skipped flat {flat_id} ({reason})[/yellow]"
            )
            return

        if dry_run:
            console.print(
                f"[cyan]auto-apply (dry-run): would apply flat {flat_id} "
                f"via saved-search '{name}'[/cyan]"
            )
            return

        try:
            apply_to_flat(flat_id, method="auto", saved_search=name)
            console.print(
                f"[green]auto-apply: submitted flat {flat_id} "
                f"via saved-search '{name}'[/green]"
            )
            return
        except FillError as exc:
            console.print(
                f"[red]auto-apply: filler failed for flat {flat_id}: {exc}[/red]"
            )
            return
