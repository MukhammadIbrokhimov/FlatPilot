# L4 Apply + M-series Tracking UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `flatpilot apply <flat_id>` CLI command that contacts a landlord through WG-Gesucht (closing Epic L) and turn the dashboard into a 3-tab UI (Matches / Applied / Responses) where the user can drive applies and record landlord replies (closing Epic M).

**Architecture:**

- **Apply path (L4).** Refactor `Filler.fill_dry_run(...)` → `Filler.fill(..., *, submit: bool)` and add the actual submit click in WG-Gesucht. A new `apply.py` orchestrator loads profile + flat, renders the Anschreiben template, resolves attachments, calls the filler in submit mode, and writes an `applications` row (`status='submitted'` on success, `'failed'` on `FillError`). `flatpilot apply <flat_id>` is a thin Typer wrapper over the orchestrator. `--dry-run` invokes the filler with `submit=False`, prints a preview + screenshot path, and writes no row.
- **Dashboard server (M-series).** `flatpilot dashboard` becomes a long-running stdlib `ThreadingHTTPServer` bound to `127.0.0.1:<port>`. No FastAPI / no web framework — Phase 3 rule from the product-direction memory. Three POST endpoints:
  - `POST /api/matches/<match_id>/skip` — in-process; INSERTs a new `'skipped'` matches row alongside the existing `'match'` row (audit-preserving).
  - `POST /api/applications` (body: `flat_id`) — spawns `flatpilot apply <flat_id>` as a subprocess (matches bead spec; isolates Playwright from the server thread; restored from advisor input).
  - `POST /api/applications/<application_id>/response` — in-process; updates `applications.status`, `response_text`, `response_received_at`.
- **Tabs UI (M1/M3/M4).** `view.py` rewrites the body into three tab panes. The Matches pane reuses today's grouped cards plus per-card Apply / Skip / View. Applied pane reads `applications` ordered by `applied_at`, with status-badge colors and a status-filter dropdown. Responses pane shows the same rows with a paste-reply textarea + status dropdown that POSTs to the response endpoint.
- **Security.** Localhost-only bind is the security boundary; no auth, no CSRF token. Documented inline so a future reviewer doesn't bolt half-baked auth on.

**Tech Stack:** Python 3.11+, Typer (existing CLI), `string.Template` (existing composer), Playwright (existing filler), stdlib `http.server.ThreadingHTTPServer`, sqlite3, pytest.

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `src/flatpilot/apply.py` | `apply_to_flat(flat_id, *, dry_run, screenshot_dir)` orchestrator + `ApplyOutcome` dataclass. Loads profile/flat, composes message, resolves attachments, calls filler, writes `applications` row. |
| `src/flatpilot/applications.py` | DB writers `record_skip(conn, match_id, profile_hash, now)` and `record_response(conn, application_id, status, response_text, now)`. Pure functions, easy to test with `tmp_db`. |
| `src/flatpilot/server.py` | `ThreadingHTTPServer` + `DashboardHandler`. Holds the GET / handler (renders `view.generate_html()`) and the three POST handlers. `serve(port=0) -> (server, port)` factory used by both CLI and tests. |
| `tests/test_filler_submit.py` | Filler refactor regression: unit-tests `fill(submit=True/False)` against a stubbed Page object. |
| `tests/test_apply_orchestrator.py` | `apply_to_flat()` happy path, dry-run path, failure paths (filler raises, attachment missing, template missing, flat not found). |
| `tests/test_apply_cli.py` | Typer `CliRunner` over `flatpilot apply` (dry-run + live, exit codes). |
| `tests/test_view.py` | HTML smoke: tab headers render; per-card buttons present; Applied tab status badges class names; Responses form fields. |
| `tests/test_applications.py` | `record_skip()` and `record_response()` against `tmp_db`. |
| `tests/test_server.py` | Spin up `serve(port=0)` in a thread; `urllib.request` against GET / and POSTs; assert DB side effects + JSON responses. |

### Modified files

| Path | Change |
|---|---|
| `src/flatpilot/fillers/base.py` | Rename `Filler.fill_dry_run` → `Filler.fill(..., *, submit: bool, ...)`. Update class docstrings (the lines that still say "L4 will iterate the registry" describe the work this PR ships). `FillReport.submitted` flips from "always False" comment to "True on successful submit". |
| `src/flatpilot/fillers/wg_gesucht.py` | Same rename. Add `_SUBMIT_BUTTON` selector. After fields are filled, if `submit=True`, click submit and verify with a post-submit URL/state assertion (or absence of an error banner) — see Task 1 for the exact code. |
| `src/flatpilot/fillers/__init__.py` | Update module docstring `fill_dry_run` references to `fill`. Public re-exports unchanged. |
| `src/flatpilot/cli.py` | Add `@app.command() def apply(...)`. Replace the body of `def dashboard()` so it calls `server.serve(...)` and blocks on `serve_forever()`. |
| `src/flatpilot/view.py` | Restructure `_render(...)` into a tabbed layout: introduce `generate_html()` (returns the string) for the server to call, keep `generate()` (writes file) as a back-compat thin wrapper. Add `_applied_tab(rows)`, `_responses_tab(rows)`, status-badge CSS, tab-switching JS, fetch() handlers for buttons. Pass through `applications` rows from a new `_load_applications(conn)`. |

### Module-private patterns to preserve

