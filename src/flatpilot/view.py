"""Generate the static / served HTML dashboard.

Reads ``matches`` joined against ``flats`` and the ``applications``
table, groups rows by tab (Matches / Applied / Responses), and renders
a single self-contained HTML page. The Matches tab keeps its session /
historical sub-grouping. Filters (district / rent max / rooms / WBS) on
Matches and a status filter on Applied run client-side in vanilla JS
against ``data-*`` attributes.

``generate_html(conn=None)`` is the pure renderer the dashboard server
calls per request. ``generate()`` is the back-compat wrapper that
writes the rendered string to ``~/.flatpilot/dashboard.html`` and
returns the path — kept so any caller that still writes-and-opens a
static file works without changes.

No LLM scoring and no server-side filtering; Phase 1 / 3 stays
deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from flatpilot.config import APP_DIR, ensure_dirs
from flatpilot.database import get_conn, init_db

DASHBOARD_FILENAME = "dashboard.html"
SESSION_WINDOW = timedelta(hours=24)


def generate_html(conn: Any = None) -> str:
    """Render the full dashboard HTML against the current DB state."""
    init_db()
    if conn is None:
        conn = get_conn()

    match_rows = conn.execute(
        """
        SELECT m.id AS match_id, m.flat_id, m.decision, m.decision_reasons_json,
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
    skipped_flat_ids: set[int] = set()

    session_cutoff = datetime.now(UTC) - SESSION_WINDOW
    # First pass: collect skipped flat_ids so the matches pane can hide
    # any flat the user has skipped (preserves audit while keeping the
    # Matches view decluttered).
    for row in match_rows:
        item = dict(row)
        if item["decision"] == "skipped":
            skipped_flat_ids.add(int(item["flat_id"]))

    for row in match_rows:
        item = dict(row)
        decision = item["decision"]
        if decision == "match":
            if int(item["flat_id"]) in skipped_flat_ids:
                continue
            matched.append(item)
            continue
        if decision != "reject":
            continue
        decided_at = _parse_ts(item.get("decided_at"))
        if decided_at is None or decided_at < session_cutoff:
            rejected_historical.append(item)
        else:
            rejected_session.append(item)

    applications = _load_applications(conn)

    return _render(matched, rejected_session, rejected_historical, applications)


def generate() -> Path:
    """Back-compat: render the dashboard and write it to the app dir."""
    ensure_dirs()
    path = APP_DIR / DASHBOARD_FILENAME
    path.write_text(generate_html(), encoding="utf-8")
    return path


