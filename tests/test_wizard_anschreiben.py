"""Coverage for the Anschreiben preview step in ``flatpilot init``."""
from __future__ import annotations

from io import StringIO
from string import Template

import pytest
from rich.console import Console

from flatpilot import compose
from flatpilot.profile import Profile, SavedSearch
from flatpilot.wizard import init as wizard_init


def _capture_console():
    return Console(file=StringIO(), force_terminal=False, width=120)


def _profile_with_searches(*searches: SavedSearch) -> Profile:
    return Profile.load_example().model_copy(update={"saved_searches": list(searches)})


def test_shipped_example_renders_against_example_profile():
    """Acceptance (a): shipped example renders without TemplateSubstitutionError."""
    profile = Profile.load_example()
    text = compose.example_template_path().read_text(encoding="utf-8")
    ctx = compose.build_context(profile, wizard_init._ANSCHREIBEN_PREVIEW_FLAT)
    rendered = Template(text).substitute(ctx)
    assert "Beispielwohnung 2 Zi" in rendered
    assert "750" in rendered
    assert profile.move_in_date.isoformat() in rendered


def test_step_skips_when_no_saved_searches(tmp_db, monkeypatch):
    """No saved searches → no prompts, no template files written."""
    profile = Profile.load_example()
    out = _capture_console()

    monkeypatch.setattr(
        "flatpilot.wizard.init.Confirm.ask",
        lambda *a, **kw: pytest.fail("no prompt should fire"),
    )

    wizard_init._anschreiben_preview_step(out, profile)
    assert list(compose.TEMPLATES_DIR.glob("*.md")) == []


def test_step_skips_platforms_not_in_any_saved_search(tmp_db, monkeypatch):
    """Acceptance (b): only platforms referenced by saved_searches are prompted."""
    profile = _profile_with_searches(
        SavedSearch(name="kreuzberg", platforms=["wg-gesucht"]),
    )
    out = _capture_console()

    prompted_for: list[str] = []

    def fake_confirm(prompt, *a, **kw):
        prompted_for.append(str(prompt))
        return True

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", fake_confirm)

    wizard_init._anschreiben_preview_step(out, profile)

    assert (compose.TEMPLATES_DIR / "wg-gesucht.md").is_file()
    assert not (compose.TEMPLATES_DIR / "kleinanzeigen.md").exists()
    assert len(prompted_for) == 1


def test_accept_default_writes_shipped_template(tmp_db, monkeypatch):
    """Acceptance (c): accepting the default copies the example into TEMPLATES_DIR."""
    profile = _profile_with_searches(
        SavedSearch(name="x", platforms=["wg-gesucht"]),
    )
    out = _capture_console()

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", lambda *a, **kw: True)

    wizard_init._anschreiben_preview_step(out, profile)

    target = compose.TEMPLATES_DIR / "wg-gesucht.md"
    assert target.is_file()
    expected = compose.example_template_path().read_text(encoding="utf-8")
    assert target.read_text(encoding="utf-8") == expected


def test_decline_default_without_editor_does_not_write(tmp_db, monkeypatch):
    """Acceptance (c): declining without $EDITOR leaves the file unwritten."""
    profile = _profile_with_searches(
        SavedSearch(name="x", platforms=["wg-gesucht"]),
    )
    out = _capture_console()

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", lambda *a, **kw: False)
    monkeypatch.delenv("EDITOR", raising=False)

    wizard_init._anschreiben_preview_step(out, profile)

    assert not (compose.TEMPLATES_DIR / "wg-gesucht.md").exists()
    assert "$EDITOR not set" in out.file.getvalue()


def test_decline_default_with_editor_invokes_subprocess(tmp_db, monkeypatch):
    """Decline + $EDITOR set → launches the editor with the target path."""
    profile = _profile_with_searches(
        SavedSearch(name="x", platforms=["wg-gesucht"]),
    )
    out = _capture_console()

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", lambda *a, **kw: False)
    monkeypatch.setenv("EDITOR", "vim")

    invocations: list[list[str]] = []
    monkeypatch.setattr(
        "flatpilot.wizard.init.subprocess.call",
        lambda cmd, *a, **kw: invocations.append(cmd) or 0,
    )

    wizard_init._anschreiben_preview_step(out, profile)

    expected_path = str(compose.TEMPLATES_DIR / "wg-gesucht.md")
    assert invocations == [["vim", expected_path]]


def test_existing_template_keep_path_does_not_rewrite(tmp_db, monkeypatch):
    """Existing template + 'keep current' = file untouched."""
    profile = _profile_with_searches(
        SavedSearch(name="x", platforms=["wg-gesucht"]),
    )
    out = _capture_console()

    target = compose.TEMPLATES_DIR / "wg-gesucht.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    custom = "Hallo, ich bewerbe mich für $title.\n"
    target.write_text(custom, encoding="utf-8")

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", lambda *a, **kw: True)

    wizard_init._anschreiben_preview_step(out, profile)

    assert target.read_text(encoding="utf-8") == custom


def test_empty_platforms_expands_to_all_apply_capable(tmp_db, monkeypatch):
    """saved_search.platforms=[] means 'any' → prompt fires for every filler."""
    profile = _profile_with_searches(SavedSearch(name="any-platform", platforms=[]))
    out = _capture_console()

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", lambda *a, **kw: True)

    wizard_init._anschreiben_preview_step(out, profile)

    written = sorted(p.stem for p in compose.TEMPLATES_DIR.glob("*.md"))
    assert "wg-gesucht" in written
    assert "kleinanzeigen" in written


def test_inberlinwohnen_excluded_even_when_listed(tmp_db, monkeypatch):
    """Platforms without a filler are skipped — apply can't run there anyway."""
    profile = _profile_with_searches(
        SavedSearch(name="x", platforms=["inberlinwohnen", "wg-gesucht"]),
    )
    out = _capture_console()

    monkeypatch.setattr("flatpilot.wizard.init.Confirm.ask", lambda *a, **kw: True)

    wizard_init._anschreiben_preview_step(out, profile)

    assert (compose.TEMPLATES_DIR / "wg-gesucht.md").is_file()
    assert not (compose.TEMPLATES_DIR / "inberlinwohnen.md").exists()
