"""Kleinanzeigen contact-form filler.

Mirrors the structure of :mod:`flatpilot.fillers.wg_gesucht`: navigates
to a listing URL using the same polite Playwright session that the
scraper uses (so cookies, consent banner and stealth fingerprint are
shared), opens the modal contact form by clicking its trigger button,
fills the message body, and (with ``submit=True``) clicks submit and
verifies the JSON endpoint reported success.

Selectors were transcribed from an unauthenticated DOM snapshot of a
real Berlin listing on 2026-04-27. The form lives under
``form#viewad-contact-modal-form`` and is hidden by default
(``mfp-popup-large mfp-hide modal-dialog``); clicking
``button#viewad-contact-button-login-modal`` reveals it for an
authenticated user — for an unauthenticated user the same trigger
opens a login modal, which we never reach because :meth:`_guard_login`
raises first.

Why no name / phone fill: Kleinanzeigen prefills both fields from the
logged-in user's account. The WG-Gesucht filler follows the same
policy. If a user's Kleinanzeigen account has no phone number set, the
form's required-field validation fails on submit and we surface that as
:class:`SubmitVerificationError`.

Why no file upload: Kleinanzeigen's contact form has no file-input
field — landlords cannot receive PDF attachments via the platform.
Passing a non-empty ``attachments`` list raises
:class:`SelectorMissingError` so the user discovers the platform
mismatch loudly rather than silently sending an unaccompanied message.

Submit verification differs from WG-Gesucht: Kleinanzeigen submits via
XHR to ``/s-anbieter-kontaktieren.json`` so the form URL never
changes. Instead we wait for either ``.ajaxform-success`` (success
banner) or ``.outcomebox-error`` (error banner) to become visible; a
timeout falls through to :class:`SubmitVerificationError`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from flatpilot.fillers import register
from flatpilot.fillers.base import (
    FillError,  # noqa: F401 — re-exported for callers that catch the base class
    FillReport,
    FormNotFoundError,
    NotAuthenticatedError,
    SelectorMissingError,
    SubmitVerificationError,
)
from flatpilot.scrapers.kleinanzeigen import CONSENT_SELECTORS, HOST, WARMUP_URL
from flatpilot.scrapers.session import (
    DEFAULT_USER_AGENT,
    SessionConfig,
    check_rate_limit,
    polite_session,
)
from flatpilot.scrapers.session import (
    page as session_page,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Selectors:
    # Trigger button on the listing page that reveals the modal form
    # for authenticated users; for unauthenticated users the same id
    # raises a login modal — _guard_login redirects those flows first.
    contact_trigger: str = "button#viewad-contact-button-login-modal"
    form: str = "form#viewad-contact-modal-form"
    message_input: str = (
        "textarea#viewad-contact-message, "
        "textarea[name='message']"
    )
    file_input: str = (
        "form#viewad-contact-modal-form input[type='file']"
    )
    submit_button: str = (
        "form#viewad-contact-modal-form button.viewad-contact-submit, "
        "form#viewad-contact-modal-form button[type='submit']"
    )
    success_marker: str = "form#viewad-contact-modal-form .ajaxform-success"
    error_marker: str = "form#viewad-contact-modal-form .outcomebox-error"


SELECTORS = _Selectors()

# URL fragments that indicate the listing redirected us to a login wall.
LOGIN_URL_FRAGMENTS: tuple[str, ...] = (
    "/m-einloggen.html",
    "/login",
    "/anmelden",
    "/registrieren",
)

SUBMIT_NAV_WAIT_MS = 7_000
FORM_WAIT_MS = 5_000
FIELD_WAIT_MS = 3_000


@register
class KleinanzeigenFiller:
    platform: ClassVar[str] = "kleinanzeigen"
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT

    def fill(
        self,
        listing_url: str,
        message: str,
        attachments: list[Path],
        *,
        submit: bool,
        screenshot_dir: Path | None = None,
    ) -> FillReport:
        if not message.strip():
            raise ValueError("message must be non-empty")
        for path in attachments:
            if not path.is_file():
                raise FileNotFoundError(f"attachment not found: {path}")

        config = SessionConfig(
            platform=self.platform,
            user_agent=self.user_agent,
            warmup_url=WARMUP_URL,
            consent_selectors=CONSENT_SELECTORS,
            stealth=True,
        )
        started = datetime.now(UTC).isoformat()

        with polite_session(config) as context, session_page(context) as pg:
            response = pg.goto(listing_url, wait_until="domcontentloaded")
            if response is not None:
                check_rate_limit(response.status, self.platform)
                if response.status >= 400:
                    raise FormNotFoundError(
                        f"{self.platform}: listing returned HTTP {response.status} "
                        f"({listing_url})"
                    )

            self._guard_login(pg)
            self._reveal_contact_form(pg)
            contact_url = pg.url

            fields_filled: dict[str, str] = {}

            self._fill_required(pg, SELECTORS.message_input, message, label="message")
            fields_filled["message"] = message

            if attachments:
                file_input = pg.locator(SELECTORS.file_input).first
                if file_input.count() == 0:
                    raise SelectorMissingError(
                        f"{self.platform}: contact form does not support attachments "
                        f"(no input[type=file] under form#viewad-contact-modal-form). "
                        f"Remove '{self.platform}' from "
                        f"profile.attachments.per_platform, or rely on the default "
                        f"list only when applying to platforms that accept files."
                    )
                file_input.set_input_files([str(p) for p in attachments])
                fields_filled["attachments"] = ", ".join(p.name for p in attachments)

            submitted = False
            if submit:
                submit_btn = pg.locator(SELECTORS.submit_button).first
                if submit_btn.count() == 0:
                    raise SelectorMissingError(
                        f"{self.platform}: no submit button matching "
                        f"{SELECTORS.submit_button!r} on {contact_url}"
                    )
                submit_btn.click()
                submitted = self._verify_submitted(pg, contact_url)

            screenshot_path = self._maybe_screenshot(pg, screenshot_dir, listing_url)

        return FillReport(
            platform=self.platform,
            listing_url=listing_url,
            contact_url=contact_url,
            fields_filled=fields_filled,
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=screenshot_path,
            submitted=submitted,
            started_at=started,
            finished_at=datetime.now(UTC).isoformat(),
        )

    def _guard_login(self, pg: Any) -> None:
        url = pg.url or ""
        if any(frag in url for frag in LOGIN_URL_FRAGMENTS):
            raise NotAuthenticatedError(
                f"{self.platform}: navigation landed on {url} — log in once "
                f"by running the polite_session in headed mode so cookies "
                f"persist to ~/.flatpilot/sessions/{self.platform}/state.json"
            )

    def _reveal_contact_form(self, pg: Any) -> None:
        # The modal form is hidden by default (mfp-hide). The trigger
        # button shows it inline for authenticated users; for unauth
        # users the trigger raises a login modal — _guard_login has
        # already redirected those flows.
        if pg.locator(SELECTORS.form).first.is_visible():
            return

        trigger = pg.locator(SELECTORS.contact_trigger).first
        if trigger.count() == 0:
            raise FormNotFoundError(
                f"{self.platform}: no contact-form trigger matching "
                f"{SELECTORS.contact_trigger!r} at {pg.url}"
            )
        trigger.click()

        try:
            pg.locator(SELECTORS.form).first.wait_for(
                state="visible", timeout=FORM_WAIT_MS
            )
        except Exception as exc:
            raise FormNotFoundError(
                f"{self.platform}: trigger clicked but form selector "
                f"{SELECTORS.form!r} never became visible at {pg.url}"
            ) from exc

    def _fill_required(self, pg: Any, selector: str, value: str, *, label: str) -> None:
        target = pg.locator(selector).first
        try:
            target.wait_for(state="visible", timeout=FIELD_WAIT_MS)
        except Exception as exc:
            raise SelectorMissingError(
                f"{self.platform}: {label} field {selector!r} not visible at {pg.url}"
            ) from exc
        target.fill(value)

    def _verify_submitted(self, pg: Any, contact_url: str) -> bool:
        # Kleinanzeigen submits via XHR to /s-anbieter-kontaktieren.json
        # so the form URL never changes. Wait for either the success or
        # error indicator to become visible; treat a timeout as a verify
        # failure too — silence after a click is not success.
        success = pg.locator(SELECTORS.success_marker).first
        error = pg.locator(SELECTORS.error_marker).first
        try:
            success.wait_for(state="visible", timeout=SUBMIT_NAV_WAIT_MS)
            return True
        except PlaywrightTimeoutError:
            pass
        if error.is_visible():
            raise SubmitVerificationError(
                f"{self.platform}: submit failed — error banner visible at {contact_url}"
            )
        raise SubmitVerificationError(
            f"{self.platform}: neither success nor error indicator appeared "
            f"within {SUBMIT_NAV_WAIT_MS}ms after submit at {contact_url}"
        )

    def _maybe_screenshot(
        self,
        pg: Any,
        screenshot_dir: Path | None,
        listing_url: str,
    ) -> Path | None:
        if screenshot_dir is None:
            return None
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        # Kleinanzeigen detail URL ends with the ad ID:
        # /s-anzeige/<slug>/<id>-203-<X>. The trailing segment is
        # unique enough for a per-listing filename.
        slug = listing_url.rstrip("/").split("/")[-1] or "listing"
        path = screenshot_dir / f"{self.platform}-{slug}.png"
        pg.screenshot(path=str(path), full_page=True)
        return path


__all__ = ["HOST", "LOGIN_URL_FRAGMENTS", "SELECTORS", "KleinanzeigenFiller"]
