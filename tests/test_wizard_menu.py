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


def test_add_minimal_saved_search(monkeypatch):
    """Add flow with no overrides: 4 prompts (name, auto-apply, platforms, override-notif=no),
    then customize-filters=no."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "kreuzberg-2br", "wg-gesucht", "done"])
    # auto-apply=True, override notif=No, customize filters=No
    confirm_answers = iter([True, False, False])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    assert len(result.saved_searches) == 1
    ss = result.saved_searches[0]
    assert ss.name == "kreuzberg-2br"
    assert ss.auto_apply is True
    assert ss.platforms == ["wg-gesucht"]
    assert ss.notifications is None
    assert ss.rent_min_warm is None  # customize-filters=No left this unset


def test_add_with_telegram_override(monkeypatch):
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter([
        "add", "k-2br",
        "",  # platforms blank → all
        "K_BOT_TOKEN", "k_chat_id",  # telegram override values
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply
        True,   # override notifications?
        True,   # telegram have an opinion?
        True,   # telegram enabled?
        False,  # email have an opinion?
        False,  # customize filters?
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.notifications is not None
    assert ss.notifications.telegram is not None
    assert ss.notifications.telegram.enabled is True
    assert ss.notifications.telegram.bot_token_env == "K_BOT_TOKEN"
    assert ss.notifications.telegram.chat_id == "k_chat_id"
    assert ss.notifications.email is None


def test_add_with_explicit_email_suppress(monkeypatch):
    """User says 'have an opinion=yes, enabled=no' → enabled=False stored."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "x", "", "done"])
    confirm_answers = iter([
        True,    # auto-apply
        True,    # override notifications?
        False,   # telegram opinion
        True,    # email opinion
        False,   # email enabled?
        False,   # customize filters?
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.notifications is not None
    assert ss.notifications.telegram is None
    assert ss.notifications.email is not None
    assert ss.notifications.email.enabled is False


def test_add_no_opinion_collapses_to_none(monkeypatch):
    """All channels 'no opinion' → notifications stored as None (not empty block)."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "x", "", "done"])
    confirm_answers = iter([
        True,    # auto-apply
        True,    # override notifications? = yes
        False,   # telegram opinion = no
        False,   # email opinion = no
        False,   # customize filters?
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.notifications is None


def test_add_with_filter_overrides(monkeypatch):
    """customize-filters=yes walks all 8 overlay prompts."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter([
        "add", "x", "",
        # 6 int prompts (rent_min, rent_max, rooms_min, rooms_max, radius_km, min_contract_months)
        "800", "1500", "1", "3", "10", "",
        # district list (override=yes path)
        "kreuzberg, mitte",
        # furnished_pref (override=yes path)
        "any",
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply
        False,  # override notifications? = no
        True,   # customize filters? = yes
        True,   # override district allowlist? = yes
        True,   # override furnished pref? = yes
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.rent_min_warm == 800
    assert ss.rent_max_warm == 1500
    assert ss.rooms_min == 1
    assert ss.rooms_max == 3
    assert ss.radius_km == 10
    assert ss.min_contract_months is None
    assert ss.district_allowlist == ["kreuzberg", "mitte"]
    assert ss.furnished_pref == "any"


def test_add_district_override_blank_means_empty_list(monkeypatch):
    """override=yes + blank list input → [] (override-to-empty)."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter([
        "add", "x", "",
        "", "", "", "", "", "",  # 6 ints, all blank
        "",  # district list blank → []
        # furnished_pref override=no, so no prompt for value
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply
        False,  # override notifications? = no
        True,   # customize filters? = yes
        True,   # override district allowlist? = yes
        False,  # override furnished pref? = no
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.district_allowlist == []
    assert ss.furnished_pref is None


def test_add_invalid_name_reprompts(monkeypatch):
    """Invalid name pattern triggers a re-prompt loop."""
    profile = Profile.load_example()
    out = _capture_console()

    prompt_answers = iter(["add", "Bad Name!", "valid-name", "", "done"])
    confirm_answers = iter([False, False, False])  # auto-apply, override-notif, customize-filters

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    assert len(result.saved_searches) == 1
    assert result.saved_searches[0].name == "valid-name"


def test_edit_existing(monkeypatch):
    """Edit branch loads defaults from the existing search."""
    profile = Profile.load_example().model_copy(update={
        "saved_searches": [
            SavedSearch(name="kreuzberg-2br", auto_apply=False, platforms=["wg-gesucht"])
        ]
    })
    out = _capture_console()

    prompt_answers = iter([
        "edit", "1",  # picks first search
        "kreuzberg-2br",  # name (kept)
        "wg-gesucht, kleinanzeigen",  # platforms updated
        "done",
    ])
    confirm_answers = iter([
        True,   # auto-apply now True
        False,  # override notifications? = no
        False,  # customize filters? = no
    ])

    monkeypatch.setattr(
        "flatpilot.wizard.init.Prompt.ask",
        lambda *a, **kw: next(prompt_answers),
    )
    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: next(confirm_answers),
    )

    result = _saved_searches_menu(out, profile)
    ss = result.saved_searches[0]
    assert ss.name == "kreuzberg-2br"
    assert ss.auto_apply is True
    assert ss.platforms == ["wg-gesucht", "kleinanzeigen"]
