"""Cross-process apply lock (FlatPilot-bxm).

Two FlatPilot processes (CLI ``flatpilot apply 42`` + dashboard
``POST /api/applications {"flat_id": 42}``) hitting the same flat_id
must NOT both reach ``filler.fill(submit=True)`` — that sends two
messages to the landlord. The new ``apply_locks`` table provides
cross-process serialization via ``INSERT OR FAIL`` on a primary key:
the loser raises ``AlreadyAppliedError("apply already in progress")``.

The lock is acquired AFTER the existing AlreadyAppliedError SELECT
check (so a fully-submitted flat still 409s with its older message)
and BEFORE the filler runs. It's released in a finally so a crash
inside filler.fill() doesn't strand the slot.

Stale rows older than ``apply_timeout_sec() + 60`` are reaped on every
acquire, so a process crash (kill -9) doesn't permanently block future
applies for that flat.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flatpilot.apply import (
    AlreadyAppliedError,
    acquire_apply_lock,
    release_apply_lock,
)

# --------------------------------------------------------------------------- #
# Unit tests against the lock primitives directly.
# --------------------------------------------------------------------------- #


def test_acquire_first_call_succeeds(tmp_db):
    acquire_apply_lock(tmp_db, flat_id=42)
    row = tmp_db.execute(
        "SELECT flat_id, pid FROM apply_locks WHERE flat_id = 42"
    ).fetchone()
    assert row is not None
    assert row["flat_id"] == 42
    assert row["pid"] == os.getpid()


def test_acquire_second_call_raises_in_progress_message(tmp_db):
    acquire_apply_lock(tmp_db, flat_id=42)
    with pytest.raises(AlreadyAppliedError, match="apply already in progress"):
        acquire_apply_lock(tmp_db, flat_id=42)


def test_acquire_second_call_message_distinct_from_submitted_row_message(tmp_db):
    """Two AlreadyAppliedError causes must surface distinct messages.

    'already has a submitted application' = the SELECT-check path.
    'apply already in progress'           = the lock-acquire path.

    Same exception class is fine — the messages disambiguate so the
    user knows whether to wait (in-progress) or accept (already done).
    """
    acquire_apply_lock(tmp_db, flat_id=42)
    with pytest.raises(AlreadyAppliedError) as exc_info:
        acquire_apply_lock(tmp_db, flat_id=42)
    msg = str(exc_info.value)
    assert "apply already in progress" in msg
    assert "already has a submitted application" not in msg


def test_release_then_reacquire_succeeds(tmp_db):
    acquire_apply_lock(tmp_db, flat_id=42)
    release_apply_lock(tmp_db, flat_id=42)
    # Should not raise.
    acquire_apply_lock(tmp_db, flat_id=42)


def test_release_unheld_lock_is_a_noop(tmp_db):
    """release on a flat we never acquired must not raise."""
    release_apply_lock(tmp_db, flat_id=42)
    # No row, no error.
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM apply_locks WHERE flat_id = 42"
        ).fetchone()[0]
        == 0
    )


def test_acquire_clears_stale_lock(tmp_db, monkeypatch):
    """A row older than apply_timeout_sec() + 60 is reaped on next acquire.

    Simulates a kill -9'd process: it inserted a row, then died without
    running release. The next process to try this flat sees the stale row
    and reaps it before its own INSERT.
    """
    # Fake holder row, timestamped well past the threshold.
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s
    stale_ts = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (42, stale_ts, 99999),
    )

    # Should NOT raise — stale row is reaped first, then we INSERT fresh.
    acquire_apply_lock(tmp_db, flat_id=42)

    row = tmp_db.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = 42"
    ).fetchone()
    assert row["pid"] == os.getpid()


def test_acquire_does_not_clear_fresh_lock(tmp_db, monkeypatch):
    """A row inside the threshold must NOT be reaped — it's a real holder."""
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s
    fresh_ts = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (42, fresh_ts, 99999),
    )

    with pytest.raises(AlreadyAppliedError, match="apply already in progress"):
        acquire_apply_lock(tmp_db, flat_id=42)


