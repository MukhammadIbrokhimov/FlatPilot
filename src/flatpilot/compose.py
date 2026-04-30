"""Template-based Anschreiben composer.

Users maintain one Markdown template per rental platform at
``~/.flatpilot/templates/<platform>.md``. At apply time the L4 CLI
command (FlatPilot-cjtz) passes the profile, platform and flat row to
:func:`compose_anschreiben`, which substitutes ``$placeholder``
references with values from the profile and flat and returns the
rendered text ready to paste into a contact form.

Why :mod:`string.Template` rather than ``str.format``: these Markdown
files are user-owned plain text, and ``str.format`` allows attribute
traversal (``{obj.__class__}`` and friends). ``string.Template`` limits
placeholders to single identifiers — all we need, and safe against
ill-formed templates.

Phase 3 rule: no LLM-generated variation. The composer is pure
substitution; same inputs always produce the same output.

Available placeholders
----------------------

From the profile: ``city``, ``rooms_min``, ``rooms_max``,
``household_size``, ``kids``, ``net_income_eur``, ``move_in_date``,
``employment_status`` (renamed from ``profile.status`` so it doesn't
collide with flat / application status elsewhere), ``pets`` (a
comma-joined string, empty when no pets), ``wbs_status``.

From the flat: ``title``, ``listing_url``, ``rent_warm_eur``, ``rooms``,
``district``, ``address``. All flat fields except ``title`` and
``listing_url`` can be absent on a given listing — keep templates
tolerant, e.g. prefer ``Wohnung$district_suffix`` patterns composed
ahead of time over sentences that read oddly when a field is empty.

Escape a literal dollar sign in a template with ``$$``.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from flatpilot.config import TEMPLATES_DIR
from flatpilot.errors import FlatPilotError
from flatpilot.profile import Profile


class TemplateError(FlatPilotError):
    """Base class for all composer errors."""


class TemplateMissingError(TemplateError):
    """No ``<platform>.md`` file exists in the templates directory."""


class TemplateSubstitutionError(TemplateError):
    """Template references a placeholder the composer doesn't provide."""


def example_template_path() -> Path:
    """Path to the shipped example Anschreiben bundled with the package.

    Mirrors :meth:`flatpilot.profile.Profile.example_path` — uses
    ``importlib.resources`` so the example is found whether FlatPilot
    runs from a checkout or from an installed wheel.
    """

    return Path(str(resources.files("flatpilot") / "anschreiben.example.md"))


def _fmt_num(value: Any) -> str:
    """Render a numeric flat value as a plain string.

    Integer-valued floats become ``"1250"`` (no trailing ``.0``);
    fractional floats become ``"2.5"``; ``None`` becomes ``""``. Raw
    strings pass through so a scraper that stored a pre-formatted value
    still renders.
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        # bool is a subclass of int; guard before the int branch.
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:g}"
    return str(value)


def _coerce(value: Any) -> str:
    return "" if value is None else str(value)


def build_context(profile: Profile, flat: Mapping[str, Any]) -> dict[str, str]:
    """Merge profile + flat values into the substitution namespace."""

    return {
        "city": profile.city,
        "rooms_min": str(profile.rooms_min),
        "rooms_max": str(profile.rooms_max),
        "household_size": str(profile.household_size),
        "kids": str(profile.kids),
        "net_income_eur": str(profile.net_income_eur),
        "move_in_date": profile.move_in_date.isoformat(),
        "employment_status": profile.status,
        "pets": ", ".join(profile.pets),
        "wbs_status": profile.wbs.status,
        "title": _coerce(flat.get("title")),
        "listing_url": _coerce(flat.get("listing_url")),
        "rent_warm_eur": _fmt_num(flat.get("rent_warm_eur")),
        "rooms": _fmt_num(flat.get("rooms")),
        "district": _coerce(flat.get("district")),
        "address": _coerce(flat.get("address")),
    }


def _known_platforms(templates_dir: Path) -> list[str]:
    if not templates_dir.is_dir():
        return []
    return sorted(p.stem for p in templates_dir.glob("*.md"))


def compose_anschreiben(
    profile: Profile,
    platform: str,
    flat: Mapping[str, Any],
    templates_dir: Path | None = None,
) -> str:
    """Render ``<platform>.md`` with the profile + flat context.

    Raises :class:`TemplateMissingError` if no template file exists for
    ``platform``, or :class:`TemplateSubstitutionError` if the template
    references a placeholder the composer doesn't provide (or contains a
    syntactically invalid ``$`` reference).
    """

    dir_ = templates_dir or TEMPLATES_DIR
    path = dir_ / f"{platform}.md"
    if not path.is_file():
        known = _known_platforms(dir_) or ["(none)"]
        raise TemplateMissingError(
            f"no template for platform {platform!r} at {path} — "
            f"existing templates: {', '.join(known)}"
        )

    template = Template(path.read_text(encoding="utf-8"))
    ctx = build_context(profile, flat)
    try:
        return template.substitute(ctx)
    except KeyError as exc:
        missing_key = exc.args[0] if exc.args else "?"
        available = ", ".join(sorted(ctx.keys()))
        raise TemplateSubstitutionError(
            f"template {path} references unknown placeholder "
            f"${{{missing_key}}} — available: {available}"
        ) from exc
    except ValueError as exc:
        # Template.substitute raises ValueError for malformed placeholders,
        # e.g. a bare ``$`` not escaped as ``$$``.
        raise TemplateSubstitutionError(
            f"template {path} has a malformed placeholder: {exc}"
        ) from exc
