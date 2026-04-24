"""Unit tests for the stealth knob on SessionConfig.

The stealth option layers two hand-rolled tweaks on top of the existing
session: a launch arg that prevents Chromium from announcing it's
automation-controlled, plus an init script that deletes
navigator.webdriver before any page script sees it. We do not pull
playwright-stealth — clean-room policy plus supply-chain aversion.

The tests drive the config via a Playwright stub that records the
arguments each call receives, so we can assert on the exact shape of
the launch + context + init-script calls without starting a real
browser.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakePage:
    def __init__(self) -> None:
        self.default_timeout: int | None = None

    def set_default_navigation_timeout(self, ms: int) -> None:
        self.default_timeout = ms

    def goto(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def locator(self, *_args: Any, **_kwargs: Any) -> Any:
        class _Null:
            def is_visible(self, **_kw: Any) -> bool:
                return False

            first = property(lambda self: self)

        return _Null()

    def close(self) -> None:
        pass

    def content(self) -> str:
        return ""


class _FakeContext:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []
        self.closed = False
        self.saved_paths: list[str] = []

    def new_page(self) -> _FakePage:
        return _FakePage()

    def add_init_script(self, *, script: str) -> None:
        self.init_scripts.append(script)

    def storage_state(self, *, path: str) -> None:
        self.saved_paths.append(path)

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, launch_args: list[str]) -> None:
        self.launch_args = launch_args
        self.contexts: list[_FakeContext] = []
        self.closed = False

    def new_context(self, **_kwargs: Any) -> _FakeContext:
        ctx = _FakeContext()
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.browsers: list[_FakeBrowser] = []

    def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launches.append(kwargs)
        browser = _FakeBrowser(launch_args=list(kwargs.get("args", [])))
        self.browsers.append(browser)
        return browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()

    def __enter__(self) -> _FakePlaywright:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> _FakePlaywright:
    fake = _FakePlaywright()

    def _factory() -> _FakePlaywright:
        return fake

    # Patch the module so polite_session's lazy import picks up our stub.
    import playwright.sync_api as pw

    monkeypatch.setattr(pw, "sync_playwright", _factory)
    return fake


def test_stealth_defaults_to_off(tmp_db, fake_playwright: _FakePlaywright) -> None:
    from flatpilot.scrapers.session import SessionConfig, polite_session

    with polite_session(SessionConfig(platform="kleinanzeigen")):
        pass

    launch = fake_playwright.chromium.launches[-1]
    assert "--disable-blink-features=AutomationControlled" not in launch.get("args", [])
    # And no init script was injected on the default path.
    browser = fake_playwright.chromium.browsers[-1]
    assert browser.contexts[-1].init_scripts == []


def test_stealth_adds_automation_flag_and_init_script(
    tmp_db, fake_playwright: _FakePlaywright
) -> None:
    from flatpilot.scrapers.session import SessionConfig, polite_session

    with polite_session(SessionConfig(platform="kleinanzeigen", stealth=True)):
        pass

    launch = fake_playwright.chromium.launches[-1]
    assert "--disable-blink-features=AutomationControlled" in launch["args"]

    # The context saw one add_init_script call with a snippet that drops
    # navigator.webdriver.
    ctx = fake_playwright.chromium.browsers[-1].contexts[-1]
    assert len(ctx.init_scripts) == 1
    snippet = ctx.init_scripts[0]
    assert "navigator" in snippet
    assert "webdriver" in snippet


def test_stealth_preserves_caller_launch_args(
    tmp_db, fake_playwright: _FakePlaywright
) -> None:
    """Interactive flows already pass --start-maximized; stealth must append, not replace."""
    from flatpilot.scrapers.session import SessionConfig, polite_session

    cfg = SessionConfig(
        platform="kleinanzeigen",
        stealth=True,
        launch_args=("--start-maximized",),
    )
    with polite_session(cfg):
        pass

    launch = fake_playwright.chromium.launches[-1]
    assert "--start-maximized" in launch["args"]
    assert "--disable-blink-features=AutomationControlled" in launch["args"]
