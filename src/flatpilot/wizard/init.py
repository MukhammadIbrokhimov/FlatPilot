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
import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from flatpilot.config import PROFILE_PATH, ensure_dirs
from flatpilot.matcher.distance import geocode
from flatpilot.profile import (
    WBS,
    EmailNotification,
    EmailNotificationOverride,
    Notifications,
    Profile,
    SavedSearch,
    SavedSearchNotifications,
    TelegramNotification,
    TelegramNotificationOverride,
    load_profile,
    save_profile,
)

logger = logging.getLogger(__name__)

_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_PLATFORM_VALUES = {"wg-gesucht", "kleinanzeigen", "inberlinwohnen"}


def _maybe_add_auto_apply(profile: Profile, *, answer: bool) -> Profile:
    if any(ss.name == "auto-default" for ss in profile.saved_searches):
        return profile
    if not answer:
        return profile
    new = list(profile.saved_searches)
    new.append(SavedSearch(name="auto-default", auto_apply=True))
    return profile.model_copy(update={"saved_searches": new})


def _saved_searches_menu(out: Console, profile: Profile) -> Profile:
    """Drive add/edit/delete/caps interactively until user picks done.

    Returns a possibly-modified Profile. Pure of side effects beyond
    Prompt.ask / Confirm.ask interaction, so easy to test by patching
    those.
    """
    while True:
        _render_saved_searches_table(out, profile)
        has_searches = bool(profile.saved_searches)
        choices = ["add"]
        if has_searches:
            choices += ["edit", "delete"]
        choices += ["caps", "done"]
        action = Prompt.ask(
            "Action", choices=choices, default="done",
        )
        if action == "done":
            return profile
        if action == "add":
            profile = _add_saved_search(out, profile)
        elif action == "edit":
            profile = _edit_saved_search(out, profile)
        elif action == "delete":
            profile = _delete_saved_search(out, profile)
        elif action == "caps":
            profile = _edit_caps_and_cooldowns(out, profile)


def _render_saved_searches_table(out: Console, profile: Profile) -> None:
    out.rule("Saved searches & auto-apply")
    if not profile.saved_searches:
        out.print("[dim](no saved searches yet)[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Auto-apply")
    table.add_column("Platforms")
    table.add_column("Notifications")
    for i, ss in enumerate(profile.saved_searches, start=1):
        table.add_row(
            str(i),
            ss.name,
            "✓" if ss.auto_apply else "✗",
            ", ".join(ss.platforms) if ss.platforms else "any",
            _summarize_notifications(ss),
        )
    out.print(table)


def _summarize_notifications(ss: SavedSearch) -> str:
    if ss.notifications is None:
        return "base"
    parts: list[str] = []
    if ss.notifications.telegram is not None:
        if ss.notifications.telegram.enabled:
            label = "telegram"
            if (
                ss.notifications.telegram.bot_token_env is not None
                or ss.notifications.telegram.chat_id is not None
            ):
                label += " (override)"
            parts.append(label)
        else:
            parts.append("telegram (off)")
    if ss.notifications.email is not None:
        if ss.notifications.email.enabled:
            label = "email"
            if ss.notifications.email.smtp_env is not None:
                label += " (override)"
            parts.append(label)
        else:
            parts.append("email (off)")
    return " + ".join(parts) if parts else "none"


def _add_saved_search(out: Console, profile: Profile) -> Profile:
    new_ss = _build_saved_search(out, current=None, existing_names={ss.name for ss in profile.saved_searches})
    return profile.model_copy(update={"saved_searches": [*profile.saved_searches, new_ss]})