def _load_applications(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, flat_id, platform, listing_url, title,
               rent_warm_eur, rooms, size_sqm, district,
               applied_at, method, message_sent, attachments_sent_json,
               status, response_received_at, response_text, notes
        FROM applications
        ORDER BY applied_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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
    flat_id = item.get("flat_id") or item.get("id") or ""
    match_id = item.get("match_id") or ""
    decision = item.get("decision") or ""

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

    # Apply / Skip render only on actual match rows. Rejected cards in
    # the Matches pane keep View / Copy so the user can still inspect
    # them, but applying to a flat the matcher already declined is not
    # a supported flow.
    actions_html = ""
    if url:
        action_buttons = ""
        if decision == "match":
            action_buttons = (
                f'<button class="apply" type="button" '
                f'data-flat-id="{escape(str(flat_id), quote=True)}">Apply</button>'
                f'<button class="skip" type="button" '
                f'data-match-id="{escape(str(match_id), quote=True)}">Skip</button>'
            )
        actions_html = (
            '<div class="actions">'
            + action_buttons
            + f'<a class="open" href="{escape(url, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">View</a>'
            f'<button class="copy" type="button" '
            f'data-url="{escape(url, quote=True)}">Copy link</button>'
            '</div>'
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
        + actions_html
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


def _applied_pane(applications: list[dict[str, Any]]) -> str:
    """Placeholder pane — Task 7 fills this in. Empty list shows hint."""
    if not applications:
        return (
            '<p class="empty">No applications yet. '
            "Apply from the Matches tab to populate this view.</p>"
        )
    return '<p class="empty">Applied tab populated by M3.</p>'


def _responses_pane(applications: list[dict[str, Any]]) -> str:
    """Placeholder pane — Task 9 fills this in. Empty list shows hint."""
    if not applications:
        return '<p class="empty">No responses to record. Apply to a flat first.</p>'
    return '<p class="empty">Responses tab populated by M4.</p>'


def _render(
    matched: list[dict[str, Any]],
    rejected_session: list[dict[str, Any]],
    rejected_historical: list[dict[str, Any]],
    applications: list[dict[str, Any]],
) -> str:
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    district_options = _district_options(
        [matched, rejected_session, rejected_historical]
    )
    total_match = len(matched)
    total_reject = len(rejected_session) + len(rejected_historical)
    total_applied = len(applications)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FlatPilot dashboard</title>
<style>
  :root {{ color-scheme: light dark; --accent: #2b7cff; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; padding: 1.5rem; max-width: 1100px; margin: 0 auto; }}
  header p {{ color: #666; margin: 0.2rem 0 1rem; }}
  nav.tabs {{ display: flex; gap: 0.25rem; margin-bottom: 1rem; border-bottom: 1px solid rgba(128,128,128,0.3); }}
  nav.tabs button {{ font: inherit; padding: 0.5rem 1rem; border: none; background: transparent;
                     border-bottom: 2px solid transparent; cursor: pointer; color: inherit; }}
  nav.tabs button.active {{ border-bottom-color: var(--accent); color: var(--accent); font-weight: 600; }}
  nav.tabs button:hover:not(.active) {{ background: rgba(128,128,128,0.08); }}
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}
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
  .card .actions {{ display: flex; gap: 0.5rem; align-items: center; margin: 0.5rem 0 0; flex-wrap: wrap; }}
  .card .actions a.open, .card .actions button {{ font: inherit; font-size: 0.85rem;
      padding: 0.25rem 0.6rem; border-radius: 4px; cursor: pointer; }}
  .card .actions a.open {{ color: var(--accent); text-decoration: none;
                           border: 1px solid var(--accent); }}
  .card .actions a.open:hover {{ background: var(--accent); color: white; }}
  .card .actions button.apply {{ background: var(--accent); color: white; border: 1px solid var(--accent); }}
  .card .actions button.apply:hover {{ filter: brightness(1.1); }}
  .card .actions button.apply:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .card .actions button.skip {{ background: transparent; border: 1px solid rgba(128,128,128,0.4); color: inherit; }}
  .card .actions button.skip:hover {{ background: rgba(128,128,128,0.1); }}
  .card .actions button.copy {{ background: transparent; border: 1px solid rgba(128,128,128,0.4); color: inherit; }}
  .card .actions button.copy:hover {{ background: rgba(128,128,128,0.1); }}
  .card .actions button.copy.copied {{ color: #2a7; border-color: #2a7; }}
  .card .reasons {{ color: #c33; font-size: 0.85rem; margin: 0.4rem 0 0; }}
  .empty {{ color: #888; font-style: italic; padding: 1rem 0; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px;
            font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }}
  .badge-submitted {{ background: rgba(43,124,255,0.15); color: #2b7cff; }}
  .badge-failed {{ background: rgba(220,68,68,0.15); color: #c33; }}
  .badge-viewing_invited {{ background: rgba(34,170,85,0.15); color: #2a7; }}
  .badge-rejected {{ background: rgba(120,120,120,0.15); color: #888; }}
  .badge-no_response {{ background: rgba(180,140,40,0.15); color: #b8860b; }}
  .toast {{ position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%);
            background: #333; color: white; padding: 0.75rem 1.25rem; border-radius: 6px;
            font-size: 0.9rem; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 100; }}
  .toast.show {{ opacity: 1; }}
  .toast.error {{ background: #c33; }}
</style>
</head>
<body>
<header>
  <h1>FlatPilot</h1>
  <p>Generated {escape(generated_at)} · {total_match} matched · {total_reject} rejected · {total_applied} applied</p>
</header>

<nav class="tabs">
  <button type="button" data-tab="matches" class="active">Matches</button>
  <button type="button" data-tab="applied">Applied</button>
  <button type="button" data-tab="responses">Responses</button>
</nav>

<section class="tab-pane active" data-pane="matches">
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
</section>

<section class="tab-pane" data-pane="applied">
  {_applied_pane(applications)}
</section>

<section class="tab-pane" data-pane="responses">
  {_responses_pane(applications)}
</section>

<div id="toast" class="toast" role="status" aria-live="polite"></div>

<script>
(function() {{
  // Tab switching.
  const tabs = document.querySelectorAll('nav.tabs button');
  const panes = document.querySelectorAll('.tab-pane');
  tabs.forEach(btn => {{
    btn.addEventListener('click', () => {{
      const target = btn.dataset.tab;
      tabs.forEach(b => b.classList.toggle('active', b === btn));
      panes.forEach(p => p.classList.toggle('active', p.dataset.pane === target));
    }});
  }});

  // Toast helper used by Task 6/7/9 fetch handlers.
  const toastEl = document.getElementById('toast');
  let toastTimer = null;
  window.flatpilotToast = function(msg, isError) {{
    toastEl.textContent = msg;
    toastEl.classList.toggle('error', !!isError);
    toastEl.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 3500);
  }};

  // Matches-pane filters (unchanged from pre-tabs version).
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

  function applyFilters() {{
    const wD = district.value;
    const wR = parseInt(rent.value, 10);
    const wRooms = rooms.value;
    const wWbs = wbs.value;
    rentVal.textContent = wR;

    document.querySelectorAll('[data-pane="matches"] .card').forEach(card => {{
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
    el.addEventListener('input', applyFilters);
    el.addEventListener('change', applyFilters);
  }});

  // Copy-link handler (unchanged).
  document.querySelectorAll('.card .actions button.copy').forEach(btn => {{
    btn.addEventListener('click', async () => {{
      const url = btn.dataset.url;
      try {{
        await navigator.clipboard.writeText(url);
        btn.classList.add('copied');
        const prev = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(() => {{ btn.classList.remove('copied'); btn.textContent = prev; }}, 1500);
      }} catch (e) {{
        btn.textContent = 'Failed';
        setTimeout(() => {{ btn.textContent = 'Copy link'; }}, 1500);
      }}
    }});
  }});

  applyFilters();
}})();
</script>
</body>
</html>
"""
