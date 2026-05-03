"""Scrape → match → notify orchestration.

Called by ``flatpilot run`` (full pipeline) and ``flatpilot scrape``
(scrape-only). Separated from the CLI module so the orchestration logic
has no dependency on typer or rich presentation concerns.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from flatpilot.profile import Profile


def run_pipeline_once(
    profile: Profile,
    console,
    *,
    skip_apply: bool = False,
    dry_run_apply: bool = False,
) -> int:
    """Run one scrape → match → apply → notify pass. Return number of stage failures."""
    failures = 0

    console.rule("scrape")
    try:
        run_pipeline_scrape(profile, console)
    except Exception as exc:
        console.print(f"[red]scrape failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    console.rule("match")
    try:
        run_pipeline_match(console)
    except Exception as exc:
        console.print(f"[red]match failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    if not skip_apply:
        console.rule("apply")
        try:
            run_pipeline_apply(profile, console, dry_run=dry_run_apply)
        except Exception as exc:
            console.print(f"[red]apply failed: {exc.__class__.__name__}: {exc}[/red]")
            failures += 1

    console.rule("notify")
    try:
        run_pipeline_notify(profile, console)
    except Exception as exc:
        console.print(f"[red]notify failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    return failures


def _ensure_scrapers_registered() -> None:
    import flatpilot.scrapers.immoscout24_rss  # noqa: F401 — triggers @register
    import flatpilot.scrapers.inberlinwohnen  # noqa: F401 — triggers @register
    import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
    import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register


def run_pipeline_scrape(profile: Profile, console) -> None:
    from flatpilot.scrapers import all_scrapers

    _ensure_scrapers_registered()
    scrapers = [cls() for cls in all_scrapers()]
    if not scrapers:
        console.print("[yellow]no scrapers registered[/yellow]")
        return
    run_scrape_pass(scrapers, profile, console)


def run_pipeline_match(console) -> None:
    from flatpilot.matcher.runner import run_match

    summary = run_match()
    console.print(
        f"[green]{summary['match']} matched[/green], "
        f"[yellow]{summary['reject']} rejected[/yellow] "
        f"(processed {summary['processed']} flats, profile {summary['profile_hash']})"
    )


def run_pipeline_apply(profile: Profile, console, *, dry_run: bool = False) -> None:
    from flatpilot.auto_apply import run_pipeline_apply as _impl
    _impl(profile, console, dry_run=dry_run)


def run_pipeline_notify(profile: Profile, console) -> None:
    from flatpilot.notifications.dispatcher import dispatch_pending, enabled_channels

    channels = enabled_channels(profile)
    if not channels:
        console.print("[dim]no channels enabled — skipping[/dim]")
        return
    summary = dispatch_pending(profile)
    if summary["processed"] == 0:
        console.print("[dim]nothing pending[/dim]")
        return
    parts = []
    for ch in channels:
        sent = summary["sent"].get(ch, 0)
        failed = summary["failed"].get(ch, 0)
        parts.append(
            f"{ch}: [green]{sent} sent[/green]"
            + (f", [red]{failed} failed[/red]" if failed else "")
        )
    console.print(" · ".join(parts))


def run_scrape_pass(scrapers: list, profile: Profile, console) -> None:
    from flatpilot.database import get_conn
    from flatpilot.scrapers import backoff, supports_city
    from flatpilot.scrapers.session import ChallengeDetectedError, RateLimitedError

    conn = get_conn()
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    for scraper in scrapers:
        plat = scraper.platform
        if not supports_city(type(scraper), profile.city):
            console.print(
                f"[dim]{plat}: skipping — city {profile.city!r} not supported[/dim]"
            )
            continue
        skip, remaining = backoff.should_skip(plat, now=now_dt)
        if skip:
            console.print(
                f"[dim]{plat}: cooling off for {remaining:.0f}s more — skipping[/dim]"
            )
            continue
        known_external_ids = frozenset(
            row[0]
            for row in conn.execute(
                "SELECT external_id FROM flats WHERE platform = ?",
                (plat,),
            )
        )
        try:
            flats = list(
                scraper.fetch_new(profile, known_external_ids=known_external_ids)
            )
        except RateLimitedError as exc:
            backoff.on_failure(plat, "rate_limit", now=datetime.now(UTC))
            console.print(f"[yellow]{plat}: {exc} — skipping this pass[/yellow]")
            continue
        except ChallengeDetectedError as exc:
            backoff.on_failure(plat, "challenge", now=datetime.now(UTC))
            console.print(
                f"[red]{plat}: anti-bot challenge detected ({exc}) — "
                f"extended cool-off[/red]"
            )
            continue
        except Exception as exc:
            console.print(
                f"[red]{plat}: fetch failed ({exc.__class__.__name__}: {exc})[/red]"
            )
            continue

        new_count = 0
        for flat in flats:
            if insert_flat(conn, flat, plat, now):
                new_count += 1
        console.print(
            f"{plat}: [bold]{len(flats)}[/bold] listings, "
            f"[green]{new_count}[/green] new"
        )
        backoff.on_success(plat)


def insert_flat(conn: sqlite3.Connection, flat, platform: str, now: str) -> bool:
    """Insert a scraped flat row; assign dedup canonical_flat_id. Return True if new."""
    row = dict(flat)
    row["platform"] = platform
    row["scraped_at"] = now
    row["first_seen_at"] = now
    cols = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = (
        f"INSERT OR IGNORE INTO flats ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    cursor = conn.execute(sql, row)
    if cursor.rowcount == 0:
        return False
    from flatpilot.matcher.dedup import assign_canonical

    assign_canonical(conn, cursor.lastrowid)  # type: ignore[arg-type]  # non-None when rowcount > 0
    return True