def test_acquire_only_clears_stale_for_target_flat(tmp_db, monkeypatch):
    """Stale-row reaping must not cascade into other unrelated flats.

    Insert two stale rows; acquire flat 42; flat 99's stale row must
    survive because it's a different flat. (Cleanup is bounded to the
    minimum that unblocks the requested flat — global sweeps would
    interact awkwardly with parallel acquires for sibling flats.)
    """
    monkeypatch.setenv("FLATPILOT_APPLY_TIMEOUT_SEC", "60")  # threshold = 120s
    stale_ts = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (42, stale_ts, 11111),
    )
    tmp_db.execute(
        "INSERT INTO apply_locks (flat_id, acquired_at, pid) VALUES (?, ?, ?)",
        (99, stale_ts, 22222),
    )

    acquire_apply_lock(tmp_db, flat_id=42)

    # 42 is now held by us.
    row42 = tmp_db.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = 42"
    ).fetchone()
    assert row42["pid"] == os.getpid()

    # 99 still has its old stale row — bounded cleanup.
    row99 = tmp_db.execute(
        "SELECT pid FROM apply_locks WHERE flat_id = 99"
    ).fetchone()
    assert row99["pid"] == 22222


# --------------------------------------------------------------------------- #
# Integration with apply_to_flat — the lock must wrap filler.fill().
# --------------------------------------------------------------------------- #


def _profile_for_test(tmp_path: Path):
    from flatpilot.profile import Attachments, Profile, save_profile

    pdf = tmp_path / ".flatpilot" / "attachments" / "schufa.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    profile = Profile.load_example().model_copy(
        update={
            "city": "Berlin",
            "attachments": Attachments(default=["schufa.pdf"], per_platform={}),
        }
    )
    save_profile(profile)
    return profile


def _write_template(tmp_path: Path) -> None:
    tpl_dir = tmp_path / ".flatpilot" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "wg-gesucht.md").write_text(
        "Hallo, ich bin interessiert an $title.\n",
        encoding="utf-8",
    )


def _insert_flat(conn, *, external_id: str = "ext-1") -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        """
        INSERT INTO flats (
            external_id, platform, listing_url, title,
            rent_warm_eur, rooms, district,
            scraped_at, first_seen_at, requires_wbs
        ) VALUES (?, 'wg-gesucht', 'https://x/1', 'T1', 900.0, 2.0, 'X', ?, ?, 0)
        """,
        (external_id, now, now),
    )
    return int(cur.lastrowid)


def test_apply_to_flat_acquires_and_releases_lock_on_success(
    tmp_db, tmp_path, monkeypatch
):
    """apply_to_flat must DELETE the lock row on the success path."""
    from flatpilot.apply import apply_to_flat
    from flatpilot.fillers.base import FillReport

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)

    def fake_fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None):
        # While the filler is running, the lock row must exist.
        row = tmp_db.execute(
            "SELECT pid FROM apply_locks WHERE flat_id = ?", (flat_id,)
        ).fetchone()
        assert row is not None, "lock row must be held during filler.fill()"
        return FillReport(
            platform="wg-gesucht",
            listing_url=listing_url,
            contact_url=listing_url,
            fields_filled={"message": message},
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=None,
            submitted=True,
            started_at="2026-04-29T00:00:00+00:00",
            finished_at="2026-04-29T00:00:01+00:00",
        )

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill", fake_fill, raising=True
    )

    apply_to_flat(flat_id, dry_run=False)

    # After return, the lock must be released.
    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM apply_locks WHERE flat_id = ?", (flat_id,)
    ).fetchone()[0]
    assert cnt == 0


def test_apply_to_flat_releases_lock_on_filler_error(tmp_db, tmp_path, monkeypatch):
    """When filler.fill() raises, the lock must still be released."""
    from flatpilot.apply import apply_to_flat
    from flatpilot.fillers.base import NotAuthenticatedError

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)

    def boom(self, *args, **kwargs):
        raise NotAuthenticatedError("session expired")

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill", boom, raising=True
    )

    with pytest.raises(NotAuthenticatedError):
        apply_to_flat(flat_id, dry_run=False)

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM apply_locks WHERE flat_id = ?", (flat_id,)
    ).fetchone()[0]
    assert cnt == 0


