#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 dual-write: mirror every card write into SQLite, YAML still canonical.

THE NUMBER THAT JUSTIFIES ALL OF THIS (measured on the live store, 1,257 cards):

    full YAML rewrite  : 11,176 ms   <- the cost of EVERY card write, today
    full SQLite rebuild:  1,243 ms   <- what the mirror adds (+11%)
    ONE row update     :      4.71 ms  <- what S2 buys (2,375x)

Every card write takes ELEVEN SECONDS while holding a fleet-wide lock. That is the
convoy, entire. These tests pin the three rules that make the migration to fix it
safe to run under real traffic.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards import _dual_write, _store
from scitex_cards._db import ENV_DB, resolve_db_path


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Each test gets its own store + DB, and a clean failure counter.

    The env var is ``SCITEX_TODO_DB`` (``_db.ENV_DB``) — checked, not assumed.
    """
    _dual_write.reset_failures()
    monkeypatch.setenv(ENV_DB, str(tmp_path / "todo.db"))
    yield
    _dual_write.reset_failures()


def _db_ids(db_path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM tasks")}
    finally:
        conn.close()


def _break_the_mirror(monkeypatch) -> None:
    """Make the incremental mirror raise, exactly as a disk I/O error would."""

    def boom(doc, db_path=None):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("scitex_cards._db_mirror.mirror_doc_incremental", boom)


# --------------------------------------------------------------------------
# RULE 1 — the mirror is OFF unless explicitly switched on.
# --------------------------------------------------------------------------


def test_mirror_is_off_by_default(monkeypatch, tmp_path):
    """The write path of the fleet's critical store does not get a flag day."""
    # Arrange
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)

    # Act
    on = _dual_write.enabled()

    # Assert
    assert on is False


def test_a_card_write_with_the_mirror_off_touches_no_db(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)
    store = tmp_path / "tasks.yaml"

    # Act
    _store.add_task(store, id="a", title="A", assignee="tester")

    # Assert
    assert not resolve_db_path().exists()


# --------------------------------------------------------------------------
# RULE 2 — with the mirror ON, the DB tracks the YAML.
# --------------------------------------------------------------------------


def test_mirror_writes_the_card_into_sqlite(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    # Act
    _store.add_task(store, id="a", title="A", assignee="tester")

    # Assert
    assert _db_ids(resolve_db_path()) == {"a"}


def test_mirror_tracks_a_delete(monkeypatch, tmp_path):
    """A rebuild-mirror must DROP rows that left the YAML, not just add new ones."""
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="tester")
    _store.add_task(store, id="b", title="B", assignee="tester")

    # Act
    _store.delete_task(store, "a")

    # Assert
    assert _db_ids(resolve_db_path()) == {"b"}


