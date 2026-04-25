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

from flatpilot.fillers import register
from flatpilot.fillers.base import (
    FillError,
    FillReport,
    FormNotFoundError,
    NotAuthenticatedError,
    SelectorMissingError,
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
                submit_btn.click()
                # Give the page a moment to navigate. WG-Gesucht's
                # messenger redirects to /nachrichten/<thread> on
                # success; on validation failure it stays on the form
                # URL and renders an inline error banner.
                with contextlib.suppress(Exception):
                    pg.wait_for_load_state("networkidle", timeout=SUBMIT_NAV_WAIT_MS)
                if FORM_URL_SEGMENT in (pg.url or ""):
                    raise FillError(
                        f"{self.platform}: submit did not navigate away from the "
                        f"form URL ({pg.url}) — message likely rejected by validation"
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
            raise FormNotFoundError(
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


__all__ = ["HOST", "SELECTORS", "WGGesuchtFiller"]
