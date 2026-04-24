"""Typer CLI entry point.

Each command is a placeholder until its implementation lands. The shape of
the CLI surface — `init`, `doctor`, `run`, `scrape`, `match`, `notify`,
`status`, `dashboard` — is fixed here so downstream work can wire real
behaviour in without renaming commands.
"""

from __future__ import annotations

import logging
from datetime import UTC

import typer
from rich import print as rprint

app = typer.Typer(
    name="flatpilot",
    help="Flat-hunting agent for the German rental market.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _bootstrap(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging."),
) -> None:
    from flatpilot.config import load_env
    from flatpilot.log import setup_logging

    setup_logging(level=logging.DEBUG if verbose else logging.INFO)
    # Load ~/.flatpilot/.env (or ./.env as fallback) so commands that look
    # up secrets via os.environ — notify, run, scrape — see credentials
    # that users put in the app dir. dotenv does not overwrite vars that
    # are already set, so Docker's compose-injected env still wins.
    load_env()


def _placeholder(command: str) -> None:
    rprint(f"[yellow]{command}[/yellow] is not implemented yet.")


@app.command()
def init() -> None:
    """Run the interactive setup wizard."""
    from rich.console import Console

    from flatpilot.wizard.init import run as run_wizard

    run_wizard(Console())


@app.command()
def doctor() -> None:
    """Check that the install is healthy."""
    from flatpilot.doctor import run as run_doctor

    raise typer.Exit(run_doctor())


@app.command()
def login(
    platform: str = typer.Argument(..., help="Platform to log in to, e.g. 'wg-gesucht'."),
) -> None:
    """Open a headed browser so you can log in to a platform by hand.

    Captures the resulting cookies to ~/.flatpilot/sessions/<platform>/ so
    every headless command after this (scrape, run, future apply) reuses
    them. Must run on the host, not in Docker — headed Playwright needs a
    visible display.
    """
    from rich.console import Console

    from flatpilot.login import (
        ContainerDetectedError,
        UnknownPlatformError,
        run_login,
    )

    console = Console()
    try:
        run_login(platform, console)
    except UnknownPlatformError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except ContainerDetectedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted before login finished.[/yellow]")
        raise typer.Exit(130) from None


@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Loop until SIGINT / SIGTERM."),
    interval: int = typer.Option(
        120, "--interval", help="Seconds between passes when --watch is set (default 120)."
    ),
) -> None:
    """One scrape + match + notify pass (add --watch to loop)."""
    import signal
    import time

    from rich.console import Console

    from flatpilot.database import init_db
    from flatpilot.profile import load_profile

    console = Console()

    profile = load_profile()
    if profile is None:
        console.print(
            "[red]No profile at ~/.flatpilot/profile.json — run `flatpilot init` first.[/red]"
        )
        raise typer.Exit(1)

    init_db()

    if not watch:
        failures = _run_pipeline_once(profile, console)
        if failures:
            raise typer.Exit(1)
        return

    stop = False

    def _handler(signum, _frame) -> None:
        nonlocal stop
        stop = True
        console.print(
            f"\n[yellow]received {signal.Signals(signum).name} — "
            f"finishing current pass, then exiting…[/yellow]"
        )

    prev_int = signal.signal(signal.SIGINT, _handler)
    prev_term = signal.signal(signal.SIGTERM, _handler)

    pass_num = 0
    total_failures = 0
    try:
        while not stop:
            pass_num += 1
            console.rule(f"[bold]pass {pass_num}[/bold]")
            try:
                total_failures += _run_pipeline_once(profile, console)
            except Exception as exc:
                console.print(f"[red]pass {pass_num} aborted: {exc}[/red]")
                total_failures += 1
            if stop:
                break
            console.print(
                f"[dim]sleeping {interval}s before next pass "
                f"(Ctrl-C / SIGTERM to stop)…[/dim]"
            )
            for _ in range(interval):
                if stop:
                    break
                time.sleep(1)
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)

    console.print(
        f"[bold]stopped[/bold] · {pass_num} pass(es) · "
        f"{total_failures} stage failure(s)"
    )
    if total_failures:
        raise typer.Exit(1)


