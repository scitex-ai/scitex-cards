#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SQLite inbox backend (Phase 1 of the store migration).

Real sqlite temp DBs, NO mocks (STX-NM / PA-306): every test writes to a real
DB under ``tmp_path``'s runtime dir (resolved from an explicit ``store=``).
Covers the semantics the YAML path guarantees, re-implemented against SQLite:

* round-trip enqueue -> poll -> ack / mark_seen advances the cursor;
* dedup on ``(event_type, card_id, ts, actor)`` — a re-emit yields one record;
* ``supersede=True`` drops UNSEEN predecessors matching ``(event_type, card_id)``
  while leaving SEEN history untouched;
* ``unseen_only`` filter + full-history read;
* the migration verb copies file-backed ``inboxes.json`` records into the
  store's rail, idempotently, without deleting the source;
* the backend switch (``SCITEX_CARDS_INBOX_BACKEND=sqlite``) routes the public
  ``_inbox`` API onto the SQLite implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_cards import _inbox_sqlite as sq


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    """A DB store, because the rail now lives IN the store.

    This used to be ``tasks.yaml``, which worked only while the rail wrote a
    separate ``runtime/cards.db`` beside the store. Now that the rail's target
    IS the store, handing it a YAML document makes the enqueue open a YAML file
    as SQLite — and it says so, loudly (``file is not a database``). That is the
    correct answer for a YAML store; the fix is to stop naming one.
    """
    return tmp_path / "cards.db"


def _sidecar(store):
    """Path of the break-glass ``inboxes.json`` sidecar beside ``store``."""
    from scitex_cards._inbox import _INBOXES_FILENAME

    return store.parent / _INBOXES_FILENAME


def _read_sidecar(store):
    """Read the sidecar document (``{"inboxes": {recipient: [...]}}``)."""
    import json

    path = _sidecar(store)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _rail_row_count(store):
    """Rows on the rail, read WITHOUT going through the inbox API.

    An absent target, or a target with no rail table yet, is zero rows — those
    are the shapes a store has before anything has been enqueued into it.
    """
    import sqlite3

    target = Path(sq.inbox_target(store))
    if not target.exists():
        return 0
    with sq.open_connection(target) as conn:
        from scitex_cards._inbox_shape import shape_for

        try:
            return conn.execute(
                f"SELECT COUNT(*) AS n FROM {shape_for(conn).table}"
            ).fetchone()["n"]
        except sqlite3.OperationalError:
            return 0


def _enqueue_completed(
    store,
    recipient="u_abc",
    card_id="c1",
    body="x",
    actor="bob",
    ts="2026-06-26T00:00:00Z",
):
    return sq.enqueue(
        recipient,
        event_type="completed",
        card_id=card_id,
        body=body,
        actor=actor,
        ts=ts,
        store=store,
    )


def _enqueue_reassigned(store, ts="2026-06-26T00:00:00Z"):
    return sq.enqueue(
        "u_abc",
        event_type="reassigned",
        card_id="c1",
        body="Card c1 reassigned to you",
        actor="bob",
        ts=ts,
        store=store,
    )


def _enqueue_digest(store, body, ts):
    return sq.enqueue(
        "u_abc",
        event_type="digest",
        card_id="__digest__",
        body=body,
        actor=None,
        ts=ts,
        supersede=True,
        store=store,
    )


def _seed_sidecar_inbox(store, recipient, card_id, body, actor, ts, event_type):
    """Seed one record into the file-backed ``inboxes.json`` sidecar — the
    source ``migrate_to_sqlite`` still has to carry (see
    ``_inbox_migrate.gather_migratable_inboxes``).

    It used to seed the OTHER source, the embedded ``inboxes:`` section of a
    pre-cutover YAML task store. That source cannot be expressed any more: the
    rail's destination is now the store itself, so a store holding a YAML
    document would have to be a SQLite database at the same time. The sidecar
    is the source that survives a DB store, and it exercises the same carry.

    Deliberately independent of the live ``_inbox`` module's own write path, so
    a change there cannot quietly stop these tests from seeding anything.
    """
    import json

    doc = _read_sidecar(store)
    inboxes = doc.setdefault("inboxes", {})
    record = {
        "id": f"n_{recipient}_{card_id}_{len(inboxes.get(recipient, []))}",
        "event_type": event_type,
        "card_id": card_id,
        "body": body,
        "actor": actor,
        "ts": ts,
        "seen": False,
    }
    inboxes.setdefault(recipient, []).append(record)
    _sidecar(store).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dict(record)


