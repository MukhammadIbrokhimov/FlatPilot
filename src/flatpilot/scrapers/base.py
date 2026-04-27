"""Shared types for per-platform scrapers.

Each scraper is a class that sets a ``platform`` ClassVar, registers itself
through :func:`flatpilot.scrapers.register`, and implements :meth:`fetch_new`.
The orchestrator (``flatpilot scrape``, ``flatpilot run``) iterates the
registry and calls ``fetch_new(profile)`` on each scraper.

``Flat`` mirrors the C1 ``flats`` table schema. Only ``external_id``,
``listing_url``, and ``title`` are required on every yielded record —
every other field is optional and the matcher treats missing values as
reject-with-reason (see ``flatpilot.matcher.filters``). The orchestrator
writes flats with ``INSERT OR IGNORE`` against the ``(platform,
external_id)`` UNIQUE constraint, so repeated scrapes are idempotent and
scrapers do not need to track what they have already emitted.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar, Protocol, TypedDict

from flatpilot import config
from flatpilot.config import ensure_dirs
from flatpilot.profile import Profile


class Flat(TypedDict, total=False):
    # Required on every yielded flat:
    external_id: str
    listing_url: str
    title: str
    # Optional — see schemas.FLATS_CREATE_SQL for storage types / nullability.
    rent_warm_eur: float
    rent_cold_eur: float
    extra_costs_eur: float
    rooms: float
    size_sqm: float
    address: str
    district: str
    lat: float
    lng: float
    online_since: str
    available_from: str
    requires_wbs: bool
    wbs_size_category: int
    wbs_income_category: int
    furnished: bool
    deposit_eur: int
    min_contract_months: int
    pets_allowed: bool
    description: str


class Scraper(Protocol):
    """Per-platform scraper contract.

    Implementations are expected to be lightweight classes — construction
    is cheap, ``fetch_new`` is where the network work happens.
    """

    platform: ClassVar[str]
    user_agent: ClassVar[str]
    # Frozenset of exact-match city names this scraper accepts (compared
    # verbatim to ``profile.city``). ``None`` means "no city restriction —
    # supports any city". The @register decorator enforces declaration so
    # a new scraper that forgets the field fails loudly at import time
    # rather than silently running against the wrong cities. See
    # ``flatpilot.scrapers.supports_city`` for the comparison helper.
    supported_cities: ClassVar[frozenset[str] | None]

    def fetch_new(self, profile: Profile) -> Iterable[Flat]:
        """Yield every listing currently visible under ``profile``."""


def session_dir(platform: str) -> Path:
    """Return (and create) the cookie / state dir for a given platform.

    Reads ``config.SESSIONS_DIR`` at call time (not import time) so that
    ``tests/conftest.py::tmp_db`` — which monkey-patches
    ``flatpilot.config.SESSIONS_DIR`` — actually isolates writes to the
    tmp path. A module-level ``from flatpilot.config import SESSIONS_DIR``
    would be stale by the time the test runs.
    """
    ensure_dirs()
    path = config.SESSIONS_DIR / platform
    path.mkdir(parents=True, exist_ok=True)
    return path
