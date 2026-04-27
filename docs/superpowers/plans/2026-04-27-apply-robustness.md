# Phase 3 Apply Robustness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close two Phase 3 Apply gaps so `flatpilot apply` works on Kleinanzeigen flats and `flatpilot doctor` warns the user before per-platform sessions expire.

**Architecture:** Add a new per-platform filler at `src/flatpilot/fillers/kleinanzeigen.py` that mirrors `wg_gesucht.py` but adapts to Kleinanzeigen's modal-reveal contact form and async JSON submit. Extend `flatpilot doctor` with a JSON-only inspection of `~/.flatpilot/sessions/<platform>/state.json` per registered filler — no Chromium launch — that reports earliest-cookie expiry as `OK`, `optional` (yellow) within a 3-day warning window or when the file is missing/unreadable, and never affects the doctor exit code.

**Tech Stack:** Python 3.11+, Playwright (mocked in unit tests), pytest, rich, pydantic.

---

## Deliberate deviations from the bead text

The bead descriptions on `FlatPilot-1o2` and `FlatPilot-w5l` were written before the live DOM and the doctor performance budget were inspected. The plan deviates in two places:

1. **`FlatPilot-1o2`** — the bead lists "name / email / message fields, file-upload widget" as the form contents. The real Kleinanzeigen contact form (verified 2026-04-27 by `curl`-ing `https://www.kleinanzeigen.de/s-anzeige/.../<id>`) has `message` (textarea), `contactName` (input), `phoneNumber` (input), hidden `adId` / `adType`, and a submit button — **no email field, no file upload**. The filler fills `message` only (name/phone are auto-prefilled by Kleinanzeigen for logged-in users — same policy as WG-Gesucht's filler) and raises `SelectorMissingError` if the caller passes attachments. Submit verification waits for `.ajaxform-success` or `.outcomebox-error` to become visible (the form action is `/s-anbieter-kontaktieren.json`, an async XHR — the URL never changes after submit).

2. **`FlatPilot-w5l`** — the bead suggests using `polite_session()` to probe a per-platform canary URL. The plan uses JSON-only inspection of `state.json` instead. Reason: each `polite_session()` call is a ~5–10 s Chromium launch + warm-up; with two registered fillers that's 10–20 s added to a command users run multiple times a day. The doctor today is subsecond. Cookie-expiry from `state.json` answers the principal question (are credentials about to expire?) without that cost. If server-side cookie revocation turns out to be common in the wild, file a follow-up bead to add an opt-in `--probe-sessions` flag; YAGNI for now.

Both deviations are flagged in the PR body.

---

## File structure

| File | Status | Responsibility |
| --- | --- | --- |
| `src/flatpilot/fillers/kleinanzeigen.py` | **create** | `KleinanzeigenFiller` class: navigate to listing, click modal trigger, fill message, async-verify submit. |
| `tests/test_filler_kleinanzeigen.py` | **create** | Mock-Playwright unit tests mirroring `test_filler_submit.py`. |
| `src/flatpilot/apply.py` | **modify** | Add 1-line `import flatpilot.fillers.kleinanzeigen` so the registry seeds when `apply.py` is imported. |
| `src/flatpilot/doctor.py` | **modify** | Add `_check_platform_cookies(platform)` helper, `COOKIE_EXPIRY_WARN_DAYS` constant, top-of-file imports of every filler module so `all_fillers()` is non-empty, per-platform row appended in `run()`. |
| `tests/test_doctor.py` | **create** | Cover the new `_check_platform_cookies` paths and verify `run()` adds one row per registered filler. |

No other files change. No new dependencies.

---

## Task 1: Kleinanzeigen Apply filler — `FlatPilot-1o2`

**Files:**

- Create: `src/flatpilot/fillers/kleinanzeigen.py`
- Create: `tests/test_filler_kleinanzeigen.py`
- Modify: `src/flatpilot/apply.py` (add 1 import line directly below the existing `import flatpilot.fillers.wg_gesucht` line, currently `apply.py:35`)

**Why this filler is shaped the way it is** (re-stated for the implementer who is reading this task in isolation): the filler must implement the `Filler` Protocol from `src/flatpilot/fillers/base.py` — a `platform: ClassVar[str]` attribute and `fill(listing_url, message, attachments, *, submit, screenshot_dir=None) -> FillReport`. It must NOT log in. It must raise the most specific error class (`NotAuthenticatedError` on login redirect, `FormNotFoundError` if the contact CTA / form is unreachable, `SelectorMissingError` if a named field selector returns zero matches, `SubmitVerificationError` if submit clicked but verification failed). Apply orchestration (`apply.py`) instantiates it parameterless: `filler_cls()`. The wg-gesucht filler is the working reference — its tests live in `tests/test_filler_submit.py` and lock the dry-run / submit / verification contract with mocked Playwright.

### TDD

- [ ] **Step 1.1 — Write the failing test file at `tests/test_filler_kleinanzeigen.py`.**

Full file content (paste verbatim):

```python
"""Unit tests for the Kleinanzeigen filler.

Mirrors the WG-Gesucht filler tests' approach: Playwright is mocked, the
test fixture only exercises the filler's flow control (dry-run vs
submit, modal-trigger click, success / error / timeout verification,
attachment rejection). Selector accuracy is verified empirically in the
live-form follow-up bead, not here — same policy FlatPilot-fze
established for the WG-Gesucht filler.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from flatpilot.fillers.base import (
    NotAuthenticatedError,
    SelectorMissingError,
    SubmitVerificationError,
)
from flatpilot.fillers.kleinanzeigen import (
    SELECTORS,
    KleinanzeigenFiller,
)

_LISTING_URL = "https://www.kleinanzeigen.de/s-anzeige/example/9999-203-0001"


class _Locator:
    """Minimal stand-in for a Playwright Locator used by the filler."""

    def __init__(
        self,
        *,
        count: int = 1,
        visible: bool = True,
        click_handler=None,
    ) -> None:
        self._count = count
        self._visible = visible
        self.click_handler = click_handler
        self.fill_calls: list[str] = []
        self.set_files_calls: list[list[str]] = []
        self.click_calls: int = 0

    @property
    def first(self) -> "_Locator":
        return self

    def count(self) -> int:
        return self._count

    def is_visible(self, timeout: int | None = None) -> bool:
        return self._visible

    def fill(self, value: str) -> None:
        self.fill_calls.append(value)

    def set_input_files(self, paths: list[str]) -> None:
        self.set_files_calls.append(paths)

    def click(self) -> None:
        self.click_calls += 1
        if self.click_handler is not None:
            self.click_handler()

    def wait_for(self, *, state: str = "visible", timeout: int = 1000) -> None:
        if state == "visible" and not self._visible:
            raise PlaywrightTimeoutError(
                f"locator never became {state} within {timeout}ms"
            )

    def screenshot(self, **kwargs) -> None:
        pass


class _FakePage:
    """Stand-in for a Playwright Page that lets the filler walk its flow."""

    def __init__(self, *, file_input_count: int = 0) -> None:
        # Filler flow: goto(listing_url), guard_login (url check),
        # reveal_contact_form (click trigger -> form becomes visible),
        # fill message, optionally submit (success or error reveal).
        self.url = _LISTING_URL
        self._goto_calls: list[str] = []
        self._locators: dict[str, _Locator] = {
            SELECTORS.form: _Locator(visible=False),
            SELECTORS.contact_trigger: _Locator(),
            SELECTORS.message_input: _Locator(),
            SELECTORS.file_input: _Locator(count=file_input_count, visible=False),
            SELECTORS.submit_button: _Locator(),
            SELECTORS.success_marker: _Locator(visible=False),
            SELECTORS.error_marker: _Locator(visible=False),
        }
        # Default trigger: clicking it reveals the modal form.
        self._locators[SELECTORS.contact_trigger].click_handler = (
            lambda: setattr(self._locators[SELECTORS.form], "_visible", True)
        )
        # Convenient accessors for tests.
        self.form_locator = self._locators[SELECTORS.form]
        self.trigger_locator = self._locators[SELECTORS.contact_trigger]
        self.message_locator = self._locators[SELECTORS.message_input]
        self.submit_locator = self._locators[SELECTORS.submit_button]
        self.success_locator = self._locators[SELECTORS.success_marker]
        self.error_locator = self._locators[SELECTORS.error_marker]

    def goto(self, url: str, **kwargs):
        self._goto_calls.append(url)
        response = MagicMock()
        response.status = 200
        return response

    def locator(self, selector: str) -> _Locator:
        if selector in self._locators:
            return self._locators[selector]
        return _Locator(count=0, visible=False)

    def screenshot(self, **kwargs) -> None:
        pass

    def wait_for_load_state(self, *args, **kwargs) -> None:
        return None


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _PageCtx:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return self._page

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_session(monkeypatch):
    """Patch ``polite_session`` and ``session_page`` so no browser starts."""

    page = _FakePage()

    def fake_polite_session(config):
        return _Ctx()

    def fake_session_page(context):
        return _PageCtx(page)

    monkeypatch.setattr(
        "flatpilot.fillers.kleinanzeigen.polite_session", fake_polite_session
    )
    monkeypatch.setattr(
        "flatpilot.fillers.kleinanzeigen.session_page", fake_session_page
    )
    return page


def test_fill_dry_run_does_not_click_submit(fake_session):
    filler = KleinanzeigenFiller()
    report = filler.fill(
        listing_url=_LISTING_URL,
        message="Hallo, ich bin interessiert.",
        attachments=[],
        submit=False,
    )

    assert fake_session.message_locator.fill_calls == ["Hallo, ich bin interessiert."]
    assert fake_session.submit_locator.click_calls == 0
    assert report.submitted is False
    assert report.message_sent == "Hallo, ich bin interessiert."
    assert report.platform == "kleinanzeigen"


def test_fill_with_submit_true_clicks_submit_and_marks_submitted(fake_session):
    # Submit click reveals the success banner — the filler waits for
    # success_marker.is_visible to flip and records submitted=True.
    fake_session.submit_locator.click_handler = lambda: setattr(
        fake_session.success_locator, "_visible", True
    )

    filler = KleinanzeigenFiller()
    report = filler.fill(
        listing_url=_LISTING_URL,
        message="Hallo, ich bin interessiert.",
        attachments=[],
        submit=True,
    )

    assert fake_session.submit_locator.click_calls == 1
    assert report.submitted is True


def test_fill_submit_raises_when_error_banner_visible(fake_session):
    # Submit click reveals the error banner instead of success.
    fake_session.submit_locator.click_handler = lambda: setattr(
        fake_session.error_locator, "_visible", True
    )

    filler = KleinanzeigenFiller()
    with pytest.raises(SubmitVerificationError, match="error banner"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=True,
        )


def test_fill_submit_raises_when_neither_indicator_appears(fake_session):
    # No click_handler — neither success nor error appears within timeout.
    filler = KleinanzeigenFiller()
    with pytest.raises(SubmitVerificationError, match="neither success nor error"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=True,
        )


def test_fill_message_must_be_non_empty(fake_session):
    filler = KleinanzeigenFiller()
    with pytest.raises(ValueError, match="message must be non-empty"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="   ",
            attachments=[],
            submit=False,
        )


def test_fill_attachment_must_exist(tmp_path, fake_session):
    filler = KleinanzeigenFiller()
    missing = tmp_path / "nope.pdf"
    with pytest.raises(FileNotFoundError):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[missing],
            submit=False,
        )


def test_fill_with_attachments_raises_selector_missing(tmp_path, fake_session):
    # Kleinanzeigen contact form has no file-input field — passing
    # any attachment must raise SelectorMissingError so the user
    # discovers the platform mismatch.
    real_file = tmp_path / "schufa.pdf"
    real_file.write_text("dummy")
    # file_input_count defaults to 0 in _FakePage, simulating real DOM.
    filler = KleinanzeigenFiller()
    with pytest.raises(SelectorMissingError, match="does not support attachments"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[real_file],
            submit=False,
        )


def test_fill_login_redirect_raises_not_authenticated(fake_session):
    # Pretend the listing URL 302'd to /m-einloggen.html (the
    # Kleinanzeigen login page). _guard_login should fire.
    fake_session.url = "https://www.kleinanzeigen.de/m-einloggen.html?targetUrl=..."

    filler = KleinanzeigenFiller()
    with pytest.raises(NotAuthenticatedError, match="m-einloggen"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=False,
        )
```

- [ ] **Step 1.2 — Run the test file and verify every test fails with `ModuleNotFoundError: No module named 'flatpilot.fillers.kleinanzeigen'` (or an equivalent import-time failure).**

Run: `.venv/bin/pytest tests/test_filler_kleinanzeigen.py -v`

Expected: collection or import errors on every test, all of them tracing back to the missing module / class. **STOP and report if any test passes** — that means the file already exists or the imports somehow resolved, and the red-test integrity is broken.

- [ ] **Step 1.3 — Create the implementation at `src/flatpilot/fillers/kleinanzeigen.py`.**

Full file content (paste verbatim):

```python
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
```

- [ ] **Step 1.4 — Wire the filler into the apply registry.**

Open `src/flatpilot/apply.py`. Find the existing block (currently lines 33–35):

```python
# Force the filler registry to populate before apply_to_flat runs.
# init_db() handles the schemas import internally.
import flatpilot.fillers.wg_gesucht  # noqa: F401
```

Add a single line directly after `import flatpilot.fillers.wg_gesucht  # noqa: F401`:

```python
import flatpilot.fillers.kleinanzeigen  # noqa: F401
```

The block becomes:

```python
# Force the filler registry to populate before apply_to_flat runs.
# init_db() handles the schemas import internally.
import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.fillers.kleinanzeigen  # noqa: F401
```

No other changes to `apply.py`.

- [ ] **Step 1.5 — Run the new test file and verify every test passes.**

Run: `.venv/bin/pytest tests/test_filler_kleinanzeigen.py -v`

Expected: 8 passed.

- [ ] **Step 1.6 — Run the full test suite + ruff to confirm no regressions.**

Run: `.venv/bin/pytest -q`

Expected: all tests pass (existing suite + 8 new).

Run: `.venv/bin/ruff check src tests`

Expected: clean (no errors).

- [ ] **Step 1.7 — Commit Task 1.**

```bash
git add src/flatpilot/fillers/kleinanzeigen.py tests/test_filler_kleinanzeigen.py src/flatpilot/apply.py
git commit -m "FlatPilot-1o2: add Kleinanzeigen Apply filler"
```

Beads pre-commit hook will auto-stage `.beads/issues.jsonl`. Let it ride. Do not pass `--no-verify`.

---

## Task 2: Doctor per-platform cookie-expiry check — `FlatPilot-w5l`

**Files:**

- Modify: `src/flatpilot/doctor.py`
- Create: `tests/test_doctor.py`

**Why this check is JSON-only and lives where it does** (re-stated for the implementer): the doctor command is fast today (subsecond except for Playwright executable resolution). Launching a polite_session per platform to probe a canary URL would add a 5–10 s Chromium boot per platform — for a command users run often that cost is unjustified given the principal question (are credentials about to expire?) is answered by parsing `state.json`. The check appends one row per registered filler to the existing `rich.Table` in `doctor.run()`, with the status mapped into the existing `_STYLES` palette (`OK` green, `optional` yellow) — no new style states are added. Status is `optional` in every non-`OK` case (no session, expired, expiring soon, unreadable, session-only), so the doctor exit code is never affected by these rows; the user might intentionally not have logged into a platform yet and that's not a doctor failure.

The new check needs the filler registry seeded. Add top-of-file `import flatpilot.fillers.wg_gesucht` and `import flatpilot.fillers.kleinanzeigen` to `doctor.py`, mirroring the pattern in `apply.py` (lines 33–35 after Task 1). Don't move registration into `fillers/__init__.py` — that creates a circular-import risk and isn't how the scrapers package does it either.

### TDD

- [ ] **Step 2.1 — Write the failing test file at `tests/test_doctor.py`.**

Full file content (paste verbatim):

```python
"""Tests for the ``flatpilot doctor`` per-platform cookie check.

Pre-existing checks (Python version, app dir, Playwright, Telegram,
SMTP) have install-environment tails and aren't unit-tested here. The
new ``_check_platform_cookies`` helper, which only ever touches
``~/.flatpilot/sessions/<platform>/state.json`` (redirected by
``tmp_db``), is tractable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from rich.console import Console

from flatpilot import doctor
from flatpilot.scrapers.base import session_dir


def _write_state(platform: str, *, expires_unix: list[int | float]) -> None:
    """Drop a state.json with the given cookie expiry timestamps under tmp_db."""
    path = session_dir(platform) / "state.json"
    cookies = [
        {
            "name": f"c{i}",
            "value": "x",
            "expires": exp,
            "domain": ".example",
            "path": "/",
        }
        for i, exp in enumerate(expires_unix)
    ]
    path.write_text(json.dumps({"cookies": cookies, "origins": []}))


def test_check_platform_cookies_no_state_returns_optional(tmp_db):
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "no session" in detail
    assert "flatpilot login wg-gesucht" in detail


def test_check_platform_cookies_unreadable_returns_optional(tmp_db):
    path = session_dir("wg-gesucht") / "state.json"
    path.write_text("{not valid json")
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "unreadable" in detail


def test_check_platform_cookies_expired_returns_optional(tmp_db):
    past = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    _write_state("wg-gesucht", expires_unix=[past])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "EXPIRED" in detail


def test_check_platform_cookies_within_warn_window_returns_optional(tmp_db):
    # 1.5 days from now — inside the 3-day warning window.
    soon = (datetime.now(UTC) + timedelta(days=1, hours=12)).timestamp()
    _write_state("wg-gesucht", expires_unix=[soon])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "soon" in detail


def test_check_platform_cookies_fresh_returns_ok(tmp_db):
    future = (datetime.now(UTC) + timedelta(days=30)).timestamp()
    _write_state("wg-gesucht", expires_unix=[future])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "OK"
    assert "30d" in detail


def test_check_platform_cookies_session_only_cookies_returns_optional(tmp_db):
    # All cookies have expires == -1 (Playwright's marker for browser-
    # session cookies that don't persist across runs).
    _write_state("wg-gesucht", expires_unix=[-1, -1])
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "session-only" in detail


def test_check_platform_cookies_picks_earliest_expiry(tmp_db):
    """Multi-cookie state — doctor warns on the earliest expiry."""
    now = datetime.now(UTC)
    cookies = [
        (now + timedelta(days=30)).timestamp(),
        (now + timedelta(days=2)).timestamp(),
    ]
    _write_state("wg-gesucht", expires_unix=cookies)
    status, detail = doctor._check_platform_cookies("wg-gesucht")
    assert status == "optional"
    assert "soon" in detail


def test_run_includes_a_row_per_filler_platform(tmp_db, monkeypatch):
    # Replace static checks with an empty list so the test only
    # exercises the new per-platform iteration — the static checks
    # have install-time tails (playwright executable presence,
    # telegram env, smtp env) that flake in CI.
    monkeypatch.setattr(doctor, "CHECKS", [])
    console = Console(record=True, width=200, force_terminal=False)
    exit_code = doctor.run(console=console)
    output = console.export_text()
    # Per-platform rows are optional-only, so they must never push the
    # exit code from 0 to 1.
    assert exit_code == 0
    # Both fillers registered as of this PR — the iteration order is
    # alphabetic so kleinanzeigen comes before wg-gesucht.
    assert "Session: wg-gesucht" in output
    assert "Session: kleinanzeigen" in output
    assert "no session" in output
```

- [ ] **Step 2.2 — Run the test file and verify every test fails red.**

Run: `.venv/bin/pytest tests/test_doctor.py -v`

Expected: 8 failures total, two distinct failure modes:
- The seven `test_check_platform_cookies_*` tests fail with `AttributeError: module 'flatpilot.doctor' has no attribute '_check_platform_cookies'` (or `ImportError` raised at collection time, depending on how pytest binds the symbol).
- `test_run_includes_a_row_per_filler_platform` fails with `AssertionError` because the current `run()` only iterates `CHECKS` and emits no `Session: ...` rows — the test monkey-patches `CHECKS = []`, calls `run()`, and asserts the per-platform rows are present in the rendered output.

**STOP and report if any test passes** — that means the helper already exists or `run()` already emits per-platform rows, either of which breaks red-test integrity.

- [ ] **Step 2.3 — Modify `src/flatpilot/doctor.py` to add the helper, the constant, the registry-seed imports, and the per-platform iteration in `run()`.**

The file currently looks like (re-stated for the implementer):

```python
"""Health checks for ``flatpilot doctor``.

Each check is a function returning ``(status, detail)``. Status is one of
``"OK"``, ``"MISSING"`` (required check failed — affects exit code), or
``"optional"`` (nice to have, never fails the exit code).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.table import Table

from flatpilot import config
from flatpilot.profile import Profile, load_profile

CheckFn = Callable[[], "tuple[str, str]"]
```

Replace the imports block (lines 1–21) with:

```python
"""Health checks for ``flatpilot doctor``.

Each check is a function returning ``(status, detail)``. Status is one of
``"OK"``, ``"MISSING"`` (required check failed — affects exit code), or
``"optional"`` (nice to have, never fails the exit code).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from flatpilot import config

# Import every filler module so the registry is populated when doctor
# walks ``all_fillers()`` for per-platform cookie checks. Mirrors the
# pattern in apply.py (see apply.py:33–36). Do not move this into
# fillers/__init__.py — that creates a circular-import risk.
import flatpilot.fillers.kleinanzeigen  # noqa: F401, E402
import flatpilot.fillers.wg_gesucht  # noqa: F401, E402
from flatpilot.fillers import all_fillers
from flatpilot.profile import Profile, load_profile
from flatpilot.scrapers.base import session_dir

CheckFn = Callable[[], "tuple[str, str]"]

# Minimum days remaining before doctor flags an upcoming session expiry.
COOKIE_EXPIRY_WARN_DAYS: int = 3
```

Then add the new helper directly above the `CHECKS` list (i.e. between `_check_smtp` and `CHECKS = [ … ]`):

```python
def _check_platform_cookies(platform: str) -> tuple[str, str]:
    """Probe ``~/.flatpilot/sessions/<platform>/state.json`` for cookie freshness.

    JSON-only — does not launch a browser. Reports:

    - ``"OK"`` with "expires in Nd" when the earliest persistent cookie's
      expiry is more than :data:`COOKIE_EXPIRY_WARN_DAYS` days away.
    - ``"optional"`` (yellow) with "expires in Nd — re-run … soon" when
      within the warning window.
    - ``"optional"`` with "EXPIRED — run …" when the earliest expiry is
      already past.
    - ``"optional"`` with "no session — run … if you plan to apply" when
      the file does not exist.
    - ``"optional"`` with "session state unreadable" when the file
      exists but is not parseable JSON.
    - ``"optional"`` with "session-only cookies" when every cookie has
      ``expires == -1`` (Playwright's marker for browser-session cookies
      that don't persist across runs).

    Doctor must not crash on the thing it's diagnosing — every error
    path returns a tuple, never raises.
    """
    state_path = session_dir(platform) / "state.json"
    if not state_path.exists():
        return (
            "optional",
            f"no session — run `flatpilot login {platform}` if you plan to apply",
        )
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return "optional", "session state unreadable"

    cookies = state.get("cookies", []) if isinstance(state, dict) else []
    expiries = [
        c["expires"]
        for c in cookies
        if isinstance(c, dict)
        and isinstance(c.get("expires"), (int, float))
        and c["expires"] > 0
    ]
    if not expiries:
        return (
            "optional",
            f"session-only cookies — re-run `flatpilot login {platform}` if expired",
        )
    earliest = min(expiries)
    now = datetime.now(UTC).timestamp()
    days_remaining = (earliest - now) / 86_400.0
    if days_remaining <= 0:
        return "optional", f"EXPIRED — run `flatpilot login {platform}`"
    if days_remaining < COOKIE_EXPIRY_WARN_DAYS:
        return (
            "optional",
            f"expires in {days_remaining:.0f}d — re-run `flatpilot login {platform}` soon",
        )
    return "OK", f"expires in {days_remaining:.0f}d"
```

Then update the `run()` function to append per-platform rows. Replace the existing `run()` body (it currently iterates `CHECKS` only) with:

```python
def run(console: Console | None = None) -> int:
    """Run every check, print a summary table, return a CLI exit code.

    Returns 0 if every required check passed, 1 otherwise. Optional
    checks that come back missing do not affect the exit code — they're
    reminders. Per-platform cookie rows are always ``"optional"`` or
    ``"OK"``, so they never affect the exit code either; a user who
    hasn't logged into a platform yet is not a doctor failure.

    ``config.load_env()`` is called in the CLI ``_bootstrap`` callback
    so every command sees ``~/.flatpilot/.env`` — no need to repeat it
    here.
    """
    out = console or Console()
    table = Table(title="FlatPilot doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    exit_code = 0
    for name, check_fn in CHECKS:
        status, detail = check_fn()
        style = _STYLES[status]
        table.add_row(name, f"[{style}]{status}[/{style}]", detail)
        if status == "MISSING":
            exit_code = 1
    # Per-platform cookie rows. Sorted by platform string for stable
    # output. Status is always "optional" or "OK" — never fails the
    # exit code.
    for filler_cls in sorted(all_fillers(), key=lambda c: c.platform):
        platform = filler_cls.platform
        status, detail = _check_platform_cookies(platform)
        style = _STYLES[status]
        table.add_row(
            f"Session: {platform}",
            f"[{style}]{status}[/{style}]",
            detail,
        )
    out.print(table)
    return exit_code
```

- [ ] **Step 2.4 — Run the new test file and verify every test passes.**

Run: `.venv/bin/pytest tests/test_doctor.py -v`

Expected: 8 passed.

- [ ] **Step 2.5 — Run the full suite + ruff to confirm no regressions.**

Run: `.venv/bin/pytest -q`

Expected: all tests pass.

Run: `.venv/bin/ruff check src tests`

Expected: clean.

- [ ] **Step 2.6 — Smoke `flatpilot doctor` end-to-end manually.**

Run: `.venv/bin/python -m flatpilot doctor`

Expected: the existing 5 rows still appear, plus two new "Session: kleinanzeigen" and "Session: wg-gesucht" rows. If the developer has session state for either platform, the row reports OK / a warning / EXPIRED appropriately; if not, "no session — run `flatpilot login <platform>` if you plan to apply".

- [ ] **Step 2.7 — Commit Task 2.**

```bash
git add src/flatpilot/doctor.py tests/test_doctor.py
git commit -m "FlatPilot-w5l: add doctor per-platform cookie-expiry check"
```

Beads pre-commit hook auto-stages `.beads/issues.jsonl`.

---

## Final-stage cleanup (after both tasks)

- [ ] **Step F.1 — Run the full suite one last time + ruff.**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src tests`

Expected: all green.

- [ ] **Step F.2 — File a deferred follow-up bead for empirical Kleinanzeigen selector verification.**

Run:

```bash
bd create \
  --title="Empirically verify Kleinanzeigen contact-form selectors" \
  --type=task --priority=2 \
  --description="The Kleinanzeigen filler (FlatPilot-1o2) ships with provisional selectors transcribed from the unauthenticated DOM of a real Berlin listing on 2026-04-27 (form#viewad-contact-modal-form, button#viewad-contact-button-login-modal, textarea#viewad-contact-message, .ajaxform-success / .outcomebox-error). Authenticated DOM may differ — name / phone may be auto-prefilled, the trigger may behave differently. Resolution mirrors FlatPilot-fze: log into kleinanzeigen.de via polite_session in headed mode, navigate to a real listing's contact form, inspect the DOM, update SELECTORS in fillers/kleinanzeigen.py, re-run a dry-run until every field locates correctly. Blocks live submission to Kleinanzeigen with confidence."
bd dep add <new-id> FlatPilot-1o2
```

Replace `<new-id>` with the ID returned by `bd create`. The dependency declares the new bead is blocked by 1o2 (must merge first).

---

## Self-review (run before opening the PR)

**1. Spec coverage.** Walk each acceptance criterion:

- *FlatPilot-1o2 — `flatpilot apply <flat_id>` for a logged-in Kleinanzeigen profile fills the form (`--dry-run` shows preview, no submit).* ✓ Covered by `test_fill_dry_run_does_not_click_submit` (unit) plus the registry seed in `apply.py`.
- *FlatPilot-1o2 — non-dry-run successfully posts a contact message and writes status='submitted' to applications.* ✓ Filler returns `submitted=True` on the success path; `apply.py:163-181` writes the row. Verified in unit tests for the filler half; the apply-orchestrator-vs-filler integration is already covered by existing `tests/test_apply_orchestrator.py` (which exercises wg-gesucht; the registry-seed change makes kleinanzeigen reachable on the same code path).
- *FlatPilot-w5l — `flatpilot doctor` shows a row per registered platform with its session status.* ✓ `test_run_includes_a_row_per_filler_platform`.
- *FlatPilot-w5l — expiry warnings fire ≥3 days before actual expiry.* ✓ `COOKIE_EXPIRY_WARN_DAYS = 3`, `test_check_platform_cookies_within_warn_window_returns_optional`, `test_check_platform_cookies_picks_earliest_expiry`.

**2. Placeholder scan.** No "TBD", "similar to Task N", or "add appropriate error handling". Every code block is full and copy-paste-ready.

**3. Type consistency.** `KleinanzeigenFiller.platform == "kleinanzeigen"` matches the scraper's platform string and the `flats.platform` column convention (lowercase, no dash). `_check_platform_cookies` returns the same `tuple[str, str]` shape as every other check function. `_STYLES` is not extended — `optional` (yellow) carries every non-`OK` cookie state.

---

## Execution

Once the plan is saved, execute via `superpowers:subagent-driven-development` per the project's PR workflow:

- **Plan-doc commit first** (`FlatPilot-1o2: write implementation plan`) — single bead ID per the prior-PR convention.
- **Task 1 commit** — `FlatPilot-1o2: add Kleinanzeigen Apply filler`.
- **Task 2 commit** — `FlatPilot-w5l: add doctor per-platform cookie-expiry check`.
- **Final code-reviewer (opus)** over the full branch vs `origin/main` before pushing.
- **File deferred follow-up bead** (Step F.2) before opening the PR; depends-on `FlatPilot-1o2`.
- **PR body**: framing (preemptive vs reactive), explicit deviations from bead text (the two listed at the top of this plan), closes list (`Closes FlatPilot-1o2, FlatPilot-w5l`), commit grouping, behaviour-change call-outs, test plan checklist, deferred follow-ups, what's NOT in this PR (no `--probe-sessions` flag, no Profile phone field, no Kleinanzeigen empirical-selector verification).