def _run_pipeline_once(profile, console) -> int:
    """Run one scrape → match → notify pass. Return number of stage failures."""
    failures = 0

    console.rule("scrape")
    try:
        _run_pipeline_scrape(profile, console)
    except Exception as exc:
        console.print(f"[red]scrape failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    console.rule("match")
    try:
        _run_pipeline_match(console)
    except Exception as exc:
        console.print(f"[red]match failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    console.rule("notify")
    try:
        _run_pipeline_notify(profile, console)
    except Exception as exc:
        console.print(f"[red]notify failed: {exc.__class__.__name__}: {exc}[/red]")
        failures += 1

    return failures


def _run_pipeline_scrape(profile, console) -> None:
    import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
    import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register
    from flatpilot.scrapers import all_scrapers

    scrapers = [cls() for cls in all_scrapers()]
    if not scrapers:
        console.print("[yellow]no scrapers registered[/yellow]")
        return
    _run_scrape_pass(scrapers, profile, console)


def _run_pipeline_match(console) -> None:
    from flatpilot.matcher.runner import run_match

    summary = run_match()
    console.print(
        f"[green]{summary['match']} matched[/green], "
        f"[yellow]{summary['reject']} rejected[/yellow] "
        f"(processed {summary['processed']} flats, profile {summary['profile_hash']})"
    )


def _run_pipeline_notify(profile, console) -> None:
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


@app.command()
def scrape(
    platform: str | None = typer.Option(
        None, "--platform", "-p", help="Scrape only this platform (default: all registered)."
    ),
    watch: bool = typer.Option(False, "--watch", help="Loop until Ctrl-C."),
    interval: int = typer.Option(
        120, "--interval", help="Seconds between passes when --watch is set (default 120)."
    ),
) -> None:
    """Scrape configured platforms and insert new listings into the flats table."""
    import time

    from rich.console import Console

    import flatpilot.scrapers.kleinanzeigen  # noqa: F401 — triggers @register
    import flatpilot.scrapers.wg_gesucht  # noqa: F401 — triggers @register
    from flatpilot.database import init_db
    from flatpilot.profile import load_profile
    from flatpilot.scrapers import all_scrapers, get_scraper

    console = Console()

    profile = load_profile()
    if profile is None:
        console.print(
            "[red]No profile at ~/.flatpilot/profile.json — run `flatpilot init` first.[/red]"
        )
        raise typer.Exit(1)

    init_db()

    if platform:
        try:
            scrapers = [get_scraper(platform)()]
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    else:
        scrapers = [cls() for cls in all_scrapers()]

    if not scrapers:
        console.print("[yellow]No scrapers registered.[/yellow]")
        raise typer.Exit(1)

    try:
        while True:
            _run_scrape_pass(scrapers, profile, console)
            if not watch:
                break
            console.print(f"[dim]Sleeping {interval}s before next pass (Ctrl-C to stop)…[/dim]")
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


def _run_scrape_pass(scrapers: list, profile, console) -> None:
    from datetime import datetime

    from flatpilot.database import get_conn
    from flatpilot.scrapers.session import RateLimitedError

    conn = get_conn()
    now = datetime.now(UTC).isoformat()
    for scraper in scrapers:
        plat = scraper.platform
        try:
            flats = list(scraper.fetch_new(profile))
        except RateLimitedError as exc:
            console.print(f"[yellow]{plat}: {exc} — skipping this pass[/yellow]")
            continue
        except Exception as exc:
            console.print(
                f"[red]{plat}: fetch failed ({exc.__class__.__name__}: {exc})[/red]"
            )
            continue

        new_count = 0
        for flat in flats:
            if _insert_flat(conn, flat, plat, now):
                new_count += 1
        console.print(
            f"{plat}: [bold]{len(flats)}[/bold] listings, "
            f"[green]{new_count}[/green] new"
        )


def _insert_flat(conn, flat, platform: str, now: str) -> bool:
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

    assign_canonical(conn, cursor.lastrowid)
    return True


@app.command()
def dedup(
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Recompute canonical_flat_id for every flat."
    ),
) -> None:
    """Populate flats.canonical_flat_id across the database."""
    from rich.console import Console

    from flatpilot.database import get_conn, init_db
    from flatpilot.matcher.dedup import rebuild as do_rebuild

    console = Console()
    if not rebuild:
        console.print("[yellow]Nothing to do. Pass --rebuild to re-cluster.[/yellow]")
        raise typer.Exit(code=0)

    init_db()
    conn = get_conn()
    total, clusters = do_rebuild(conn)
    console.print(f"rebuilt [bold]{total}[/bold] flats → [bold]{clusters}[/bold] clusters")


@app.command()
def match() -> None:
    """Apply the matcher to unmatched listings and write matches."""
    from rich.console import Console

    from flatpilot.matcher.runner import ProfileMissingError, run_match

    console = Console()
    try:
        summary = run_match()
    except ProfileMissingError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"Processed [bold]{summary['processed']}[/bold] flats "
        f"(profile {summary['profile_hash']}): "
        f"[green]{summary['match']} match[/green], "
        f"[yellow]{summary['reject']} reject[/yellow]"
    )


