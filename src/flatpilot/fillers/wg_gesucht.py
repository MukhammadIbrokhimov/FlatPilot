"""WG-Gesucht contact-form filler (dry-run only).

Navigates to a listing URL using the same polite Playwright session that
the scraper uses (so cookies, consent banner and fingerprint are
shared), opens the listing's contact form, fills the message body and
attaches files, then stops short of clicking submit. The actual submit
path will land with FlatPilot-cjtz (L4 apply command).

Selectors below are **provisional** — chosen from documented WG-Gesucht
form names but not yet validated against the live form. First real
dry-run that fails on a :class:`SelectorMissingError` is the signal to
update them. FlatPilot-fze tracks the empirical verification and
blocks L4.

Why click-to-navigate over guessing a contact-form URL: WG-Gesucht has
shipped multiple URL schemas for contact (``/nachricht-senden.<id>``,
``?ask=true`` query, in-page modal). Following the visible CTA lets
the filler survive schema changes the same way a human would.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from flatpilot.fillers import register
from flatpilot.fillers.base import (
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
# the first match wins. Provisional — see module docstring.
@dataclass(frozen=True)
class _Selectors:
    contact_cta: str = (
        "a:has-text('Nachricht senden'), "
        "button:has-text('Nachricht senden'), "
        "a:has-text('Nachricht schreiben'), "
        "button:has-text('Anfrage senden')"
    )
    form: str = (
        "form#contact_form, "
        "form[name='nachricht_form'], "
        "form#nachrichten-formular, "
        "form[action*='nachricht']"
    )
    subject_input: str = (
        "input[name='subject'], "
        "input#subject, "
        "input[name='betreff']"
    )
    message_input: str = (
        "textarea[name='message'], "
        "textarea#message, "
        "textarea[name='nachricht'], "
        "textarea#nachricht"
    )
    file_input: str = "input[type='file']"


SELECTORS = _Selectors()

# URL fragments that indicate the listing redirected us to a login wall.
# Checked against ``page.url`` after the navigation settles.
LOGIN_URL_FRAGMENTS: tuple[str, ...] = (
    "/login",
    "/anmelden",
    "/registrieren",
    "/sign-in",
)

DEFAULT_SUBJECT = "Anfrage zur Wohnung"
FORM_WAIT_MS = 5_000
FIELD_WAIT_MS = 3_000


@register
class WGGesuchtFiller:
    platform: ClassVar[str] = "wg-gesucht"
    user_agent: ClassVar[str] = DEFAULT_USER_AGENT

    def fill_dry_run(
        self,
        listing_url: str,
        message: str,
        attachments: list[Path],
        screenshot_dir: Path | None = None,
        *,
        subject: str | None = None,
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
            resolved_subject = subject or DEFAULT_SUBJECT
            if self._fill_optional(pg, SELECTORS.subject_input, resolved_subject):
                fields_filled["subject"] = resolved_subject

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

            screenshot_path = self._maybe_screenshot(pg, screenshot_dir, listing_url)

        return FillReport(
            platform=self.platform,
            listing_url=listing_url,
            contact_url=contact_url,
            fields_filled=fields_filled,
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=screenshot_path,
            submitted=False,
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
        # Some listings render the form inline for logged-in users; others
        # require clicking a CTA that opens a modal or navigates. Prefer the
        # already-rendered form, fall back to clicking the CTA.
        if pg.locator(SELECTORS.form).first.count() > 0:
            return

        cta = pg.locator(SELECTORS.contact_cta).first
        if cta.count() == 0:
            raise FormNotFoundError(
                f"{self.platform}: no contact form on page and no CTA matching "
                f"{SELECTORS.contact_cta!r} at {pg.url}"
            )
        cta.click()
        try:
            pg.locator(SELECTORS.form).first.wait_for(
                state="visible", timeout=FORM_WAIT_MS
            )
        except Exception as exc:
            self._guard_login(pg)
            raise FormNotFoundError(
                f"{self.platform}: clicked contact CTA but form selector "
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

    def _fill_optional(self, pg: Any, selector: str, value: str) -> bool:
        target = pg.locator(selector).first
        if target.count() == 0:
            return False
        target.fill(value)
        return True

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
