"""Unit tests for the WG-Gesucht filler refactor.

We don't drive a real browser here — Playwright is mocked. The point is
to lock the contract: ``fill(submit=False)`` does not click the submit
selector; ``fill(submit=True)`` does, and updates ``FillReport.submitted``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from flatpilot.fillers.base import FillError
from flatpilot.fillers.wg_gesucht import SELECTORS, WGGesuchtFiller


class _Locator:
    """Minimal stand-in for a Playwright Locator used by the filler."""

    def __init__(self, *, count: int = 1, href: str | None = None) -> None:
        self._count = count
        self._href = href
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

    def click(self) -> None:
        self.click_calls += 1

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
        post_submit_url: str | None = None,
    ) -> None:
        self.url = "https://www.wg-gesucht.de/listing/123.html"
        self._post_submit_url = post_submit_url
        self._goto_calls: list[str] = []
        self._locators: dict[str, _Locator] = {
            SELECTORS.form: _Locator(count=1 if on_form_already_visible else 0),
            SELECTORS.message_input: _Locator(),
            SELECTORS.file_input: _Locator(),
            SELECTORS.submit_button: _Locator(),
        }
        self.submit_locator = self._locators[SELECTORS.submit_button]
        self.message_locator = self._locators[SELECTORS.message_input]

    def goto(self, url: str, **kwargs):
        self._goto_calls.append(url)
        if self._post_submit_url:
            self.url = self._post_submit_url
        response = MagicMock()
        response.status = 200
        return response

    def locator(self, selector: str) -> _Locator:
        if selector in self._locators:
            return self._locators[selector]
        return _Locator(count=0)

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
    fake_session._post_submit_url = "https://www.wg-gesucht.de/nachrichten/inbox.html"

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
    # Page never navigates away — submit was effectively ignored.
    fake_session._post_submit_url = "https://www.wg-gesucht.de/nachricht-senden/123.html"
    fake_session.url = "https://www.wg-gesucht.de/nachricht-senden/123.html"

    filler = WGGesuchtFiller()
    with pytest.raises(FillError, match="submit did not navigate"):
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
