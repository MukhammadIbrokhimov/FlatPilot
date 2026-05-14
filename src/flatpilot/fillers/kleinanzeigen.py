"""Kleinanzeigen contact-form filler.

Mirrors the structure of :mod:`flatpilot.fillers.wg_gesucht`: navigates
to a listing URL using the same polite Playwright session that the
scraper uses (so cookies, consent banner and stealth fingerprint are
shared), opens the modal contact form by clicking its trigger button,
fills the message body, and (with ``submit=True``) clicks submit and
verifies the JSON endpoint reported success.

Selectors were verified empirically against the authenticated DOM of a
live Berlin listing on 2026-05-05 (FlatPilot-92j). The contact form
for logged-in users is the inline ``form#viewad-contact-form``, which
is visible by default — no trigger click needed in the common case.
The separate ``form#viewad-contact-modal-form`` (hidden,
``mfp-popup-large mfp-hide modal-dialog``) is the unauthenticated
fallback path that :meth:`_guard_login` short-circuits before we reach
it. ``button#viewad-contact-button`` lives outside both forms and is
kept only as a defensive fallback for DOM variants where the inline
form is not pre-rendered.

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
banner) or ``.outcomebox-error`` (error banner) under
``form#viewad-contact-form`` to become visible; a timeout falls
through to :class:`SubmitVerificationError`.
"""

from __future__ import annotations

import logging
import re
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
    ListingExpiredError,
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
    # Verified on the authenticated DOM 2026-05-05 (FlatPilot-92j): the
    # inline #viewad-contact-form is visible by default, so the trigger
    # below is only exercised when that form happens to be missing
    # (mobile variant, soft-deauth state, etc.). On clean auth runs
    # _reveal_contact_form early-exits before the trigger is touched.
    contact_trigger: str = "button#viewad-contact-button"
    form: str = "form#viewad-contact-form"
    message_input: str = (
        "form#viewad-contact-form textarea#viewad-contact-message, "
        "form#viewad-contact-form textarea[name='message']"
    )
    file_input: str = (
        "form#viewad-contact-form input[type='file']"
    )
    submit_button: str = (
        "form#viewad-contact-form button.viewad-contact-submit, "
        "form#viewad-contact-form button[type='submit']"
    )
    success_marker: str = "form#viewad-contact-form .ajaxform-success"
    error_marker: str = "form#viewad-contact-form .outcomebox-error"
    # Kleinanzeigen's full-page server-error template ("Huch, da ist ein
    # Fehler (400) aufgetreten") — rendered when the submit POST is
    # rejected by the backend and the page navigates away from the form.
    # Neither success_marker nor error_marker (both scoped to the form)
    # exist on this page. Playwright text-regex selector matches the
    # bracketed status code in the heading. FlatPilot-b13.
    server_error_marker: str = r"text=/Fehler\s*\[\d+\]/"


SELECTORS = _Selectors()

# URL fragments that indicate the listing redirected us to a login wall.
LOGIN_URL_FRAGMENTS: tuple[str, ...] = (
    "/m-einloggen.html",
    "/login",
    "/anmelden",
    "/registrieren",
)

# Bumped from 7s → 15s after observing real submit timeouts on kleinanzeigen
# (FlatPilot-8kt followup): the platform's XHR roundtrip can be slow under
# load. 15s is long enough that genuine network slowness clears but short
# enough that a stuck submit does not block the auto-apply queue for an
# entire pacing cycle.
SUBMIT_NAV_WAIT_MS = 15_000
FORM_WAIT_MS = 5_000
FIELD_WAIT_MS = 3_000

# Real listing detail URLs always contain this segment. Deleted ads 301
# to a category page like /s-wohnung-mieten/<location>/<cat-loc-id>; the
# category page returns 200 so a status check alone misses it.
LISTING_URL_MARKER = "/s-anzeige/"

