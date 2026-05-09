"""Unit tests for the WG-Gesucht filler refactor.

We don't drive a real browser here — Playwright is mocked. The point is
to lock the contract: ``fill(submit=False)`` does not click the submit
selector; ``fill(submit=True)`` does, and updates ``FillReport.submitted``.

URL ordering invariant: the click is what drives the URL transition. The
fixture starts ``page.url`` on the form URL. In the success test, the
submit locator's ``click_handler`` flips ``page.url`` to the post-submit
URL so the URL guard sees a navigated page. In the "stayed on form" test
no handler is wired, so URL stays on the form URL and the guard raises.
This means a regression that checks the URL *before* the click fires
will see the form URL and raise — the success test catches that.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from flatpilot.fillers.base import ListingExpiredError, SubmitVerificationError
from flatpilot.fillers.wg_gesucht import (
    SELECTORS,
    SUBMIT_ADVISORY_SELECTORS,
    WGGesuchtFiller,
)

_FORM_URL = "https://www.wg-gesucht.de/nachricht-senden/listing-123.html"
_POST_SUBMIT_URL = "https://www.wg-gesucht.de/nachrichten/inbox.html"


class _Locator:
    """Minimal stand-in for a Playwright Locator used by the filler."""

    def __init__(
        self,
        *,
        count: int = 1,
        href: str | None = None,
        click_handler=None,
        is_visible: bool | None = None,
    ) -> None:
        self._count = count
        self._href = href
        self.click_handler = click_handler
        self._is_visible = is_visible
        self.fill_calls: list[str] = []
        self.set_files_calls: list[list[str]] = []
        self.click_calls: int = 0
        self.wait_calls: list[dict] = []

    @property
    def first(self) -> _Locator:
        return self

    def count(self) -> int:
        return self._count

    def get_attribute(self, name: str) -> str | None:
        if name == "href":
            return self._href
        return None

    def fill(self, value: str) -> None:
        self.fill_calls.append(value)

    def set_input_files(self, paths: list[str]) -> None:
        self.set_files_calls.append(paths)

    def click(self, **kwargs) -> None:
        self.click_calls += 1
        if self.click_handler is not None:
            self.click_handler()

    def is_visible(self, timeout: int | None = None) -> bool:
        if self._is_visible is None:
            return self._count > 0
        return self._is_visible

    def wait_for(self, **kwargs) -> None:
        self.wait_calls.append(kwargs)

    def screenshot(self, **kwargs) -> None:
        pass


class _FakePage:
    """Stand-in for a Playwright Page that lets the filler walk its flow."""

    def __init__(
        self,
        *,
        on_form_already_visible: bool = True,
    ) -> None:
        # Start on the form URL so the URL guard is meaningful.
        self.url = _FORM_URL
        self._goto_calls: list[str] = []
        self._locators: dict[str, _Locator] = {
            SELECTORS.form: _Locator(count=1 if on_form_already_visible else 0),
            SELECTORS.message_input: _Locator(),
            SELECTORS.file_input: _Locator(),
            SELECTORS.submit_button: _Locator(),
        }
        self.submit_locator = self._locators[SELECTORS.submit_button]
        self.message_locator = self._locators[SELECTORS.message_input]
        self.screenshot_calls: list[dict] = []

    def goto(self, url: str, **kwargs):
        self._goto_calls.append(url)
        response = MagicMock()
        response.status = 200
        return response

    def locator(self, selector: str) -> _Locator:
        if selector in self._locators:
            return self._locators[selector]
        return _Locator(count=0)

    def screenshot(self, **kwargs) -> None:
        # Record kwargs so failure-path tests can assert path/full_page.
        self.screenshot_calls.append(dict(kwargs))

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

    monkeypatch.setattr("flatpilot.fillers.wg_gesucht.polite_session", fake_polite_session)
    monkeypatch.setattr("flatpilot.fillers.wg_gesucht.session_page", fake_session_page)
    return page


def test_fill_dry_run_does_not_click_submit(fake_session):
    filler = WGGesuchtFiller()
    report = filler.fill(
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        message="Hallo, ich bin interessiert.",
        attachments=[],
        submit=False,
    )

    assert fake_session.message_locator.fill_calls == ["Hallo, ich bin interessiert."]
    assert fake_session.submit_locator.click_calls == 0
    assert report.submitted is False
    assert report.message_sent == "Hallo, ich bin interessiert."


def test_fill_with_submit_true_clicks_submit_and_marks_submitted(fake_session):
    # Wire click_handler so the click — not goto — drives the URL transition.
    fake_session.submit_locator.click_handler = lambda: setattr(
        fake_session, "url", _POST_SUBMIT_URL
    )

    filler = WGGesuchtFiller()
    report = filler.fill(
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        message="Hallo, ich bin interessiert.",
        attachments=[],
        submit=True,
    )

    assert fake_session.submit_locator.click_calls == 1
    assert report.submitted is True


def test_fill_submit_raises_when_url_stayed_on_form(fake_session):
    # No click_handler wired — page URL stays on the form URL after click.
    filler = WGGesuchtFiller()
    with pytest.raises(SubmitVerificationError, match="submit did not navigate"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="Hallo, ich bin interessiert.",
            attachments=[],
            submit=True,
        )


def test_fill_message_must_be_non_empty(fake_session):
    filler = WGGesuchtFiller()
    with pytest.raises(ValueError, match="message must be non-empty"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="   ",
            attachments=[],
            submit=False,
        )


def test_fill_attachment_must_exist(tmp_path, fake_session):
    filler = WGGesuchtFiller()
    missing = tmp_path / "nope.pdf"
    with pytest.raises(FileNotFoundError):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="Hallo.",
            attachments=[missing],
            submit=False,
        )


def test_fill_dismisses_advisory_modal_before_submit(fake_session):
    # FlatPilot-k17: a #sec_advice Bootstrap modal can intercept submit
    # clicks. If a dismiss target is visible at submit time, the filler
    # should click it pre-emptively so the submit click is not blocked.
    advisory = _Locator(count=1, is_visible=True)
    fake_session._locators[SUBMIT_ADVISORY_SELECTORS[0]] = advisory
    fake_session.submit_locator.click_handler = lambda: setattr(
        fake_session, "url", _POST_SUBMIT_URL
    )

    filler = WGGesuchtFiller()
    report = filler.fill(
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        message="Hallo, ich bin interessiert.",
        attachments=[],
        submit=True,
    )

    assert advisory.click_calls == 1
    assert fake_session.submit_locator.click_calls == 1
    assert report.submitted is True


def test_fill_skips_dismissal_when_no_advisory_visible(fake_session):
    # If no advisory targets are visible, no dismissal click should fire —
    # the pre-emptive sweep is a cheap no-op on the common path.
    advisory = _Locator(count=1, is_visible=False)
    fake_session._locators[SUBMIT_ADVISORY_SELECTORS[0]] = advisory
    fake_session.submit_locator.click_handler = lambda: setattr(
        fake_session, "url", _POST_SUBMIT_URL
    )

    filler = WGGesuchtFiller()
    filler.fill(
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        message="Hallo.",
        attachments=[],
        submit=True,
    )

    assert advisory.click_calls == 0
    assert fake_session.submit_locator.click_calls == 1


def test_fill_recovers_when_modal_blocks_first_submit(fake_session):
    # Pre-emptive dismissal is best-effort; if the modal opens in
    # response to the click itself, the first submit times out. The
    # filler should then dismiss and retry once.
    advisory = _Locator(count=1, is_visible=True)
    fake_session._locators[SUBMIT_ADVISORY_SELECTORS[0]] = advisory

    submit_calls = {"n": 0}

    def submit_handler():
        submit_calls["n"] += 1
        if submit_calls["n"] == 1:
            raise PlaywrightTimeoutError("intercepted by modal")
        fake_session.url = _POST_SUBMIT_URL

    fake_session.submit_locator.click_handler = submit_handler

    filler = WGGesuchtFiller()
    report = filler.fill(
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        message="Hallo.",
        attachments=[],
        submit=True,
    )

    assert fake_session.submit_locator.click_calls == 2
    assert advisory.click_calls >= 1
    assert report.submitted is True


def test_fill_submit_timeout_without_advisory_raises_submit_verification(fake_session):
    # The headline silent-failure bug from FlatPilot-k17: a Playwright
    # TimeoutError from .click() must surface as SubmitVerificationError
    # so apply_to_flat's `except FillError` path records a failed row
    # and auto_apply._try_flat continues to the next candidate.
    advisory = _Locator(count=1, is_visible=False)
    fake_session._locators[SUBMIT_ADVISORY_SELECTORS[0]] = advisory

    def submit_handler():
        raise PlaywrightTimeoutError("locator click timed out")

    fake_session.submit_locator.click_handler = submit_handler

    filler = WGGesuchtFiller()
    with pytest.raises(SubmitVerificationError, match="overlay|timed out"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="Hallo.",
            attachments=[],
            submit=True,
        )

    assert fake_session.submit_locator.click_calls == 1


def test_fill_raises_listing_expired_when_contact_cta_missing(monkeypatch):
    # FlatPilot-tgw: WG-Gesucht serves a 200 page with no "Nachricht senden"
    # anchor when a listing is no longer accepting messages (deactivated /
    # rented / paused). The previous behaviour raised FormNotFoundError,
    # which the orchestrator recorded as a real failure, locking the
    # platform under a 120s cooldown. We now classify as ListingExpiredError
    # so the orchestrator can route it to the auto_skipped path.
    page = _FakePage(on_form_already_visible=False)
    # No contact_cta locator → count == 0 path in _reveal_contact_form.
    monkeypatch.setattr("flatpilot.fillers.wg_gesucht.polite_session", lambda c: _Ctx())
    monkeypatch.setattr("flatpilot.fillers.wg_gesucht.session_page", lambda c: _PageCtx(page))

    filler = WGGesuchtFiller()
    with pytest.raises(ListingExpiredError, match="contact CTA"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/wohnungen-in-Berlin-Britz.6194073.html",
            message="Hallo.",
            attachments=[],
            submit=False,
        )


@pytest.mark.parametrize("status", [404, 410])
def test_fill_raises_listing_expired_on_4xx_gone_status(fake_session, status):
    def goto_with_status(url, **kwargs):
        fake_session._goto_calls.append(url)
        response = MagicMock()
        response.status = status
        return response

    fake_session.goto = goto_with_status  # type: ignore[method-assign]

    filler = WGGesuchtFiller()
    with pytest.raises(ListingExpiredError, match=str(status)):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/wohnungen-in-Berlin-Britz.6194073.html",
            message="Hallo.",
            attachments=[],
            submit=False,
        )


def test_fill_submit_timeout_after_dismissal_raises_submit_verification(fake_session):
    # If dismissal succeeded but the retry click also times out, raise
    # SubmitVerificationError rather than letting PlaywrightTimeoutError
    # bubble to the apply pipeline as a generic Exception.
    advisory = _Locator(count=1, is_visible=True)
    fake_session._locators[SUBMIT_ADVISORY_SELECTORS[0]] = advisory

    def submit_handler():
        raise PlaywrightTimeoutError("still blocked")

    fake_session.submit_locator.click_handler = submit_handler

    filler = WGGesuchtFiller()
    with pytest.raises(SubmitVerificationError, match="even after dismissing"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="Hallo.",
            attachments=[],
            submit=True,
        )

    assert fake_session.submit_locator.click_calls == 2


# -------- failure-screenshot path (FlatPilot-8kt) --------

def test_submit_stayed_on_form_writes_failure_screenshot(tmp_db, fake_session):
    # tmp_db redirects config.FAILURE_SCREENSHOTS_DIR to tmp_path. With no
    # click_handler wired the URL stays on the form URL after submit; the
    # filler must capture a screenshot to <APP_DIR>/screenshots/wg-gesucht/
    # before raising SubmitVerificationError.
    from flatpilot import config

    filler = WGGesuchtFiller()
    with pytest.raises(SubmitVerificationError, match="submit did not navigate"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="Hallo.",
            attachments=[],
            submit=True,
        )

    assert len(fake_session.screenshot_calls) == 1
    captured_path = Path(fake_session.screenshot_calls[0]["path"])
    expected_dir = config.FAILURE_SCREENSHOTS_DIR / "wg-gesucht"
    assert captured_path.parent == expected_dir
    assert captured_path.name.startswith("123.html-")
    assert captured_path.suffix == ".png"
    assert fake_session.screenshot_calls[0].get("full_page") is True
    # Directory is created as a side effect.
    assert expected_dir.is_dir()


def test_failure_screenshot_exception_does_not_mask_submit_error(tmp_db, fake_session):
    # Instrumentation must be best-effort. If pg.screenshot raises, the
    # filler still raises SubmitVerificationError — the screenshot is
    # secondary; the original failure is what callers act on.
    def boom(**_kwargs):
        raise RuntimeError("disk full")

    fake_session.screenshot = boom

    filler = WGGesuchtFiller()
    with pytest.raises(SubmitVerificationError, match="submit did not navigate"):
        filler.fill(
            listing_url="https://www.wg-gesucht.de/listing/123.html",
            message="Hallo.",
            attachments=[],
            submit=True,
        )
