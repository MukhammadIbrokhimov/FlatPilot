"""Auto-apply primitives + pipeline stage."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import flatpilot.fillers.kleinanzeigen  # noqa: F401
import flatpilot.fillers.wg_gesucht  # noqa: F401
from flatpilot.apply import _record_application, apply_to_flat
from flatpilot.attachments import AttachmentError, resolve_for_platform
from flatpilot.compose import TemplateError, compose_anschreiben
from flatpilot.config import APP_DIR, FAILURE_SCREENSHOTS_DIR
from flatpilot.database import get_conn, init_db
from flatpilot.fillers import get_filler
from flatpilot.fillers.base import FillError
from flatpilot.profile import Profile, SavedSearch, profile_hash
from flatpilot.users import DEFAULT_USER_ID

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

# How long a flat stays excluded from auto-apply after being classified
# as expired by a filler. Long enough that re-trying a deleted listing
# every run is rare; short enough that a hypothetical filler-selector
# regression heals automatically once the selector is fixed (without
# requiring a manual DB cleanup pass). FlatPilot-tgw.
LISTING_EXPIRED_TTL = timedelta(days=7)


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
    conn: sqlite3.Connection,
    profile: Profile,
    platform: str,
    user_id: int = DEFAULT_USER_ID,
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
        "AND status = 'submitted' AND applied_at >= ? AND user_id = ?",
        (platform, today_start, user_id),
    ).fetchone()[0]
    return max(0, cap - used)


def cooldown_remaining_sec(
    conn: sqlite3.Connection,
    profile: Profile,
    platform: str,
    user_id: int = DEFAULT_USER_ID,
) -> float:
    cooldown = profile.auto_apply.cooldown_seconds_per_platform.get(platform, 0)
    pacing = profile.auto_apply.pacing_seconds_per_platform.get(platform, 0)
    effective = max(cooldown, pacing)
    if effective <= 0:
        return 0.0
    row = conn.execute(
        "SELECT MAX(applied_at) AS last FROM applications "
        "WHERE platform = ? AND method = 'auto' "
        "AND user_id = ? "
        "AND ( status = 'submitted' "
        "      OR (status = 'failed' "
        "          AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')))",
        (platform, user_id),
    ).fetchone()
    last = row["last"] if row is not None else None
    if last is None:
        return 0.0
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
    return max(0.0, effective - elapsed)


def failures_for_flat(
    conn: sqlite3.Connection,
    flat_id: int,
    user_id: int = DEFAULT_USER_ID,
) -> int:
    """Count consecutive FillError-style failures (status='failed', method='auto')
    for this flat, excluding auto_skipped rows."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications "
        "WHERE flat_id = ? AND method = 'auto' AND status = 'failed' "
        "AND user_id = ? "
        "AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')",
        (flat_id, user_id),
    ).fetchone()
    return int(row["n"])