@app.command()
def notify(
    test: bool = typer.Option(
        False, "--test", help="Send a synthetic flat to every enabled channel."
    ),
) -> None:
    """Deliver any unsent matches via Telegram / email."""
    from rich.console import Console

    from flatpilot.notifications.dispatcher import (
        dispatch_pending,
        enabled_channels,
        send_test,
    )
    from flatpilot.profile import load_profile

    console = Console()
    profile = load_profile()
    if profile is None:
        console.print(
            "[red]No profile at ~/.flatpilot/profile.json — run `flatpilot init`.[/red]"
        )
        raise typer.Exit(1)

    channels = enabled_channels(profile)
    if not channels:
        console.print("[yellow]No channels enabled in profile — nothing to send.[/yellow]")
        return

    if test:
        results = send_test(profile)
        any_failed = False
        for channel, status in results.items():
            if status == "sent":
                console.print(f"[green]{channel}[/green]: sent")
            else:
                any_failed = True
                console.print(f"[red]{channel}[/red]: {status}")
        if any_failed:
            raise typer.Exit(1)
        return

    summary = dispatch_pending(profile)
    console.print(
        f"Processed [bold]{summary['processed']}[/bold] matched flats"
    )
    for channel in channels:
        sent = summary["sent"].get(channel, 0)
        failed = summary["failed"].get(channel, 0)
        parts = []
        if sent:
            parts.append(f"[green]{sent} sent[/green]")
        if failed:
            parts.append(f"[red]{failed} failed[/red]")
        if not parts:
            parts.append("[dim]nothing pending[/dim]")
        console.print(f"  {channel}: {', '.join(parts)}")


@app.command()
def status() -> None:
    """Show DB counts and last-run info."""
    from rich.console import Console
    from rich.table import Table

    from flatpilot.database import init_db
    from flatpilot.stats import get_stats

    init_db()
    s = get_stats()
    console = Console()

    summary = Table(title="FlatPilot status")
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Total flats", str(s["total_flats"]))
    summary.add_row("New last 24h", str(s["new_last_24h"]))
    summary.add_row("Matched", str(s["matched"]))
    summary.add_row("Notified", str(s["notified"]))
    summary.add_row("Last scrape", s["last_scrape_at"] or "—")
    console.print(summary)

    if s["notifications_by_channel"]:
        ch = Table(title="Notifications by channel")
        ch.add_column("Channel")
        ch.add_column("Count", justify="right")
        for channel, count in sorted(s["notifications_by_channel"].items()):
            ch.add_row(channel, str(count))
        console.print(ch)

    if s["rejected_by_reason"]:
        rj = Table(title="Rejections by reason")
        rj.add_column("Reason")
        rj.add_column("Count", justify="right")
        for reason, count in sorted(s["rejected_by_reason"].items(), key=lambda x: -x[1]):
            rj.add_row(reason, str(count))
        console.print(rj)


@app.command()
def dashboard() -> None:
    """Build the HTML dashboard of matches."""
    import webbrowser

    from rich.console import Console

    from flatpilot.view import generate

    console = Console()
    path = generate()
    console.print(f"Dashboard written to [bold]{path}[/bold]")
    webbrowser.open(path.as_uri())


if __name__ == "__main__":
    app()