def test_apply_to_flat_dry_run_does_not_touch_lock(tmp_db, tmp_path, monkeypatch):
    """Dry-run is a preview — no lock acquired (matches existing behavior
    where dry-run also skips the AlreadyAppliedError SELECT check).
    """
    from flatpilot.apply import apply_to_flat
    from flatpilot.fillers.base import FillReport

    _profile_for_test(tmp_path)
    _write_template(tmp_path)
    flat_id = _insert_flat(tmp_db)

    def fake_fill(self, listing_url, message, attachments, *, submit, screenshot_dir=None):
        return FillReport(
            platform="wg-gesucht",
            listing_url=listing_url,
            contact_url=listing_url,
            fields_filled={},
            message_sent=message,
            attachments_sent=list(attachments),
            screenshot_path=None,
            submitted=False,
            started_at="2026-04-29T00:00:00+00:00",
            finished_at="2026-04-29T00:00:01+00:00",
        )

    monkeypatch.setattr(
        "flatpilot.fillers.wg_gesucht.WGGesuchtFiller.fill", fake_fill, raising=True
    )

    apply_to_flat(flat_id, dry_run=True)

    cnt = tmp_db.execute(
        "SELECT COUNT(*) FROM apply_locks WHERE flat_id = ?", (flat_id,)
    ).fetchone()[0]
    assert cnt == 0


# --------------------------------------------------------------------------- #
# Cross-process race test using multiprocessing.Process.
#
# SQLite WAL is process-safe. Two real OS processes opening the same
# database file race on INSERT INTO apply_locks; exactly one wins.
# This is the test that proves the lock works across the actual
# CLI-vs-dashboard topology, not just across threads.
# --------------------------------------------------------------------------- #


def _hold_lock_in_child(db_path: str, flat_id: int, acquired_evt, release_evt):
    """Top-level (picklable) child entry point — runs in a separate process.

    Opens its own sqlite3 connection (not the parent's thread-local one),
    acquires the lock, signals the parent, then waits for the parent to
    signal it can release and exit.
    """
    import sqlite3

    from flatpilot.apply import acquire_apply_lock

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        acquire_apply_lock(conn, flat_id)
        acquired_evt.set()
        # Hold the lock until parent finishes its assertion. 5s ceiling
        # so a buggy parent doesn't leak this process forever.
        release_evt.wait(timeout=5)
    finally:
        conn.close()


def test_concurrent_apply_lock_across_processes(tmp_db):
    """Real cross-process race: child holds lock, parent's acquire raises.

    Uses multiprocessing.Process — a separate OS process with its own
    SQLite connection — so we exercise WAL's process-level
    serialization. This is what stops two ``flatpilot apply 42``
    invocations (one CLI, one dashboard subprocess) from both reaching
    ``filler.fill(submit=True)`` and double-messaging the landlord.
    """
    # `tmp_db` monkeypatches `flatpilot.config.DB_PATH` to a tmp file
    # before this body runs. `from flatpilot.config import DB_PATH`
    # reads the *current* attribute value off the module, so this
    # picks up the patched path — NOT the user's real
    # ~/.flatpilot/flatpilot.db. Pass as a string to the child; the
    # spawn-mode child re-imports flatpilot.config without the
    # monkeypatch, so it must use this argument directly.
    from flatpilot.config import DB_PATH

    # The child needs the apply_locks schema; tmp_db's init_db() in the
    # parent created it on the same file already.
    db_path = str(DB_PATH)

    ctx = mp.get_context("spawn")  # explicit; default on macOS, portable
    acquired_evt = ctx.Event()
    release_evt = ctx.Event()
    proc = ctx.Process(
        target=_hold_lock_in_child,
        args=(db_path, 42, acquired_evt, release_evt),
    )
    proc.start()
    try:
        assert acquired_evt.wait(timeout=10), "child never acquired lock"
        with pytest.raises(AlreadyAppliedError, match="apply already in progress"):
            acquire_apply_lock(tmp_db, flat_id=42)
    finally:
        release_evt.set()
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            pytest.fail("child process did not exit cleanly")
    assert proc.exitcode == 0, f"child exited with {proc.exitcode}"
