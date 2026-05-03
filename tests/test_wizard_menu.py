"""Coverage for the saved-searches wizard menu loop."""
from __future__ import annotations

from io import StringIO

from rich.console import Console

from flatpilot.profile import Profile, SavedSearch
from flatpilot.wizard.init import _saved_searches_menu


def _capture_console():
    return Console(file=StringIO(), force_terminal=False, width=120)


def test_menu_done_immediately_returns_unchanged(monkeypatch):
    """Picking 'done' on the first prompt returns the profile unchanged."""
    profile = Profile.load_example()
    out = _capture_console()

    answers = iter(["done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(answers),
    )

    result = _saved_searches_menu(out, profile)
    assert result.saved_searches == profile.saved_searches


def test_menu_renders_existing_searches(monkeypatch):
    """Existing saved searches appear as numbered rows in the menu output."""
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [
            SavedSearch(name="auto-default", auto_apply=True),
            SavedSearch(name="kreuzberg-2br", auto_apply=True, platforms=["wg-gesucht"]),
        ]
    })
    out = _capture_console()

    answers = iter(["done"])
    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(answers),
    )

    _saved_searches_menu(out, profile)
    output_text = out.file.getvalue()
    assert "auto-default" in output_text
    assert "kreuzberg-2br" in output_text


def test_menu_empty_state_omits_edit_delete_choices(monkeypatch):
    """When list is empty, [e]dit/[d]elete should not be valid choices."""
    profile = Profile.load_example()  # no saved searches
    out = _capture_console()

    captured_choices: list = []
    def fake_ask(*a, choices=None, **kw):
        if choices is not None:
            captured_choices.append(list(choices))
        return "done"
    monkeypatch.setattr("flatpilot.wizard.init.Prompt.ask", fake_ask)

    _saved_searches_menu(out, profile)
    assert captured_choices, "menu should have prompted with explicit choices"
    first_choices = captured_choices[0]
    assert "edit" not in first_choices
    assert "delete" not in first_choices
    assert "add" in first_choices
    assert "caps" in first_choices
    assert "done" in first_choices
