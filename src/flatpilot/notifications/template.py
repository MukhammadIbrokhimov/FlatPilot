"""Shared plain + HTML templates for matched-flat notifications.

Both the Telegram and email adapters format a flat the same way — a
headline with the title, followed by key facts (Warmmiete, rooms,
district, online_since) and the listing URL. Keeping the two renderers
colocated guarantees the channels stay in sync.

The HTML template sticks to the tag subset Telegram's ``parse_mode=HTML``
accepts (``<b>``, ``<a>``) so it renders identically in email clients
and in Telegram without a second template.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any


def _fmt_rent(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(round(float(value)))} €"
    except (TypeError, ValueError):
        return str(value)


def _fmt_rooms(value: Any) -> str:
    if value is None:
        return "—"
    try:
        rooms = float(value)
    except (TypeError, ValueError):
        return str(value)
    if rooms.is_integer():
        return str(int(rooms))
    return f"{rooms:g}"


def _get(flat: Mapping[str, Any], key: str) -> Any:
    try:
        return flat[key]
    except (KeyError, IndexError):
        return None


def render_plain(flat: Mapping[str, Any]) -> str:
    lines = [
        _get(flat, "title") or "Untitled listing",
        "",
        f"Warmmiete: {_fmt_rent(_get(flat, 'rent_warm_eur'))}",
        f"Rooms: {_fmt_rooms(_get(flat, 'rooms'))}",
    ]
    if district := _get(flat, "district"):
        lines.append(f"District: {district}")
    if online_since := _get(flat, "online_since"):
        lines.append(f"Posted: {online_since}")
    if url := _get(flat, "listing_url"):
        lines.append("")
        lines.append(str(url))
    return "\n".join(lines)


def render_html(flat: Mapping[str, Any]) -> str:
    title = _get(flat, "title") or "Untitled listing"
    lines = [
        f"<b>{escape(str(title))}</b>",
        "",
        f"Warmmiete: <b>{escape(_fmt_rent(_get(flat, 'rent_warm_eur')))}</b>",
        f"Rooms: {escape(_fmt_rooms(_get(flat, 'rooms')))}",
    ]
    if district := _get(flat, "district"):
        lines.append(f"District: {escape(str(district))}")
    if online_since := _get(flat, "online_since"):
        lines.append(f"Posted: {escape(str(online_since))}")
    if url := _get(flat, "listing_url"):
        lines.append("")
        lines.append(f'<a href="{escape(str(url), quote=True)}">View listing</a>')
    return "\n".join(lines)
