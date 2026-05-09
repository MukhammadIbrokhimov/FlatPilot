"""WG-Gesucht contact-form filler.

Navigates to a listing URL using the same polite Playwright session that
the scraper uses (so cookies, consent banner and fingerprint are
shared), follows the listing's "Nachricht senden" link to the
``/nachricht-senden/<slug>`` contact page, fills the message body and
attaches files. With ``submit=True`` it then clicks the form's submit
button and verifies the page navigated away from the form URL; with
``submit=False`` it stops at the filled form for preview / screenshot.

Selectors were verified empirically in FlatPilot-fze against the live
"messenger" form WG-Gesucht ships today (2026-04-22): both a
long-term Friedrichshain apartment and a short-term Treptow rental
render the same ``form#messenger_form`` with a single
``textarea#message_input`` (``name="content"``) and a hidden
``input#file_input``. The current form has no subject field; if
WG-Gesucht adds one back, thread a subject param through
:meth:`fill` at that point.

Why extract-href-and-goto over clicking the CTA: WG-Gesucht renders
three responsive copies of the "Nachricht senden" anchor (xs / sm / md
breakpoints) and only one is visible at any viewport size. Clicking
``.first`` in a headless session hits a hidden copy and times out.
Reading the ``href`` from any of the copies is equivalent and
deterministic — and still survives URL-schema changes, because the
href is whatever URL scheme WG-Gesucht ships today.
"""

from __future__ import annotations

import contextlib
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
    FormNotFoundError,  # noqa: F401 — re-exported for backwards compatibility
    ListingExpiredError,
    NotAuthenticatedError,
    SelectorMissingError,
    SubmitVerificationError,
)
from flatpilot.scrapers.session import (
    DEFAULT_USER_AGENT,
    SessionConfig,
    check_rate_limit,
    polite_session,
)
from flatpilot.scrapers.session import (
    page as session_page,
)
from flatpilot.scrapers.wg_gesucht import CONSENT_SELECTORS, HOST, WARMUP_URL

logger = logging.getLogger(__name__)


# Comma-separated CSS selectors are tried in order via ``locator(...).first``;
# the first match wins. Verified against the live form in FlatPilot-fze.
@dataclass(frozen=True)
class _Selectors:
    # The listing page renders three responsive copies of this anchor
    # (xs / sm / md breakpoints); we read the ``href`` off the first
    # match rather than clicking, to avoid visibility flakes.
    contact_cta: str = "a:has-text('Nachricht senden')"
    form: str = "form#messenger_form"
    message_input: str = (
        "textarea#message_input, "
        "textarea[name='content']"
    )
    file_input: str = (
        "input#file_input, "
        "input[type='file']"
    )
    # Both <button type=submit> and <input type=submit> are valid; we
    # match either inside the messenger form so a future React rewrite
    # that swaps the element type still hits.
    submit_button: str = (
        "form#messenger_form button[type='submit'], "
        "form#messenger_form input[type='submit']"
    )


SELECTORS = _Selectors()

# URL fragments that indicate the listing redirected us to a login wall.
# Checked against ``page.url`` after the navigation settles.
LOGIN_URL_FRAGMENTS: tuple[str, ...] = (
    "/login",
    "/anmelden",
    "/registrieren",
    "/sign-in",
)

FORM_URL_SEGMENT = "/nachricht-senden/"
SUBMIT_NAV_WAIT_MS = 5_000
FORM_WAIT_MS = 5_000
FIELD_WAIT_MS = 3_000
# Shorter than Playwright's 30s default so we recover from a #sec_advice
# intercept in ~10s + retry rather than ~30s + retry. FlatPilot-k17.
SUBMIT_CLICK_TIMEOUT_MS = 10_000
ADVISORY_VISIBLE_TIMEOUT_MS = 500

# WG-Gesucht's "security advice" Bootstrap modal — `<div id="sec_advice"
# class="modal fade ... in">…</div>` — can render above the messenger
# form on the first submit per session for newly-authenticated accounts
# and intercept pointer events on the submit button. Selectors are tried
# in order against ``locator(...).first``; the first visible match is
# clicked. FlatPilot-k17.
SUBMIT_ADVISORY_SELECTORS: tuple[str, ...] = (
    "#sec_advice button:has-text('Verstanden')",
    "#sec_advice .btn-primary",
    "#sec_advice [data-dismiss='modal']",
    "#sec_advice button.close",
)

