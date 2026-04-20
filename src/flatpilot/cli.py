"""Typer CLI entry point.

Each command is a placeholder until its implementation lands. The shape of
the CLI surface — `init`, `doctor`, `run`, `scrape`, `match`, `notify`,
`status`, `dashboard` — is fixed here so downstream work can wire real
behaviour in without renaming commands.
"""

from __future__ import annotations

import typer
from rich import print as rprint


app = typer.Typer(
    name="flatpilot",
    help="Flat-hunting agent for the German rental market.",
    no_args_is_help=True,
    add_completion=False,
)


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
    _placeholder("match")


@app.command()
def notify() -> None:
    """Deliver any unsent matches via Telegram / email."""
    _placeholder("notify")


@app.command()
def status() -> None:
    """Show DB counts and last-run info."""
    _placeholder("status")


@app.command()
def dashboard() -> None:
    """Build the HTML dashboard of matches."""
    _placeholder("dashboard")


if __name__ == "__main__":
    app()