def test_mirror_leaves_no_failures_on_the_happy_path(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"

    # Act
    _store.add_task(store, id="a", title="A", assignee="tester")

    # Assert
    assert _dual_write.failure_count() == 0


# --------------------------------------------------------------------------
# RULE 3 — a mirror failure NEVER costs the user their card, and NEVER hides.
#
# This is the load-bearing pair. The YAML is canonical: by the time the mirror
# runs, the card is already durably on disk. Raising there would turn a cosmetic
# problem into DATA LOSS. But swallowing it quietly would let the DB rot out of
# sync while every check reports green — and S2 would then cut the fleet over to
# a store that is confidently wrong.
# --------------------------------------------------------------------------


def test_a_mirror_failure_does_not_fail_the_card_write(monkeypatch, tmp_path):
    """The card MUST still be written. The user's data is not the mirror's hostage."""
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _break_the_mirror(monkeypatch)

    # Act — must NOT raise.
    _store.add_task(store, id="a", title="A", assignee="tester")

    # Assert
    assert [t["id"] for t in _store.list_tasks(store)] == ["a"]


def test_a_mirror_failure_is_counted_not_swallowed(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _break_the_mirror(monkeypatch)

    # Act
    _store.add_task(store, id="a", title="A", assignee="tester")

    # Assert
    assert _dual_write.failure_count() == 1


def test_a_mirror_failure_is_logged_loud(monkeypatch, tmp_path, caplog):
    """Silence is the failure mode this whole codebase spent two days digging out of."""
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _break_the_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        _store.add_task(store, id="a", title="A", assignee="tester")

    # Assert
    assert "DUAL-WRITE MIRROR FAILED" in caplog.text


# A single failure means the DB no longer matches the YAML. There is no partial
# credit for a store that is only mostly right — and a health check that shrugs at
# divergence is how S2 ends up cutting over to a store that is confidently wrong.
def _health_after_one_recorded_failure(monkeypatch):
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    _dual_write._failures.append("sqlite3.OperationalError: disk I/O error")
    return _dual_write.check_mirror_healthy()


def test_health_reports_a_diverged_mirror_as_not_ok(monkeypatch):
    # Arrange
    # Act
    res = _health_after_one_recorded_failure(monkeypatch)

    # Assert
    assert res["ok"] is False


def test_health_names_the_divergence_in_its_detail(monkeypatch):
    # Arrange
    # Act
    res = _health_after_one_recorded_failure(monkeypatch)

    # Assert
    assert "DIVERGED" in res["detail"]


def test_health_hints_the_actual_repair_command(monkeypatch):
    # Arrange
    # Act
    res = _health_after_one_recorded_failure(monkeypatch)

    # Assert — names the actual repair, not a vague "check the logs".
    assert "db import" in (res["hint"] or "")


def test_health_is_ok_when_the_mirror_is_off(monkeypatch):
    # Arrange
    monkeypatch.delenv(_dual_write.ENV_DUAL_WRITE, raising=False)

    # Act
    res = _dual_write.check_mirror_healthy()

    # Assert
    assert res["ok"] is True


# --------------------------------------------------------------------------
# THE NEAR-MISS — pinned so it can never come back.
# --------------------------------------------------------------------------


def test_the_mirror_does_not_touch_the_messages_table(monkeypatch, tmp_path):
    """DM threads MUST survive a card write.

    My first design rebuilt EVERY table from the doc — including `messages`, which
    is derived from the threads.yaml SIDECAR and not from the doc at all. That
    would have DELETED EVERY DM THREAD ON EVERY CARD WRITE.

    Caught while writing it, and pinned here because it is exactly the kind of
    thing that gets "helpfully" reintroduced by someone tidying up the clear-list.
    A table must be owned by exactly the file that produces it.
    """
    # Arrange — a card write builds the DB, then a DM lands in `messages`.
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    store = tmp_path / "tasks.yaml"
    _store.add_task(store, id="a", title="A", assignee="tester")
    db = resolve_db_path()
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO messages(id, thread_key, sender, recipient, body, ts, read) "
        "VALUES('m1','dm:x::y','x','y','hello','2026-07-12T00:00:00Z',0)"
    )
    conn.commit()
    conn.close()

    # Act — a card write now runs the mirror again.
    _store.add_task(store, id="b", title="B", assignee="tester")

    # Assert
    conn = sqlite3.connect(str(db))
    try:
        surviving = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    assert surviving == 1, "the mirror DELETED a DM thread on a card write"


# --------------------------------------------------------------------------- #
# VERSION GUARD: the flag must REFUSE code that cannot honour it              #
# --------------------------------------------------------------------------- #
# This guard exists because the flag alone cost 135 SECONDS PER CARD WRITE.
#
# MEASURED on the live 1,449-card board, in the configuration the fleet was really
# running (2026-07-13):
#     scitex-todo 0.9.4, dual-write ON  : add_task()    = 135.2 s
#     scitex-todo 0.9.4, dual-write OFF : delete_task() =   3.8 s      -> 35x
#
# The flag was switched on because the incremental mirror had shipped — and it had, on
# PyPI. But the fleet runs a wheel BAKED INTO A CONTAINER IMAGE, still on 0.9.4. So the
# flag did not enable the incremental mirror; it enabled the FULL REBUILD that the
# incremental mirror had replaced.
#
# The precondition was real, agreed, and written down — in a MESSAGE BETWEEN TWO AGENTS.
# A precondition that lives only in a message is not a precondition; it is a hope. It
# lives in the code now, and these tests are what keep it there.
def _flag_on_without_an_incremental_mirror(monkeypatch) -> None:
    """Env var ON, but the code cannot honour it."""
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: False)
    monkeypatch.setattr(_dual_write, "_refusal_logged", False)


def test_flag_is_REFUSED_when_the_code_has_no_incremental_mirror(monkeypatch, caplog):
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        on = _dual_write.enabled()

    # Assert
    assert on is False, (
        "dual-write MUST refuse to run on code without the incremental mirror — "
        "honouring it there falls back to the full rebuild: 135 s per card write"
    )


def test_the_refusal_names_itself_in_the_log(monkeypatch, caplog):
    """It must not fail in silence: a refusal nobody sees is a 35x slowdown nobody debugs."""
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        _dual_write.enabled()

    # Assert
    assert "REFUSING TO DUAL-WRITE" in caplog.text


def test_the_refusal_log_hints_how_to_recover_the_mirror(monkeypatch, caplog):
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        _dual_write.enabled()

    # Assert
    assert "db import" in caplog.text, "the hint must say how to recover the mirror"


def test_flag_is_HONOURED_when_the_code_does_have_the_incremental_mirror(monkeypatch):
    """The guard must not be a blanket off-switch — real code must still dual-write."""
    # Arrange
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    monkeypatch.setattr(_dual_write, "_has_incremental_mirror", lambda: True)

    # Act
    on = _dual_write.enabled()

    # Assert
    assert on is True


