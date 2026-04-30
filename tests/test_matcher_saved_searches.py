from __future__ import annotations

import json
from datetime import UTC, datetime

from flatpilot.matcher.runner import run_match
from flatpilot.profile import Profile, SavedSearch, save_profile


def _seed_flat(conn, **overrides):
    now = datetime.now(UTC).isoformat()
    row = {
        "external_id": "ext1",
        "platform": "wg-gesucht",
        "listing_url": "https://example.test/1",
        "title": "Test flat",
        "rent_warm_eur": 1200,
        "rooms": 2,
        "size_sqm": 50,
        "address": None,
        "district": None,
        "lat": None,
        "lng": None,
        "online_since": None,
        "available_from": None,
        "requires_wbs": 0,
        "wbs_size_category": None,
        "wbs_income_category": None,
        "furnished": None,
        "deposit_eur": None,
        "min_contract_months": None,
        "pets_allowed": None,
        "description": None,
        "scraped_at": now,
        "first_seen_at": now,
        "canonical_flat_id": None,
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    cur = conn.execute(
        f"INSERT INTO flats ({cols}) VALUES ({placeholders})", row
    )
    return cur.lastrowid


def test_match_with_no_saved_searches_writes_empty_json(tmp_db):
    profile = Profile.load_example()
    save_profile(profile)
    flat_id = _seed_flat(
        tmp_db,
        rent_warm_eur=profile.rent_max_warm,
        rooms=profile.rooms_min,
    )

    summary = run_match()
    assert summary["match"] == 1

    row = tmp_db.execute(
        "SELECT matched_saved_searches_json FROM matches WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert json.loads(row["matched_saved_searches_json"]) == []


def test_saved_search_widening_produces_match(tmp_db):
    base = Profile.load_example()
    profile = base.model_copy(
        update={
            "rent_max_warm": 1500,
            "saved_searches": [
                SavedSearch(name="luxury", auto_apply=True, rent_max_warm=2500)
            ],
        }
    )
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, rent_warm_eur=2000, rooms=profile.rooms_min)

    summary = run_match()
    assert summary["match"] == 1

    row = tmp_db.execute(
        "SELECT decision, matched_saved_searches_json FROM matches WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert row["decision"] == "match"
    assert json.loads(row["matched_saved_searches_json"]) == ["luxury"]


def test_full_reject_when_neither_base_nor_saved_search_matches(tmp_db):
    base = Profile.load_example()
    profile = base.model_copy(
        update={
            "rent_max_warm": 1500,
            "saved_searches": [
                SavedSearch(name="strict", auto_apply=True, rent_max_warm=1000),
            ],
        }
    )
    save_profile(profile)
    flat_id = _seed_flat(tmp_db, rent_warm_eur=2000, rooms=profile.rooms_min)

    summary = run_match()
    assert summary["reject"] == 1

    row = tmp_db.execute(
        "SELECT decision, decision_reasons_json, matched_saved_searches_json "
        "FROM matches WHERE flat_id = ?",
        (flat_id,),
    ).fetchone()
    assert row["decision"] == "reject"
    assert json.loads(row["matched_saved_searches_json"]) == []
    assert "rent_too_high" in json.loads(row["decision_reasons_json"])
