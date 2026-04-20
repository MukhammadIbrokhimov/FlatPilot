"""Great-circle distance plus Nominatim geocoding with on-disk cache.

Resolution order for a flat's coordinates:

1. Scraper-provided ``lat`` / ``lng`` on the flat mapping.
2. Nominatim geocode of ``flat.address``.
3. District centroid (``district, profile.city, Germany``) via Nominatim.
4. City centroid (``profile.city, Germany``) via Nominatim — coarse, but
   keeps a flat in the radius check instead of silently dropping it.

The geocode cache lives at ``~/.flatpilot/geocode_cache.json`` with a
180-day TTL. Cached misses are stored too so repeated unresolvable
addresses don't re-hit Nominatim.

Politeness toward Nominatim: a self-imposed 1 request per second limit
(module-level monotonic timestamp) and a descriptive ``User-Agent``.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from flatpilot import __version__
from flatpilot.config import GEOCODE_CACHE_PATH, ensure_dirs
from flatpilot.profile import Profile


logger = logging.getLogger(__name__)


EARTH_RADIUS_KM = 6371.0
CACHE_TTL = timedelta(days=180)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = (
    f"FlatPilot/{__version__} (https://github.com/MukhammadIbrokhimov/FlatPilot)"
)
REQ_INTERVAL_SEC = 1.0
REQ_TIMEOUT = 10.0


_last_request_at = 0.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _load_cache() -> dict[str, Any]:
    if not GEOCODE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(GEOCODE_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("geocode cache unreadable (%s); starting empty", exc)
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    ensure_dirs()
    GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _entry_fresh(entry: Mapping[str, Any]) -> bool:
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
    except (KeyError, ValueError):
        return False
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - cached_at < CACHE_TTL


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < REQ_INTERVAL_SEC:
        time.sleep(REQ_INTERVAL_SEC - elapsed)
    _last_request_at = time.monotonic()


def geocode(address: str) -> tuple[float, float] | None:
    """Geocode a free-form address. Returns ``None`` when unresolvable."""
    key = address.strip().lower()
    if not key:
        return None

    cache = _load_cache()
    entry = cache.get(key)
    if entry is not None and _entry_fresh(entry):
        lat = entry.get("lat")
        lng = entry.get("lng")
        if lat is None or lng is None:
            return None
        return float(lat), float(lng)

    _throttle()
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "de"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQ_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Nominatim request failed for %r: %s", address, exc)
        return None

    now = datetime.now(timezone.utc).isoformat()
    if not results:
        cache[key] = {"lat": None, "lng": None, "cached_at": now}
        _save_cache(cache)
        return None

    try:
        lat = float(results[0]["lat"])
        lng = float(results[0]["lon"])
    except (KeyError, ValueError, TypeError):
        logger.warning("Nominatim returned unparsable result for %r", address)
        return None

    cache[key] = {"lat": lat, "lng": lng, "cached_at": now}
    _save_cache(cache)
    return lat, lng


def resolve_flat_coords(
    flat: Mapping[str, Any], profile: Profile | None = None
) -> tuple[float, float] | None:
    """Best-effort coordinate resolution for a flat with fallbacks."""
    lat = flat.get("lat")
    lng = flat.get("lng")
    if lat is not None and lng is not None:
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            pass

    address = flat.get("address")
    if address:
        coords = geocode(str(address))
        if coords is not None:
            return coords

    district = flat.get("district")
    if district and profile:
        coords = geocode(f"{district}, {profile.city}, Germany")
        if coords is not None:
            return coords

    if profile:
        return geocode(f"{profile.city}, Germany")

    return None