# The probe imports the FUNCTION. It must never trust a version string: a version
# string is metadata, and metadata lies — an orphaned .dist-info, a stale wheel, a
# container image baked months ago all report a version that outlived the code beside
# them. This repo has been bitten by exactly that. The only honest question is "is the
# function here?", so the probe answers it by importing it.
def test_the_guard_probe_finds_the_shipped_incremental_mirror():
    # Arrange
    # Act
    found = _dual_write._has_incremental_mirror()

    # Assert
    assert found is True, (
        "the shipped code HAS _db_mirror.mirror_doc_incremental, so the probe must find it"
    )


def test_the_guard_asks_for_a_SYMBOL_not_a_version_string():
    # Arrange
    import scitex_cards._db_mirror as m

    # Act
    symbol = m.mirror_doc_incremental

    # Assert — a real, importable callable, not a version claim about one.
    assert callable(symbol)


def test_the_refusal_is_logged_ONCE_not_once_per_write(monkeypatch, caplog):
    """A 135 s bug deserves a loud message. The same message on every write is noise.

    An alert that fires constantly on something the reader cannot act on teaches them to
    ignore the channel — which is how the next real failure goes unread.
    """
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        for _ in range(5):
            _dual_write.enabled()

    # Assert
    assert caplog.text.count("REFUSING TO DUAL-WRITE") == 1


def test_every_repeated_call_still_refuses_the_flag(monkeypatch, caplog):
    """Logging once must not mean RELENTING once — every call still refuses."""
    # Arrange
    _flag_on_without_an_incremental_mirror(monkeypatch)

    # Act
    with caplog.at_level("ERROR"):
        results = [_dual_write.enabled() for _ in range(5)]

    # Assert
    assert results == [False] * 5


# --------------------------------------------------------------------------- #
# The mirror belongs to ONE store (2026-07-19 incident)                        #
# --------------------------------------------------------------------------- #
#
# The package's own concurrency test copies os.environ into writer subprocesses,
# so they inherited SCITEX_CARDS_DUAL_WRITE=1, wrote to a pytest tmp store, and
# rebuilt the LIVE fleet DB from a 21-card fixture — replacing 2,136 real cards.
# The three tests below pin the rule that makes that unrepresentable, and the two
# controls guard the opposite error of refusing legitimate mirrors.


def _mirror_of_store_a(monkeypatch, tmp_path):
    """Build a DB that is store A's mirror, holding A's single card."""
    store_a = tmp_path / "a" / "tasks.yaml"
    store_a.parent.mkdir()
    db = tmp_path / "mirror-of-a.db"
    monkeypatch.setenv(ENV_DB, str(db))
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    _store.add_task(store_a, id="a-only", title="A", assignee="agent:test-suite")
    return db


def test_the_mirror_of_store_a_really_holds_store_as_card(monkeypatch, tmp_path):
    # Arrange
    # Act
    db = _mirror_of_store_a(monkeypatch, tmp_path)

    # Assert — the precondition every foreign-write test below rests on.
    assert "a-only" in _db_ids(db), "precondition: the DB mirrors store A"


def test_a_write_to_a_foreign_store_does_not_clobber_another_stores_mirror(
    monkeypatch, tmp_path
):
    """The incident, in miniature: store B must not overwrite store A's mirror."""
    # Arrange — a DB that is store A's mirror, holding A's card.
    db = _mirror_of_store_a(monkeypatch, tmp_path)

    # Act — a DIFFERENT store writes while the same DB is resolved from env.
    store_b = tmp_path / "b" / "tasks.yaml"
    store_b.parent.mkdir()
    _store.add_task(store_b, id="b-only", title="B", assignee="agent:test-suite")

    # Assert — A's mirror is untouched; B never entered it.
    assert _db_ids(db) == {"a-only"}, (
        "a write to store B must not reach the DB that mirrors store A — "
        "mirroring is a REPLACE, so this is how a fixture destroys a live board"
    )


def test_an_unstamped_db_is_adoptable_so_a_fresh_mirror_still_bootstraps(
    monkeypatch, tmp_path
):
    """Control: refusing must not break the FIRST write to a brand-new mirror."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "fresh.db"
    monkeypatch.setenv(ENV_DB, str(db))
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")

    # Act
    _store.add_task(store, id="first", title="first", assignee="agent:test-suite")

    # Assert
    assert "first" in _db_ids(db), (
        "an unstamped DB has no store to protect, so the first write claims it"
    )


def test_a_store_writing_to_its_own_mirror_is_not_refused(monkeypatch, tmp_path):
    """Control: the normal case — repeated writes to one's own mirror keep working."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    db = tmp_path / "own.db"
    monkeypatch.setenv(ENV_DB, str(db))
    monkeypatch.setenv(_dual_write.ENV_DUAL_WRITE, "1")
    _store.add_task(store, id="one", title="one", assignee="agent:test-suite")

    # Act
    _store.add_task(store, id="two", title="two", assignee="agent:test-suite")

    # Assert
    assert _db_ids(db) == {"one", "two"}, (
        "a store writing to the DB stamped for that same store must mirror normally"
    )


