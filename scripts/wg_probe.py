#!/usr/bin/env python3
"""WG-Gesucht anti-bot survival probe.

FlatPilot-h8ug (D0): time-boxed investigation — can a Playwright session
poll WG-Gesucht Wohnungen search every 60-120 s for multiple hours without
tripping hCaptcha? This script is the empirical test. It is **not** a
scraper — it does not parse listings, it only classifies the outcome of
each request so we can decide D2's cadence and warm-up strategy.

What it does
------------
1. Reuses any previously saved Playwright storage state at
   ``~/.flatpilot/sessions/wg-gesucht/state.json`` (cookies + localStorage).
2. Cookie warm-up: loads the WG-Gesucht homepage once, accepts the consent
   banner if present, lets the page settle.
3. Polling loop: every ``--interval`` seconds until ``--duration-min``
   minutes elapse, navigates to the Wohnungen search results URL for the
   chosen city and classifies the response.
4. Every poll is appended to ``~/.flatpilot/logs/wg_probe.log`` with a
   timestamp + outcome code. A summary table is printed at the end.
5. Final storage state is saved back to the session file so a follow-up
   run picks up where we left off.

Outcome codes
-------------
- ``ok``              — search results rendered (at least one card selector hit).
- ``captcha``         — hCaptcha widget / iframe detected on the page.
- ``block_http_<n>``  — HTTP status >= 400 (403 / 429 / 503 are the usual
                        anti-bot signals).
- ``block_keyword``   — body mentions "unusual traffic" / "ungewöhnlich" etc.
- ``unknown``         — no results, no captcha, no obvious block keyword —
                        probably a DOM change; inspect the saved state.
- ``error_<Class>``   — Playwright navigation raised (timeout, net error).

Running
-------
Inside the Docker container (Chromium already installed)::

    docker compose run --rm flatpilot \\
        python scripts/wg_probe.py --city Berlin --interval 90 --duration-min 240

Short smoke run (five minutes, ~30s cadence) to sanity-check selectors::

    docker compose run --rm flatpilot \\
        python scripts/wg_probe.py --city Berlin --interval 30 --duration-min 5

After the probe finishes, paste the summary table plus any noteworthy
observations into ``bd update FlatPilot-h8ug --notes=...`` so D2 design
picks them up.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
)
HOMEPAGE_URL = "https://www.wg-gesucht.de/"
NAV_TIMEOUT_MS = 30_000
WARM_UP_SETTLE_SEC = 2

COOKIE_ACCEPT_SELECTORS = (
    "button:has-text('Einverstanden')",
    "button:has-text('Zustimmen')",
    "button:has-text('Accept')",
    "button:has-text('Alle akzeptieren')",
    "#cmpwelcomebtnyes",
    "#cmpbntyestxt",
)
RESULT_SELECTORS = (
    "div.offer_list_item",
    "article.offer",
    ".wgg_card",
    "[data-id][data-listing-type]",
)
BLOCK_KEYWORDS = (
    "unusual traffic",
    "ungewöhnlichen datenverkehr",
    "ungewoehnlichen datenverkehr",
    "too many requests",
)


def _app_dir() -> Path:
    import os

    override = os.environ.get("FLATPILOT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".flatpilot"


APP_DIR = _app_dir()
SESSIONS_DIR = APP_DIR / "sessions" / "wg-gesucht"
LOGS_DIR = APP_DIR / "logs"
STATE_FILE = SESSIONS_DIR / "state.json"
LOG_FILE = LOGS_DIR / "wg_probe.log"


def search_url(city: str) -> str:
    slug = quote(city.strip().replace(" ", "-"))
    return f"https://www.wg-gesucht.de/wohnungen-in-{slug}.html"


def _configure_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wg_probe")
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


def _classify(page) -> str:
    try:
        if page.locator("iframe[src*='hcaptcha']").count() > 0:
            return "captcha"
        if page.locator("iframe[src*='recaptcha']").count() > 0:
            return "captcha"
        body = (page.content() or "").lower()
    except Exception as exc:
        return f"error_{type(exc).__name__}"

    if "hcaptcha" in body or "g-recaptcha" in body:
        return "captcha"
    if any(kw in body for kw in BLOCK_KEYWORDS):
        return "block_keyword"
    for selector in RESULT_SELECTORS:
        try:
            if page.locator(selector).count() > 0:
                return "ok"
        except Exception:
            continue
    return "unknown"


def _poll(context, url: str, logger: logging.Logger) -> str:
    page = context.new_page()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        status = response.status if response is not None else 0
        if status >= 400:
            return f"block_http_{status}"
        return _classify(page)
    except Exception as exc:
        return f"error_{type(exc).__name__}"
    finally:
        page.close()


def _save_state(context) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(STATE_FILE))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="Berlin", help="German city name (default: Berlin)")
    parser.add_argument(
        "--interval", type=int, default=90, help="Seconds between polls (default: 90)"
    )
    parser.add_argument(
        "--duration-min",
        type=int,
        default=240,
        help="Total probe duration in minutes (default: 240 = 4 h)",
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

    url = search_url(args.city)
    logger.info(
        "probe starting · url=%s · interval=%ss · duration=%smin · headless=%s",
        url,
        args.interval,
        args.duration_min,
        args.headless,
    )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "playwright is not installed. Run inside Docker "
            "(`docker compose run --rm flatpilot python scripts/wg_probe.py ...`) "
            "or `pip install -e '.[dev]' && playwright install chromium`."
        )
        return 1

    storage_state = str(STATE_FILE) if STATE_FILE.exists() else None
    deadline = datetime.now(timezone.utc) + timedelta(minutes=args.duration_min)
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
            while datetime.now(timezone.utc) < deadline:
                poll_num += 1
                outcome = _poll(context, url, logger)
                counts[outcome] += 1
                logger.info("poll %d: %s", poll_num, outcome)
                _save_state(context)
                if datetime.now(timezone.utc) + timedelta(seconds=args.interval) >= deadline:
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
            logger.info("  %-20s %4d  (%.1f%%)", outcome, n, 100 * n / total)
    logger.info("log file: %s", LOG_FILE)
    logger.info("cookie state: %s", STATE_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
