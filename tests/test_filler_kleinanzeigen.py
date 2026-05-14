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

from flatpilot.fillers import get_filler
from flatpilot.fillers.base import (
    ListingExpiredError,
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
        text: str = "",
    ) -> None:
        self._count = count
        self._visible = visible
        self.click_handler = click_handler
        self._text = text
        self.fill_calls: list[str] = []
        self.set_files_calls: list[list[str]] = []
        self.click_calls: int = 0

    @property
    def first(self) -> _Locator:
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

    def inner_text(self) -> str:
        return self._text

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
        self.screenshot_calls: list[dict] = []

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


def test_fill_submit_surfaces_status_code_when_server_error_page_visible(fake_session):
    # FlatPilot-b13: when the submit POST is rejected with HTTP 4xx/5xx,
    # kleinanzeigen navigates to a full-page error template — neither
    # success_marker nor error_marker render under the (now-gone) form.
    # The filler must detect the "Fehler [<code>]" heading and surface
    # the status code in SubmitVerificationError, replacing the prior
    # generic "neither indicator" message that hid the failure mode.
    server_error = _Locator(count=1, text="Fehler [400]")
    fake_session._locators[SELECTORS.server_error_marker] = server_error
    fake_session.submit_locator.click_handler = lambda: setattr(
        fake_session, "url", "https://www.kleinanzeigen.de/error"
    )

    filler = KleinanzeigenFiller()
    with pytest.raises(SubmitVerificationError, match="HTTP 400 server-error"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=True,
        )


def test_server_error_marker_without_recognizable_code_falls_through(fake_session):
    # Defensive: if kleinanzeigen restyles the error page so the text
    # matches our locator but the bracketed status code is missing
    # (e.g. layout change drops the brackets), we must fall through to
    # the generic "neither indicator" branch rather than raising with
    # a confusing "HTTP None" or crashing on .group() of a None match.
    marker = _Locator(count=1, text="Fehler — Seite nicht erreichbar")
    fake_session._locators[SELECTORS.server_error_marker] = marker

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


def test_fill_raises_listing_expired_when_redirected_to_category(fake_session):
    # FlatPilot-tgw: a deleted listing redirects to the category page
    # (e.g. /s-wohnung-mieten/neukoelln/c203l3386). The page returns 200
    # so response.status checks miss it; the inline contact form is also
    # not on the category page. Detect via the URL pattern: real listing
    # URLs contain '/s-anzeige/'.
    fake_session.url = (
        "https://www.kleinanzeigen.de/s-wohnung-mieten/neukoelln/c203l3386"
    )

    filler = KleinanzeigenFiller()
    with pytest.raises(ListingExpiredError, match="listing no longer at"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=False,
        )


@pytest.mark.parametrize("status", [404, 410])
def test_fill_raises_listing_expired_on_4xx_gone_status(fake_session, status):
    # If the platform returns 404 / 410 for the listing URL, the listing
    # is gone — classify as expired so the orchestrator does not record
    # it as a real failure that triggers a platform cooldown.
    def goto_with_status(url, **kwargs):
        fake_session._goto_calls.append(url)
        response = MagicMock()
        response.status = status
        return response

    fake_session.goto = goto_with_status  # type: ignore[method-assign]

    filler = KleinanzeigenFiller()
    with pytest.raises(ListingExpiredError, match=str(status)):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=False,
        )


def test_kleinanzeigen_filler_is_registered():
    # Locks the registry seed against a future "tidy the imports" pass
    # that drops `import flatpilot.fillers.kleinanzeigen` from
    # apply.py. Without that import the registry is empty for
    # kleinanzeigen and `flatpilot apply` on a Kleinanzeigen flat
    # raises KeyError — the original bug closed by this PR.
    assert get_filler("kleinanzeigen") is KleinanzeigenFiller


# -------- failure-screenshot path (FlatPilot-8kt followup) --------

def test_neither_marker_failure_writes_failure_screenshot(tmp_db, fake_session):
    # Default fake_session: success/error markers present but not visible →
    # _verify_submitted's wait_for(visible) times out and the helper raises.
    # The new instrumentation must capture a screenshot to
    # FAILURE_SCREENSHOTS_DIR/kleinanzeigen/<slug>-<isots>.png before raising.
    from pathlib import Path

    from flatpilot import config

    filler = KleinanzeigenFiller()
    with pytest.raises(SubmitVerificationError, match="neither success nor error"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=True,
        )

    assert len(fake_session.screenshot_calls) == 1
    captured_path = Path(fake_session.screenshot_calls[0]["path"])
    expected_dir = config.FAILURE_SCREENSHOTS_DIR / "kleinanzeigen"
    assert captured_path.parent == expected_dir
    assert captured_path.name.startswith("9999-203-0001-")
    assert captured_path.suffix == ".png"
    assert fake_session.screenshot_calls[0].get("full_page") is True


def test_error_banner_failure_writes_failure_screenshot(tmp_db, fake_session):
    # Error banner becomes visible synchronously: wait_for(visible) on the
    # success marker still times out, then the error_marker.is_visible()
    # check fires the SubmitVerificationError. Both raise sites must capture
    # a screenshot for post-mortem.
    fake_session.error_locator._visible = True

    filler = KleinanzeigenFiller()
    with pytest.raises(SubmitVerificationError, match="error banner visible"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=True,
        )

    assert len(fake_session.screenshot_calls) == 1


def test_failure_screenshot_exception_does_not_mask_submit_error(tmp_db, fake_session):
    # Instrumentation must be best-effort. If pg.screenshot raises, the
    # filler still raises SubmitVerificationError.
    def boom(**_kwargs):
        raise RuntimeError("disk full")

    fake_session.screenshot = boom

    filler = KleinanzeigenFiller()
    with pytest.raises(SubmitVerificationError, match="neither success nor error"):
        filler.fill(
            listing_url=_LISTING_URL,
            message="Hallo.",
            attachments=[],
            submit=True,
        )
