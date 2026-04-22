#!/usr/bin/env python3
"""Kleinanzeigen anti-bot survival probe.

Time-boxed investigation before FlatPilot-3hu2: can a Playwright session
poll Kleinanzeigen's Wohnung-mieten search at 60-120s cadence without
tripping Cloudflare challenges? This script is **not** a scraper — it
does not parse listings, only classifies the outcome of each request so
we can calibrate D2's cadence and decide whether stealth flags or
request randomization are needed before the scraper design crystallises.

Why this diverges from wg_probe.py
----------------------------------
- Kleinanzeigen fronts with Cloudflare (challenges.cloudflare.com /
  Turnstile + "Just a moment" interstitials), not hCaptcha. The
  classification logic reflects that.
- Kleinanzeigen's location ID scheme (``c203l<id>``) is less
  well-documented than WG-Gesucht's numeric city IDs. Instead of baking
  guesses into a CITY_IDS map, this probe takes ``--url`` as a required
  argument — the user pastes a working search URL from their browser
  once, and the probe reuses it. That keeps the probe from inheriting a
  bad guess into the eventual scraper.
- "ok" detection is generic, not selector-based: status 200 + no
  captcha iframe + no block keywords + non-trivial body + body mentions
  the city. The probe's job is anti-bot characterization; committing to
  a DOM selector is the scraper's job (FlatPilot-3hu2).

Outcome codes
-------------
- ``ok``                  — search page rendered, no challenge signals.
- ``captcha``             — Turnstile / Cloudflare challenge iframe present.
- ``challenge_cloudflare`` — body contains "Just a moment" / "Checking
                             your browser" (soft challenge interstitial).
- ``block_http_<n>``       — HTTP status >= 400 (403 / 429 / 503 are
                             typical Cloudflare block signals).
- ``block_keyword``        — body matches a known rate-limit phrase.
- ``unknown``              — no challenge, no block, but the city name
                             isn't in the body and size is small —
                             probably a DOM change or stale URL.
- ``error_<Class>``        — Playwright navigation raised.

Running
-------
First, sanity-check the URL in a browser, then smoke-test for 30 minutes::

    docker compose run --rm --entrypoint python flatpilot \\
        scripts/kleinanzeigen_probe.py \\
        --url 'https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/c203l3331' \\
        --city Berlin --interval 90 --duration-min 30

If the smoke run is all ``ok``, extend to a 4-hour run to match D0
discipline::

    docker compose run --rm --entrypoint python flatpilot \\
        scripts/kleinanzeigen_probe.py \\
        --url 'https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/c203l3331' \\
        --city Berlin --interval 90 --duration-min 240

After the probe finishes, paste the summary table + any noteworthy
observations into ``bd update FlatPilot-3hu2 --notes=...`` so the
scraper design picks them up.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
)
HOMEPAGE_URL = "https://www.kleinanzeigen.de/"
NAV_TIMEOUT_MS = 30_000
WARM_UP_SETTLE_SEC = 3
MIN_BODY_CHARS = 5_000  # below this, the "ok" body is suspiciously thin

COOKIE_ACCEPT_SELECTORS = (
    "#gdpr-banner-accept",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
    "button:has-text('Einverstanden')",
    "button:has-text('Zustimmen')",
    "button:has-text('Accept')",
)
# Cloudflare + Turnstile serve challenges via these iframe origins.
CAPTCHA_IFRAME_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='recaptcha']",
)
# Soft interstitial Cloudflare shows during JS challenges.
CHALLENGE_KEYWORDS = (
    "just a moment",
    "checking your browser",
    "einen moment",
)
# Hard rate-limit / block phrases.
BLOCK_KEYWORDS = (
    "unusual traffic",
    "ungewöhnlichen datenverkehr",
    "ungewoehnlichen datenverkehr",
    "too many requests",
    "access denied",
)


def _app_dir() -> Path:
    import os

    override = os.environ.get("FLATPILOT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".flatpilot"


APP_DIR = _app_dir()
SESSIONS_DIR = APP_DIR / "sessions" / "kleinanzeigen"
LOGS_DIR = APP_DIR / "logs"
STATE_FILE = SESSIONS_DIR / "state.json"
LOG_FILE = LOGS_DIR / "kleinanzeigen_probe.log"


def _configure_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kleinanzeigen_probe")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file = logging.FileHandler(LOG_FILE)
    file.setFormatter(fmt)
    logger.addHandler(stream)
    logger.addHandler(file)
    return logger


def _accept_cookie_banner(page, logger: logging.Logger) -> None:
    for selector in COOKIE_ACCEPT_SELECTORS:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2_000):
                btn.click()
                logger.info("accepted consent banner via %s", selector)
                return
        except Exception:
            continue
    logger.info("no consent banner matched (probably already accepted)")


def _classify(page, city: str) -> str:
    try:
        for selector in CAPTCHA_IFRAME_SELECTORS:
            if page.locator(selector).count() > 0:
                return "captcha"
        body = (page.content() or "").lower()
    except Exception as exc:
        return f"error_{type(exc).__name__}"

    if any(kw in body for kw in CHALLENGE_KEYWORDS):
        return "challenge_cloudflare"
    if any(kw in body for kw in BLOCK_KEYWORDS):
        return "block_keyword"
    # Generic "ok" heuristic — avoids committing to a DOM selector the
    # scraper will own. A real search page is large and contains the
    # requested city somewhere in the rendered content.
    if len(body) >= MIN_BODY_CHARS and city.lower() in body:
        return "ok"
    return "unknown"


def _poll(context, url: str, city: str) -> str:
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        status = response.status if response is not None else 0
        if status >= 400:
            return f"block_http_{status}"
        return _classify(page, city)
    except Exception as exc:
        return f"error_{type(exc).__name__}"
    finally:
        page.close()


def _save_state(context) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(STATE_FILE))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url",
        required=True,
        help=(
            "Full Kleinanzeigen search URL (copy from your browser). "
            "Example: https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/c203l3331"
        ),
    )
    parser.add_argument(
        "--city",
        required=True,
        help="City name used for the 'ok' body-content check (e.g. Berlin)",
    )
    parser.add_argument(
        "--interval", type=int, default=90, help="Seconds between polls (default: 90)"
    )
    parser.add_argument(
        "--duration-min",
        type=int,
        default=30,
        help="Total probe duration in minutes (default: 30 = smoke run)",
    )
    headless = parser.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true", default=True)
    headless.add_argument("--no-headless", dest="headless", action="store_false")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logger = _configure_logger()

    if args.interval < 30:
        logger.warning("interval < 30s is aggressive — consider raising to at least 60s")

    logger.info(
        "probe starting · url=%s · city=%s · interval=%ss · duration=%smin · headless=%s",
        args.url,
        args.city,
        args.interval,
        args.duration_min,
        args.headless,
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "playwright is not installed. Run inside Docker "
            "(`docker compose run --rm --entrypoint python flatpilot "
            "scripts/kleinanzeigen_probe.py ...`) or "
            "`pip install -e '.[dev]' && playwright install chromium`."
        )
        return 1

    storage_state = str(STATE_FILE) if STATE_FILE.exists() else None
    deadline = datetime.now(UTC) + timedelta(minutes=args.duration_min)
    counts: Counter[str] = Counter()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1280, "height": 900},
            storage_state=storage_state,
        )
        try:
            warm_page = context.new_page()
            warm_page.goto(HOMEPAGE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            _accept_cookie_banner(warm_page, logger)
            time.sleep(WARM_UP_SETTLE_SEC)
            warm_page.close()
            _save_state(context)
            logger.info("warm-up complete; state saved to %s", STATE_FILE)

            poll_num = 0
            while datetime.now(UTC) < deadline:
                poll_num += 1
                outcome = _poll(context, args.url, args.city)
                counts[outcome] += 1
                logger.info("poll %d: %s", poll_num, outcome)
                _save_state(context)
                if datetime.now(UTC) + timedelta(seconds=args.interval) >= deadline:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.warning("interrupted by user — wrapping up")
        finally:
            try:
                _save_state(context)
            except Exception as exc:
                logger.warning("final state save failed: %s", exc)
            context.close()
            browser.close()

    logger.info("=== summary ===")
    if not counts:
        logger.info("no polls recorded")
    else:
        total = sum(counts.values())
        for outcome, n in counts.most_common():
            logger.info("  %-24s %4d  (%.1f%%)", outcome, n, 100 * n / total)
    logger.info("log file: %s", LOG_FILE)
    logger.info("cookie state: %s", STATE_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