def _edit_saved_search(out: Console, profile: Profile) -> Profile:
    n = len(profile.saved_searches)
    raw = Prompt.ask(f"Which one to edit? [1-{n}]")
    try:
        idx = int(raw) - 1
        if not 0 <= idx < n:
            raise ValueError
    except ValueError:
        out.print("[red]Invalid selection.[/red]")
        return profile
    current = profile.saved_searches[idx]
    other_names = {ss.name for i, ss in enumerate(profile.saved_searches) if i != idx}
    updated = _build_saved_search(out, current=current, existing_names=other_names)
    new_list = list(profile.saved_searches)
    new_list[idx] = updated
    return profile.model_copy(update={"saved_searches": new_list})


def _build_saved_search(
    out: Console,
    *,
    current: SavedSearch | None,
    existing_names: set[str],
) -> SavedSearch:
    name = _prompt_name(out, current=current, existing_names=existing_names)
    auto_apply_default = current.auto_apply if current else False
    auto_apply = Confirm.ask("Auto-apply for matches against this search?", default=auto_apply_default)
    platforms = _prompt_platforms(out, current=current)
    notifications = _prompt_notifications_override(out, current=current)
    overlay = _prompt_filter_overrides(out, current=current)

    return SavedSearch(
        name=name,
        auto_apply=auto_apply,
        platforms=platforms,
        notifications=notifications,
        **overlay,
    )


def _prompt_name(out: Console, *, current: SavedSearch | None, existing_names: set[str]) -> str:
    default = current.name if current else None
    while True:
        raw = Prompt.ask(
            "Saved search name (lowercase, digits, _, -)",
            default=default,
        )
        if not _NAME_PATTERN.match(raw):
            out.print("[red]Name must match ^[a-z0-9_-]+$[/red]")
            continue
        if raw in existing_names:
            out.print(f"[red]A saved search named {raw!r} already exists.[/red]")
            continue
        return raw


def _prompt_platforms(out: Console, *, current: SavedSearch | None) -> list[str]:
    default = ", ".join(current.platforms) if current and current.platforms else ""
    while True:
        raw = Prompt.ask(
            "Platforms (comma-separated; empty = all platforms)",
            default=default,
        )
        if not raw.strip():
            return []
        items = [p.strip() for p in raw.split(",") if p.strip()]
        unknown = [p for p in items if p not in _PLATFORM_VALUES]
        if unknown:
            out.print(f"[red]Unknown platform(s): {', '.join(unknown)}. "
                      f"Valid: {sorted(_PLATFORM_VALUES)}[/red]")
            continue
        return items


def _prompt_notifications_override(
    out: Console, *, current: SavedSearch | None,
) -> SavedSearchNotifications | None:
    default_override = current is not None and current.notifications is not None
    if not Confirm.ask(
        "Override notifications for matches against this search?",
        default=default_override,
    ):
        return None

    out.print(
        "[dim]For each channel you set here, this search REPLACES base profile's "
        "setting for matches against this search alone. Set 'enabled=no' to suppress "
        "the channel for this search.[/dim]"
    )

    telegram = _prompt_channel_override(
        out,
        channel="telegram",
        current=current.notifications.telegram if current and current.notifications else None,
        fields=(("bot_token_env", "Bot token env var"), ("chat_id", "Chat ID")),
        builder=TelegramNotificationOverride,
    )
    email = _prompt_channel_override(
        out,
        channel="email",
        current=current.notifications.email if current and current.notifications else None,
        fields=(("smtp_env", "SMTP env-var prefix"),),
        builder=EmailNotificationOverride,
    )

    if telegram is None and email is None:
        return None
    return SavedSearchNotifications(telegram=telegram, email=email)


def _prompt_channel_override(
    out: Console,
    *,
    channel: str,
    current,
    fields: tuple[tuple[str, str], ...],
    builder,
):
    have_opinion_default = current is not None
    if not Confirm.ask(
        f"{channel}: have an opinion for this search?",
        default=have_opinion_default,
    ):
        return None
    enabled_default = current.enabled if current else True
    enabled = Confirm.ask(f"{channel} enabled for this search?", default=enabled_default)
    kwargs = {"enabled": enabled}
    if enabled:
        for field_name, prompt_label in fields:
            current_value = getattr(current, field_name, None) if current else None
            default = current_value or ""
            raw = Prompt.ask(f"{prompt_label} (blank = inherit base)", default=default)
            kwargs[field_name] = raw.strip() or None
    return builder(**kwargs)