# The second door. mirror_after_save declines; the CANONICAL path must RAISE.
#
# Guarding only the mirror path left this one open, and the same pytest run that
# first rebuilt the live DB from a fixture (2,136 -> 21) did it again through here,
# harder (2,138 -> 1). Declining quietly is right when YAML still holds the card and
# wrong when the DB is the only copy: it would report success for a card that was
# never stored.
def test_canonical_write_to_a_foreign_db_RAISES_rather_than_clobbering(
    monkeypatch, tmp_path
):
    # Arrange — a DB that belongs to store A.
    from scitex_cards import _store_backend

    db = _mirror_of_store_a(monkeypatch, tmp_path)
    store_b = tmp_path / "b" / "tasks.yaml"

    # Act
    # Assert — a canonical write addressed to store B must refuse LOUDLY.
    with pytest.raises(RuntimeError, match="DIFFERENT path"):
        _store_backend.write_doc_to_db({"tasks": [{"id": "b-only"}]}, store_b)


def test_a_refused_canonical_write_leaves_the_foreign_rows_untouched(
    monkeypatch, tmp_path
):
    # Arrange — a DB that belongs to store A.
    from scitex_cards import _store_backend

    db = _mirror_of_store_a(monkeypatch, tmp_path)
    store_b = tmp_path / "b" / "tasks.yaml"

    # Act — the refused write. (Captured, not `pytest.raises`: the refusal
    # itself is pinned by the test above; here it is only the setup for the
    # single question this test asks — did any row change?)
    try:
        _store_backend.write_doc_to_db({"tasks": [{"id": "b-only"}]}, store_b)
    except RuntimeError:
        pass

    # Assert — store A's rows are untouched. (Had the write been honoured
    # instead of refused, `b-only` would be here and this would fail.)
    assert _db_ids(db) == {"a-only"}


def test_a_missing_canonical_db_RAISES_instead_of_reading_an_empty_store(
    monkeypatch, tmp_path
):
    """A failed READ must never become a write of nothing.

    In canonical mode the value returned here is written back as the WHOLE
    store, so `export_doc` answering a missing database with a well-formed
    {"tasks": []} is not "no cards" but "delete every card". That is not
    theoretical: one comment_task call took the live board from 2,138 cards to
    3 this way. Type-checking the result cannot catch it — the empty document
    is indistinguishable from a real empty board — so the check asks the file
    system instead.
    """
    # Arrange — point at a database that does not exist.
    from scitex_cards._store import _read_canonical_db_or_raise

    monkeypatch.setenv(ENV_DB, str(tmp_path / "not-here.db"))

    # Act
    # Assert
    with pytest.raises(RuntimeError, match="does not exist"):
        _read_canonical_db_or_raise()


def test_restoring_from_a_snapshot_keeps_the_stores_own_identity(monkeypatch, tmp_path):
    """A RESTORE must not re-label the database as the backup it read.

    The provenance stamp is the DB's IDENTITY — the ownership guards refuse a
    write whose store does not match it. Stamping it with the imported FILE is
    right for a bootstrap and wrong for a restore: recovering the live board
    from snapshots/tasks.yaml made the DB claim to be the snapshot's, and every
    ordinary write was then correctly-but-uselessly refused. That was patched by
    hand with an UPDATE on schema_meta during the 2026-07-19 recovery; this pins
    the supported way.
    """
    # Arrange — data lives in a snapshot; the DB serves a different (logical) store
    import sqlite3

    from scitex_cards._db_bootstrap import import_from_yaml

    snap = tmp_path / "snapshots" / "tasks.yaml"
    snap.parent.mkdir()
    snap.write_text(
        "tasks:\n- id: a\n  title: A\n  status: done\n  assignee: x\n  created_by: x\n"
    )
    live = tmp_path / "cards" / "tasks.yaml"
    live.parent.mkdir()
    db = tmp_path / "cards" / "cards.db"
    monkeypatch.setenv(ENV_DB, str(db))

    # Act
    import_from_yaml(tasks_path=str(snap), as_store=str(live))

    # Assert
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        stamped = dict(conn.execute("SELECT key, value FROM schema_meta"))["yaml_path"]
    finally:
        conn.close()
    assert stamped == str(live), (
        "the DB must keep the identity of the store it SERVES, not adopt the "
        "identity of the backup it was restored FROM"
    )