def _mark_sidecar_seen(store, recipient, record_id):
    """Flip one seeded sidecar record's ``seen`` flag directly (test-only
    counterpart to :func:`_seed_sidecar_inbox` — see its docstring)."""
    import json

    doc = _read_sidecar(store)
    for record in doc.get("inboxes", {}).get(recipient, []):
        if record.get("id") == record_id:
            record["seen"] = True
    _sidecar(store).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# fixtures — shared setup for the split tests below                            #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def two_record_inbox(tmp_path):
    """Two completed notices for one recipient, both unseen."""
    store = _store(tmp_path)
    r1 = _enqueue_completed(store, card_id="c1", body="a", ts="2026-06-26T00:00:01Z")
    r2 = _enqueue_completed(store, card_id="c2", body="b", ts="2026-06-26T00:00:02Z")
    return {"store": store, "r1": r1, "r2": r2}


@pytest.fixture()
def migrated_store(tmp_path):
    """Two YAML inbox records (one acked) migrated into SQLite."""
    store = _store(tmp_path)
    a = _seed_sidecar_inbox(
        store, "u_abc", "c1", "hi", "bob", "2026-06-26T00:00:00Z", "reassigned"
    )
    b = _seed_sidecar_inbox(
        store, "dave", "c2", "bye", "carol", "2026-06-26T00:00:01Z", "completed"
    )
    # Mark one seen so the seen flag carries across.
    _mark_sidecar_seen(store, "dave", b["id"])
    stats = sq.migrate_to_sqlite(store=store)
    return {"store": store, "a": a, "b": b, "stats": stats}


@pytest.fixture()
def twice_migrated_store(tmp_path):
    """One YAML inbox record run through migrate_to_sqlite TWICE."""
    store = _store(tmp_path)
    _seed_sidecar_inbox(
        store, "u_abc", "c1", "hi", "bob", "2026-06-26T00:00:00Z", "reassigned"
    )
    first = sq.migrate_to_sqlite(store=store)
    second = sq.migrate_to_sqlite(store=store)
    return {"store": store, "first": first, "second": second}


@pytest.fixture()
def sqlite_backend_store(tmp_path, env):
    """One record written through the public API with the sqlite backend."""
    from scitex_cards import _inbox

    store = _store(tmp_path)
    env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")
    rec = _inbox.enqueue(
        "u_abc",
        event_type="completed",
        card_id="c1",
        body="x",
        actor="bob",
        ts="2026-06-26T00:00:00Z",
        store=store,
    )
    return {"store": store, "rec": rec, "inbox": _inbox}


@pytest.fixture()
def default_backend_store(tmp_path, env):
    """One record written through the public API with the backend UNSET."""
    from scitex_cards import _inbox

    store = _store(tmp_path)
    env.delete("SCITEX_CARDS_INBOX_BACKEND")  # unset -> the default
    _inbox.enqueue(
        "u_abc",
        event_type="completed",
        card_id="c1",
        body="x",
        actor="bob",
        ts="2026-06-26T00:00:00Z",
        store=store,
    )
    return {"store": store, "inbox": _inbox}


@pytest.fixture()
def yaml_backend_store(tmp_path, env):
    """One record written through the explicit break-glass yaml backend."""
    from scitex_cards import _inbox

    store = _store(tmp_path)
    env.set("SCITEX_CARDS_INBOX_BACKEND", "yaml")  # explicit break-glass
    _inbox.enqueue(
        "u_abc",
        event_type="completed",
        card_id="c1",
        body="x",
        actor="bob",
        ts="2026-06-26T00:00:00Z",
        store=store,
    )
    return {"store": store, "inbox": _inbox, "env": env}


