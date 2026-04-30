"""Interactive setup wizard driven by ``flatpilot init``.

Walks the user through every required ``Profile`` field in short sections,
geocodes the home city against Nominatim and asks for confirmation of the
resolved coordinates, and writes the result to
``~/.flatpilot/profile.json``.

Defaults on each prompt come from (1) the user's existing profile if one
exists and parses, (2) the shipped ``profile.example.json`` otherwise.
Range validators on the schema (``rent_max >= rent_min``, ``rooms_max >=
rooms_min``, ``radius_km`` 0-500, ``wbs.status=='yes'`` needs size + income)
are enforced inline as the user types so pydantic never rejects a filled
form at the end.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from flatpilot.config import PROFILE_PATH, ensure_dirs
from flatpilot.matcher.distance import geocode
from flatpilot.profile import (
    EmailNotification,
    Notifications,
    Profile,
    TelegramNotification,
    WBS,
    load_profile,
    save_profile,
)


logger = logging.getLogger(__name__)


def run(console: Console | None = None) -> Path | None:
    """Drive the prompts, write the profile, and return the saved path.

    Returns ``None`` if the user aborts at the final save confirmation.
    """
    out = console or Console()
    out.rule("[bold]FlatPilot setup[/bold]")

    existing = _load_existing(out)
    defaults = existing if existing is not None else _load_example_safe(out)

    out.rule("Location")
    city = Prompt.ask("City (Germany)", default=defaults.city)
    home_lat, home_lng = _resolve_home(city, existing, out)
    radius_km = _prompt_int(
        out,
        "Search radius (km)",
        default=defaults.radius_km,
        min_value=0,
        max_value=500,
    )
    district_default = ", ".join(defaults.district_allowlist)
    districts_raw = Prompt.ask(
        "District allowlist (comma-separated; empty = any)",
        default=district_default,
    )
    district_allowlist = [d.strip() for d in districts_raw.split(",") if d.strip()]

    out.rule("Rent (warm, €/month)")
    rent_min_warm = _prompt_int(out, "Minimum", default=defaults.rent_min_warm, min_value=0)
    rent_max_warm = _prompt_int(
        out,
        "Maximum",
        default=max(defaults.rent_max_warm, rent_min_warm),
        min_value=rent_min_warm,
    )

    out.rule("Rooms")
    rooms_min = _prompt_int(out, "Minimum", default=defaults.rooms_min, min_value=1)
    rooms_max = _prompt_int(
        out,
        "Maximum",
        default=max(defaults.rooms_max, rooms_min),
        min_value=rooms_min,
    )

    out.rule("Household")
    household_size = _prompt_int(
        out, "Household size", default=defaults.household_size, min_value=1
    )
    kids = _prompt_int(out, "Kids (under 18)", default=defaults.kids, min_value=0)
    pets_default = ", ".join(defaults.pets)
    pets_raw = Prompt.ask(
        "Pets (comma-separated; empty = none)", default=pets_default
    )
    pets = [p.strip() for p in pets_raw.split(",") if p.strip()]

    out.rule("Move-in")
    move_in_date = _prompt_date(
        out, "Earliest move-in date (YYYY-MM-DD)", default=defaults.move_in_date
    )
    min_contract_default = (
        str(defaults.min_contract_months) if defaults.min_contract_months else ""
    )
    min_contract_months = _prompt_optional_int(
        out,
        "Minimum contract length in months (empty = no limit)",
        default=min_contract_default,
        min_value=0,
    )

    out.rule("Employment & income")
    status = Prompt.ask(
        "Employment status",
        choices=["student", "employed", "self_employed", "other"],
        default=defaults.status,
    )
    net_income_eur = _prompt_int(
        out, "Net monthly income (€)", default=defaults.net_income_eur, min_value=0
    )

    out.rule("Preferences")
    smoker = Confirm.ask("Smoker?", default=defaults.smoker)
    furnished_pref = Prompt.ask(
        "Furnished preference",
        choices=["any", "furnished", "unfurnished"],
        default=defaults.furnished_pref,
    )

    out.rule("WBS (Wohnberechtigungsschein)")
    wbs = _collect_wbs(out, defaults.wbs)

    out.rule("Notifications")
    notifications = _collect_notifications(out, defaults.notifications)

    out.rule("Review")
    payload: dict[str, Any] = {
        "city": city,
        "radius_km": radius_km,
        "district_allowlist": district_allowlist,
        "home_lat": home_lat,
        "home_lng": home_lng,
        "rent_min_warm": rent_min_warm,
        "rent_max_warm": rent_max_warm,
        "rooms_min": rooms_min,
        "rooms_max": rooms_max,
        "household_size": household_size,
        "kids": kids,
        "pets": pets,
        "status": status,
        "net_income_eur": net_income_eur,
        "move_in_date": move_in_date,
        "smoker": smoker,
        "furnished_pref": furnished_pref,
        "min_contract_months": min_contract_months,
        "wbs": wbs,
        "notifications": notifications,
    }
    try:
        profile = Profile(**payload)
    except ValidationError as exc:
        out.print("[red]Profile failed validation:[/red]")
        out.print(str(exc))
        raise

    out.print(
        f"[bold]{profile.city}[/bold] · {profile.radius_km} km · "
        f"€{profile.rent_min_warm}–{profile.rent_max_warm} warm · "
        f"{profile.rooms_min}–{profile.rooms_max} rooms · "
        f"WBS={profile.wbs.status}"
    )
    if not Confirm.ask("Save profile?", default=True):
        out.print("[yellow]Aborted; existing profile (if any) untouched.[/yellow]")
        return None

    ensure_dirs()
    save_profile(profile)
    out.print(f"[green]Wrote {PROFILE_PATH}[/green]")
    return PROFILE_PATH


def _load_existing(out: Console) -> Profile | None:
    try:
        return load_profile()
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        out.print(
            f"[yellow]Existing profile at {PROFILE_PATH} is unreadable "
            f"({exc.__class__.__name__}); starting fresh.[/yellow]"
        )
        return None


def _load_example_safe(out: Console) -> Profile:
    try:
        return Profile.load_example()
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        logger.warning("profile.example.json unreadable: %s", exc)
        out.print(
            "[yellow]Shipped example profile unreadable; "
            "prompts will have no defaults.[/yellow]"
        )
        return _fallback_profile()


def _fallback_profile() -> Profile:
    return Profile(
        city="Berlin",
        radius_km=10,
        rent_min_warm=800,
        rent_max_warm=1500,
        rooms_min=1,
        rooms_max=3,
        household_size=1,
        kids=0,
        status="employed",
        net_income_eur=2500,
        move_in_date=date.today(),
    )


def _resolve_home(
    city: str, existing: Profile | None, out: Console
) -> tuple[float | None, float | None]:
    if existing and existing.city == city and existing.home_lat and existing.home_lng:
        if Confirm.ask(
            f"Keep saved coordinates ({existing.home_lat:.4f}, {existing.home_lng:.4f})?",
            default=True,
        ):
            return existing.home_lat, existing.home_lng

    out.print(f"Looking up [bold]{city}, Germany[/bold] via Nominatim…")
    try:
        coords = geocode(f"{city}, Germany")
    except Exception as exc:  # network, JSON, anything
        out.print(f"[yellow]Geocoding failed ({exc}); home coordinates left unset.[/yellow]")
        return None, None

    if coords is None:
        out.print(
            "[yellow]Could not resolve coordinates; "
            "matcher will fall back to Nominatim at match time.[/yellow]"
        )
        return None, None

    lat, lng = coords
    out.print(f"Resolved: [bold]{lat:.4f}, {lng:.4f}[/bold]")
    if Confirm.ask("Use these coordinates for the radius filter?", default=True):
        return lat, lng
    return None, None


def _collect_wbs(out: Console, current: WBS) -> WBS:
    has_wbs = Confirm.ask(
        "Do you have a WBS (Wohnberechtigungsschein)?",
        default=current.status == "yes",
    )
    if not has_wbs:
        return WBS(status="none")

    size_default = str(current.size_category) if current.size_category else "1"
    size_str = Prompt.ask(
        "WBS size category (1=single · 5=5+ person household)",
        choices=["1", "2", "3", "4", "5"],
        default=size_default,
    )
    income_default = str(current.income_category) if current.income_category else "100"
    income_str = Prompt.ask(
        "WBS income category (Berlin multipliers of the baseline limit)",
        choices=["100", "140", "160", "180"],
        default=income_default,
    )
    return WBS(
        status="yes",
        size_category=int(size_str),
        income_category=int(income_str),  # type: ignore[arg-type]  # choices enforces valid values
    )


def _collect_notifications(out: Console, current: Notifications) -> Notifications:
    tg_enabled = Confirm.ask("Enable Telegram?", default=current.telegram.enabled)
    if tg_enabled:
        bot_token_env = Prompt.ask(
            "Env var holding the bot token", default=current.telegram.bot_token_env
        )
        chat_id = Prompt.ask("Telegram chat ID", default=current.telegram.chat_id)
    else:
        bot_token_env = current.telegram.bot_token_env
        chat_id = current.telegram.chat_id
    telegram = TelegramNotification(
        enabled=tg_enabled, bot_token_env=bot_token_env, chat_id=chat_id
    )

    email_enabled = Confirm.ask("Enable email?", default=current.email.enabled)
    if email_enabled:
        smtp_env = Prompt.ask(
            "SMTP env-var prefix (looks up <prefix>_HOST, _PORT, _USER, _PASSWORD, _FROM)",
            default=current.email.smtp_env,
        )
    else:
        smtp_env = current.email.smtp_env
    email = EmailNotification(enabled=email_enabled, smtp_env=smtp_env)

    return Notifications(telegram=telegram, email=email)


def _prompt_int(
    out: Console,
    prompt: str,
    *,
    default: int,
    min_value: int = 0,
    max_value: int | None = None,
) -> int:
    while True:
        value = IntPrompt.ask(prompt, default=default)
        if value < min_value:
            out.print(f"[red]Must be at least {min_value}.[/red]")
            continue
        if max_value is not None and value > max_value:
            out.print(f"[red]Must be at most {max_value}.[/red]")
            continue
        return value


def _prompt_optional_int(
    out: Console, prompt: str, *, default: str, min_value: int = 0
) -> int | None:
    while True:
        raw = Prompt.ask(prompt, default=default)
        if not raw.strip():
            return None
        try:
            value = int(raw)
        except ValueError:
            out.print("[red]Enter a whole number, or leave blank.[/red]")
            continue
        if value < min_value:
            out.print(f"[red]Must be at least {min_value}.[/red]")
            continue
        return value


def _prompt_date(out: Console, prompt: str, *, default: date) -> date:
    default_str = default.isoformat()
    while True:
        raw = Prompt.ask(prompt, default=default_str)
        try:
            return date.fromisoformat(raw)
        except ValueError:
            out.print("[red]Enter a date in YYYY-MM-DD format.[/red]")
