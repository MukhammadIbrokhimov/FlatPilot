"""Unit tests for src/flatpilot/scrapers/block_detect.py.

The classifier is the gatekeeper between "parse these listings" and
"back off, we're flagged." False negatives cost us applied flats; false
positives trigger unnecessary cool-offs and the user never sees
anything. The tests below exercise both directions:

- Real Kleinanzeigen search HTML (tests/fixtures/kleinanzeigen/search.html)
  must classify as "ok" — proves we don't regress a happy path after
  the "einen moment" tightening.
- Fabricated challenge + block HTML exercises each outcome branch.
- The "unknown" outcome stays pass-through — short result pages are
  legitimate, not blocks.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "kleinanzeigen" / "search.html"


def test_real_search_page_is_ok() -> None:
    """The shipped fixture (from the live site) must not trip any block heuristic."""
    from flatpilot.scrapers.block_detect import classify_content

    html = FIXTURE.read_text()
    assert classify_content(html, city="Berlin") == "ok"


def test_cloudflare_interstitial_classifies_as_challenge() -> None:
    from flatpilot.scrapers.block_detect import classify_content

    html = """<html><body>
      <h1>Just a moment...</h1>
      <p>Checking your browser before accessing the site.</p>
    </body></html>"""
    assert classify_content(html, city="Berlin") == "challenge_cloudflare"


def test_german_cloudflare_interstitial_classifies_as_challenge() -> None:
    """Tightened form: 'Einen Moment, bitte' is the real Cloudflare DE text."""
    from flatpilot.scrapers.block_detect import classify_content

    html = """<html><body>
      <h1>Einen Moment, bitte...</h1>
      <p>Wir überprüfen Ihren Browser.</p>
    </body></html>"""
    assert classify_content(html, city="Berlin") == "challenge_cloudflare"


def test_plain_einen_moment_is_not_a_challenge() -> None:
    """Bare 'einen moment' (without bitte) is common benign loading text."""
    from flatpilot.scrapers.block_detect import classify_content

    html = (
        "<html><body><h1>Berlin Mietwohnungen</h1>"
        "<p>Einen Moment - wir laden weitere Ergebnisse.</p>"
        + "<div>" + ("filler " * 2000) + "</div>"
        + "</body></html>"
    )
    assert classify_content(html, city="Berlin") == "ok"


def test_block_keyword_classifies_as_block() -> None:
    from flatpilot.scrapers.block_detect import classify_content

    for phrase in (
        "Your IP address has been flagged for unusual traffic.",
        "Ungewöhnlichen Datenverkehr festgestellt.",
        "Too many requests from your network.",
        "Access denied — please try again later.",
    ):
        html = f"<html><body>{phrase}</body></html>"
        assert (
            classify_content(html, city="Berlin") == "block_keyword"
        ), f"expected block_keyword for: {phrase!r}"


def test_short_result_page_is_unknown_not_block() -> None:
    """A legitimate search with few results must pass through to the parser."""
    from flatpilot.scrapers.block_detect import classify_content

    html = "<html><body><main>No results for Berlin today.</main></body></html>"
    assert classify_content(html, city="Berlin") == "unknown"


def test_ok_requires_city_in_body() -> None:
    """Large pages that don't mention the city look stale/broken, not blocked."""
    from flatpilot.scrapers.block_detect import classify_content

    html = "<html><body><main>" + ("filler " * 2000) + "</main></body></html>"
    assert classify_content(html, city="Berlin") == "unknown"


def test_has_captcha_iframe_returns_true_when_iframe_present() -> None:
    from flatpilot.scrapers.block_detect import has_captcha_iframe

    class _Locator:
        def __init__(self, count: int) -> None:
            self._count = count

        def count(self) -> int:
            return self._count

    class _Page:
        def __init__(self, hit_selector: str | None) -> None:
            self._hit = hit_selector

        def locator(self, selector: str):
            return _Locator(1 if selector == self._hit else 0)

    assert has_captcha_iframe(_Page("iframe[src*='challenges.cloudflare.com']")) is True
    assert has_captcha_iframe(_Page("iframe[src*='turnstile']")) is True
    assert has_captcha_iframe(_Page("iframe[src*='hcaptcha.com']")) is True
    assert has_captcha_iframe(_Page(None)) is False


def test_challenge_detected_error_is_exported() -> None:
    """block_detect re-exports the canonical exception from session.py."""
    from flatpilot.scrapers.block_detect import ChallengeDetectedError as Re
    from flatpilot.scrapers.session import ChallengeDetectedError as Canon

    assert Re is Canon