@pytest.fixture()
def cli_migrated_store():
    """A YAML-seeded store run through the `inbox migrate-to-sqlite` verb.

    The verb resolves the store itself (``tests/conftest.py`` provisions one
    per test), so the seed has to land on the SAME resolved store rather than
    on a path this test names.
    """
    from click.testing import CliRunner

    from scitex_cards._cli import main
    from scitex_cards._paths import resolve_tasks_path

    store = resolve_tasks_path(None)
    _seed_sidecar_inbox(
        store, "u_abc", "c1", "hi", "bob", "2026-06-26T00:00:00Z", "reassigned"
    )
    result = CliRunner().invoke(main, ["inbox", "migrate-to-sqlite", "-y"])
    return {"store": store, "result": result}


@pytest.fixture()
def cli_dry_run_store():
    """A YAML-seeded store run through the migrate verb in --dry-run mode."""
    from click.testing import CliRunner

    from scitex_cards._cli import main
    from scitex_cards._paths import resolve_tasks_path

    store = resolve_tasks_path(None)
    _seed_sidecar_inbox(
        store, "u_abc", "c1", "hi", "bob", "2026-06-26T00:00:00Z", "reassigned"
    )
    result = CliRunner().invoke(main, ["inbox", "migrate-to-sqlite", "--dry-run"])
    return {"store": store, "result": result}


# --------------------------------------------------------------------------- #
# db path + schema                                                            #
# --------------------------------------------------------------------------- #
def test_db_path_lives_under_runtime_dir(tmp_path):
    # <store_dir>/runtime/cards.db per the SciTeX runtime-DB convention.
    # Arrange
    store = _store(tmp_path)
    # Act
    p = sq.inbox_db_path(store)
    # Assert
    assert p.parent.name == "runtime"


