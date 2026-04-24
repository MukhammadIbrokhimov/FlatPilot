"""Residential-style user-agent pool with per-session pinning.

The Kleinanzeigen scraper (FlatPilot-6hix) uses a small set of realistic
Firefox / Chrome UAs so repeated fresh sessions don't all share a single
fingerprint. Within a *single* cookie jar we must not rotate: presenting
a cookie issued under UA X from UA Y is a stronger bot signal than
staying on one UA. :func:`pin_user_agent` therefore picks a UA at random
on first call for a platform, writes it to a sidecar
``fingerprint.json`` next to ``state.json``, and returns the pinned
value on every subsequent call until that file is deleted.
"""

from __future__ import annotations

import json
import logging
import random

from flatpilot.scrapers.base import session_dir

logger = logging.getLogger(__name__)


# Firefox 121 Linux must stay at index 0 — that's the exact fingerprint
# the D0 probe validated over 4.5 h of polling. Additional entries are
# here so repeated fresh sessions don't all present the same string.
POOL: tuple[str, ...] = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
)


def pin_user_agent(platform: str) -> str:
    """Return a persistent UA for ``platform``.

    First call writes ``~/.flatpilot/sessions/<platform>/fingerprint.json``
    and returns the pinned value. Subsequent calls read it back. If the
    file is missing or malformed, a new UA is picked and persisted.

    When ``fingerprint.json`` is absent but a sibling ``state.json``
    cookie jar already exists (upgrade from a pre-UA-pool version), the
    cookies were established under :data:`flatpilot.scrapers.session.DEFAULT_USER_AGENT`
    — pin that same UA to avoid presenting the existing jar under a
    fresh random string, which is a stronger bot signal than staying on
    one UA.
    """
    # Local import keeps the ua_pool <-> session import graph acyclic
    # (session imports from base only; we reach into it lazily here).
    from flatpilot.scrapers.session import DEFAULT_USER_AGENT

    sdir = session_dir(platform)
    path = sdir / "fingerprint.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            ua = payload.get("user_agent")
            if isinstance(ua, str) and ua in POOL:
                return ua
            logger.warning(
                "%s: fingerprint.json has unknown UA %r; re-pinning", platform, ua
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("%s: fingerprint.json unreadable (%s); re-pinning", platform, exc)

    if (sdir / "state.json").exists():
        ua = DEFAULT_USER_AGENT
        logger.info(
            "%s: existing cookie jar with no fingerprint.json — "
            "pinning DEFAULT_USER_AGENT to preserve jar/UA pairing",
            platform,
        )
    else:
        ua = random.choice(POOL)
        logger.info("%s: pinned user-agent fingerprint", platform)
    path.write_text(json.dumps({"user_agent": ua}))
    return ua
