"""Form-filler framework and per-platform implementations.

L4 (``flatpilot apply <flat_id>``) calls :func:`get_filler` with the
flat's ``platform`` and invokes :meth:`Filler.fill` to navigate to the
contact form, fill fields, attach files, and (when ``submit=True``)
click submit. Selectors that target real DOM elements live in each
platform module; this package's job is the registry, identical in
shape to :mod:`flatpilot.scrapers`.
"""

from __future__ import annotations

from collections.abc import Iterable

from flatpilot.fillers.base import (
    Filler,
    FillError,
    FillReport,
    FormNotFoundError,
    NotAuthenticatedError,
    SelectorMissingError,
    SubmitVerificationError,
)

_REGISTRY: dict[str, type[Filler]] = {}


def register(cls: type[Filler]) -> type[Filler]:
    """Class decorator that indexes ``cls`` under its ``platform`` attribute."""

    platform = getattr(cls, "platform", None)
    if not platform:
        raise TypeError(
            f"{cls.__name__} must set a non-empty `platform` ClassVar before @register"
        )
    if platform in _REGISTRY:
        raise ValueError(
            f"Duplicate filler registration for platform {platform!r}: "
            f"{_REGISTRY[platform].__name__} vs {cls.__name__}"
        )
    _REGISTRY[platform] = cls
    return cls


def get_filler(platform: str) -> type[Filler]:
    try:
        return _REGISTRY[platform]
    except KeyError as exc:
        raise KeyError(
            f"No filler registered for platform {platform!r} "
            f"(known: {sorted(_REGISTRY)})"
        ) from exc


def all_fillers() -> Iterable[type[Filler]]:
    return list(_REGISTRY.values())


__all__ = [
    "FillError",
    "FillReport",
    "Filler",
    "FormNotFoundError",
    "NotAuthenticatedError",
    "SelectorMissingError",
    "SubmitVerificationError",
    "all_fillers",
    "get_filler",
    "register",
]
