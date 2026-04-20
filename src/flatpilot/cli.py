"""Typer CLI entry point.

Each command is a placeholder until its implementation lands. The shape of
the CLI surface — `init`, `doctor`, `run`, `scrape`, `match`, `notify`,
`status`, `dashboard` — is fixed here so downstream work can wire real
behaviour in without renaming commands.
"""

from __future__ import annotations

import logging

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
    from flatpilot.log import setup_logging

    setup_logging(level=logging.DEBUG if verbose else logging.INFO)


def _placeholder(command: str) -> None:
    rprint(f"[yellow]{command}[/yellow] is not implemented yet.")


@app.command()
def init() -> None:
    """Run the interactive setup wizard."""
    _placeholder("init")


@app.command()
def doctor() -> None:
    """Check that the install is healthy."""
    from flatpilot.doctor import run as run_doctor

    raise typer.Exit(run_doctor())


@app.command()
def run(
    watch: bool = typer.Option(False, "--watch", help="Keep polling."),
    interval: int = typer.Option(120, "--interval", help="Seconds between passes."),
) -> None:
    """One scrape + match + notify pass (add --watch to loop)."""
    _placeholder("run")


@app.command()
def scrape() -> None:
    """Scrape all configured platforms and store raw listings."""
    _placeholder("scrape")


@app.command()
def match() -> None:
    """Apply the matcher to unmatched listings and write matches."""
    from rich.console import Console

    from flatpilot.matcher.runner import ProfileMissing, run_match

    console = Console()
    try:
        summary = run_match()
    except ProfileMissing as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"Processed [bold]{summary['processed']}[/bold] flats "
        f"(profile {summary['profile_hash']}): "
        f"[green]{summary['match']} match[/green], "
        f"[yellow]{summary['reject']} reject[/yellow]"
    )


@app.command()
def notify() -> None:
    """Deliver any unsent matches via Telegram / email."""
    _placeholder("notify")


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
    _placeholder("dashboard")


if __name__ == "__main__":
    app()
