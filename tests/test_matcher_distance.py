"""Unit coverage for matcher.distance: Haversine + Nominatim cache."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import flatpilot.matcher.distance as dist


def test_haversine_km_known_pair():
    # Berlin Hbf (~52.5251, 13.3694) to Alexanderplatz (~52.5219, 13.4132)
    km = dist.haversine_km(52.5251, 13.3694, 52.5219, 13.4132)
    assert 1.0 < km < 4.0


def test_haversine_km_zero_for_same_point():
    km = dist.haversine_km(52.5, 13.4, 52.5, 13.4)
    assert km == 0.0


def test_geocode_cache_hit_does_not_call_httpx(tmp_path, monkeypatch):
    cache_path = tmp_path / "geocode_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "berlin hbf": {
                    "lat": 52.5,
                    "lng": 13.4,
                    "cached_at": datetime.now(UTC).isoformat(),
                }
            }
        )
    )
    monkeypatch.setattr(dist, "GEOCODE_CACHE_PATH", cache_path)

    def fake_get(*_a, **_kw):
        raise AssertionError("httpx.get should not be called on cache hit")

    monkeypatch.setattr(dist.httpx, "get", fake_get)

    coords = dist.geocode("Berlin Hbf")
    assert coords == (52.5, 13.4)


def test_geocode_cache_miss_writes_through(tmp_path, monkeypatch):
    cache_path = tmp_path / "geocode_cache.json"
    monkeypatch.setattr(dist, "GEOCODE_CACHE_PATH", cache_path)
    # ensure_dirs() in _save_cache walks config.APP_DIR — point that at
    # tmp_path so the test doesn't write to ~/.flatpilot.
    from flatpilot import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(config, "TEMPLATES_DIR", tmp_path / "templates")

    # Bypass the 1-req/sec throttle so the test runs fast.
    monkeypatch.setattr(dist, "_throttle", lambda: None)

    class _Resp:
        status_code = 200

        def json(self):
            return [{"lat": "52.50", "lon": "13.40"}]

        def raise_for_status(self):
            return None

    calls = {"n": 0}

    def fake_get(*_a, **_kw):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(dist.httpx, "get", fake_get)

    coords = dist.geocode("Berlin Hbf")
    assert coords == (52.5, 13.4)
    assert calls["n"] == 1
    assert cache_path.exists()
    saved = json.loads(cache_path.read_text())
    assert "berlin hbf" in {k.lower() for k in saved}


def test_geocode_returns_none_when_nominatim_empty(tmp_path, monkeypatch):
    cache_path = tmp_path / "geocode_cache.json"
    monkeypatch.setattr(dist, "GEOCODE_CACHE_PATH", cache_path)
    from flatpilot import config

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(config, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(dist, "_throttle", lambda: None)

    class _Resp:
        status_code = 200

        def json(self):
            return []

        def raise_for_status(self):
            return None

    monkeypatch.setattr(dist.httpx, "get", lambda *a, **kw: _Resp())
    assert dist.geocode("Definitely Not A Real Place") is None
    # Negative cache entry written.
    saved = json.loads(cache_path.read_text())
    assert any(v.get("lat") is None for v in saved.values())


def test_entry_fresh_ttl():
    fresh = {"cached_at": (datetime.now(UTC) - timedelta(days=30)).isoformat()}
    stale = {"cached_at": (datetime.now(UTC) - timedelta(days=200)).isoformat()}
    assert dist._entry_fresh(fresh) is True
    assert dist._entry_fresh(stale) is False


def test_entry_fresh_handles_missing_cached_at():
    # Defensive: a malformed cache entry without the timestamp should
    # be treated as stale (per distance.py:74-78), not raise.
    assert dist._entry_fresh({}) is False
    assert dist._entry_fresh({"cached_at": "not-a-date"}) is False


def test_resolve_flat_coords_short_circuits_when_present(monkeypatch):
    def boom(*_a, **_kw):
        raise AssertionError("geocode must not be called when coords present")

    monkeypatch.setattr(dist, "geocode", boom)

    # NOTE: keys are `lat`/`lng` (per distance.py:143-144), NOT `latitude`/
    # `longitude`. If you use the long names the short-circuit silently
    # fails and the boom() stub fires.
    flat = {"lat": 52.5, "lng": 13.4, "title": "x"}
    coords = dist.resolve_flat_coords(flat)
    assert coords == (52.5, 13.4)


def test_resolve_flat_coords_returns_none_with_no_signal_no_profile(monkeypatch):
    # No coords, no address, no district, no profile → None.
    monkeypatch.setattr(dist, "geocode", lambda *a, **kw: None)
    assert dist.resolve_flat_coords({}, profile=None) is None