def _prompt_filter_overrides(out: Console, *, current: SavedSearch | None) -> dict:
    has_overrides = current is not None and any(
        getattr(current, f) is not None for f in (
            "rent_min_warm", "rent_max_warm", "rooms_min", "rooms_max",
            "district_allowlist", "radius_km", "furnished_pref", "min_contract_months",
        )
    )
    if not Confirm.ask(
        "Customize filter overrides for this search?",
        default=has_overrides,
    ):
        return {
            "rent_min_warm": getattr(current, "rent_min_warm", None) if current else None,
            "rent_max_warm": getattr(current, "rent_max_warm", None) if current else None,
            "rooms_min": getattr(current, "rooms_min", None) if current else None,
            "rooms_max": getattr(current, "rooms_max", None) if current else None,
            "radius_km": getattr(current, "radius_km", None) if current else None,
            "min_contract_months": getattr(current, "min_contract_months", None) if current else None,
            "district_allowlist": getattr(current, "district_allowlist", None) if current else None,
            "furnished_pref": getattr(current, "furnished_pref", None) if current else None,
        }

    overlay: dict = {}
    for field, label in (
        ("rent_min_warm", "Min warm rent (€/month, blank=inherit)"),
        ("rent_max_warm", "Max warm rent (€/month, blank=inherit)"),
        ("rooms_min", "Min rooms (blank=inherit)"),
        ("rooms_max", "Max rooms (blank=inherit)"),
        ("radius_km", "Radius km (blank=inherit)"),
        ("min_contract_months", "Min contract months (blank=inherit)"),
    ):
        current_val = getattr(current, field, None) if current else None
        default = str(current_val) if current_val is not None else ""
        overlay[field] = _prompt_optional_int(out, label, default=default, min_value=0)

    # district_allowlist: two-step override
    current_districts = getattr(current, "district_allowlist", None) if current else None
    if Confirm.ask(
        "Override district allowlist for this search?",
        default=current_districts is not None,
    ):
        default = ", ".join(current_districts) if current_districts else ""
        raw = Prompt.ask(
            "Districts (comma-separated; blank = any district)",
            default=default,
        )
        overlay["district_allowlist"] = [d.strip() for d in raw.split(",") if d.strip()]
    else:
        overlay["district_allowlist"] = None

    # furnished_pref: two-step override
    current_furnished = getattr(current, "furnished_pref", None) if current else None
    if Confirm.ask(
        "Override furnished preference for this search?",
        default=current_furnished is not None,
    ):
        overlay["furnished_pref"] = Prompt.ask(
            "Furnished preference",
            choices=["any", "furnished", "unfurnished"],
            default=current_furnished or "any",
        )
    else:
        overlay["furnished_pref"] = None

    return overlay


def _delete_saved_search(out: Console, profile: Profile) -> Profile:
    raise NotImplementedError("Task 10 implements this")


def _edit_caps_and_cooldowns(out: Console, profile: Profile) -> Profile:
    raise NotImplementedError("Task 11 implements this")


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

    out.rule("Auto-apply (Phase 4)")
    if not any(ss.name == "auto-default" for ss in profile.saved_searches):
        enable = Confirm.ask(
            "Enable auto-apply with a starter saved search? "
            "(Use `flatpilot pause` to disable temporarily.)",
            default=False,
        )
        profile = _maybe_add_auto_apply(profile, answer=enable)

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
    if (
        existing
        and existing.city == city
        and existing.home_lat
        and existing.home_lng
        and Confirm.ask(
            f"Keep saved coordinates ({existing.home_lat:.4f}, {existing.home_lng:.4f})?",
            default=True,
        )
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