# Positive signals that the post-submit success card has rendered. Either
# match → submission accepted by WG-Gesucht. Used by ``_verify_submitted``
# alongside the legacy URL-change check. FlatPilot-8kt: form-absence is
# unreliable because WG-Gesucht hides the form (display:none) rather than
# removing it from the DOM, so a positive marker on the success card is
# the durable detection point.
SUBMIT_SUCCESS_INDICATORS: tuple[str, ...] = (
    # Navigation link to the user's inbox; only rendered on the in-place
    # success page, not on the form itself. Structural and language-stable.
    "a:has-text('Nachrichten ansehen')",
    # German "successfully contacted" copy inside the success alert.
    # Scoped to .alert-success so unrelated success toasts elsewhere on
    # the page (e.g. "Notiz gespeichert") do not match.
    ".alert-success:has-text('erfolgreich kontaktiert')",
)


@register
class WGGesuchtFiller:
    platform: ClassVar[str] = "wg-gesucht"
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
            self._reveal_contact_form(pg)
            contact_url = pg.url

            fields_filled: dict[str, str] = {}

            self._fill_required(pg, SELECTORS.message_input, message, label="message")
            fields_filled["message"] = message

            if attachments:
                file_input = pg.locator(SELECTORS.file_input).first
                if file_input.count() == 0:
                    raise SelectorMissingError(
                        f"{self.platform}: no file input matching "
                        f"{SELECTORS.file_input!r} on {contact_url}"
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
                self._click_submit(pg, submit_btn, listing_url)
                with contextlib.suppress(PlaywrightTimeoutError):
                    pg.wait_for_load_state("networkidle", timeout=SUBMIT_NAV_WAIT_MS)
                if not self._verify_submitted(pg):
                    self._capture_failure_screenshot(pg, listing_url)
                    raise SubmitVerificationError(
                        f"{self.platform}: submit verification failed at "
                        f"{pg.url} — messenger form still present and URL "
                        f"unchanged, message likely rejected"
                    )
                submitted = True

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
        # The messenger form never renders inline on the listing page —
        # it lives at /nachricht-senden/<slug>, linked from a
        # "Nachricht senden" anchor. We read that href off the first
        # matching anchor (there are three responsive copies) and
        # navigate, rather than clicking, so visibility at any
        # particular headless viewport size cannot flake the flow.
        if pg.locator(SELECTORS.form).first.count() > 0:
            return

        cta = pg.locator(SELECTORS.contact_cta).first
        if cta.count() == 0:
            # WG-Gesucht serves a 200 page with no "Nachricht senden"
            # anchor when a listing is deactivated / rented / paused —
            # that's the dominant cause of this branch in practice.
            # Classify as ListingExpiredError so the orchestrator records
            # ``auto_skipped: listing_expired`` (no platform cooldown,
            # excluded from auto-apply for 7 days). The TTL exclusion
            # means a hypothetical selector regression on this filler
            # heals on its own once the selector is fixed, rather than
            # poisoning the database permanently.
            raise ListingExpiredError(
                f"{self.platform}: no contact CTA matching "
                f"{SELECTORS.contact_cta!r} at {pg.url}"
            )
        href = cta.get_attribute("href")
        if not href:
            raise FormNotFoundError(
                f"{self.platform}: contact CTA at {pg.url} has no href"
            )
        target = href if href.startswith("http") else f"{HOST}{href}"
        pg.goto(target, wait_until="domcontentloaded")

        self._guard_login(pg)

        try:
            pg.locator(SELECTORS.form).first.wait_for(
                state="visible", timeout=FORM_WAIT_MS
            )
        except Exception as exc:
            raise FormNotFoundError(
                f"{self.platform}: navigated to {pg.url} but form selector "
                f"{SELECTORS.form!r} never became visible"
            ) from exc

    def _click_submit(self, pg: Any, submit_btn: Any, listing_url: str) -> None:
        # FlatPilot-k17. The #sec_advice Bootstrap modal can intercept
        # the submit click — sometimes it is already rendered with the
        # `in` class at form load, sometimes it opens in response to the
        # click itself. Pre-emptive dismissal handles the former cheaply;
        # one dismiss-and-retry handles the latter. A still-blocked click
        # converts to SubmitVerificationError so apply_to_flat records a
        # `status='failed'` row instead of letting PlaywrightTimeoutError
        # bubble out as a generic exception that aborts the auto-apply
        # queue.
        self._dismiss_submit_advisory(pg)
        try:
            submit_btn.click(timeout=SUBMIT_CLICK_TIMEOUT_MS)
            return
        except PlaywrightTimeoutError as first_exc:
            if not self._dismiss_submit_advisory(pg):
                self._capture_failure_screenshot(pg, listing_url)
                raise SubmitVerificationError(
                    f"{self.platform}: submit click timed out at {pg.url} — "
                    f"likely intercepted by an overlay we could not dismiss"
                ) from first_exc
        try:
            submit_btn.click(timeout=SUBMIT_CLICK_TIMEOUT_MS)
        except PlaywrightTimeoutError as retry_exc:
            self._capture_failure_screenshot(pg, listing_url)
            raise SubmitVerificationError(
                f"{self.platform}: submit click timed out at {pg.url} "
                f"even after dismissing advisory modal"
            ) from retry_exc

    def _dismiss_submit_advisory(self, pg: Any) -> bool:
        for selector in SUBMIT_ADVISORY_SELECTORS:
            try:
                btn = pg.locator(selector).first
                if btn.is_visible(timeout=ADVISORY_VISIBLE_TIMEOUT_MS):
                    btn.click()
                    logger.info(
                        "%s: dismissed advisory modal via %s",
                        self.platform,
                        selector,
                    )
                    return True
            except Exception:
                continue
        return False

    def _fill_required(self, pg: Any, selector: str, value: str, *, label: str) -> None:
        target = pg.locator(selector).first
        try:
            target.wait_for(state="visible", timeout=FIELD_WAIT_MS)
        except Exception as exc:
            raise SelectorMissingError(
                f"{self.platform}: {label} field {selector!r} not visible at {pg.url}"
            ) from exc
        target.fill(value)

    def _maybe_screenshot(
        self,
        pg: Any,
        screenshot_dir: Path | None,
        listing_url: str,
    ) -> Path | None:
        if screenshot_dir is None:
            return None
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        # Stable per-listing filename so re-runs overwrite rather than pile up.
        # The trailing path segment of WG-Gesucht listing URLs is the asset
        # ID, which is unique enough.
        slug = listing_url.rstrip("/").split("/")[-1] or "listing"
        path = screenshot_dir / f"{self.platform}-{slug}.png"
        pg.screenshot(path=str(path), full_page=True)
        return path

    def _verify_submitted(self, pg: Any) -> bool:
        # Two equivalent success signals:
        #   1. URL navigated away from /nachricht-senden/<slug> — the legacy
        #      redirect path (still observed on some flows).
        #   2. A SUBMIT_SUCCESS_INDICATORS selector matches — the in-place
        #      success card rendered on the form URL (current path on flats
        #      29 / 41 / 616, 2026-05-09).
        # A real validation failure leaves the URL on /nachricht-senden/ AND
        # renders no success card. FlatPilot-8kt.
        if FORM_URL_SEGMENT not in (pg.url or ""):
            return True
        for selector in SUBMIT_SUCCESS_INDICATORS:
            try:
                if pg.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _capture_failure_screenshot(self, pg: Any, listing_url: str) -> Path | None:
        # Best-effort post-mortem aid for SubmitVerificationError. Reads
        # FAILURE_SCREENSHOTS_DIR from the config module each call so test
        # monkeypatches of APP_DIR are honoured. Any exception here is
        # logged and swallowed so the original SubmitVerificationError —
        # the thing the caller actually needs — is never masked. FlatPilot-8kt.
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


__all__ = ["HOST", "SELECTORS", "WGGesuchtFiller"]
