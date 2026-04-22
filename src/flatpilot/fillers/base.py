"""Shared types for per-platform contact-form fillers.

Each filler is a class that sets a ``platform`` ClassVar, registers
itself through :func:`flatpilot.fillers.register`, and implements
:meth:`Filler.fill_dry_run`. The L4 apply command will iterate the
registry by ``platform`` to drive each platform's filler — same shape
as :mod:`flatpilot.scrapers.base`.

:class:`FillReport` carries everything L4 needs to write an
``applications`` row (see :data:`flatpilot.schemas.APPLICATIONS_CREATE_SQL`)
without a second round of data marshaling: the rendered ``message_sent``
goes into the message column, ``attachments_sent`` is JSON-serialized to
the attachments column, ``submitted=False`` (always, in dry-run) maps to
the row's ``status``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol


class FillError(RuntimeError):
    """Base class for all filler errors."""


class FormNotFoundError(FillError):
    """Couldn't locate the contact form on the listing page."""


class SelectorMissingError(FillError):
    """A required form-field selector did not match anything on the page."""


class NotAuthenticatedError(FillError):
    """Navigation landed on a login / register page — no cookies for this platform.

    Recovery is human: run the platform's polite_session in headed mode
    once, log in by hand, let the storage state save to
    ``~/.flatpilot/sessions/<platform>/state.json``. The dry-run will
    then reuse those cookies.
    """


@dataclass
class FillReport:
    """Outcome of a :meth:`Filler.fill_dry_run` call.

    Field semantics:

    - ``contact_url``: the URL the page settled on after the contact CTA
      was clicked (or the listing URL if the form was already inline).
    - ``fields_filled``: human-readable ``{field_name: value}`` mapping
      so a CLI preview can show "subject = ...", "message = ...", etc.
      without re-rendering the template.
    - ``message_sent``: the full Anschreiben body that was typed into
      the form. L4 stores this verbatim in ``applications.message_sent``.
    - ``attachments_sent``: the absolute paths that were attached. L4
      JSON-serializes these into ``applications.attachments_sent_json``.
    - ``screenshot_path``: optional path to a PNG capturing the filled
      (un-submitted) form. Useful for the ``apply --dry-run`` UX so the
      user can eyeball what would be sent.
    - ``submitted``: always ``False`` in dry-run; reserved for the
      future live-submit path that will land alongside L4.
    """

    platform: str
    listing_url: str
    contact_url: str
    fields_filled: Mapping[str, str]
    message_sent: str
    attachments_sent: list[Path] = field(default_factory=list)
    screenshot_path: Path | None = None
    submitted: bool = False
    started_at: str = ""
    finished_at: str = ""


class Filler(Protocol):
    """Per-platform contact-form filler contract."""

    platform: ClassVar[str]

    def fill_dry_run(
        self,
        listing_url: str,
        message: str,
        attachments: list[Path],
        screenshot_dir: Path | None = None,
    ) -> FillReport:
        """Navigate to ``listing_url``, open the contact form, fill it, stop.

        Implementations MUST NOT click submit and MUST NOT attempt to
        log in. Failures should raise the most specific error class
        available — :class:`NotAuthenticatedError` when the page
        redirects to login, :class:`FormNotFoundError` when the contact
        CTA / form can't be located, :class:`SelectorMissingError` when
        a specific named field selector returns zero matches.
        """