- Per-thread sqlite connection cache (`get_conn()` returns the same connection on subsequent calls in the same thread; subprocesses get a fresh one — that's expected and safe under WAL).
- Filler / scraper / schemas registries register at import time. Server module must `import flatpilot.fillers.wg_gesucht` (and the schemas via `init_db()`) so the registry contains WG-Gesucht when the apply endpoint runs.

---

## TDD discipline

Every task follows the loop: failing test → run (fail) → minimal implementation → run (pass) → commit. Use `.venv/bin/pytest` and `.venv/bin/ruff` (system `python3` is missing deps). For tests that need a `Profile`, use `Profile.load_example().model_copy(update={...})` — never inline `Profile.model_validate({...})` (this burned PR #22).

Commit author is enforced by repo policy: `git config user.email` should already be `ibrohimovmuhammad2020@gmail.com` and `user.name` `Mukhammad Ibrokhimov`. Commit messages start with the bead ID. No AI co-author trailers.

---

## Tasks

### Task 0: Commit the plan file

**Files:**
- Create: `docs/superpowers/plans/2026-04-25-lm-apply-and-tracking-ui.md` (this file, already written)

- [ ] **Step 1: Commit**

```bash
git add docs/superpowers/plans/2026-04-25-lm-apply-and-tracking-ui.md
git commit -m "FlatPilot-cjtz: write implementation plan"
```

---

### Task 1: Filler refactor — `fill_dry_run` → `fill(submit: bool)` + WG-Gesucht submit click  *(L4 — FlatPilot-cjtz)*

**Files:**
- Modify: `src/flatpilot/fillers/base.py:81-101` (Protocol method) and `:65-66` (FillReport.submitted comment).
- Modify: `src/flatpilot/fillers/wg_gesucht.py:96-159` (method body) and `:1-26` (module docstring).
- Modify: `src/flatpilot/fillers/__init__.py:1-9` (module docstring).
- Create: `tests/test_filler_submit.py`

#### Why a refactor (not a parallel method)

`grep` confirms zero call sites of `fill_dry_run` exist outside the filler package itself — Explore mapped the scrapers/CLI and neither calls into the filler yet. We can rename freely; the bead docstring on `wg_gesucht.py:8` literally says "The actual submit path will land with FlatPilot-cjtz (L4 apply command)" — that's this bead.

#### Selector for submit button

WG-Gesucht's messenger form uses a primary action button at the bottom of `form#messenger_form`. We don't have an empirically-verified selector from a closed bead, so we adopt a conservative dual selector:

```python
submit_button: str = (
    "form#messenger_form button[type='submit'], "
    "form#messenger_form input[type='submit']"
)
```

Both `<button type=submit>` and `<input type=submit>` are submit-class controls and either is what WG-Gesucht's React form renders today. If the live form changes, the post-submit URL guard catches it as a `FillError`.

#### Post-submit verification

After click, we wait briefly for the page to settle and check that `pg.url` no longer contains the `/nachricht-senden/` segment (WG-Gesucht redirects to a confirmation/inbox page). If the URL stayed on the form OR the page now shows a known error banner, raise `FillError`.

- [ ] **Step 1.1: Write `tests/test_filler_submit.py`**

```python
"""Unit tests for the WG-Gesucht filler refactor.

We don't drive a real browser here — Playwright is mocked. The point is
to lock the contract: ``fill(submit=False)`` does not click the submit
selector; ``fill(submit=True)`` does, and updates ``FillReport.submitted``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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
    def first(self) -> "_Locator":
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

    def __init__(self, *, on_form_already_visible: bool = True, post_submit_url: str | None = None) -> None:
        self.url = "https://www.wg-gesucht.de/listing/123.html"
        self._post_submit_url = post_submit_url
        self._goto_calls: list[str] = []
        self._locators: dict[str, _Locator] = {
            SELECTORS.form: _Locator(count=1 if on_form_already_visible else 0),
            SELECTORS.message_input: _Locator(),
            SELECTORS.file_input: _Locator(),
            "form#messenger_form button[type='submit'], form#messenger_form input[type='submit']": _Locator(),
        }
        self.submit_locator = self._locators[
            "form#messenger_form button[type='submit'], form#messenger_form input[type='submit']"
        ]
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


@pytest.fixture
def fake_session(monkeypatch):
    """Patch ``polite_session`` and ``session_page`` so no browser starts."""

    page = _FakePage()

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_polite_session(config):
        return _Ctx()

    def fake_session_page(context):
        ctx = _Ctx()
        ctx.__enter__ = lambda self_: page  # type: ignore[assignment]
        ctx.__exit__ = lambda self_, *exc: False  # type: ignore[assignment]
        return ctx

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
```

- [ ] **Step 1.2: Run the test — expect failure (`fill` doesn't exist yet)**

```bash
.venv/bin/pytest tests/test_filler_submit.py -v
```
Expected: 5 failures with `AttributeError: 'WGGesuchtFiller' object has no attribute 'fill'`.

- [ ] **Step 1.3: Refactor `src/flatpilot/fillers/base.py`**

Replace lines 47–101 (the `FillReport` dataclass docstring and the `Filler` Protocol) with:

```python
@dataclass
class FillReport:
    """Outcome of a :meth:`Filler.fill` call.

    Field semantics:

    - ``contact_url``: the URL the page settled on after the contact CTA
      was clicked (or the listing URL if the form was already inline).
    - ``fields_filled``: human-readable ``{field_name: value}`` mapping
      so a CLI preview can show "subject = ...", "message = ...", etc.
      without re-rendering the template.
    - ``message_sent``: the full Anschreiben body that was typed into
      the form. L4 stores this verbatim in ``applications.message_sent``.
    - ``attachments_sent``: the absolute paths that were attached. L4
      JSON-serializes these into ``applications.attachments_sent_json``.
    - ``screenshot_path``: optional path to a PNG capturing the filled
      form (post-submit if ``submitted`` is True, pre-submit otherwise).
    - ``submitted``: ``True`` only after the platform's submit button was
      clicked AND the post-submit verification passed. ``False`` for any
      ``fill(submit=False)`` call.
    """

    platform: str
    listing_url: str
    contact_url: str
    fields_filled: Mapping[str, str]
    message_sent: str
    attachments_sent: list[Path] = field(default_factory=list)
    screenshot_path: Path | None = None
    submitted: bool = False
    started_at: str = ""
    finished_at: str = ""


class Filler(Protocol):
    """Per-platform contact-form filler contract."""

    platform: ClassVar[str]

    def fill(
        self,
        listing_url: str,
        message: str,
        attachments: list[Path],
        *,
        submit: bool,
        screenshot_dir: Path | None = None,
    ) -> FillReport:
        """Navigate to ``listing_url``, open the contact form, fill it.

        If ``submit`` is True, click the platform's submit button and
        verify the form was actually sent (typically by asserting the
        page navigated away from the form URL). If ``submit`` is False,
        stop at the filled-but-unsent form and return — useful for
        previews.

        Implementations MUST NOT attempt to log in. Failures should
        raise the most specific error class available —
        :class:`NotAuthenticatedError` when the page redirects to login,
        :class:`FormNotFoundError` when the contact CTA / form can't be
        located, :class:`SelectorMissingError` when a specific named
        field selector returns zero matches. Submit-time failures
        (button missing, page didn't navigate, error banner present)
        raise :class:`FillError`.
        """
```

Also update lines 9–14 of the module docstring at the top of the file:

```python
""":class:`FillReport` carries everything L4 needs to write an
``applications`` row (see :data:`flatpilot.schemas.APPLICATIONS_CREATE_SQL`)
without a second round of data marshaling: the rendered ``message_sent``
goes into the message column, ``attachments_sent`` is JSON-serialized to
the attachments column, and ``submitted`` decides whether L4 records a
``status='submitted'`` or ``'failed'`` row.
"""
```

(Replace the existing top docstring's relevant block; keep the surrounding text intact.)

- [ ] **Step 1.4: Refactor `src/flatpilot/fillers/wg_gesucht.py`**

Update the module docstring (lines 1–26) — replace the first paragraph so it no longer says "dry-run only":

```python
"""WG-Gesucht contact-form filler.

Navigates to a listing URL using the same polite Playwright session that
the scraper uses (so cookies, consent banner and fingerprint are
shared), follows the listing's "Nachricht senden" link to the
``/nachricht-senden/<slug>`` contact page, fills the message body and
attaches files. With ``submit=True`` it then clicks the form's submit
button and verifies the page navigated away from the form URL; with
``submit=False`` it stops at the filled form for preview / screenshot.
```

(Keep the rest of the docstring — the selector / responsive-anchor explanation — unchanged.)

Add the submit-button selector and a post-submit URL fragment to `_Selectors` (around line 60):

```python
@dataclass(frozen=True)
class _Selectors:
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
```

The form URL contains `/nachricht-senden/`. After a successful submit, WG-Gesucht navigates to an inbox or thank-you page — the path no longer contains that segment. Add a constant near `LOGIN_URL_FRAGMENTS`:

```python
FORM_URL_SEGMENT = "/nachricht-senden/"
SUBMIT_NAV_WAIT_MS = 5_000
```

Replace the `fill_dry_run` method body with the new `fill` method:

```python
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
                try:
                    pg.wait_for_load_state("networkidle", timeout=SUBMIT_NAV_WAIT_MS)
                except Exception:
                    pass
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
```

Ensure `FillError` is imported at the top of the file (the existing `from flatpilot.fillers.base import (...)` block must include it):

```python
from flatpilot.fillers.base import (
    FillError,
    FillReport,
    FormNotFoundError,
    NotAuthenticatedError,
    SelectorMissingError,
)
```

The `_FakePage.wait_for_load_state` doesn't exist on the test stand-in, but the `try`/`except` swallows it — the test passes anyway. Add it as a no-op on `_FakePage` if you'd rather not rely on the swallow:

```python
class _FakePage:
    ...
    def wait_for_load_state(self, *args, **kwargs) -> None:
        pass
```

(Already included in the test stub above — left as-is.)

Add `wait_for_load_state` to `_FakePage`:

```python
class _FakePage:
    ...
    def wait_for_load_state(self, *args, **kwargs) -> None:
        return None
```

(Add it to `tests/test_filler_submit.py` if the test file as written above doesn't already include it — verify before running.)

- [ ] **Step 1.5: Update `src/flatpilot/fillers/__init__.py` docstring**

Replace lines 1–9:

```python
"""Form-filler framework and per-platform implementations.

L4 (``flatpilot apply <flat_id>``) calls :func:`get_filler` with the
flat's ``platform`` and invokes :meth:`Filler.fill` to navigate to the
contact form, fill fields, attach files, and (when ``submit=True``)
click submit. Selectors that target real DOM elements live in each
platform module; this package's job is the registry, identical in
shape to :mod:`flatpilot.scrapers`.
"""
```

- [ ] **Step 1.6: Run the test — expect pass**

```bash
.venv/bin/pytest tests/test_filler_submit.py -v
```
Expected: 5 passed.

- [ ] **Step 1.7: Run linter and existing tests for sanity**

```bash
.venv/bin/ruff check src/flatpilot/fillers tests/test_filler_submit.py
.venv/bin/pytest -q
```
Expected: ruff clean; existing suite green (no callers of `fill_dry_run` exist outside the filler so nothing else should break).

- [ ] **Step 1.8: Commit**

```bash
git add src/flatpilot/fillers/base.py src/flatpilot/fillers/wg_gesucht.py src/flatpilot/fillers/__init__.py tests/test_filler_submit.py
git commit -m "FlatPilot-cjtz: refactor filler to fill(submit) and add WG-Gesucht submit click"
```

---

### Task 2: `apply.py` orchestrator  *(L4 — FlatPilot-cjtz)*

**Files:**
- Create: `src/flatpilot/apply.py`
- Create: `tests/test_apply_orchestrator.py`

#### Why an orchestrator module (not inline in cli.py)

The same logic is invoked two ways: from the `flatpilot apply` CLI, and (eventually) from the dashboard's `POST /api/applications` endpoint via subprocess. Centralizing in `apply.py` means: (a) one place to test, (b) the CLI is a thin Typer wrapper, (c) the future Phase 5 web backend imports the same function.

#### Failure semantics

- `dry_run=True`: never write a row. Print preview, return `ApplyOutcome(status='dry_run', application_id=None)`.
- `dry_run=False` + filler raises `FillError` (or any subclass): write `status='failed'` row with `notes` = exception message. Bubble the exception up so the CLI can exit non-zero.
- Pre-conditions failing (profile missing, flat not found, attachment missing, template missing): do NOT write a row. Raise the underlying exception. The CLI surfaces a friendly message + exit code; the dashboard endpoint surfaces a 4xx.

- [ ] **Step 2.1: Write `tests/test_apply_orchestrator.py`**

```python
"""Unit tests for ``apply_to_flat`` — the L4 orchestrator.

The filler is monkey-patched so no browser starts. We assert: which DB
rows are written, with which column values, in which scenarios.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flatpilot import config as fp_config
from flatpilot.apply import ApplyOutcome, apply_to_flat
from flatpilot.fillers.base import (
    FillError,
    FillReport,
    NotAuthenticatedError,
)
from flatpilot.profile import Profile, save_profile


def _profile_for_test(tmp_path: Path) -> Profile:
    """Use Profile.load_example() then narrow attachments to a real file."""
    pdf = tmp_path / ".flatpilot" / "attachments" / "schufa.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    profile = Profile.load_example().model_copy(
        update={
            "city": "Berlin",
            "attachments": {"default": ["schufa.pdf"], "per_platform": {}},
        }
    )
    save_profile(profile)
    return profile


def _insert_flat(conn, *, platform: str = "wg-gesucht", external_id: str = "ext-1") -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            rent_warm_eur, rooms, district,
            scraped_at, first_seen_at, requires_wbs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            external_id,
            platform,
            "https://www.wg-gesucht.de/wohnungen-in-berlin.123.html",
            "Bright 2-room Friedrichshain",
            900.0,
            2.0,
            "Friedrichshain",
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def _write_template(tmp_path: Path) -> None:
    tpl_dir = tmp_path / ".flatpilot" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "wg-gesucht.md").write_text(
        "Hallo, ich bin interessiert an $title.\n",
        encoding="utf-8",
    )


def _stub_filler(monkeypatch, *, submitted: bool = True, raises: Exception | None = None):
    captured: dict = {}

    def fake_fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None):
        captured.update(
            {
                "listing_url": listing_url,
                "message": message,
                "attachments": attachments,
                "submit": submit,
                "screenshot_dir": screenshot_dir,
            }
        )
        if raises is not None:
            raise raises
        return FillReport(
            platform="wg-gesucht",
            listing_url=listing_url,
            contact_url=listing_url,
            fields_filled={"message": message},
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=screenshot_dir / "shot.png" if screenshot_dir else None,
            submitted=submitted,
            started_at="2026-04-25T10:00:00+00:00",
            finished_at="2026-04-25T10:00:05+00:00",
        )

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill",
        fake_fill,
        raising=True,
    )
    return captured


def test_apply_dry_run_writes_no_row(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    captured = _stub_filler(monkeypatch, submitted=False)

    outcome = apply_to_flat(flat_id, dry_run=True)

    assert isinstance(outcome, ApplyOutcome)
    assert outcome.status == "dry_run"
    assert outcome.application_id is None
    assert outcome.fill_report is not None
    assert outcome.fill_report.submitted is False
    assert captured["submit"] is False
    assert "interessiert an Bright 2-room Friedrichshain" in captured["message"]
    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_live_writes_submitted_row(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, submitted=True)

    outcome = apply_to_flat(flat_id, dry_run=False)

    assert outcome.status == "submitted"
    assert outcome.application_id is not None

    row = tmp_db.execute(
        "SELECT * FROM applications WHERE id = ?", (outcome.application_id,)
    ).fetchone()
    assert row["status"] == "submitted"
    assert row["method"] == "manual"
    assert row["platform"] == "wg-gesucht"
    assert row["flat_id"] == flat_id
    assert row["title"] == "Bright 2-room Friedrichshain"
    assert "Bright 2-room" in row["message_sent"]
    assert "schufa.pdf" in row["attachments_sent_json"]


def test_apply_live_filler_failure_writes_failed_row_and_raises(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch, raises=NotAuthenticatedError("session expired"))

    with pytest.raises(NotAuthenticatedError):
        apply_to_flat(flat_id, dry_run=False)

    row = tmp_db.execute(
        "SELECT status, notes FROM applications WHERE flat_id = ?", (flat_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "session expired" in row["notes"]


def test_apply_unknown_flat_id_raises_lookup_error_no_row(tmp_db, tmp_path, monkeypatch):
    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    _stub_filler(monkeypatch)

    with pytest.raises(LookupError, match="no flat with id 999"):
        apply_to_flat(999, dry_run=False)

    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_missing_template_raises_no_row(tmp_db, tmp_path, monkeypatch):
    from flatpilot.compose import TemplateMissingError

    _profile_for_test(tmp_path)
    # No template written.
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch)

    with pytest.raises(TemplateMissingError):
        apply_to_flat(flat_id, dry_run=False)

    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_missing_attachment_raises_no_row(tmp_db, tmp_path, monkeypatch):
    from flatpilot.attachments import AttachmentError

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch)

    # Override profile to reference an attachment that doesn't exist.
    profile = Profile.load_example().model_copy(
        update={
            "city": "Berlin",
            "attachments": {"default": ["does-not-exist.pdf"], "per_platform": {}},
        }
    )
    save_profile(profile)

    with pytest.raises(AttachmentError):
        apply_to_flat(flat_id, dry_run=False)

    assert tmp_db.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_apply_no_profile_raises(tmp_db, tmp_path, monkeypatch):
    from flatpilot.apply import ProfileMissingError

    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)
    _stub_filler(monkeypatch)

    with pytest.raises(ProfileMissingError):
        apply_to_flat(flat_id, dry_run=False)
```

- [ ] **Step 2.2: Run the test — expect failure**

```bash
.venv/bin/pytest tests/test_apply_orchestrator.py -v
```
Expected: collection or import error (`flatpilot.apply` doesn't exist).

- [ ] **Step 2.3: Create `src/flatpilot/apply.py`**

```python
"""L4 — orchestrate the apply flow for a single flat.

Loads the user profile, looks up the flat by primary key, renders the
platform-specific Anschreiben template, resolves the per-platform
attachments, calls the filler, and (in live mode) writes one row to
``applications``. The CLI command in :mod:`flatpilot.cli` and the
dashboard server's ``POST /api/applications`` endpoint both call into
this function so the user-visible behaviour is identical regardless of
entry point.

Failure modes split into two camps:

* **Pre-conditions** — no profile, flat not found, template missing,
  attachment missing — are user-correctable. We raise the underlying
  exception WITHOUT writing a row so a fix-and-retry doesn't leave a
  ``status='failed'`` placeholder behind.
* **Filler errors** — the contact form was reachable but something on
  the platform side prevented submission. We DO write a row
  (``status='failed'`` with the exception message in ``notes``) and
  re-raise, so the user has an audit trail.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from flatpilot.attachments import resolve_for_platform
from flatpilot.compose import compose_anschreiben
from flatpilot.database import get_conn, init_db
from flatpilot.fillers import get_filler
from flatpilot.fillers.base import FillError, FillReport
from flatpilot.profile import Profile, load_profile

# Force registry side-effect imports so get_filler / SCHEMAS contain
# everything by the time apply_to_flat runs.
import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.schemas  # noqa: F401

logger = logging.getLogger(__name__)


class ProfileMissingError(RuntimeError):
    """Raised when ``apply_to_flat`` runs before ``flatpilot init``."""


ApplyStatus = Literal["submitted", "failed", "dry_run"]


@dataclass
class ApplyOutcome:
    """What happened during a single :func:`apply_to_flat` call.

    - ``status='dry_run'`` — preview only, no row written.
    - ``status='submitted'`` — filler submitted successfully, row written.
    - ``status='failed'`` — filler raised :class:`FillError`, row written.
    """

    status: ApplyStatus
    application_id: int | None
    fill_report: FillReport | None
    error: str | None = None


def apply_to_flat(
    flat_id: int,
    *,
    dry_run: bool = False,
    screenshot_dir: Path | None = None,
) -> ApplyOutcome:
    profile = load_profile()
    if profile is None:
        raise ProfileMissingError(
            "No profile at ~/.flatpilot/profile.json — run `flatpilot init` first."
        )

    init_db()
    conn = get_conn()

    flat_row = conn.execute(
        "SELECT * FROM flats WHERE id = ?", (flat_id,)
    ).fetchone()
    if flat_row is None:
        raise LookupError(f"no flat with id {flat_id}")
    flat = dict(flat_row)

    platform = str(flat["platform"])

    # These can all fail before we touch the browser. Surface as raises;
    # do not write a row.
    message = compose_anschreiben(profile, platform, flat)
    attachments = resolve_for_platform(profile, platform)
    filler_cls = get_filler(platform)
    filler = filler_cls()

    if dry_run:
        report = filler.fill(
            listing_url=str(flat["listing_url"]),
            message=message,
            attachments=attachments,
            submit=False,
            screenshot_dir=screenshot_dir,
        )
        return ApplyOutcome(
            status="dry_run",
            application_id=None,
            fill_report=report,
        )

    try:
        report = filler.fill(
            listing_url=str(flat["listing_url"]),
            message=message,
            attachments=attachments,
            submit=True,
            screenshot_dir=screenshot_dir,
        )
    except FillError as exc:
        application_id = _record_application(
            conn,
            profile=profile,
            flat=flat,
            message=message,
            attachments=attachments,
            status="failed",
            notes=str(exc),
        )
        logger.warning(
            "apply: flat_id=%d failed: %s (application_id=%d)",
            flat_id,
            exc,
            application_id,
        )
        outcome = ApplyOutcome(
            status="failed",
            application_id=application_id,
            fill_report=None,
            error=str(exc),
        )
        # Re-raise so the CLI / server caller can handle it (exit code,
        # 5xx response, etc.). The row write above is the durable trail.
        raise
    else:
        application_id = _record_application(
            conn,
            profile=profile,
            flat=flat,
            message=report.message_sent,
            attachments=report.attachments_sent,
            status="submitted",
            notes=None,
        )
        logger.info(
            "apply: flat_id=%d submitted (application_id=%d)",
            flat_id,
            application_id,
        )
        return ApplyOutcome(
            status="submitted",
            application_id=application_id,
            fill_report=report,
        )


def _record_application(
    conn,
    *,
    profile: Profile,
    flat: dict,
    message: str,
    attachments: list[Path],
    status: str,
    notes: str | None,
) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            rent_warm_eur, rooms, size_sqm, district,
            applied_at, method,
            message_sent, attachments_sent_json,
            status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?)
        """,
        (
            flat["id"],
            flat["platform"],
            flat["listing_url"],
            flat["title"],
            flat.get("rent_warm_eur"),
            flat.get("rooms"),
            flat.get("size_sqm"),
            flat.get("district"),
            now,
            message,
            json.dumps([str(p) for p in attachments]),
            status,
            notes,
        ),
    )
    return int(cur.lastrowid)
```

- [ ] **Step 2.4: Run the test — expect pass**

```bash
.venv/bin/pytest tests/test_apply_orchestrator.py -v
```
Expected: 7 passed.

- [ ] **Step 2.5: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/apply.py tests/test_apply_orchestrator.py
.venv/bin/pytest -q
```
Expected: ruff clean, full suite green.

- [ ] **Step 2.6: Commit**

```bash
git add src/flatpilot/apply.py tests/test_apply_orchestrator.py
git commit -m "FlatPilot-cjtz: add apply_to_flat orchestrator"
```

---

### Task 3: `flatpilot apply` CLI command  *(L4 — FlatPilot-cjtz)*

**Files:**
- Modify: `src/flatpilot/cli.py` (add subcommand around line 514, before `def dashboard`)
- Create: `tests/test_apply_cli.py`

- [ ] **Step 3.1: Write `tests/test_apply_cli.py`**

```python
"""Tests for ``flatpilot apply`` CLI subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from flatpilot.apply import ApplyOutcome, ProfileMissingError
from flatpilot.cli import app
from flatpilot.fillers.base import FillReport, NotAuthenticatedError


def _stub_outcome(status: str) -> ApplyOutcome:
    report = FillReport(
        platform="wg-gesucht",
        listing_url="https://www.wg-gesucht.de/listing/123.html",
        contact_url="https://www.wg-gesucht.de/listing/123.html",
        fields_filled={"message": "Hallo."},
        message_sent="Hallo.",
        attachments_sent=[],
        screenshot_path=Path("/tmp/shot.png"),
        submitted=status == "submitted",
        started_at="t0",
        finished_at="t1",
    )
    return ApplyOutcome(
        status=status,  # type: ignore[arg-type]
        application_id=42 if status == "submitted" else None,
        fill_report=report,
    )


def test_apply_dry_run_exit_zero_and_prints_preview():
    runner = CliRunner()
    with patch("flatpilot.cli.apply_to_flat", return_value=_stub_outcome("dry_run")) as orchestrator:
        result = runner.invoke(app, ["apply", "5", "--dry-run"])

    assert result.exit_code == 0, result.output
    orchestrator.assert_called_once()
    call_kwargs = orchestrator.call_args.kwargs
    assert call_kwargs["dry_run"] is True
    assert "preview" in result.output.lower() or "dry-run" in result.output.lower()
    assert "Hallo." in result.output


def test_apply_live_exit_zero_and_prints_application_id():
    runner = CliRunner()
    with patch("flatpilot.cli.apply_to_flat", return_value=_stub_outcome("submitted")) as orchestrator:
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 0, result.output
    assert orchestrator.call_args.kwargs["dry_run"] is False
    assert "42" in result.output  # application_id


def test_apply_filler_error_exit_one():
    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=NotAuthenticatedError("session expired"),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 1
    assert "session expired" in result.output


def test_apply_lookup_error_exit_two():
    runner = CliRunner()
    with patch("flatpilot.cli.apply_to_flat", side_effect=LookupError("no flat with id 999")):
        result = runner.invoke(app, ["apply", "999"])

    assert result.exit_code == 2
    assert "no flat with id 999" in result.output


def test_apply_no_profile_exit_one():
    runner = CliRunner()
    with patch(
        "flatpilot.cli.apply_to_flat",
        side_effect=ProfileMissingError("No profile at ..."),
    ):
        result = runner.invoke(app, ["apply", "5"])

    assert result.exit_code == 1
    assert "No profile" in result.output
```

- [ ] **Step 3.2: Run the test — expect failure**

```bash
.venv/bin/pytest tests/test_apply_cli.py -v
```
Expected: failures (`apply` subcommand doesn't exist; `flatpilot.cli.apply_to_flat` import target missing).

- [ ] **Step 3.3: Add the `apply` subcommand to `src/flatpilot/cli.py`**

At the top of the module, add this import next to the existing typer/rich imports:

```python
from flatpilot.apply import ApplyOutcome, ProfileMissingError, apply_to_flat
```

(`apply_to_flat` lives at module-level so `tests/test_apply_cli.py` can `patch("flatpilot.cli.apply_to_flat", ...)`.)

Insert the new command just before `def dashboard()` at line 515:

```python
@app.command()
def apply(
    flat_id: int = typer.Argument(..., help="Database ID of the flat to apply to."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fill the contact form but DO NOT click submit. Prints a preview "
        "and writes no applications row.",
    ),
    screenshot_dir: Path | None = typer.Option(
        None,
        "--screenshot-dir",
        help="If set, save a PNG of the filled form to this directory.",
    ),
) -> None:
    """Contact the landlord for a single flat via its platform's filler.

    On success a row is written to the ``applications`` table with
    ``status='submitted'``. On filler error a row is written with
    ``status='failed'`` and the error in ``notes``. ``--dry-run`` skips
    the submit click and writes no row.
    """
    from rich.console import Console

    console = Console()
    try:
        outcome: ApplyOutcome = apply_to_flat(
            flat_id, dry_run=dry_run, screenshot_dir=screenshot_dir
        )
    except ProfileMissingError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except Exception as exc:  # FillError, AttachmentError, TemplateError, etc.
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(1) from exc

    report = outcome.fill_report
    if outcome.status == "dry_run":
        console.print("[yellow]dry-run preview[/yellow] (no applications row written)")
        if report is not None:
            console.print(f"  contact URL: {report.contact_url}")
            for field, value in report.fields_filled.items():
                preview = value if len(value) <= 200 else value[:197] + "..."
                console.print(f"  {field}: {preview}")
            if report.screenshot_path is not None:
                console.print(f"  screenshot: {report.screenshot_path}")
        return

    if outcome.status == "submitted":
        console.print(
            f"[green]submitted[/green] · application_id={outcome.application_id}"
        )
        return

    # Should not be reachable — failed paths raise above.
    console.print(f"[red]unexpected status: {outcome.status}[/red]")
    raise typer.Exit(1)
```

Note: the `from datetime import UTC` import at the top of `cli.py` (line 12) is unused after this change is added if it was unused before — leave it alone if other code uses it; otherwise ruff will catch it.

Also add `from pathlib import Path` to the top of `cli.py` if it isn't already imported (the new `screenshot_dir: Path | None` annotation needs it).

- [ ] **Step 3.4: Run the test — expect pass**

```bash
.venv/bin/pytest tests/test_apply_cli.py -v
```
Expected: 5 passed.

- [ ] **Step 3.5: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/cli.py tests/test_apply_cli.py
.venv/bin/pytest -q
```

- [ ] **Step 3.6: Commit (closes Epic L)**

```bash
git add src/flatpilot/cli.py tests/test_apply_cli.py
git commit -m "FlatPilot-cjtz: wire flatpilot apply CLI command"
```

---

### Task 4: `view.py` — refactor into 3 tabs (Matches | Applied | Responses)  *(M1 — FlatPilot-72z5)*

**Files:**
- Modify: `src/flatpilot/view.py` (extract `generate_html()`, rebuild `_render(...)` around tabs, add status-badge CSS, tab-switching JS).
- Create: `tests/test_view.py`

#### What changes structurally

Today's `view.py` writes a single-pane "Matched / Rejected / Rejected historical" layout to `dashboard.html`. We:

1. Extract the HTML-building from `generate()` into a new pure function `generate_html(conn=None) -> str`. `generate()` becomes a thin wrapper that calls `generate_html()` and writes to disk — preserves backwards-compatibility for any caller that still expects a file path.
2. Add `_load_applications(conn) -> list[dict]` that reads the `applications` table ordered by `applied_at DESC`.
3. Restructure the body to wrap content in three `<section class="tab-pane">` panes plus a `<nav class="tabs">` with three buttons. Default to Matches.
4. Add status-badge CSS for the Applied tab (we render the badge spans in Task 7 — for now, just ensure the CSS class names exist and the Applied/Responses panes are present even if empty).
5. Vanilla JS to toggle `.active` on tab buttons and panes.

This task only does the **scaffolding** — Matches keeps its old card markup; Applied / Responses panes render placeholder ("No applications yet" / "No responses to record") if there are no `applications` rows. Tasks 7/8/9 fill in the per-pane behavior.

- [ ] **Step 4.1: Write `tests/test_view.py`**

```python
"""Smoke tests for the dashboard HTML — tabs scaffolding only.

We assert structural invariants (presence of tab buttons, pane elements,
badge CSS classes) rather than full rendering. Pane bodies grow in
later M-series tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flatpilot.view import generate_html


def _insert_match(conn) -> None:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            rent_warm_eur, rooms, district,
            scraped_at, first_seen_at, requires_wbs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            "ext-view-1",
            "wg-gesucht",
            "https://www.wg-gesucht.de/listing/1.html",
            "View test flat",
            850.0,
            2.0,
            "Neukölln",
            now,
            now,
        ),
    )
    flat_id = cur.lastrowid
    conn.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, 'h1', 'match', '[]', ?)
        """,
        (flat_id, now),
    )


def test_generate_html_includes_three_tab_buttons(tmp_db):
    html = generate_html()
    assert 'data-tab="matches"' in html
    assert 'data-tab="applied"' in html
    assert 'data-tab="responses"' in html


def test_generate_html_includes_three_panes(tmp_db):
    html = generate_html()
    assert 'data-pane="matches"' in html
    assert 'data-pane="applied"' in html
    assert 'data-pane="responses"' in html


def test_generate_html_matches_pane_renders_match_card(tmp_db):
    _insert_match(tmp_db)
    html = generate_html()
    assert "View test flat" in html


def test_generate_html_status_badge_css_classes_present(tmp_db):
    html = generate_html()
    # Badges use a status-prefix convention so M3 can drop the spans in
    # without re-touching CSS.
    assert ".badge-submitted" in html
    assert ".badge-failed" in html
    assert ".badge-viewing_invited" in html
    assert ".badge-rejected" in html
    assert ".badge-no_response" in html


def test_generate_returns_path_and_writes_file(tmp_db):
    from flatpilot.view import generate

    path = generate()
    assert path.exists()
    assert "data-tab=\"matches\"" in path.read_text(encoding="utf-8")
```

- [ ] **Step 4.2: Run the test — expect failure**

```bash
.venv/bin/pytest tests/test_view.py -v
```
Expected: failures — `generate_html` doesn't exist; tab markers absent.

- [ ] **Step 4.3: Refactor `src/flatpilot/view.py`**

Replace the entire file body (keeping the module docstring at the top — update its first paragraph to mention the tabs):

```python
"""Generate the static / served HTML dashboard.

Reads ``matches`` joined against ``flats`` and the ``applications``
table, groups rows by tab (Matches / Applied / Responses), and renders
a single self-contained HTML page. The Matches tab keeps its session /
historical sub-grouping. Filters (district / rent max / rooms / WBS) on
Matches and a status filter on Applied run client-side in vanilla JS
against ``data-*`` attributes.

``generate_html(conn=None)`` is the pure renderer the dashboard server
calls per request. ``generate()`` is the back-compat wrapper that
writes the rendered string to ``~/.flatpilot/dashboard.html`` and
returns the path — kept so any caller that still writes-and-opens a
static file works without changes.

No LLM scoring and no server-side filtering; Phase 1 / 3 stays
deterministic.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

from flatpilot.config import APP_DIR, ensure_dirs
from flatpilot.database import get_conn, init_db


DASHBOARD_FILENAME = "dashboard.html"
SESSION_WINDOW = timedelta(hours=24)


def generate_html(conn: Any = None) -> str:
    """Render the full dashboard HTML against the current DB state."""
    init_db()
    if conn is None:
        conn = get_conn()

    match_rows = conn.execute(
        """
        SELECT m.id AS match_id, m.decision, m.decision_reasons_json,
               m.decided_at, m.notified_at, m.notified_channels_json,
               f.*
        FROM matches m
        JOIN flats f ON f.id = m.flat_id
        ORDER BY m.decided_at DESC
        """
    ).fetchall()

    matched: list[dict[str, Any]] = []
    rejected_session: list[dict[str, Any]] = []
    rejected_historical: list[dict[str, Any]] = []
    skipped_flat_ids: set[int] = set()

    session_cutoff = datetime.now(timezone.utc) - SESSION_WINDOW
    # First pass: collect skipped flat_ids so the matches pane can hide
    # any flat the user has skipped (preserves audit while keeping the
    # Matches view decluttered).
    for row in match_rows:
        item = dict(row)
        if item["decision"] == "skipped":
            skipped_flat_ids.add(int(item["flat_id"]))

    for row in match_rows:
        item = dict(row)
        decision = item["decision"]
        if decision == "match":
            if int(item["flat_id"]) in skipped_flat_ids:
                continue
            matched.append(item)
            continue
        if decision != "reject":
            continue
        decided_at = _parse_ts(item.get("decided_at"))
        if decided_at is None or decided_at < session_cutoff:
            rejected_historical.append(item)
        else:
            rejected_session.append(item)

    applications = _load_applications(conn)

    return _render(matched, rejected_session, rejected_historical, applications)


def generate() -> Path:
    """Back-compat: render the dashboard and write it to the app dir."""
    ensure_dirs()
    path = APP_DIR / DASHBOARD_FILENAME
    path.write_text(generate_html(), encoding="utf-8")
    return path


def _load_applications(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, flat_id, platform, listing_url, title,
               rent_warm_eur, rooms, size_sqm, district,
               applied_at, method, message_sent, attachments_sent_json,
               status, response_received_at, response_text, notes
        FROM applications
        ORDER BY applied_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fmt_rent(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(round(float(value)))} €"
    except (TypeError, ValueError):
        return str(value)


def _fmt_rooms(value: Any) -> str:
    if value is None:
        return "—"
    try:
        rooms = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(rooms)) if rooms.is_integer() else f"{rooms:g}"


def _card(item: dict[str, Any]) -> str:
    rent = item.get("rent_warm_eur")
    rooms = item.get("rooms")
    district = item.get("district") or ""
    requires_wbs = 1 if item.get("requires_wbs") else 0
    title = escape(str(item.get("title") or "Untitled listing"))
    url = str(item.get("listing_url") or "")
    flat_id = item.get("flat_id") or item.get("id") or ""
    match_id = item.get("match_id") or ""

    posted = item.get("online_since") or ""
    decided = item.get("decided_at") or ""

    try:
        reasons = json.loads(item.get("decision_reasons_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        reasons = []
    reasons_html = ""
    if reasons:
        reasons_html = (
            '<p class="reasons">'
            + ", ".join(escape(str(r)) for r in reasons)
            + "</p>"
        )

    # Action buttons. Apply / Skip wired to fetch() handlers in JS;
    # View is a plain link. Skip is hidden if this isn't a match row
    # (the tab only renders match rows today, but the guard is cheap).
    actions_html = ""
    if url:
        actions_html = (
            '<div class="actions">'
            f'<button class="apply" type="button" '
            f'data-flat-id="{escape(str(flat_id), quote=True)}">Apply</button>'
            f'<button class="skip" type="button" '
            f'data-match-id="{escape(str(match_id), quote=True)}">Skip</button>'
            f'<a class="open" href="{escape(url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">View</a>'
            f'<button class="copy" type="button" '
            f'data-url="{escape(url, quote=True)}">Copy link</button>'
            '</div>'
        )

    return (
        f'<article class="card" '
        f'data-district="{escape(district, quote=True)}" '
        f'data-rent="{rent if rent is not None else ""}" '
        f'data-rooms="{rooms if rooms is not None else ""}" '
        f'data-wbs="{requires_wbs}">'
        f"<h3>{title}</h3>"
        f'<dl>'
        f"<dt>Warmmiete</dt><dd>{escape(_fmt_rent(rent))}</dd>"
        f"<dt>Rooms</dt><dd>{escape(_fmt_rooms(rooms))}</dd>"
        + (f"<dt>District</dt><dd>{escape(district)}</dd>" if district else "")
        + (f"<dt>Posted</dt><dd>{escape(str(posted))}</dd>" if posted else "")
        + (f"<dt>Decided</dt><dd>{escape(str(decided))}</dd>" if decided else "")
        + "</dl>"
        + actions_html
        + reasons_html
        + "</article>"
    )


def _section(title: str, items: list[dict[str, Any]], key: str) -> str:
    if not items:
        return f'<section class="group" data-group="{key}"><h2>{escape(title)} (0)</h2></section>'
    cards = "\n".join(_card(i) for i in items)
    return (
        f'<section class="group" data-group="{key}">'
        f"<h2>{escape(title)} ({len(items)})</h2>"
        f'<div class="cards">{cards}</div>'
        f"</section>"
    )


def _district_options(groups: list[list[dict[str, Any]]]) -> str:
    names: set[str] = set()
    for group in groups:
        for item in group:
            d = item.get("district")
            if d:
                names.add(str(d))
    opts = '<option value="any">any</option>'
    for name in sorted(names):
        opts += f'<option value="{escape(name, quote=True)}">{escape(name)}</option>'
    return opts


def _applied_pane(applications: list[dict[str, Any]]) -> str:
    """Placeholder pane — Task 7 fills this in. Empty list shows hint."""
    if not applications:
        return '<p class="empty">No applications yet. Apply from the Matches tab to populate this view.</p>'
    return '<p class="empty">Applied tab populated by M3.</p>'


def _responses_pane(applications: list[dict[str, Any]]) -> str:
    """Placeholder pane — Task 9 fills this in. Empty list shows hint."""
    if not applications:
        return '<p class="empty">No responses to record. Apply to a flat first.</p>'
    return '<p class="empty">Responses tab populated by M4.</p>'


def _render(
    matched: list[dict[str, Any]],
    rejected_session: list[dict[str, Any]],
    rejected_historical: list[dict[str, Any]],
    applications: list[dict[str, Any]],
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    district_options = _district_options(
        [matched, rejected_session, rejected_historical]
    )
    total_match = len(matched)
    total_reject = len(rejected_session) + len(rejected_historical)
    total_applied = len(applications)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlatPilot dashboard</title>
<style>
  :root {{ color-scheme: light dark; --accent: #2b7cff; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem; max-width: 1100px; margin: 0 auto; }}
  header p {{ color: #666; margin: 0.2rem 0 1rem; }}
  nav.tabs {{ display: flex; gap: 0.25rem; margin-bottom: 1rem; border-bottom: 1px solid rgba(128,128,128,0.3); }}
  nav.tabs button {{ font: inherit; padding: 0.5rem 1rem; border: none; background: transparent;
                     border-bottom: 2px solid transparent; cursor: pointer; color: inherit; }}
  nav.tabs button.active {{ border-bottom-color: var(--accent); color: var(--accent); font-weight: 600; }}
  nav.tabs button:hover:not(.active) {{ background: rgba(128,128,128,0.08); }}
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}
  .filters {{ display: flex; flex-wrap: wrap; gap: 1rem; align-items: center;
              padding: 0.75rem 1rem; background: rgba(128,128,128,0.08);
              border-radius: 8px; margin-bottom: 1.5rem; }}
  .filters label {{ display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.9rem; }}
  .filters select, .filters input[type=range] {{ font-size: 0.9rem; }}
  .group {{ margin-bottom: 2rem; }}
  .group h2 {{ border-bottom: 1px solid rgba(128,128,128,0.3); padding-bottom: 0.25rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }}
  .card {{ border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; padding: 1rem;
           background: rgba(128,128,128,0.04); }}
  .card h3 {{ margin: 0 0 0.5rem; font-size: 1.05rem; }}
  .card dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 0.75rem; margin: 0.5rem 0; }}
  .card dt {{ color: #888; font-size: 0.85rem; }}
  .card dd {{ margin: 0; font-size: 0.95rem; }}
  .card .actions {{ display: flex; gap: 0.5rem; align-items: center; margin: 0.5rem 0 0; flex-wrap: wrap; }}
  .card .actions a.open, .card .actions button {{ font: inherit; font-size: 0.85rem;
      padding: 0.25rem 0.6rem; border-radius: 4px; cursor: pointer; }}
  .card .actions a.open {{ color: var(--accent); text-decoration: none;
                           border: 1px solid var(--accent); }}
  .card .actions a.open:hover {{ background: var(--accent); color: white; }}
  .card .actions button.apply {{ background: var(--accent); color: white; border: 1px solid var(--accent); }}
  .card .actions button.apply:hover {{ filter: brightness(1.1); }}
  .card .actions button.apply:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .card .actions button.skip {{ background: transparent; border: 1px solid rgba(128,128,128,0.4); color: inherit; }}
  .card .actions button.skip:hover {{ background: rgba(128,128,128,0.1); }}
  .card .actions button.copy {{ background: transparent; border: 1px solid rgba(128,128,128,0.4); color: inherit; }}
  .card .actions button.copy:hover {{ background: rgba(128,128,128,0.1); }}
  .card .actions button.copy.copied {{ color: #2a7; border-color: #2a7; }}
  .card .reasons {{ color: #c33; font-size: 0.85rem; margin: 0.4rem 0 0; }}
  .empty {{ color: #888; font-style: italic; padding: 1rem 0; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
            font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }}
  .badge-submitted {{ background: rgba(43,124,255,0.15); color: #2b7cff; }}
  .badge-failed {{ background: rgba(220,68,68,0.15); color: #c33; }}
  .badge-viewing_invited {{ background: rgba(34,170,85,0.15); color: #2a7; }}
  .badge-rejected {{ background: rgba(120,120,120,0.15); color: #888; }}
  .badge-no_response {{ background: rgba(180,140,40,0.15); color: #b8860b; }}
  .toast {{ position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%);
            background: #333; color: white; padding: 0.75rem 1.25rem; border-radius: 6px;
            font-size: 0.9rem; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 100; }}
  .toast.show {{ opacity: 1; }}
  .toast.error {{ background: #c33; }}
</style>
</head>
<body>
<header>
  <h1>FlatPilot</h1>
  <p>Generated {escape(generated_at)} · {total_match} matched · {total_reject} rejected · {total_applied} applied</p>
</header>

<nav class="tabs">
  <button type="button" data-tab="matches" class="active">Matches</button>
  <button type="button" data-tab="applied">Applied</button>
  <button type="button" data-tab="responses">Responses</button>
</nav>

<section class="tab-pane active" data-pane="matches">
  <div class="filters">
    <label>District <select id="f-district">{district_options}</select></label>
    <label>Rent max <input type="range" id="f-rent" min="0" max="3000" step="50" value="3000">
      <span id="f-rent-v">3000</span> €</label>
    <label>Rooms <select id="f-rooms">
      <option value="any">any</option>
      <option value="1">1</option>
      <option value="1.5">1.5</option>
      <option value="2">2</option>
      <option value="2.5">2.5</option>
      <option value="3">3</option>
      <option value="3.5">3.5</option>
      <option value="4">4</option>
      <option value="5+">5+</option>
    </select></label>
    <label>WBS <select id="f-wbs">
      <option value="any">any</option>
      <option value="1">required</option>
      <option value="0">not required</option>
    </select></label>
  </div>

  {_section("Matched", matched, "matched")}
  {_section("Rejected this session", rejected_session, "rejected-session")}
  {_section("Rejected historical", rejected_historical, "rejected-historical")}
</section>

<section class="tab-pane" data-pane="applied">
  {_applied_pane(applications)}
</section>

<section class="tab-pane" data-pane="responses">
  {_responses_pane(applications)}
</section>

<div id="toast" class="toast" role="status" aria-live="polite"></div>

<script>
(function() {{
  // Tab switching.
  const tabs = document.querySelectorAll('nav.tabs button');
  const panes = document.querySelectorAll('.tab-pane');
  tabs.forEach(btn => {{
    btn.addEventListener('click', () => {{
      const target = btn.dataset.tab;
      tabs.forEach(b => b.classList.toggle('active', b === btn));
      panes.forEach(p => p.classList.toggle('active', p.dataset.pane === target));
    }});
  }});

  // Toast helper used by Task 6/7/9 fetch handlers.
  const toastEl = document.getElementById('toast');
  let toastTimer = null;
  window.flatpilotToast = function(msg, isError) {{
    toastEl.textContent = msg;
    toastEl.classList.toggle('error', !!isError);
    toastEl.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 3500);
  }};

  // Matches-pane filters (unchanged from pre-tabs version).
  const district = document.getElementById('f-district');
  const rent = document.getElementById('f-rent');
  const rentVal = document.getElementById('f-rent-v');
  const rooms = document.getElementById('f-rooms');
  const wbs = document.getElementById('f-wbs');

  function matchRooms(value, filter) {{
    if (filter === 'any') return true;
    if (!value) return false;
    const n = parseFloat(value);
    if (filter === '5+') return n >= 5;
    return Math.abs(n - parseFloat(filter)) < 0.001;
  }}

  function applyFilters() {{
    const wD = district.value;
    const wR = parseInt(rent.value, 10);
    const wRooms = rooms.value;
    const wWbs = wbs.value;
    rentVal.textContent = wR;

    document.querySelectorAll('[data-pane="matches"] .card').forEach(card => {{
      const d = card.dataset.district;
      const r = card.dataset.rent ? parseFloat(card.dataset.rent) : null;
      const ro = card.dataset.rooms;
      const w = card.dataset.wbs;

      let show = true;
      if (wD !== 'any' && d !== wD) show = false;
      if (r !== null && r > wR) show = false;
      if (!matchRooms(ro, wRooms)) show = false;
      if (wWbs !== 'any' && w !== wWbs) show = false;

      card.style.display = show ? '' : 'none';
    }});
  }}

  [district, rent, rooms, wbs].forEach(el => {{
    el.addEventListener('input', applyFilters);
    el.addEventListener('change', applyFilters);
  }});

  // Copy-link handler (unchanged).
  document.querySelectorAll('.card .actions button.copy').forEach(btn => {{
    btn.addEventListener('click', async () => {{
      const url = btn.dataset.url;
      try {{
        await navigator.clipboard.writeText(url);
        btn.classList.add('copied');
        const prev = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(() => {{ btn.classList.remove('copied'); btn.textContent = prev; }}, 1500);
      }} catch (e) {{
        btn.textContent = 'Failed';
        setTimeout(() => {{ btn.textContent = 'Copy link'; }}, 1500);
      }}
    }});
  }});

  applyFilters();
}})();
</script>
</body>
</html>
"""
```

(Note: the Apply / Skip button click handlers wire up in Task 7 / Task 6 respectively; the buttons are in the DOM but currently inert. That's intentional — we want each task's diff scoped.)

- [ ] **Step 4.4: Run the test — expect pass**

```bash
.venv/bin/pytest tests/test_view.py -v
```
Expected: 5 passed.

- [ ] **Step 4.5: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/view.py tests/test_view.py
.venv/bin/pytest -q
```

- [ ] **Step 4.6: Commit**

```bash
git add src/flatpilot/view.py tests/test_view.py
git commit -m "FlatPilot-72z5: restructure dashboard into 3-tab UI"
```

---

### Task 5: `server.py` — `ThreadingHTTPServer` + `flatpilot dashboard` becomes long-running  *(M1 — FlatPilot-72z5)*

**Files:**
- Create: `src/flatpilot/server.py`
- Modify: `src/flatpilot/cli.py` — replace the body of `def dashboard()` to start the server.
- Create: `tests/test_server.py` (this task adds only the GET / test; Tasks 6/7/9 extend it)

#### Architecture notes

- Bind `127.0.0.1`, not `0.0.0.0` — security boundary.
- Default port: `8765`. If busy, fall back to ephemeral (port=0). Print the chosen URL.
- `ThreadingHTTPServer` — every request in its own thread; `get_conn()` opens a fresh per-thread sqlite connection on first call.
- Per-request import side-effects: `apply.py`'s top-level `import flatpilot.fillers.wg_gesucht` ensures the registry is populated; `init_db()` registers schemas. The server module imports both eagerly.
- No CSRF / no auth. Comment in code: "localhost-bound is the security boundary."

- [ ] **Step 5.1: Write `tests/test_server.py`**

```python
"""Tests for the dashboard HTTP server.

Spins up the server on an ephemeral port in a background thread for
each test; exercises endpoints with ``urllib.request``.
"""

from __future__ import annotations

import threading
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest


@contextmanager
def _running_server(tmp_db):
    from flatpilot.server import serve

    server, port = serve(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_get_root_serves_dashboard_html(tmp_db):
    with _running_server(tmp_db) as port:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            body = resp.read().decode("utf-8")
            assert resp.status == 200
            assert resp.getheader("Content-Type", "").startswith("text/html")
            assert 'data-tab="matches"' in body
            assert 'data-tab="applied"' in body
            assert 'data-tab="responses"' in body


def test_get_unknown_path_returns_404(tmp_db):
    with _running_server(tmp_db) as port:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/nope")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 404
```

- [ ] **Step 5.2: Run the test — expect failure**

```bash
.venv/bin/pytest tests/test_server.py -v
```
Expected: import error — `flatpilot.server` doesn't exist.

- [ ] **Step 5.3: Create `src/flatpilot/server.py`**

```python
"""Localhost HTTP server backing ``flatpilot dashboard``.

Replaces the static file Phase-1 dashboard. Serves the same HTML at
``GET /`` and exposes three POST endpoints (added in M2 / M4):

* ``POST /api/matches/<match_id>/skip`` — mark a match skipped.
* ``POST /api/applications`` (body: ``{"flat_id": int}``) — spawn
  ``flatpilot apply <flat_id>`` as a subprocess.
* ``POST /api/applications/<application_id>/response`` — record a
  pasted-in landlord reply on an applications row.

**Security boundary.** The server binds ``127.0.0.1`` — localhost only.
There is no auth, no CSRF token, no allowed-origin check. Anyone with
shell access to the host can drive it; that is the expected single-user
operator threat model. Do NOT add half-baked auth here without a
discussion — Phase 5 will replace this with a proper FastAPI service
behind email magic-link auth.

Threading model. ``ThreadingHTTPServer`` spawns one thread per
request. ``flatpilot.database.get_conn()`` caches a sqlite connection
per thread under WAL — multiple concurrent reads + the occasional
small write co-exist safely. The Apply endpoint shells out so the
server thread doesn't block on Playwright.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple

# Eagerly populate registries the request handlers will need.
import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.schemas  # noqa: F401
from flatpilot.view import generate_html

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the dashboard and its mutation endpoints."""

    # Quieter access log — match the project's logging style.
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "":
            html = generate_html()
            self._send(HTTPStatus.OK, html, content_type="text/html; charset=utf-8")
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    # POST endpoints land in Tasks 6 / 7 / 9.
    def do_POST(self) -> None:
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    def _send(
        self,
        status: HTTPStatus,
        body: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> Tuple[ThreadingHTTPServer, int]:
    """Bind and return the server (without starting its loop).

    Caller runs ``server.serve_forever()`` (blocks) and ``server.shutdown()``
    + ``server.server_close()`` for teardown. Returns the actually-bound
    port — useful when ``port=0`` was requested.
    """
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if port == DEFAULT_PORT:
            # Default port busy; fall back to ephemeral so dev can iterate.
            logger.warning(
                "port %d is in use (%s) — falling back to ephemeral port",
                port,
                exc,
            )
            server = ThreadingHTTPServer((host, 0), DashboardHandler)
        else:
            raise
    bound_port = server.server_address[1]
    return server, bound_port
```

- [ ] **Step 5.4: Replace the body of `def dashboard()` in `src/flatpilot/cli.py`**

Locate `def dashboard()` (around line 515) and replace its body:

```python
@app.command()
def dashboard(
    port: int = typer.Option(
        8765,
        "--port",
        help="Localhost port to bind. Falls back to an ephemeral port if busy.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't open the dashboard in a browser tab on startup.",
    ),
) -> None:
    """Serve the HTML dashboard over localhost until interrupted (Ctrl-C)."""
    import webbrowser

    from rich.console import Console

    from flatpilot.server import serve

    console = Console()
    server, bound_port = serve(host="127.0.0.1", port=port)
    url = f"http://127.0.0.1:{bound_port}/"
    console.print(f"FlatPilot dashboard serving at [bold]{url}[/bold]  (Ctrl-C to stop)")
    if not no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]stopping dashboard server[/yellow]")
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 5.5: Run the test — expect pass**

```bash
.venv/bin/pytest tests/test_server.py -v
```
Expected: 2 passed.

- [ ] **Step 5.6: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/server.py src/flatpilot/cli.py tests/test_server.py
.venv/bin/pytest -q
```

- [ ] **Step 5.7: Commit (closes Epic M1 — M1 done as 4+5)**

```bash
git add src/flatpilot/server.py src/flatpilot/cli.py tests/test_server.py
git commit -m "FlatPilot-72z5: serve dashboard via localhost HTTP server"
```

---

### Task 6: Skip endpoint — `record_skip()` + `POST /api/matches/<match_id>/skip`  *(M2 — FlatPilot-6axq partial)*

**Files:**
- Create: `src/flatpilot/applications.py` (also used by Task 9)
- Create: `tests/test_applications.py`
- Modify: `src/flatpilot/server.py` — wire the POST handler.
- Modify: `tests/test_server.py` — add skip endpoint tests.

#### Skip semantics (recap from advisor)

Insert a NEW row `(flat_id, current_profile_hash, decision='skipped')` rather than mutating the existing match row. Preserves audit trail. Multiple skips on the same `flat_id` are idempotent thanks to `UNIQUE (flat_id, profile_version_hash, decision)`. The view code (Task 4 already shipped this) hides any flat with a skipped row from Matches. Pass `match_id` in the URL so we look up the right `flat_id` even if the profile has changed since.

- [ ] **Step 6.1: Write `tests/test_applications.py`**

```python
"""Tests for the applications.py DB writers (skip + response)."""

from __future__ import annotations

import pytest


def _seed_match(conn) -> tuple[int, int]:
    """Insert one flat + one matches row; return (flat_id, match_id)."""
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES ('e1', 'wg-gesucht', 'https://x/1', 'T1', '2026-04-25', '2026-04-25')
        """
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, 'phash-1', 'match', '[]', '2026-04-25T00:00:00+00:00')
        """,
        (flat_id,),
    )
    return flat_id, int(cur.lastrowid)


def test_record_skip_inserts_skipped_row(tmp_db):
    from flatpilot.applications import record_skip

    flat_id, match_id = _seed_match(tmp_db)

    record_skip(tmp_db, match_id=match_id, profile_hash="phash-1")

    rows = tmp_db.execute(
        "SELECT decision FROM matches WHERE flat_id = ? ORDER BY id",
        (flat_id,),
    ).fetchall()
    decisions = [r["decision"] for r in rows]
    assert decisions == ["match", "skipped"]


def test_record_skip_is_idempotent(tmp_db):
    from flatpilot.applications import record_skip

    _flat_id, match_id = _seed_match(tmp_db)
    record_skip(tmp_db, match_id=match_id, profile_hash="phash-1")
    record_skip(tmp_db, match_id=match_id, profile_hash="phash-1")

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM matches WHERE decision = 'skipped'"
    ).fetchone()[0]
    assert cnt == 1


def test_record_skip_unknown_match_id_raises(tmp_db):
    from flatpilot.applications import record_skip

    with pytest.raises(LookupError, match="no match with id 999"):
        record_skip(tmp_db, match_id=999, profile_hash="phash-1")
```

- [ ] **Step 6.2: Run the test — expect failure**

```bash
.venv/bin/pytest tests/test_applications.py -v
```
Expected: import error — `flatpilot.applications` doesn't exist.

- [ ] **Step 6.3: Create `src/flatpilot/applications.py`**

```python
"""DB writers for user-driven actions on matches and applications.

These functions are kept off the request handlers so they can be unit
tested with the ``tmp_db`` fixture without a server thread. Each
function receives an open ``sqlite3.Connection`` so callers from
multiple threads (the dashboard server) supply their own per-thread
connection via ``flatpilot.database.get_conn()``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

ResponseStatus = Literal["viewing_invited", "rejected", "no_response"]


def record_skip(conn, *, match_id: int, profile_hash: str) -> None:
    """Insert a 'skipped' matches row for the flat referenced by ``match_id``.

    Audit-preserving: leaves the original 'match' row alone. Idempotent
    via the table's ``UNIQUE (flat_id, profile_version_hash, decision)``
    constraint.
    """
    row = conn.execute(
        "SELECT flat_id FROM matches WHERE id = ?", (match_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no match with id {match_id}")
    flat_id = int(row["flat_id"])
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO matches
            (flat_id, profile_version_hash, decision,
             decision_reasons_json, decided_at)
        VALUES (?, ?, 'skipped', '[]', ?)
        """,
        (flat_id, profile_hash, now),
    )


def record_response(
    conn,
    *,
    application_id: int,
    status: ResponseStatus,
    response_text: str,
) -> None:
    """Update an applications row with a landlord reply.

    Sets ``status``, ``response_text`` and ``response_received_at=now``.
    Allowed status values are constrained to the post-application
    transitions (the L4 path-set values 'submitted'/'failed' are
    rejected). Raises ``LookupError`` if the row doesn't exist and
    ``ValueError`` for an out-of-range status.
    """
    if status not in ("viewing_invited", "rejected", "no_response"):
        raise ValueError(f"unsupported response status: {status!r}")
    row = conn.execute(
        "SELECT id FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no application with id {application_id}")
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        UPDATE applications
           SET status = ?,
               response_text = ?,
               response_received_at = ?
         WHERE id = ?
        """,
        (status, response_text, now, application_id),
    )
```

- [ ] **Step 6.4: Add the skip endpoint to `src/flatpilot/server.py`**

Add the import block at the top:

```python
import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple

import flatpilot.fillers.wg_gesucht  # noqa: F401
import flatpilot.schemas  # noqa: F401
from flatpilot.applications import record_response, record_skip
from flatpilot.database import get_conn, init_db
from flatpilot.profile import load_profile, profile_hash
from flatpilot.view import generate_html
```

Add a route table at module level (just under `DEFAULT_PORT = 8765`):

```python
_SKIP_RE = re.compile(r"^/api/matches/(\d+)/skip$")
```

Replace `do_POST` with:

```python
    def do_POST(self) -> None:
        skip_match = _SKIP_RE.match(self.path)
        if skip_match:
            self._handle_skip(int(skip_match.group(1)))
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    def _handle_skip(self, match_id: int) -> None:
        profile = load_profile()
        if profile is None:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "no profile — run `flatpilot init` first"},
            )
            return
        init_db()
        conn = get_conn()
        try:
            record_skip(conn, match_id=match_id, profile_hash=profile_hash(profile))
        except LookupError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "match_id": match_id})

    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        payload = json.dumps(body)
        self._send(status, payload, content_type="application/json; charset=utf-8")
```

- [ ] **Step 6.5: Extend `tests/test_server.py` with skip endpoint tests**

Append:

```python
def _post(url: str, body: bytes = b"") -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _seed_match_with_profile(conn, tmp_path):
    """Insert a flat + match and write a profile so endpoints have one."""
    from flatpilot.profile import Profile, save_profile

    profile = Profile.load_example().model_copy(update={"city": "Berlin"})
    save_profile(profile)
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES ('e1', 'wg-gesucht', 'https://x/1', 'T1',
                  '2026-04-25', '2026-04-25')
        """
    )
    flat_id = int(cur.lastrowid)
    from flatpilot.profile import profile_hash

    cur = conn.execute(
        """
        INSERT INTO matches (
            flat_id, profile_version_hash, decision,
            decision_reasons_json, decided_at
        ) VALUES (?, ?, 'match', '[]', '2026-04-25T00:00:00+00:00')
        """,
        (flat_id, profile_hash(profile)),
    )
    return flat_id, int(cur.lastrowid)


def test_post_skip_marks_match_skipped(tmp_db, tmp_path):
    flat_id, match_id = _seed_match_with_profile(tmp_db, tmp_path)
    with _running_server(tmp_db) as port:
        status, body = _post(f"http://127.0.0.1:{port}/api/matches/{match_id}/skip")

    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM matches WHERE flat_id = ? AND decision = 'skipped'",
        (flat_id,),
    ).fetchone()[0]
    assert cnt == 1


def test_post_skip_unknown_match_id_returns_404(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)  # ensure profile exists
    with _running_server(tmp_db) as port:
        status, body = _post(f"http://127.0.0.1:{port}/api/matches/999/skip")

    assert status == 404
    payload = json.loads(body)
    assert "no match with id 999" in payload["error"]
```

Add `import json` near the top of `tests/test_server.py` if not already present.

- [ ] **Step 6.6: Run tests — expect pass**

```bash
.venv/bin/pytest tests/test_applications.py tests/test_server.py -v
```

- [ ] **Step 6.7: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/applications.py src/flatpilot/server.py tests/test_applications.py tests/test_server.py
.venv/bin/pytest -q
```

- [ ] **Step 6.8: Commit**

```bash
git add src/flatpilot/applications.py src/flatpilot/server.py tests/test_applications.py tests/test_server.py
git commit -m "FlatPilot-6axq: add skip endpoint and record_skip writer"
```

---

### Task 7: Apply endpoint — `POST /api/applications` spawns subprocess + wire Matches buttons  *(M2 — FlatPilot-6axq final)*

**Files:**
- Modify: `src/flatpilot/server.py` — add the apply endpoint.
- Modify: `src/flatpilot/view.py` — add the JS `fetch()` handlers for Apply / Skip buttons (the buttons themselves were rendered in Task 4).
- Modify: `tests/test_server.py` — add apply endpoint tests with subprocess mocked.
- Modify: `tests/test_view.py` — add a smoke test that the Apply / Skip JS handlers are present.

#### Why subprocess (recap)

- Matches the bead description ("Apply triggers L4 via a local helper (CLI spawn)").
- Isolates Playwright from the server thread (sync Playwright + threaded HTTPServer is a known foot-gun).
- Failure isolation: an apply crash doesn't take down the dashboard.

We wrap the spawn in `_spawn_apply(flat_id) -> dict` so tests can patch it.

- [ ] **Step 7.1: Extend `tests/test_server.py`**

Append:

```python
from unittest.mock import patch


def test_post_apply_spawns_subprocess_and_returns_result(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)
    fake_result = {"ok": True, "stdout_tail": "submitted · application_id=7", "returncode": 0}

    with _running_server(tmp_db) as port:
        with patch("flatpilot.server._spawn_apply", return_value=fake_result) as spawn:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/applications",
                body=json.dumps({"flat_id": 1}).encode("utf-8"),
            )

    assert status == 200
    spawn.assert_called_once_with(1)
    payload = json.loads(body)
    assert payload["ok"] is True
    assert "application_id=7" in payload["stdout_tail"]


def test_post_apply_subprocess_failure_returns_500(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)
    fake_result = {"ok": False, "stdout_tail": "NotAuthenticatedError: session expired", "returncode": 1}

    with _running_server(tmp_db) as port:
        with patch("flatpilot.server._spawn_apply", return_value=fake_result):
            status, body = _post(
                f"http://127.0.0.1:{port}/api/applications",
                body=json.dumps({"flat_id": 1}).encode("utf-8"),
            )

    assert status == 500
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "session expired" in payload["stdout_tail"]


def test_post_apply_invalid_body_returns_400(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)
    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications",
            body=b"not-json",
        )

    assert status == 400
    payload = json.loads(body)
    assert "flat_id" in payload["error"] or "json" in payload["error"].lower()
```

- [ ] **Step 7.2: Run the new tests — expect failure**

```bash
.venv/bin/pytest tests/test_server.py::test_post_apply_spawns_subprocess_and_returns_result -v
```
Expected: 404 returned (route not handled yet).

- [ ] **Step 7.3: Add the apply endpoint to `src/flatpilot/server.py`**

Add `subprocess` and `sys` imports near the top:

```python
import subprocess
import sys
```

Add the apply route constant near `_SKIP_RE`:

```python
_APPLY_PATH = "/api/applications"
```

Add `_spawn_apply` and the handler. Insert this `do_POST` rewrite (replacing the existing one):

```python
    def do_POST(self) -> None:
        skip_match = _SKIP_RE.match(self.path)
        if skip_match:
            self._handle_skip(int(skip_match.group(1)))
            return
        if self.path == _APPLY_PATH:
            self._handle_apply()
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    def _handle_apply(self) -> None:
        body = self._read_json_body()
        if body is None:
            return  # _read_json_body already responded.
        flat_id = body.get("flat_id")
        if not isinstance(flat_id, int):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "request body must be {'flat_id': <int>}"},
            )
            return
        result = _spawn_apply(flat_id)
        status = HTTPStatus.OK if result["ok"] else HTTPStatus.INTERNAL_SERVER_ERROR
        self._send_json(status, result)

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"error": "empty body, expected JSON"}
            )
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"invalid JSON: {exc}"},
            )
            return None
        if not isinstance(data, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON body must be an object"})
            return None
        return data
```

Add the spawn helper at module level:

```python
def _spawn_apply(flat_id: int) -> dict:
    """Run ``flatpilot apply <flat_id>`` as a subprocess.

    Captures stdout/stderr; returns a small dict the handler can ship to
    the browser. Stdout is tail-trimmed to ~2 KB so a verbose Playwright
    log doesn't bloat the JSON response.

    Patched in tests so we don't actually invoke the CLI.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "flatpilot", "apply", str(flat_id)],
        capture_output=True,
        text=True,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = combined[-2000:].strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout_tail": tail,
    }
```

- [ ] **Step 7.4: Wire the JS `fetch()` handlers in `src/flatpilot/view.py`**

Inside the inline `<script>` block (the IIFE near the end of `_render`), add these handlers right before the final `applyFilters();` call:

```javascript
  // M2: Apply / Skip buttons in the Matches pane.
  document.querySelectorAll('[data-pane="matches"] .actions button.apply').forEach(btn => {{
    btn.addEventListener('click', async () => {{
      const flatId = parseInt(btn.dataset.flatId, 10);
      if (!flatId) return;
      btn.disabled = true;
      const originalText = btn.textContent;
      btn.textContent = 'Applying…';
      try {{
        const resp = await fetch('/api/applications', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ flat_id: flatId }}),
        }});
        const data = await resp.json();
        if (resp.ok && data.ok) {{
          window.flatpilotToast('Applied: ' + (data.stdout_tail || 'ok'));
          btn.textContent = 'Applied ✓';
        }} else {{
          window.flatpilotToast('Apply failed: ' + (data.stdout_tail || data.error || resp.status), true);
          btn.textContent = originalText;
          btn.disabled = false;
        }}
      }} catch (e) {{
        window.flatpilotToast('Network error: ' + e.message, true);
        btn.textContent = originalText;
        btn.disabled = false;
      }}
    }});
  }});

  document.querySelectorAll('[data-pane="matches"] .actions button.skip').forEach(btn => {{
    btn.addEventListener('click', async () => {{
      const matchId = parseInt(btn.dataset.matchId, 10);
      if (!matchId) return;
      btn.disabled = true;
      try {{
        const resp = await fetch('/api/matches/' + matchId + '/skip', {{ method: 'POST' }});
        const data = await resp.json();
        if (resp.ok) {{
          // Hide the card so the user has visual confirmation.
          const card = btn.closest('.card');
          if (card) card.style.display = 'none';
          window.flatpilotToast('Skipped');
        }} else {{
          window.flatpilotToast('Skip failed: ' + (data.error || resp.status), true);
          btn.disabled = false;
        }}
      }} catch (e) {{
        window.flatpilotToast('Network error: ' + e.message, true);
        btn.disabled = false;
      }}
    }});
  }});
```

- [ ] **Step 7.5: Add a smoke test to `tests/test_view.py`**

Append:

```python
def test_generate_html_includes_apply_and_skip_handlers(tmp_db):
    html = generate_html()
    # Both fetch() targets must be present in the inline script.
    assert "/api/applications" in html
    assert "/api/matches/" in html
    assert "data-flat-id" in html
    assert "data-match-id" in html
```

- [ ] **Step 7.6: Run tests — expect pass**

```bash
.venv/bin/pytest tests/test_server.py tests/test_view.py -v
```

- [ ] **Step 7.7: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/server.py src/flatpilot/view.py tests/test_server.py tests/test_view.py
.venv/bin/pytest -q
```

- [ ] **Step 7.8: Commit (closes M2)**

```bash
git add src/flatpilot/server.py src/flatpilot/view.py tests/test_server.py tests/test_view.py
git commit -m "FlatPilot-6axq: wire apply endpoint and Matches-pane Apply/Skip handlers"
```

---

### Task 8: Applied tab — render rows with status badges + status filter  *(M3 — FlatPilot-lgmu)*

**Files:**
- Modify: `src/flatpilot/view.py` — replace the `_applied_pane()` placeholder with a real implementation (filter dropdown + table/grid of rows + status badges).
- Modify: `tests/test_view.py` — add Applied-pane render tests.

- [ ] **Step 8.1: Extend `tests/test_view.py`**

Append:

```python
def _insert_application(conn, *, status: str, applied_at: str, title: str = "Applied flat") -> int:
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES (?, 'wg-gesucht', 'https://x/' || ?, ?,
                  '2026-04-25', '2026-04-25')
        """,
        (f"ext-{title}", title, title),
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            applied_at, method, message_sent, attachments_sent_json,
            status
        ) VALUES (?, 'wg-gesucht', 'https://x/listing', ?, ?, 'manual',
                  'msg', '[]', ?)
        """,
        (flat_id, title, applied_at, status),
    )
    return int(cur.lastrowid)


def test_applied_pane_renders_rows_in_applied_at_desc(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-20T10:00:00+00:00", title="Older")
    _insert_application(tmp_db, status="viewing_invited", applied_at="2026-04-25T10:00:00+00:00", title="Newest")
    html = generate_html()

    pane_start = html.index('data-pane="applied"')
    pane_end = html.index('data-pane="responses"')
    pane = html[pane_start:pane_end]

    # Newest must appear before older in the pane.
    assert pane.index("Newest") < pane.index("Older")


def test_applied_pane_renders_status_badges(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-25T10:00:00+00:00", title="A1")
    _insert_application(tmp_db, status="failed", applied_at="2026-04-24T10:00:00+00:00", title="A2")
    html = generate_html()

    pane_start = html.index('data-pane="applied"')
    pane_end = html.index('data-pane="responses"')
    pane = html[pane_start:pane_end]
    assert "badge-submitted" in pane
    assert "badge-failed" in pane


def test_applied_pane_renders_status_filter(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-25T10:00:00+00:00")
    html = generate_html()
    assert 'id="f-app-status"' in html
    # All five allowed status values plus "any" must be selectable.
    for value in ("any", "submitted", "failed", "viewing_invited", "rejected", "no_response"):
        assert f'value="{value}"' in html
```

- [ ] **Step 8.2: Run the test — expect failure**

Expected: index errors / missing IDs (placeholder pane has no `id="f-app-status"`).

- [ ] **Step 8.3: Replace `_applied_pane()` in `src/flatpilot/view.py`**

```python
_APPLICATION_STATUSES: tuple[str, ...] = (
    "submitted",
    "failed",
    "viewing_invited",
    "rejected",
    "no_response",
)


def _applied_pane(applications: list[dict[str, Any]]) -> str:
    if not applications:
        return (
            '<p class="empty">No applications yet. Apply from the Matches '
            "tab to populate this view.</p>"
        )
    status_options = '<option value="any">any</option>'
    for s in _APPLICATION_STATUSES:
        status_options += f'<option value="{s}">{escape(s.replace("_", " "))}</option>'

    rows_html = "\n".join(_application_row(app) for app in applications)

    return (
        '<div class="filters">'
        f'<label>Status <select id="f-app-status">{status_options}</select></label>'
        "</div>"
        f'<div class="cards">{rows_html}</div>'
    )


def _application_row(app: dict[str, Any]) -> str:
    title = escape(str(app.get("title") or "Untitled listing"))
    status = str(app.get("status") or "")
    badge = (
        f'<span class="badge badge-{escape(status, quote=True)}">'
        f"{escape(status.replace('_', ' '))}</span>"
    )
    rent = _fmt_rent(app.get("rent_warm_eur"))
    rooms = _fmt_rooms(app.get("rooms"))
    district = app.get("district") or ""
    applied_at = escape(str(app.get("applied_at") or ""))
    listing_url = str(app.get("listing_url") or "")
    response_at = app.get("response_received_at")

    response_html = ""
    if response_at:
        response_html = (
            f'<dt>Responded</dt><dd>{escape(str(response_at))}</dd>'
        )

    open_html = ""
    if listing_url:
        open_html = (
            '<div class="actions">'
            f'<a class="open" href="{escape(listing_url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">View listing</a>'
            "</div>"
        )

    return (
        f'<article class="card application" data-status="{escape(status, quote=True)}">'
        f'<h3>{title} {badge}</h3>'
        f'<dl>'
        f'<dt>Applied</dt><dd>{applied_at}</dd>'
        f"<dt>Warmmiete</dt><dd>{escape(rent)}</dd>"
        f"<dt>Rooms</dt><dd>{escape(rooms)}</dd>"
        + (f"<dt>District</dt><dd>{escape(district)}</dd>" if district else "")
        + response_html
        + "</dl>"
        + open_html
        + "</article>"
    )
```

Wire the status-filter JS into the inline `<script>` block (just before `applyFilters();`):

```javascript
  // M3: Applied-tab status filter.
  const appStatusFilter = document.getElementById('f-app-status');
  if (appStatusFilter) {{
    function filterApplications() {{
      const want = appStatusFilter.value;
      document.querySelectorAll('[data-pane="applied"] .card.application').forEach(card => {{
        const s = card.dataset.status;
        card.style.display = (want === 'any' || s === want) ? '' : 'none';
      }});
    }}
    appStatusFilter.addEventListener('change', filterApplications);
  }}
```

- [ ] **Step 8.4: Run the tests — expect pass**

```bash
.venv/bin/pytest tests/test_view.py -v
```

- [ ] **Step 8.5: Lint and full suite**

```bash
.venv/bin/ruff check src/flatpilot/view.py tests/test_view.py
.venv/bin/pytest -q
```

- [ ] **Step 8.6: Commit**

```bash
git add src/flatpilot/view.py tests/test_view.py
git commit -m "FlatPilot-lgmu: render Applied tab with status badges and filter"
```

---

### Task 9: Responses tab + paste-reply form + `POST /api/applications/<id>/response`  *(M4 — FlatPilot-ejn2)*

**Files:**
- Modify: `src/flatpilot/server.py` — add the response endpoint.
- Modify: `src/flatpilot/view.py` — replace the `_responses_pane()` placeholder with a per-application form; add JS handler.
- Modify: `tests/test_server.py` — add response endpoint tests.
- Modify: `tests/test_view.py` — add Responses-pane smoke test.

- [ ] **Step 9.1: Extend `tests/test_applications.py`**

Append:

```python
def test_record_response_updates_row(tmp_db):
    from flatpilot.applications import record_response

    flat_id, _ = _seed_match(tmp_db)
    cur = tmp_db.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            applied_at, method, message_sent, attachments_sent_json, status
        ) VALUES (?, 'wg-gesucht', 'https://x/1', 'T1',
                  '2026-04-25T10:00:00+00:00', 'manual', 'msg', '[]', 'submitted')
        """,
        (flat_id,),
    )
    app_id = int(cur.lastrowid)

    record_response(
        tmp_db,
        application_id=app_id,
        status="viewing_invited",
        response_text="Komm gern am Samstag um 15 Uhr",
    )

    row = tmp_db.execute(
        "SELECT status, response_text, response_received_at FROM applications WHERE id = ?",
        (app_id,),
    ).fetchone()
    assert row["status"] == "viewing_invited"
    assert "Komm gern" in row["response_text"]
    assert row["response_received_at"] is not None


def test_record_response_unknown_id_raises(tmp_db):
    from flatpilot.applications import record_response

    with pytest.raises(LookupError, match="no application with id 999"):
        record_response(
            tmp_db, application_id=999, status="rejected", response_text=""
        )


def test_record_response_invalid_status_raises(tmp_db):
    from flatpilot.applications import record_response

    with pytest.raises(ValueError, match="unsupported response status"):
        record_response(
            tmp_db, application_id=1, status="submitted", response_text=""  # type: ignore[arg-type]
        )
```

- [ ] **Step 9.2: Extend `tests/test_server.py`**

Append:

```python
def _seed_application(conn) -> int:
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            scraped_at, first_seen_at
        ) VALUES ('e2', 'wg-gesucht', 'https://x/2', 'T2',
                  '2026-04-25', '2026-04-25')
        """
    )
    flat_id = int(cur.lastrowid)
    cur = conn.execute(
        """
        INSERT INTO applications (
            flat_id, platform, listing_url, title,
            applied_at, method, message_sent, attachments_sent_json, status
        ) VALUES (?, 'wg-gesucht', 'https://x/2', 'T2',
                  '2026-04-25T10:00:00+00:00', 'manual', 'msg', '[]', 'submitted')
        """,
        (flat_id,),
    )
    return int(cur.lastrowid)


def test_post_response_updates_row(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)  # ensures profile exists
    app_id = _seed_application(tmp_db)
    payload = {"status": "viewing_invited", "response_text": "Komm am Samstag"}

    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications/{app_id}/response",
            body=json.dumps(payload).encode("utf-8"),
        )

    assert status == 200, body
    data = json.loads(body)
    assert data["ok"] is True

    row = tmp_db.execute(
        "SELECT status, response_text FROM applications WHERE id = ?", (app_id,)
    ).fetchone()
    assert row["status"] == "viewing_invited"
    assert "Komm am Samstag" in row["response_text"]


def test_post_response_invalid_status_returns_400(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)
    app_id = _seed_application(tmp_db)
    payload = {"status": "submitted", "response_text": ""}

    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications/{app_id}/response",
            body=json.dumps(payload).encode("utf-8"),
        )

    assert status == 400
    data = json.loads(body)
    assert "unsupported response status" in data["error"]


def test_post_response_unknown_id_returns_404(tmp_db, tmp_path):
    _seed_match_with_profile(tmp_db, tmp_path)
    payload = {"status": "rejected", "response_text": ""}

    with _running_server(tmp_db) as port:
        status, body = _post(
            f"http://127.0.0.1:{port}/api/applications/9999/response",
            body=json.dumps(payload).encode("utf-8"),
        )

    assert status == 404
    data = json.loads(body)
    assert "no application with id 9999" in data["error"]
```

- [ ] **Step 9.3: Run the tests — expect failure**

`.venv/bin/pytest tests/test_applications.py tests/test_server.py -v`

Expected: route 404 for response endpoint; missing `record_response` (already exists from Task 6 — those tests pass) — only the server tests fail.

- [ ] **Step 9.4: Add the response endpoint to `src/flatpilot/server.py`**

Add the route regex near `_SKIP_RE`:

```python
_RESPONSE_RE = re.compile(r"^/api/applications/(\d+)/response$")
```

Update `do_POST`:

```python
    def do_POST(self) -> None:
        skip_match = _SKIP_RE.match(self.path)
        if skip_match:
            self._handle_skip(int(skip_match.group(1)))
            return
        response_match = _RESPONSE_RE.match(self.path)
        if response_match:
            self._handle_response(int(response_match.group(1)))
            return
        if self.path == _APPLY_PATH:
            self._handle_apply()
            return
        self._send(HTTPStatus.NOT_FOUND, f"not found: {self.path}\n")

    def _handle_response(self, application_id: int) -> None:
        body = self._read_json_body()
        if body is None:
            return
        status_value = body.get("status")
        response_text = body.get("response_text", "")
        if not isinstance(status_value, str) or not isinstance(response_text, str):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "body must be {'status': str, 'response_text': str}"},
            )
            return
        init_db()
        conn = get_conn()
        try:
            record_response(
                conn,
                application_id=application_id,
                status=status_value,  # type: ignore[arg-type]
                response_text=response_text,
            )
        except LookupError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "application_id": application_id})
```

- [ ] **Step 9.5: Replace `_responses_pane()` in `src/flatpilot/view.py`**

```python
def _responses_pane(applications: list[dict[str, Any]]) -> str:
    if not applications:
        return (
            '<p class="empty">No responses to record. Apply to a flat first.</p>'
        )
    cards = "\n".join(_response_form(app) for app in applications)
    return f'<div class="cards">{cards}</div>'


def _response_form(app: dict[str, Any]) -> str:
    app_id = app.get("id")
    title = escape(str(app.get("title") or "Untitled listing"))
    current_status = str(app.get("status") or "")
    badge = (
        f'<span class="badge badge-{escape(current_status, quote=True)}">'
        f"{escape(current_status.replace('_', ' '))}</span>"
    )
    response_text = escape(str(app.get("response_text") or ""))
    response_at = app.get("response_received_at") or ""

    # Only post-application transitions are exposed in the form;
    # 'submitted' / 'failed' are L4-set and not user-editable here.
    options_html = ""
    for option in ("viewing_invited", "rejected", "no_response"):
        options_html += (
            f'<option value="{option}">{escape(option.replace("_", " "))}</option>'
        )

    return (
        f'<article class="card response" data-application-id="{app_id}">'
        f"<h3>{title} {badge}</h3>"
        + (
            f'<p class="reasons">Last response recorded: {escape(str(response_at))}</p>'
            if response_at
            else ""
        )
        + '<form class="response-form">'
        + '<label>New status <select class="resp-status">'
        + options_html
        + "</select></label>"
        + '<label>Reply text<br>'
        + f'<textarea class="resp-text" rows="4" cols="60">{response_text}</textarea>'
        + "</label>"
        + '<button type="submit" class="resp-submit">Save</button>'
        + "</form>"
        + "</article>"
    )
```

Add CSS for the form (inside the `<style>` block, near the badge styles):

```css
  .response-form {{ display: grid; gap: 0.5rem; margin-top: 0.5rem; }}
  .response-form textarea {{ width: 100%; font: inherit; }}
  .response-form button.resp-submit {{ font: inherit; padding: 0.4rem 1rem;
      background: var(--accent); color: white; border: none; border-radius: 4px; cursor: pointer; }}
  .response-form button.resp-submit:disabled {{ opacity: 0.5; cursor: not-allowed; }}
```

Wire the JS submit handler in the inline `<script>` block (before `applyFilters();`):

```javascript
  // M4: Responses-tab paste-reply form.
  document.querySelectorAll('[data-pane="responses"] form.response-form').forEach(form => {{
    form.addEventListener('submit', async (ev) => {{
      ev.preventDefault();
      const card = form.closest('.card.response');
      if (!card) return;
      const appId = parseInt(card.dataset.applicationId, 10);
      const status = form.querySelector('.resp-status').value;
      const text = form.querySelector('.resp-text').value;
      const submitBtn = form.querySelector('.resp-submit');
      submitBtn.disabled = true;
      const original = submitBtn.textContent;
      submitBtn.textContent = 'Saving…';
      try {{
        const resp = await fetch('/api/applications/' + appId + '/response', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ status: status, response_text: text }}),
        }});
        const data = await resp.json();
        if (resp.ok) {{
          window.flatpilotToast('Response saved');
          submitBtn.textContent = 'Saved ✓';
          setTimeout(() => {{ submitBtn.textContent = original; submitBtn.disabled = false; }}, 1500);
        }} else {{
          window.flatpilotToast('Save failed: ' + (data.error || resp.status), true);
          submitBtn.textContent = original;
          submitBtn.disabled = false;
        }}
      }} catch (e) {{
        window.flatpilotToast('Network error: ' + e.message, true);
        submitBtn.textContent = original;
        submitBtn.disabled = false;
      }}
    }});
  }});
```

- [ ] **Step 9.6: Add Responses-pane test to `tests/test_view.py`**

Append:

```python
def test_responses_pane_renders_form_per_application(tmp_db):
    _insert_application(tmp_db, status="submitted", applied_at="2026-04-25T10:00:00+00:00", title="ResponseFlat")
    html = generate_html()
    pane_start = html.index('data-pane="responses"')
    pane = html[pane_start:]
    assert "ResponseFlat" in pane
    assert "form" in pane
    assert "resp-status" in pane
    assert "resp-text" in pane
    # Status options exposed are limited to post-application transitions.
    assert 'value="viewing_invited"' in pane
    assert 'value="rejected"' in pane
    assert 'value="no_response"' in pane
    # 'submitted' / 'failed' are L4-set; the form must NOT offer them.
    pane_form_segment = pane[pane.index("form"):pane.index("</form>")]
    assert 'value="submitted"' not in pane_form_segment
    assert 'value="failed"' not in pane_form_segment
```

- [ ] **Step 9.7: Run all tests**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 9.8: Lint**

```bash
.venv/bin/ruff check src/flatpilot/server.py src/flatpilot/view.py tests/test_applications.py tests/test_server.py tests/test_view.py
```

- [ ] **Step 9.9: Commit (closes M3 → M4 → Epic M)**

```bash
git add src/flatpilot/applications.py src/flatpilot/server.py src/flatpilot/view.py tests/test_applications.py tests/test_server.py tests/test_view.py
git commit -m "FlatPilot-ejn2: add Responses tab and response endpoint"
```

---

## Self-review

### Spec coverage

- **L4** (cjtz) — ✅ Filler refactor (Task 1), orchestrator (Task 2), CLI (Task 3). `--dry-run` is implemented; default mode submits and writes a row; failure paths write `status='failed'` rows (filler errors only) and re-raise; pre-condition errors raise without writing rows.
- **M1** (72z5) — ✅ Tab UI (Task 4), HTTP server (Task 5).
- **M2** (6axq) — ✅ Skip endpoint + handler (Task 6), Apply endpoint with subprocess spawn + button JS (Task 7).
- **M3** (lgmu) — ✅ Applied tab with status badges and status filter (Task 8).
- **M4** (ejn2) — ✅ Responses tab with paste-reply form + endpoint (Task 9).

### Placeholder scan

- No "TBD" / "TODO" / "fill in later" — every step shows the actual code.
- No "similar to Task N" — code is repeated where needed.
- "Add appropriate error handling" doesn't appear.

### Type / signature consistency

- `Filler.fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None) -> FillReport` is consistent across Tasks 1, 2, 3.
- `apply_to_flat(flat_id, *, dry_run=False, screenshot_dir=None) -> ApplyOutcome` consistent in Tasks 2 and 3.
- `record_skip(conn, *, match_id, profile_hash)` and `record_response(conn, *, application_id, status, response_text)` consistent across Tasks 6 and 9.
- `generate_html(conn=None) -> str` consistent across Tasks 4, 5, 8, 9.
- Server URL paths consistent: `/api/matches/<int>/skip`, `/api/applications`, `/api/applications/<int>/response`.

### Risk register / non-blocking notes

- Task 1's submit-button selector is the best-effort guess; if WG-Gesucht's live form changes, the post-submit URL guard catches it. A real submit verification will need a follow-up bead with empirical testing on the live form (similar to FlatPilot-fze, which verified the form selectors).
- The dashboard server has no request rate limiting. Single-user / localhost; not a concern.
- `subprocess.run` blocks the request thread for the duration of the apply; in the threaded server this is fine (other requests still served). UI shows "Applying…" until it returns; expect 30s–2min for a real Playwright run.
- Tests don't drive a real browser. Filler integration is covered by the existing pre-conditions on FlatPilot-4zkt (live verification was scoped to FlatPilot-fze).
