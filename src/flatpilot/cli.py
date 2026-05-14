"""Typer CLI entry point.

Each command is a placeholder until its implementation lands. The shape of
the CLI surface — `init`, `doctor`, `run`, `scrape`, `match`, `notify`,
`status`, `dashboard` — is fixed here so downstream work can wire real
behaviour in without renaming commands.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich import print as rprint

from flatpilot.apply import (
    APPLY_LOCK_HELD_EXIT,
    AlreadyAppliedError,
    ApplyLockHeldError,
    ApplyOutcome,
    apply_to_flat,
)
from flatpilot.attachments import AttachmentError
from flatpilot.compose import TemplateError
from flatpilot.errors import ProfileMissingError
from flatpilot.fillers.base import FillError
from flatpilot.pipeline import _ensure_scrapers_registered, run_pipeline_once, run_scrape_pass

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
    skip_apply: bool = typer.Option(
        False,
        "--skip-apply",
        help="Run scrape/match/notify only. No auto-apply stage.",
    ),
    dry_run_apply: bool = typer.Option(
        False,
        "--dry-run-apply",
        help="Log what auto-apply would do without calling fillers.",
    ),
    drain: bool = typer.Option(
        False,
        "--drain",
        help="Keep submitting until every reachable platform's daily cap is "
        "met (or 2 consecutive passes add zero submits). Loops scrape → "
        "match → apply, sleeping cooldowns inside each pass and --interval "
        "between passes. On exit (cap reached, Ctrl-C, SIGTERM) prints a "
        "grouped, deduped summary of per-flat filler failures so you can "
        "fix them. Combine with --watch to ignore the cap-reached exit and "
        "loop forever.",
    ),
) -> None:
    """One scrape + match + apply + notify pass (add --watch to loop)."""
    import signal
    import time
    from datetime import UTC, datetime

    from rich.console import Console

    from flatpilot.auto_apply import (
        drain_complete,
        print_failure_summary,
        submitted_since,
    )
    from flatpilot.database import get_conn, init_db
    from flatpilot.profile import load_profile

    console = Console()

    profile = load_profile()
    if profile is None:
        console.print(
            "[red]No profile at ~/.flatpilot/profile.json — run `flatpilot init` first.[/red]"
        )
        raise typer.Exit(1)

    init_db()

    if not watch and not drain:
        failures = run_pipeline_once(
            profile, console,
            skip_apply=skip_apply,
            dry_run_apply=dry_run_apply,
            drain_apply=False,
        )
        if failures:
            raise typer.Exit(1)
        return

    if not watch and drain:
        # Drain loop: scrape → match → apply, repeat until every reachable
        # platform hits cap OR two passes in a row add zero submits. SIGINT
        # / SIGTERM raise into the loop so cooldown sleeps inside _try_flat
        # abort promptly; the failure summary always prints from `finally`.
        def _raise_signal(signum, _frame) -> None:
            raise KeyboardInterrupt(f"received {signal.Signals(signum).name}")

        prev_int = signal.signal(signal.SIGINT, _raise_signal)
        prev_term = signal.signal(signal.SIGTERM, _raise_signal)

        run_started_at = datetime.now(UTC).isoformat()
        pass_num = 0
        empty_streak = 0
        total_failures = 0
        interrupted = False
        try:
            while True:
                pass_num += 1
                console.rule(f"[bold]pass {pass_num}[/bold]")
                pass_start = datetime.now(UTC).isoformat()
                try:
                    total_failures += run_pipeline_once(
                        profile, console,
                        skip_apply=skip_apply,
                        dry_run_apply=dry_run_apply,
                        drain_apply=True,
                    )
                except Exception as exc:
                    console.print(f"[red]pass {pass_num} aborted: {exc}[/red]")
                    total_failures += 1

                submits = submitted_since(get_conn(), pass_start)
                empty_streak = 0 if submits > 0 else empty_streak + 1

                if drain_complete(
                    get_conn(), profile, empty_pass_streak=empty_streak
                ):
                    console.print(
                        f"[bold]drain complete[/bold] after pass {pass_num} "
                        f"(empty_streak={empty_streak})"
                    )
                    break

                console.print(
                    f"[dim]sleeping {interval}s before next pass "
                    f"(Ctrl-C / SIGTERM to stop)…[/dim]"
                )
                time.sleep(interval)
        except KeyboardInterrupt as exc:
            interrupted = True
            console.print(f"\n[yellow]{exc} — stopping drain loop[/yellow]")
        finally:
            signal.signal(signal.SIGINT, prev_int)
            signal.signal(signal.SIGTERM, prev_term)
            total_submits = submitted_since(get_conn(), run_started_at)
            print_failure_summary(
                console, get_conn(),
                since_iso=run_started_at,
                submitted_count=total_submits,
            )

        if interrupted:
            raise typer.Exit(130)
        if total_failures:
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

    run_started_at = datetime.now(UTC).isoformat()
    pass_num = 0
    total_failures = 0
    try:
        while not stop:
            pass_num += 1
            console.rule(f"[bold]pass {pass_num}[/bold]")
            try:
                total_failures += run_pipeline_once(
                    profile, console,
                    skip_apply=skip_apply,
                    dry_run_apply=dry_run_apply,
                    drain_apply=drain,
                )
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
        total_submits = submitted_since(get_conn(), run_started_at)
        print_failure_summary(
            console, get_conn(),
            since_iso=run_started_at,
            submitted_count=total_submits,
        )

    console.print(
        f"[bold]stopped[/bold] · {pass_num} pass(es) · "
        f"{total_failures} stage failure(s)"
    )
    if total_failures:
        raise typer.Exit(1)


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

    _ensure_scrapers_registered()
    from flatpilot.database import init_db
    from flatpilot.profile import load_profile
    from flatpilot.scrapers import all_scrapers, get_scraper, supports_city

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
            scraper_cls = get_scraper(platform)
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        if not supports_city(scraper_cls, profile.city):
            # supports_city only returns False when supported_cities is a
            # non-None frozenset, so the None branch is unreachable here;
            # an empty frozenset is rendered as "no cities".
            supported = scraper_cls.supported_cities
            cities_label = ", ".join(sorted(supported)) or "no cities"  # type: ignore[arg-type]
            console.print(
                f"[red]{platform}: city {profile.city!r} not supported "
                f"(supports: {cities_label})[/red]"
            )
            raise typer.Exit(1)
        scrapers = [scraper_cls()]
    else:
        scrapers = [cls() for cls in all_scrapers()]

    if not scrapers:
        console.print("[yellow]No scrapers registered.[/yellow]")
        raise typer.Exit(1)

    try:
        while True:
            run_scrape_pass(scrapers, profile, console)
            if not watch:
                break
            console.print(f"[dim]Sleeping {interval}s before next pass (Ctrl-C to stop)…[/dim]")
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


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

    from flatpilot.errors import ProfileMissingError
    from flatpilot.matcher.runner import run_match

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
    """Show DB counts, last-run info, and the auto-apply runtime panel."""
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

    _print_auto_apply_panel(console)


def _print_auto_apply_panel(console) -> None:
    """Render the Auto-apply runtime panel (FlatPilot-iwu).

    Pulls live values from the same helpers the pipeline uses
    (is_paused, daily_cap_remaining, cooldown_remaining_sec,
    flats_over_max_failures) so the user sees exactly what auto-apply
    will do on the next ``flatpilot run``.
    """
    from rich.table import Table

    # Filler imports populate the registry that all_fillers() walks below.
    import flatpilot.fillers.kleinanzeigen  # noqa: F401
    import flatpilot.fillers.wg_gesucht  # noqa: F401
    from flatpilot.auto_apply import (
        PAUSE_PATH,
        cooldown_remaining_sec,
        daily_cap_remaining,
        flats_over_max_failures,
        is_paused,
    )
    from flatpilot.database import get_conn
    from flatpilot.fillers import all_fillers
    from flatpilot.profile import load_profile

    panel = Table(title="Auto-apply")
    panel.add_column("Metric")
    panel.add_column("Value")

    if is_paused():
        panel.add_row("State", f"[yellow]PAUSED[/yellow] · {PAUSE_PATH}")
    else:
        panel.add_row("State", "[green]ACTIVE[/green]")

    try:
        profile = load_profile()
    except (ValueError, OSError):
        profile = None

    if profile is None:
        panel.add_row("Profile", "[yellow]no profile — run `flatpilot init`[/yellow]")
        console.print(panel)
        return

    conn = get_conn()
    apply_capable = {f.platform for f in all_fillers()}
    platforms = sorted(set(profile.auto_apply.daily_cap_per_platform) | apply_capable)
    for platform in platforms:
        cap = profile.auto_apply.daily_cap_per_platform.get(platform, 0)
        if platform not in apply_capable:
            panel.add_row(platform, "[dim]— (no filler registered)[/dim]")
            continue
        if cap <= 0:
            panel.add_row(platform, "[dim]disabled (cap=0)[/dim]")
            continue
        used = cap - daily_cap_remaining(conn, profile, platform)
        wait = cooldown_remaining_sec(conn, profile, platform)
        cooldown_str = "ready" if wait <= 0 else f"cooldown {wait:.0f}s"
        panel.add_row(platform, f"{used} / {cap} today · {cooldown_str}")

    panel.add_row(
        "Flats over max failures",
        str(flats_over_max_failures(conn, profile)),
    )
    console.print(panel)


@app.command()
def apply(
    flat_id: int = typer.Argument(..., help="Database ID of the flat to apply to."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fill the contact form but DO NOT click submit. Prints a preview "
        "and writes no applications row.",
    ),
    screenshot_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--screenshot-dir",
        help="If set, save a PNG of the filled form to this directory.",
    ),
) -> None:
    """Contact the landlord for a single flat via its platform's filler.

    On success a row is written to the ``applications`` table with
    ``status='submitted'``. On filler error a row is written with
    ``status='failed'`` and the error in ``notes``. ``--dry-run`` skips
    the submit click and writes no row.
    """
    from rich.console import Console

    console = Console()
    try:
        outcome: ApplyOutcome = apply_to_flat(
            flat_id, dry_run=dry_run, screenshot_dir=screenshot_dir
        )
    except ProfileMissingError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except ApplyLockHeldError as exc:
        # Lock-contention case (acquire_apply_lock). Exit
        # APPLY_LOCK_HELD_EXIT (4) so server._handle_apply can translate
        # to HTTP 409 ("apply already in progress, retry later"). MUST
        # come before the parent except — Python matches first compatible
        # clause. FlatPilot-wsp.
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(APPLY_LOCK_HELD_EXIT) from exc
    except AlreadyAppliedError as exc:
        # Post-submit duplicate-row case (apply_to_flat). Application
        # already completed earlier; do NOT retry. Exit 1 → HTTP 500.
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc
    except (FillError, AttachmentError, TemplateError) as exc:
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(1) from exc

    report = outcome.fill_report
    if outcome.status == "dry_run":
        console.print("[yellow]dry-run preview[/yellow] (no applications row written)")
        if report is not None:
            console.print(f"  contact URL: {report.contact_url}")
            for field, value in report.fields_filled.items():
                preview = value if len(value) <= 200 else value[:197] + "..."
                console.print(f"  {field}: {preview}")
            if report.screenshot_path is not None:
                console.print(f"  screenshot: {report.screenshot_path}")
        return

    # The remaining valid status is "submitted"; ApplyStatus narrows the type.
    console.print(
        f"[green]submitted[/green] · application_id={outcome.application_id}"
    )


@app.command()
def pause() -> None:
    """Halt auto-apply by creating ~/.flatpilot/PAUSE."""
    from flatpilot.auto_apply import PAUSE_PATH
    from flatpilot.config import ensure_dirs

    ensure_dirs()
    PAUSE_PATH.touch()
    rprint(f"[yellow]auto-apply paused[/yellow] · {PAUSE_PATH}")


@app.command()
def resume() -> None:
    """Resume auto-apply by removing ~/.flatpilot/PAUSE."""
    from flatpilot.auto_apply import PAUSE_PATH

    PAUSE_PATH.unlink(missing_ok=True)
    rprint("[green]auto-apply resumed[/green]")


@app.command(name="reclassify-submits")
def reclassify_submits(
    apply_changes: bool = typer.Option(
        False,
        "--apply",
        help="Actually update the rows. Default is dry-run (preview only).",
    ),
) -> None:
    """Re-classify silent-success false-fails as `status='submitted'`.

    See FlatPilot-8kt: WG-Gesucht's in-place success rendering caused
    real submissions to be recorded with `status='failed'`. This command
    finds those rows (failed wg-gesucht submits with a follow-up
    `auto_skipped: listing_expired` row on the same flat — proof that
    the listing's contact CTA was hidden, which only happens after
    successful contact) and updates them to `status='submitted'`.
    """
    from rich.console import Console
    from rich.table import Table

    from flatpilot.database import get_conn, init_db
    from flatpilot.reclassify import apply_reclassification, find_candidates

    init_db()
    conn = get_conn()
    candidates = find_candidates(conn)

    console = Console()
    if not candidates:
        console.print("[green]No silent-success false-fail rows found — nothing to do.[/green]")
        return

    table = Table(title=f"reclassify-submits — {len(candidates)} candidate(s)")
    table.add_column("app id", justify="right")
    table.add_column("flat id", justify="right")
    table.add_column("platform")
    table.add_column("applied_at")
    table.add_column("title")
    for c in candidates:
        table.add_row(
            str(c.application_id),
            str(c.flat_id),
            c.platform,
            c.applied_at,
            c.title[:60],
        )
    console.print(table)

    if not apply_changes:
        console.print(
            "[yellow]Dry run — no rows changed.[/yellow] "
            "Re-run with [bold]--apply[/bold] to update."
        )
        return

    n = apply_reclassification(conn, candidates)
    console.print(f"[green]Reclassified {n} row(s) to status='submitted'.[/green]")


@app.command()
def dashboard(
    port: int = typer.Option(
        8765,
        "--port",
        help="Localhost port to bind. Falls back to an ephemeral port if busy.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't open the dashboard in a browser tab on startup.",
    ),
) -> None:
    """Serve the HTML dashboard over localhost until interrupted (Ctrl-C)."""
    import webbrowser

    from rich.console import Console

    from flatpilot.server import serve

    console = Console()
    server, bound_port = serve(host="127.0.0.1", port=port)
    url = f"http://127.0.0.1:{bound_port}/"
    console.print(f"FlatPilot dashboard serving at [bold]{url}[/bold]  (Ctrl-C to stop)")
    if not no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]stopping dashboard server[/yellow]")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    app()
