"""Shared types for per-platform contact-form fillers.

Each filler is a class that sets a ``platform`` ClassVar, registers
itself through :func:`flatpilot.fillers.register`, and implements
:meth:`Filler.fill`. The L4 apply command will iterate the
registry by ``platform`` to drive each platform's filler — same shape
as :mod:`flatpilot.scrapers.base`.

:class:`FillReport` carries everything L4 needs to write an
``applications`` row (see :data:`flatpilot.schemas.APPLICATIONS_CREATE_SQL`)
without a second round of data marshaling: the rendered ``message_sent``
goes into the message column, ``attachments_sent`` is JSON-serialized to
the attachments column, and ``submitted`` decides whether L4 records a
``status='submitted'`` or ``'failed'`` row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol

from flatpilot.errors import FlatPilotError


class FillError(FlatPilotError):
    """Base class for all filler errors."""


class FormNotFoundError(FillError):
    """Couldn't locate the contact form on the listing page."""


class ListingExpiredError(FillError):
    """Listing is no longer reachable on the platform.

    Distinct from :class:`FormNotFoundError` (selectors broken on a live
    listing) so the apply orchestrator can record the row as
    ``auto_skipped: listing_expired (...)``. That note prefix piggybacks
    on the existing ``auto_skipped:`` exclusions in
    :func:`flatpilot.auto_apply.cooldown_remaining_sec` and
    :func:`flatpilot.auto_apply.failures_for_flat`, so an expired listing
    does not start a 120s platform cooldown or count toward
    ``max_failures_per_flat``.

    Raised on HTTP 404 / 410 from any platform, on a Kleinanzeigen URL
    that no longer matches ``/s-anzeige/`` (the platform redirects deleted
    listings to a category page that returns 200), and on WG-Gesucht
    pages that load successfully but render no contact CTA.
    """


class SelectorMissingError(FillError):
    """A required form-field selector did not match anything on the page."""


class NotAuthenticatedError(FillError):
    """Navigation landed on a login / register page — no cookies for this platform.

    Recovery is human: run the platform's polite_session in headed mode
    once, log in by hand, let the storage state save to
    ``~/.flatpilot/sessions/<platform>/state.json``. The dry-run will
    then reuse those cookies.
    """


class SubmitVerificationError(FillError):
    """Submit click landed but verification (URL change, banner check) failed.

    Distinct from :class:`SelectorMissingError` (the button itself is gone)
    and :class:`FormNotFoundError` (we never reached the form). Used when
    the click happened but the platform appears to have rejected the
    message — the form URL didn't change, an inline error rendered, etc.
    """


@dataclass
class FillReport:
    """Outcome of a :meth:`Filler.fill` call.

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
      form (post-submit if ``submitted`` is True, pre-submit otherwise).
    - ``submitted``: ``True`` only after the platform's submit button was
      clicked AND the post-submit verification passed. ``False`` for any
      ``fill(submit=False)`` call.
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

    def fill(
        self,
        listing_url: str,
        message: str,
        attachments: list[Path],
        *,
        submit: bool,
        screenshot_dir: Path | None = None,
    ) -> FillReport:
        """Navigate to ``listing_url``, open the contact form, fill it.

        If ``submit`` is True, click the platform's submit button and
        verify the form was actually sent (typically by asserting the
        page navigated away from the form URL). If ``submit`` is False,
        stop at the filled-but-unsent form and return — useful for
        previews.

        Implementations MUST NOT attempt to log in. Failures should
        raise the most specific error class available —
        :class:`NotAuthenticatedError` when the page redirects to login,
        :class:`FormNotFoundError` when the contact CTA / form can't be
        located, :class:`SelectorMissingError` when a specific named
        field selector returns zero matches. Submit-time failures
        (button missing, page didn't navigate, error banner present)
        raise :class:`FillError`.
        """