def test_db_path_parent_is_the_store_dir(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    p = sq.inbox_db_path(store)
    # Assert — the runtime dir sits beside the store, not somewhere global.
    assert p.parent.parent == tmp_path


def test_db_file_is_named_cards_db(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    p = sq.inbox_db_path(store)
    # Assert
    assert p.name == "cards.db"


def test_schema_has_recipient_seen_index(tmp_path):
    # Arrange — trigger creation via an enqueue, then inspect the schema.
    store = _store(tmp_path)
    _enqueue_completed(store)
    # Act
    with sq.open_connection(sq.inbox_target(store)) as conn:
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    # Assert — an index specifically on (recipient, seen) exists.
    assert "idx_inbox_recipient_seen" in indexes


def test_schema_enables_wal_journal_mode(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store)
    # Act
    with sq.open_connection(sq.inbox_target(store)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    # Assert — WAL is what lets a reader poll while a writer enqueues.
    assert journal_mode.lower() == "wal"


# --------------------------------------------------------------------------- #
# enqueue -> poll -> ack / mark_seen                                          #
# --------------------------------------------------------------------------- #
def test_enqueue_returns_a_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec is not None


def test_enqueue_record_starts_unseen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec["seen"] is False


def test_enqueue_record_carries_the_card_id(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec["card_id"] == "c1"


def test_enqueue_record_id_uses_the_n_prefix(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = _enqueue_reassigned(store)
    # Assert
    assert rec["id"].startswith("n_")


def test_enqueue_then_poll_returns_unseen_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = sq.poll_inbox("u_abc", store=store)
    # Assert
    assert len(got) == 1


def test_polled_record_carries_the_card_id(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = sq.poll_inbox("u_abc", store=store)
    # Assert
    assert got[0]["card_id"] == "c1"


def test_polled_record_carries_the_body(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = sq.poll_inbox("u_abc", store=store)
    # Assert
    assert got[0]["body"] == "Card c1 reassigned to you"


def test_polled_record_is_still_unseen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    got = sq.poll_inbox("u_abc", store=store)
    # Assert — a plain poll must not consume the record.
    assert got[0]["seen"] is False


def test_first_drain_returns_the_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="done")
    # Act
    first = sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Assert
    assert [r["card_id"] for r in first] == ["c1"]


def test_first_drain_marks_the_record_seen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="done")
    # Act
    first = sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Assert
    assert first[0]["seen"] is True


def test_mark_seen_advances_cursor(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="done")
    sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Act
    second = sq.poll_inbox("u_abc", unseen_only=True, store=store)
    # Assert — a second unseen-only poll returns nothing new.
    assert second == []


def test_full_history_still_holds_the_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="done")
    sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Act
    history = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert — draining advances a cursor; it does not delete.
    assert [r["card_id"] for r in history] == ["c1"]


def test_full_history_shows_the_record_as_seen(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, body="done")
    sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    # Act
    history = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert history[0]["seen"] is True


def test_ack_marks_specific_ids_seen(two_record_inbox):
    # Arrange
    store, r1 = two_record_inbox["store"], two_record_inbox["r1"]
    # Act
    flipped = sq.ack("u_abc", [r1["id"]], store=store)
    # Assert
    assert flipped == [r1["id"]]


def test_ack_leaves_other_records_unseen(two_record_inbox):
    # Arrange
    store = two_record_inbox["store"]
    r1, r2 = two_record_inbox["r1"], two_record_inbox["r2"]
    sq.ack("u_abc", [r1["id"]], store=store)
    # Act
    unseen = sq.poll_inbox("u_abc", unseen_only=True, store=store)
    # Assert
    assert [r["id"] for r in unseen] == [r2["id"]]


def test_ack_twice_is_a_noop(two_record_inbox):
    # Arrange
    store, r1 = two_record_inbox["store"], two_record_inbox["r1"]
    sq.ack("u_abc", [r1["id"]], store=store)
    # Act
    again = sq.ack("u_abc", [r1["id"]], store=store)
    # Assert — already seen, so nothing flips.
    assert again == []


def test_ack_unknown_id_is_a_noop(two_record_inbox):
    # Arrange
    store = two_record_inbox["store"]
    # Act
    flipped = sq.ack("u_abc", ["n_nope"], store=store)
    # Assert
    assert flipped == []


def test_poll_preserves_append_order(tmp_path):
    # Arrange
    store = _store(tmp_path)
    for i in range(3):
        _enqueue_completed(store, card_id=f"c{i}", ts=f"2026-06-26T00:00:0{i}Z")
    # Act
    got = sq.poll_inbox("u_abc", store=store)
    # Assert
    assert [r["card_id"] for r in got] == ["c0", "c1", "c2"]


# --------------------------------------------------------------------------- #
# dedup + supersede                                                           #
# --------------------------------------------------------------------------- #
def test_dedup_first_enqueue_returns_a_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    first = _enqueue_reassigned(store)
    # Assert
    assert first is not None


def test_dedup_exact_reemit_returns_none(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    second = _enqueue_reassigned(store)  # exact re-emit
    # Assert
    assert second is None


def test_dedup_keeps_only_one_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    _enqueue_reassigned(store)
    # Act
    everything = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 1


def test_dedup_distinct_ts_is_kept_separately(tmp_path):
    # A DIFFERENT ts is a genuine second event, not a re-emit.
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    # Act
    third = _enqueue_reassigned(store, ts="2026-06-26T00:00:05Z")
    # Assert
    assert third is not None


def test_dedup_distinct_ts_yields_two_records(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_reassigned(store)
    _enqueue_reassigned(store, ts="2026-06-26T00:00:05Z")
    # Act
    everything = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 2


def test_dedup_with_none_actor_first_enqueue_lands(tmp_path):
    # actor=None must be handled by the dedup (NULL-safe), not double-enqueued.
    # Arrange
    store = _store(tmp_path)
    # Act
    first = _enqueue_completed(store, actor=None)
    # Assert
    assert first is not None


def test_enqueue_dedup_with_none_actor(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, actor=None)
    # Act
    second = _enqueue_completed(store, actor=None)
    # Assert — NULL == NULL is never true in SQL; the dedup must not rely on it.
    assert second is None


def test_dedup_with_none_actor_keeps_one_record(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_completed(store, actor=None)
    _enqueue_completed(store, actor=None)
    # Act
    everything = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 1


def test_supersede_replaces_unseen_predecessors(tmp_path):
    # Two digests for the same (event_type, card_id) at different ts.
    # Arrange
    store = _store(tmp_path)
    _enqueue_digest(store, "old", "2026-06-26T00:00:00Z")
    _enqueue_digest(store, "new", "2026-06-26T00:00:05Z")
    # Act
    unseen = sq.poll_inbox("u_abc", unseen_only=True, store=store)
    # Assert — only the newest unseen digest survives.
    assert [r["body"] for r in unseen] == ["new"]


def test_supersede_keeps_seen_history(tmp_path):
    # Arrange — mark the first digest seen (history), then supersede.
    store = _store(tmp_path)
    _enqueue_digest(store, "old", "2026-06-26T00:00:00Z")
    sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    _enqueue_digest(store, "new", "2026-06-26T00:00:05Z")
    # Act
    history = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert — the SEEN predecessor is preserved; the new digest is appended.
    assert [r["body"] for r in history] == ["old", "new"]


def test_supersede_leaves_the_seen_flags_intact(tmp_path):
    # Arrange
    store = _store(tmp_path)
    _enqueue_digest(store, "old", "2026-06-26T00:00:00Z")
    sq.poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    _enqueue_digest(store, "new", "2026-06-26T00:00:05Z")
    # Act
    history = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert [r["seen"] for r in history] == [True, False]


def test_enqueue_with_falsy_recipient_returns_none(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    rec = sq.enqueue(
        "", event_type="completed", card_id="c1", body="x", actor=None, store=store
    )
    # Assert
    assert rec is None


def test_poll_inbox_with_falsy_recipient_is_empty(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    got = sq.poll_inbox("", store=store)
    # Assert
    assert got == []


def test_poll_inbox_for_unknown_recipient_is_empty(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    got = sq.poll_inbox("u_nobody", store=store)
    # Assert
    assert got == []


def test_ack_for_unknown_recipient_is_a_noop(tmp_path):
    # Arrange
    store = _store(tmp_path)
    # Act
    flipped = sq.ack("u_nobody", ["n_x"], store=store)
    # Assert
    assert flipped == []


# --------------------------------------------------------------------------- #
# migration YAML -> SQLite                                                    #
# --------------------------------------------------------------------------- #
def test_migrate_counts_every_yaml_record(migrated_store):
    # Arrange
    stats = migrated_store["stats"]
    # Act
    records = stats["records"]
    # Assert
    assert records == 2


def test_migrate_copies_yaml_records(migrated_store):
    # Arrange
    stats = migrated_store["stats"]
    # Act
    inserted = stats["inserted"]
    # Assert
    assert inserted == 2


def test_migrate_keeps_the_first_recipients_record(migrated_store):
    # Arrange
    store, a = migrated_store["store"], migrated_store["a"]
    # Act
    got_abc = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert [r["id"] for r in got_abc] == [a["id"]]


def test_migrate_carries_the_unseen_flag(migrated_store):
    # Arrange
    store = migrated_store["store"]
    # Act
    got_abc = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert got_abc[0]["seen"] is False


def test_migrate_keeps_the_second_recipients_record(migrated_store):
    # Arrange
    store, b = migrated_store["store"], migrated_store["b"]
    # Act
    got_dave = sq.poll_inbox("dave", unseen_only=False, store=store)
    # Assert
    assert [r["id"] for r in got_dave] == [b["id"]]


def test_migrate_carries_the_seen_flag(migrated_store):
    # Arrange
    store = migrated_store["store"]
    # Act
    got_dave = sq.poll_inbox("dave", unseen_only=False, store=store)
    # Assert — an already-drained record must not come back unread.
    assert got_dave[0]["seen"] is True


def test_migrate_does_not_delete_the_source(migrated_store):
    # Arrange
    store = migrated_store["store"]
    # Act
    doc = _read_sidecar(store)
    # Assert — the migration stays reversible.
    assert set(doc["inboxes"].keys()) == {"u_abc", "dave"}


def test_migrate_first_pass_inserts_the_record(twice_migrated_store):
    # Arrange
    first = twice_migrated_store["first"]
    # Act
    inserted = first["inserted"]
    # Assert
    assert inserted == 1


def test_migrate_is_idempotent(twice_migrated_store):
    # Arrange
    second = twice_migrated_store["second"]
    # Act
    inserted = second["inserted"]
    # Assert — deduped on the notification id.
    assert inserted == 0


def test_migrate_second_pass_reports_the_skip(twice_migrated_store):
    # Arrange
    second = twice_migrated_store["second"]
    # Act
    skipped = second["skipped"]
    # Assert
    assert skipped == 1


def test_migrate_twice_leaves_a_single_row(twice_migrated_store):
    # Arrange
    store = twice_migrated_store["store"]
    # Act
    everything = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 1


# --------------------------------------------------------------------------- #
# backend switch through the public _inbox API                                #
# --------------------------------------------------------------------------- #
def test_backend_switch_returns_a_record(sqlite_backend_store):
    # Arrange
    rec = sqlite_backend_store["rec"]
    # Act
    enqueued = rec
    # Assert
    assert enqueued is not None


def test_backend_switch_routes_to_sqlite(sqlite_backend_store):
    # Arrange
    store, _inbox = sqlite_backend_store["store"], sqlite_backend_store["inbox"]
    # Act — read back through the public API (still routed to sqlite).
    got = _inbox.poll_inbox("u_abc", store=store)
    # Assert
    assert [r["card_id"] for r in got] == ["c1"]


def test_backend_switch_writes_no_file_sidecar(sqlite_backend_store):
    # The write went to the STORE, not to a file beside it — the sqlite path
    # never touched inboxes.json, so no sidecar exists to read a stale copy
    # from later.
    # Arrange
    store = sqlite_backend_store["store"]
    # Act
    doc = _read_sidecar(store)
    # Assert
    assert not doc.get("inboxes")


def test_backend_switch_creates_the_sqlite_db(sqlite_backend_store):
    # Arrange
    store = sqlite_backend_store["store"]
    # Act — the rail's OWN target, which is now the store itself.
    db_path = Path(sq.inbox_target(store))
    # Assert
    assert db_path.exists()


def test_default_backend_is_sqlite(default_backend_store):
    # Arrange
    store = default_backend_store["store"]
    # Act
    db_path = Path(sq.inbox_target(store))
    # Assert — with the env var unset, the default created the DB file.
    assert db_path.exists()


def test_default_backend_round_trips_the_record(default_backend_store):
    # Arrange
    store, _inbox = default_backend_store["store"], default_backend_store["inbox"]
    # Act
    got = _inbox.poll_inbox("u_abc", store=store)
    # Assert
    assert [r["card_id"] for r in got] == ["c1"]


def test_break_glass_yaml_backend(yaml_backend_store):
    # Arrange
    store = yaml_backend_store["store"]
    # Act — the break-glass backend persists to its own inboxes.json sidecar,
    # not the main store file (see scitex_cards._inbox's module docstring).
    import json

    from scitex_cards._inbox import _INBOXES_FILENAME

    doc = json.loads((store.parent / _INBOXES_FILENAME).read_text(encoding="utf-8"))
    # Assert
    assert doc["inboxes"]["u_abc"][0]["card_id"] == "c1"


def test_break_glass_yaml_backend_creates_no_sqlite_db(yaml_backend_store):
    # Arrange
    store = yaml_backend_store["store"]
    # Act
    db_path = Path(sq.inbox_target(store))
    # Assert
    assert not db_path.exists()


#: WHY the four `lazy_auto_migration` tests below are split but share one story:
#: incident-critical — switching to the default (sqlite) backend must carry
#: pre-existing YAML inbox records over AUTOMATICALLY on first access, with no
#: explicit migrate step and no duplication on re-access. The four facts are:
#: the yaml-seeded store starts with no DB; the first sqlite read returns the
#: seeded record; that read created the DB; and a second read does NOT
#: re-migrate (the migrated_from_yaml flag holds, despite the YAML record
#: remaining on disk).


def test_lazy_auto_migration_starts_without_a_sqlite_db(yaml_backend_store):
    # Arrange
    store = yaml_backend_store["store"]
    # Act
    db_path = Path(sq.inbox_target(store))
    # Assert
    assert not db_path.exists()


def test_lazy_auto_migration_on_first_sqlite_access(yaml_backend_store):
    # Arrange — switch to the default (sqlite) after the yaml seed.
    store, _inbox = yaml_backend_store["store"], yaml_backend_store["inbox"]
    yaml_backend_store["env"].delete("SCITEX_CARDS_INBOX_BACKEND")
    # Act
    got = _inbox.poll_inbox("u_abc", store=store)
    # Assert — first access lazily migrated the record.
    assert [r["card_id"] for r in got] == ["c1"]


def test_lazy_auto_migration_creates_the_sqlite_db(yaml_backend_store):
    # Arrange
    store, _inbox = yaml_backend_store["store"], yaml_backend_store["inbox"]
    yaml_backend_store["env"].delete("SCITEX_CARDS_INBOX_BACKEND")
    # Act
    _inbox.poll_inbox("u_abc", store=store)
    # Assert
    assert Path(sq.inbox_target(store)).exists()


def test_lazy_auto_migration_does_not_re_migrate(yaml_backend_store):
    # Arrange
    store, _inbox = yaml_backend_store["store"], yaml_backend_store["inbox"]
    yaml_backend_store["env"].delete("SCITEX_CARDS_INBOX_BACKEND")
    _inbox.poll_inbox("u_abc", store=store)
    # Act
    again = _inbox.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert — still exactly one row despite the YAML record remaining on disk.
    assert len(again) == 1


# --------------------------------------------------------------------------- #
# CLI verb                                                                     #
# --------------------------------------------------------------------------- #
def test_cli_migrate_exits_zero(cli_migrated_store):
    # Arrange
    result = cli_migrated_store["result"]
    # Act
    exit_code = result.exit_code
    # Assert
    assert exit_code == 0, result.output


def test_cli_migrate_to_sqlite(cli_migrated_store):
    # Arrange
    store = cli_migrated_store["store"]
    # Act
    db_path = Path(sq.inbox_target(store))
    # Assert
    assert db_path.exists()


def test_cli_migrate_copies_the_record(cli_migrated_store):
    # Arrange
    store = cli_migrated_store["store"]
    # Act
    everything = sq.poll_inbox("u_abc", unseen_only=False, store=store)
    # Assert
    assert len(everything) == 1


def test_cli_migrate_dry_run_exits_zero(cli_dry_run_store):
    # Arrange
    result = cli_dry_run_store["result"]
    # Act
    exit_code = result.exit_code
    # Assert
    assert exit_code == 0, result.output


def test_cli_migrate_dry_run_writes_nothing(cli_dry_run_store):
    # Arrange — the rail's target is the store itself, which already exists, so
    # "wrote nothing" is now a claim about ROWS, not about a file's presence.
    # Read the rail DIRECTLY rather than through `poll_inbox`: a poll runs the
    # lazy carry itself, so it would report the record the dry run declined to
    # write and the test would be measuring its own read.
    store = cli_dry_run_store["store"]
    # Act
    carried = _rail_row_count(store)
    # Assert — a dry run carries no record.
    assert carried == 0


# --------------------------------------------------------------------------- #
# WHERE THE RAIL POINTS — the two answers, both pinned                        #
# --------------------------------------------------------------------------- #
# These exist because BOTH answers were lost at once and nothing caught it.
# `inbox_target` replaced `inbox_db_path` and dropped its env override, so a
# pinned rail was read from somewhere else with nothing said. The visible cost
# was `test_an_unreadable_inbox_says_why_it_is_silent`: its arrangement pins the
# rail at an uncreatable path to prove a broken rail SPEAKS, and an ignored
# override turned that into a healthy read of the scratch store — the test went
# quiet because the arrangement went vacuous, which is the worst way for a
# fail-loud contract to die. The default is pinned alongside it because the two
# only mean anything together: "the store, unless someone said otherwise".


def test_the_rail_targets_the_store_itself(tmp_path, env):
    """The headline of this change: notifications live IN the canonical store,
    not in a per-container ``runtime/cards.db`` beside it."""
    # Arrange
    env.delete("SCITEX_CARDS_INBOX_DB")
    store = _store(tmp_path)
    # Act
    target = str(sq.inbox_target(store))
    # Assert
    assert target == str(store)


def test_an_explicit_inbox_db_override_still_wins(tmp_path, env):
    """An operator who NAMES the rail gets the rail they named. Ignoring a set
    variable is a silent fallback, and it is what broke the fail-loud test."""
    # Arrange
    pinned = tmp_path / "pinned-rail.db"
    env.set("SCITEX_CARDS_INBOX_DB", str(pinned))
    # Act
    target = str(sq.inbox_target(_store(tmp_path)))
    # Assert
    assert target == str(pinned)


# EOF