def flats_over_max_failures(
    conn: sqlite3.Connection,
    profile: Profile,
    user_id: int = DEFAULT_USER_ID,
) -> int:
    """Count distinct flats whose real auto-apply failures meet/exceed the
    profile's ``max_failures_per_flat`` threshold (the same predicate
    :func:`failures_for_flat` uses, applied DB-wide). Auto-skipped rows
    are excluded — they are not filler errors."""
    cap = profile.auto_apply.max_failures_per_flat
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM (
          SELECT flat_id
          FROM applications
          WHERE method = 'auto' AND status = 'failed'
            AND user_id = ?
            AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')
          GROUP BY flat_id
          HAVING COUNT(*) >= ?
        )
        """,
        (user_id, cap),
    ).fetchone()
    return int(row["n"] if row is not None else 0)


def _has_filler(platform: str) -> bool:
    try:
        get_filler(platform)
    except LookupError:
        return False
    return True


def reachable_platforms(profile: Profile) -> list[str]:
    """Platforms that can plausibly advance the daily cap during a drain loop.

    Excludes cap=0 platforms (auto-apply disabled by config) and platforms
    with no registered filler (scrape-only platforms like inberlinwohnen).
    The drain loop must not block on these — their cap never decrements.
    """
    return [
        p for p, cap in profile.auto_apply.daily_cap_per_platform.items()
        if cap > 0 and _has_filler(p)
    ]


def drain_complete(
    conn: sqlite3.Connection,
    profile: Profile,
    *,
    empty_pass_streak: int,
    user_id: int = DEFAULT_USER_ID,
) -> bool:
    """Decide whether the looping drain should exit after this pass."""
    reachable = reachable_platforms(profile)
    if not reachable:
        return True
    if all(daily_cap_remaining(conn, profile, p, user_id=user_id) <= 0
           for p in reachable):
        return True
    return empty_pass_streak >= 2


def submitted_since(
    conn: sqlite3.Connection,
    since_iso: str,
    user_id: int = DEFAULT_USER_ID,
) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM applications "
        "WHERE method = 'auto' AND status = 'submitted' "
        "AND user_id = ? AND applied_at >= ?",
        (user_id, since_iso),
    ).fetchone()
    return int(row[0])


def _error_class(notes: str | None) -> str:
    if not notes:
        return "unknown"
    # notes look like "kleinanzeigen: neither success nor error indicator..."
    # or "wg-gesucht: selector_missing ..." — the leading platform token is
    # redundant here (we group by platform separately), so prefer the first
    # word AFTER the colon, falling back to the leading token.
    if ":" in notes:
        rest = notes.split(":", 1)[1].strip()
        if rest:
            return rest.split()[0] if rest.split() else rest[:40]
    return notes.split()[0] if notes.split() else "unknown"


def collect_failures_since(
    conn: sqlite3.Connection,
    since_iso: str,
    user_id: int = DEFAULT_USER_ID,
) -> list[dict]:
    """Return real filler failures since ``since_iso``, deduped per flat.

    Excludes ``auto_skipped:`` rows (no filler, expired listing) — those
    are expected, not bugs the user needs to fix. Multiple retries of the
    same flat collapse to one record so a flat at max_failures shows once.
    """
    rows = conn.execute(
        """
        SELECT platform, flat_id, listing_url, notes, applied_at
        FROM applications
        WHERE method = 'auto' AND status = 'failed'
          AND user_id = ? AND applied_at >= ?
          AND (notes IS NULL OR notes NOT LIKE 'auto_skipped:%')
        ORDER BY platform, applied_at DESC
        """,
        (user_id, since_iso),
    ).fetchall()
    seen: set[tuple[int, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (int(row["flat_id"]), _error_class(row["notes"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "platform": str(row["platform"]),
            "flat_id": int(row["flat_id"]),
            "url": str(row["listing_url"] or ""),
            "error_class": key[1],
            "notes": str(row["notes"] or ""),
        })
    return out


def print_failure_summary(
    console,
    conn: sqlite3.Connection,
    *,
    since_iso: str,
    submitted_count: int,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    from rich.table import Table

    failures = collect_failures_since(conn, since_iso, user_id=user_id)
    header = f"auto-apply: {submitted_count} submitted, {len(failures)} distinct failure(s)"
    console.rule(f"[bold]{header}[/bold]")
    if not failures:
        console.print("[green]no filler failures this run — nothing to fix.[/green]")
        return

    by_platform: dict[str, list[dict]] = {}
    for f in failures:
        by_platform.setdefault(f["platform"], []).append(f)

    for platform in sorted(by_platform):
        rows = by_platform[platform]
        screenshots_dir = FAILURE_SCREENSHOTS_DIR / platform
        table = Table(
            title=f"{platform} ({len(rows)})",
            show_lines=False,
            title_justify="left",
        )
        table.add_column("flat", style="bold")
        table.add_column("error")
        table.add_column("url", overflow="fold")
        for r in rows:
            table.add_row(str(r["flat_id"]), r["error_class"], r["url"])
        console.print(table)
        console.print(f"[dim]screenshots: {screenshots_dir}[/dim]\n")


def completeness_ok(profile: Profile, flat: dict) -> tuple[bool, str | None]:
    platform = str(flat["platform"])
    try:
        get_filler(platform)
    except LookupError:
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


def run_pipeline_apply(
    profile: Profile,
    console,
    *,
    dry_run: bool = False,
    drain: bool = False,
    user_id: int = DEFAULT_USER_ID,
) -> None:
    if is_paused():
        console.print("[yellow]auto-apply: PAUSED (~/.flatpilot/PAUSE present)[/yellow]")
        return

    init_db()
    conn = get_conn()
    phash = profile_hash(profile)

    expired_threshold = (datetime.now(UTC) - LISTING_EXPIRED_TTL).isoformat()
    rows = conn.execute(
        """
        SELECT m.id AS match_id,
               m.matched_saved_searches_json,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        WHERE m.decision = 'match'
          AND m.profile_version_hash = ?
          AND m.user_id = ?
          AND m.matched_saved_searches_json != '[]'
          AND NOT EXISTS (
            SELECT 1 FROM applications a
            WHERE a.flat_id = m.flat_id
              AND a.user_id = ?
              AND a.method = 'auto'
              AND a.status = 'submitted'
          )
          AND NOT EXISTS (
            SELECT 1 FROM applications a
            WHERE a.flat_id = m.flat_id
              AND a.user_id = ?
              AND a.method = 'auto'
              AND a.status = 'failed'
              AND a.notes LIKE 'auto_skipped: listing_expired%'
              AND a.applied_at >= ?
          )
          -- No TTL: a missing filler is permanent until a code change
          -- lands. Excluding for the lifetime of the auto_skipped row
          -- prevents unbounded growth on platforms we knowingly don't
          -- auto-apply on (FlatPilot-6pq scopes that decision). The
          -- first occurrence still writes a row for visibility.
          -- FlatPilot-289.
          AND NOT EXISTS (
            SELECT 1 FROM applications a
            WHERE a.flat_id = m.flat_id
              AND a.user_id = ?
              AND a.method = 'auto'
              AND a.status = 'failed'
              AND a.notes LIKE 'auto_skipped: filler not registered%'
          )
        ORDER BY m.decided_at ASC
        """,
        (phash, user_id, user_id, user_id, expired_threshold, user_id),
    ).fetchall()

    saved_search_by_name = {ss.name: ss for ss in profile.saved_searches}

    for row in rows:
        flat = dict(row)
        platform = str(flat["platform"])
        candidate_names = json.loads(flat["matched_saved_searches_json"])

        # Per-flat error isolation: an unhandled exception on one flat
        # (Playwright crash, anti-bot challenge, attribute error in a
        # filler edge case, …) must NOT abort the whole queue. The drain
        # use case especially depends on this: a single bad listing
        # 5 minutes into a 60-minute drain would otherwise abandon the
        # remaining ~25 flats. KeyboardInterrupt and SystemExit still
        # propagate so Ctrl-C / SIGTERM keep their semantics.
        try:
            _try_flat(
                conn=conn,
                console=console,
                profile=profile,
                flat=flat,
                platform=platform,
                candidate_names=candidate_names,
                saved_search_by_name=saved_search_by_name,
                dry_run=dry_run,
                drain=drain,
                user_id=user_id,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.exception(
                "auto-apply: unhandled error on flat %d (%s); skipping",
                int(flat["id"]),
                platform,
            )
            console.print(
                f"[red]auto-apply: unhandled error on flat {flat['id']} "
                f"({type(exc).__name__}: {exc}) — skipping[/red]"
            )


def _try_flat(
    *, conn: sqlite3.Connection, console, profile: Profile,
    flat: dict, platform: str, candidate_names: list[str],
    saved_search_by_name: dict[str, SavedSearch], dry_run: bool,
    drain: bool = False,
    user_id: int = DEFAULT_USER_ID,
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

        cap = daily_cap_remaining(conn, profile, platform, user_id=user_id)
        if cap <= 0:
            # Cap won't reset until UTC midnight, so even drain mode skips.
            console.print(
                f"[dim]auto-apply: cap reached on {platform}; skipping flat {flat_id}[/dim]"
            )
            return

        wait = cooldown_remaining_sec(conn, profile, platform, user_id=user_id)
        if wait > 0:
            if drain:
                console.print(
                    f"[dim]auto-apply: cooldown {wait:.0f}s on {platform}; "
                    f"sleeping before flat {flat_id}…[/dim]"
                )
                # Re-check pause after the long sleep — drain runs can span
                # 30+ minutes and the user must be able to halt mid-run.
                time.sleep(wait + 1)
                if is_paused():
                    console.print(
                        "[yellow]auto-apply: PAUSED (~/.flatpilot/PAUSE "
                        "appeared during drain) — stopping[/yellow]"
                    )
                    return
            else:
                console.print(
                    f"[dim]auto-apply: cooldown {wait:.0f}s on {platform}; "
                    f"skipping flat {flat_id}[/dim]"
                )
                return

        if (
            failures_for_flat(conn, flat_id, user_id=user_id)
            >= profile.auto_apply.max_failures_per_flat
        ):
            console.print(
                f"[dim]auto-apply: flat {flat_id} has reached max failures "
                f"({profile.auto_apply.max_failures_per_flat}); skipping[/dim]"
            )
            return

        ok, reason = completeness_ok(profile, flat)
        if not ok:
            attachments: list = []
            try:
                attachments = resolve_for_platform(profile, platform)
            except Exception as exc:
                logger.warning(
                    "auto-apply: attachment re-resolve failed for flat %d (%s): %s",
                    flat_id, platform, exc,
                )

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
                user_id=user_id,
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
            apply_to_flat(flat_id, method="auto", saved_search=name, user_id=user_id)
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