# Extracts the bracketed HTTP status code from kleinanzeigen's server-
# error template heading ("Fehler [400]"). The brackets are part of the
# rendered template — not a regex artifact. FlatPilot-b13.
SERVER_ERROR_CODE_RE = re.compile(r"Fehler\s*\[(\d+)\]")


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
                if response.status in (404, 410):
                    raise ListingExpiredError(
                        f"{self.platform}: listing returned HTTP {response.status} "
                        f"({listing_url})"
                    )
                if response.status >= 400:
                    raise FormNotFoundError(
                        f"{self.platform}: listing returned HTTP {response.status} "
                        f"({listing_url})"
                    )

            self._guard_login(pg)
            if LISTING_URL_MARKER not in (pg.url or ""):
                raise ListingExpiredError(
                    f"{self.platform}: listing no longer at {listing_url} — "
                    f"redirected to {pg.url}"
                )
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
                        f"(no input[type=file] under form#viewad-contact-form). "
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
                submitted = self._verify_submitted(pg, contact_url, listing_url)

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
        # Inline form is visible by default for authenticated users, so
        # the early-exit below covers the common path. The trigger
        # fallback survives for DOM variants where the inline form is
        # not pre-rendered; if the trigger reveals the modal form
        # instead of the inline one, the wait_for below times out and
        # we surface FormNotFoundError loudly.
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

    def _verify_submitted(self, pg: Any, contact_url: str, listing_url: str) -> bool:
        # Kleinanzeigen submits via XHR to /s-anbieter-kontaktieren.json
        # so the form URL never changes on the happy path. Wait for either
        # the success or error indicator to become visible; treat a timeout
        # as a verify failure too — silence after a click is not success.
        # When the submit POST is rejected at the HTTP layer, the page
        # navigates to a full-page server-error template ("Fehler [400]");
        # neither banner under the form renders because the form is gone.
        # FlatPilot-b13: probe for that template so the failure surfaces
        # the status code instead of the misleading "neither indicator"
        # message.
        success = pg.locator(SELECTORS.success_marker).first
        error = pg.locator(SELECTORS.error_marker).first
        try:
            success.wait_for(state="visible", timeout=SUBMIT_NAV_WAIT_MS)
            return True
        except PlaywrightTimeoutError:
            pass
        if error.is_visible():
            self._capture_failure_screenshot(pg, listing_url)
            raise SubmitVerificationError(
                f"{self.platform}: submit failed — error banner visible at {contact_url}"
            )
        code = self._detect_server_error_code(pg)
        if code is not None:
            self._capture_failure_screenshot(pg, listing_url)
            raise SubmitVerificationError(
                f"{self.platform}: submit rejected with HTTP {code} server-error "
                f"page at {pg.url} (listing {contact_url})"
            )
        self._capture_failure_screenshot(pg, listing_url)
        raise SubmitVerificationError(
            f"{self.platform}: neither success nor error indicator appeared "
            f"within {SUBMIT_NAV_WAIT_MS}ms after submit at {contact_url}"
        )

    def _detect_server_error_code(self, pg: Any) -> str | None:
        # Text-regex locator (`text=/Fehler\s*\[\d+\]/`) is the durable
        # signal: the template heading does not have a stable class hook.
        # Any failure to query / read is swallowed so it can never mask
        # the downstream SubmitVerificationError — the diagnostic improvement
        # is best-effort, like the failure-screenshot helper.
        try:
            marker = pg.locator(SELECTORS.server_error_marker).first
            if marker.count() == 0:
                return None
            text = marker.inner_text()
        except Exception as exc:
            logger.debug(
                "%s: server-error marker probe failed: %s", self.platform, exc
            )
            return None
        match = SERVER_ERROR_CODE_RE.search(text or "")
        return match.group(1) if match else None

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

    def _capture_failure_screenshot(self, pg: Any, listing_url: str) -> Path | None:
        # Best-effort post-mortem aid for SubmitVerificationError. Mirrors
        # the wg-gesucht filler's helper (FlatPilot-8kt). Reads
        # FAILURE_SCREENSHOTS_DIR from the config module each call so test
        # monkeypatches of APP_DIR are honoured. Any exception here is
        # logged and swallowed so the original SubmitVerificationError —
        # the thing the caller actually needs — is never masked.
        try:
            from flatpilot.config import FAILURE_SCREENSHOTS_DIR

            target_dir = FAILURE_SCREENSHOTS_DIR / self.platform
            target_dir.mkdir(parents=True, exist_ok=True)
            slug = listing_url.rstrip("/").split("/")[-1] or "listing"
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            path = target_dir / f"{slug}-{ts}.png"
            pg.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            logger.warning(
                "%s: failure-screenshot capture failed: %s", self.platform, exc
            )
            return None
        logger.info("%s: failure screenshot saved to %s", self.platform, path)
        return path


__all__ = ["HOST", "LOGIN_URL_FRAGMENTS", "SELECTORS", "KleinanzeigenFiller"]
