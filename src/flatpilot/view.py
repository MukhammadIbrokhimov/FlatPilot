"""Generate the static HTML dashboard at ``~/.flatpilot/dashboard.html``.

Reads ``matches`` joined against ``flats``, groups the rows into Matched /
Rejected-this-session (last 24h) / Rejected-historical, and renders a single
self-contained HTML file. Filters (district / rent max / rooms / WBS) run
client-side in vanilla JS against ``data-*`` attributes on each card, so the
file stays portable — copy it anywhere, open in any browser.

No LLM scoring and no server-side filtering; Phase 1 is deterministic by
design.
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


def generate() -> Path:
    init_db()
    conn = get_conn()
    rows = conn.execute(
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

    session_cutoff = datetime.now(timezone.utc) - SESSION_WINDOW
    for row in rows:
        item = dict(row)
        if item["decision"] == "match":
            matched.append(item)
            continue
        if item["decision"] != "reject":
            continue
        decided_at = _parse_ts(item.get("decided_at"))
        if decided_at is None or decided_at < session_cutoff:
            rejected_historical.append(item)
        else:
            rejected_session.append(item)

    ensure_dirs()
    path = APP_DIR / DASHBOARD_FILENAME
    path.write_text(
        _render(matched, rejected_session, rejected_historical), encoding="utf-8"
    )
    return path


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
        + (f'<p class="url">{escape(url)}</p>' if url else "")
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


def _render(
    matched: list[dict[str, Any]],
    rejected_session: list[dict[str, Any]],
    rejected_historical: list[dict[str, Any]],
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    district_options = _district_options([matched, rejected_session, rejected_historical])
    total_match = len(matched)
    total_reject = len(rejected_session) + len(rejected_historical)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlatPilot dashboard</title>
<style>
  :root {{ color-scheme: light dark; --accent: #2b7cff; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; margin: 0; padding: 1.5rem; max-width: 1100px; margin: 0 auto; }}
  header p {{ color: #666; margin: 0.2rem 0 1.5rem; }}
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
  .card .url {{ word-break: break-all; font-size: 0.85rem; color: var(--accent); margin: 0.5rem 0 0; }}
  .card .reasons {{ color: #c33; font-size: 0.85rem; margin: 0.4rem 0 0; }}
</style>
</head>
<body>
<header>
  <h1>FlatPilot</h1>
  <p>Generated {escape(generated_at)} · {total_match} matched · {total_reject} rejected</p>
</header>

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

<script>
(function() {{
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

  function apply() {{
    const wD = district.value;
    const wR = parseInt(rent.value, 10);
    const wRooms = rooms.value;
    const wWbs = wbs.value;
    rentVal.textContent = wR;

    document.querySelectorAll('.card').forEach(card => {{
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
    el.addEventListener('input', apply);
    el.addEventListener('change', apply);
  }});
  apply();
}})();
</script>
</body>
</html>
"""
