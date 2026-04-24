"""Classifier for Playwright responses from anti-bot-fronted sites.

The Kleinanzeigen D0 probe established a small, reliable set of block
heuristics — captcha iframes, Cloudflare interstitial keywords, and
rate-limit phrases. This module exposes them as reusable primitives so
the Kleinanzeigen scraper (and, later, any other Cloudflare-fronted
site) can call one function per check instead of reimplementing the
probe's ``_classify`` method each time.

Outcome vocabulary
------------------
- ``ok``                   — page mentions the target city, body is
                             substantial, no challenge or block signals.
- ``challenge_cloudflare`` — body contains a tight Cloudflare soft
                             challenge phrase.
- ``block_keyword``        — body contains a hard rate-limit phrase.
- ``unknown``              — thin body or no city mention; the scraper
                             treats this as *parse what you've got* and
                             moves on (a legitimate empty search is
                             thin by definition).

Tightening note: the probe included the bare phrase ``einen moment`` in
its challenge keyword list. That phrase appears in benign German
loading-UI strings — ``test_plain_einen_moment_is_not_a_challenge``
nails that down. We match only ``einen moment, bitte`` which is what
Cloudflare's German-locale interstitial actually renders.
"""

from __future__ import annotations

from typing import Any, Literal

from flatpilot.scrapers.session import ChallengeDetectedError

__all__ = [
    "BLOCK_KEYWORDS",
    "CAPTCHA_IFRAME_SELECTORS",
    "CHALLENGE_KEYWORDS",
    "ChallengeDetectedError",
    "MIN_BODY_CHARS",
    "Outcome",
    "classify_content",
    "has_captcha_iframe",
]


Outcome = Literal["ok", "challenge_cloudflare", "block_keyword", "unknown"]


# A real search page has a big rendered body; anything substantially
# smaller is either an empty search or a stub response we shouldn't
# try to parse. Proven out by the D0 probe over 178 ok polls.
MIN_BODY_CHARS: int = 5_000

# Cloudflare + Turnstile serve challenges via these iframe origins.
CAPTCHA_IFRAME_SELECTORS: tuple[str, ...] = (
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='recaptcha']",
)

# Soft Cloudflare interstitial phrases. The tightened "einen moment,
# bitte" is deliberate — "einen moment" alone hits benign loading UI.
CHALLENGE_KEYWORDS: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "einen moment, bitte",
)

# Hard rate-limit / block phrases.
BLOCK_KEYWORDS: tuple[str, ...] = (
    "unusual traffic",
    "ungewöhnlichen datenverkehr",
    "ungewoehnlichen datenverkehr",
    "too many requests",
    "access denied",
)


def has_captcha_iframe(page: Any) -> bool:
    """Return ``True`` if the live ``Page`` contains a Turnstile / captcha iframe.

    Must be called *before* ``page.content()`` because some challenge
    iframes are stripped from the serialised HTML by Cloudflare.
    """
    for selector in CAPTCHA_IFRAME_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            # Locator lookup can throw on a navigation-in-progress page;
            # treat as no-match rather than propagating.
            continue
    return False


def classify_content(html: str, city: str) -> Outcome:
    """Pure-function classifier over a response body.

    ``city`` is folded to lowercase and checked against the body; a
    genuine search page always echoes the requested city name in its
    header, breadcrumbs, or result summary.
    """
    body = (html or "").lower()
    if any(kw in body for kw in CHALLENGE_KEYWORDS):
        return "challenge_cloudflare"
    if any(kw in body for kw in BLOCK_KEYWORDS):
        return "block_keyword"
    if len(body) >= MIN_BODY_CHARS and city.lower() in body:
        return "ok"
    return "unknown"
