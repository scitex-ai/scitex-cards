#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A CARD WRITE MUST NOT REBUILD THE DELIVERY RAIL.

``notifications`` used to be a section of the exported document, so
``_sync_sections`` issued ``DELETE FROM notifications`` and re-inserted from
``doc["inboxes"]`` whenever that section's hash moved — which is on any change
to any notification, because ``seen`` is overlaid into the exported record. An
ordinary ``add_task`` therefore rebuilt the live rail.

WHAT THE REBUILD DESTROYED. ``_db_sections._insert_notifications`` writes NINE
of the table's columns. ``msg_id`` is the EXACT DM dedupe key (without it the
fallback key is many-to-one at second resolution, and two distinct messages were
measured collapsing into one). ``pushed_at`` and ``confirmed_at`` are the
delivery receipts built after five operator DMs were destroyed on 2026-07-29.
``seq`` is the arrival order the drain and the ack both order by. All four were
erased, and on PostgreSQL ``seq`` was re-issued from ``nextval`` in ``ts, id``
order — silently RENUMBERING the queue.

Nothing failed while that happened, which is why it needs a test rather than a
comment. ``_migrate_v7_to_v8`` predicted it in prose — "that DELETE must be
neutralised in the same change that flips the writers, or the migration turns a
dead mirror into a silent deletion trigger" — and the writers flipped in #780
while the DELETE stayed.

The rule these tests encode is the one that already protects ``messages``: A
TABLE IS OWNED BY EXACTLY THE THING THAT PRODUCES IT.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import seed_db_from_doc

from scitex_cards._db import ENV_STORE_DSN, connect, init_schema
from scitex_cards._db_bootstrap import _DOC_CLEAR_ORDER
from scitex_cards._db_export import export_doc
from scitex_cards._db_mirror import _SECTION_KEYS, mirror_doc_incremental
from scitex_cards._store_single_card import read_card_or_raise, write_card_or_raise
from scitex_cards._task import TaskValidationError

_RAIL_ROW = (
    "n_rail01",
    "agent-x",
    "dm",
    "card-1",
    "an undelivered operator DM",
    "operator",
    "2026-08-11T19:02:07Z",
    0,
    "m_9",
    "2026-08-11T19:02:10Z",
    None,
    '{"id": "n_rail01"}',
)


@pytest.fixture
def store(new_store):
    """A real store with one notification the RAIL wrote, receipts and all.

    ``bootstrap=False`` then ``init_schema``: the row inserted below is written
    directly, so the fixture has to own the provisioning in order to know that
    nothing else has touched ``notifications`` before the card write under test.
    """
    path = new_store("cards_rail", bootstrap=False)
    conn = connect(path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO notifications(id, recipient_id, event_type, card_id, body,"
        " actor, ts, seen, msg_id, pushed_at, confirmed_at, record_json)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _RAIL_ROW,
    )
    conn.commit()
    conn.close()
    yield path


def _write_a_card(store, *, title="a card"):
    """One ordinary card write against ``store`` — the thing under test."""
    doc = {"tasks": [{"id": "card-1", "title": title, "status": "pending"}]}
    mirror_doc_incremental(doc, store, store_path=store)


def _rail_row(store):
    conn = connect(store)
    try:
        return conn.execute(
            "SELECT * FROM notifications WHERE id = 'n_rail01'"
        ).fetchone()
    finally:
        conn.close()


class TestTheRailSurvivesACardWrite:
    def test_the_notification_is_still_there(self, store):
        """The whole class: an unrelated card write must not delete a message."""
        # Arrange
        _write_a_card(store)

        # Act
        row = _rail_row(store)

        # Assert
        assert row is not None

    def test_the_push_receipt_survives(self, store):
        """``pushed_at`` is the evidence the 2026-07-29 loss was built to give."""
        # Arrange
        _write_a_card(store)

        # Act
        row = _rail_row(store)

        # Assert
        assert row["pushed_at"] == "2026-08-11T19:02:10Z"

    def test_the_exact_dm_dedupe_key_survives(self, store):
        """Without ``msg_id`` the fallback key collapses distinct messages."""
        # Arrange
        _write_a_card(store)

        # Act
        row = _rail_row(store)

        # Assert
        assert row["msg_id"] == "m_9"

    def test_an_unseen_notification_stays_unseen(self, store):
        """A card write must not mark somebody's undelivered message read."""
        # Arrange
        _write_a_card(store)

        # Act
        row = _rail_row(store)

        # Assert
        assert not row["seen"]

    def test_it_survives_a_second_write_too(self, store):
        """The hash only moves once; the rebuild must not merely be deferred."""
        # Arrange
        _write_a_card(store)

        # Act
        _write_a_card(store, title="edited")

        # Assert
        assert _rail_row(store) is not None


class TestTheDocumentDoesNotOwnTheRail:
    def test_inboxes_is_not_a_rebuilt_section(self):
        """Membership here IS the DELETE — see ``_sync_sections``."""
        # Arrange
        owned_by_the_document = _SECTION_KEYS

        # Act
        rebuilt = set(owned_by_the_document)

        # Assert
        assert "inboxes" not in rebuilt

    def test_users_is_not_a_rebuilt_section_either(self):
        """The OTHER half. Nothing pinned this until 2026-08-19.

        `inboxes` left `_SECTION_KEYS` in #780 and the test above pins that it
        stays out. `users` left later, and its absence was pinned by NOTHING —
        so re-adding it would restore a P0 with every test still green, which
        is a repair with no detector.

        WHAT RE-ADDING IT COSTS, from `_db_mirror`'s own comment, because the
        sequence is not obvious and a future reader deserves it here:

            t1  targeted write        -> users = [u1]; stored hash still hash(None)
            t3  writer w/ fresh doc   -> mismatch -> rebuild [u1], store hash([u1])
            t4  writer w/ doc from BEFORE t1
                -> hash(None) != hash([u1]) -> DELETE FROM users;
                   DELETE FROM user_names; _insert_users(None) -> REGISTRY GONE

        "Any process holding a document older than a registration silently
        deletes the registry on its next ORDINARY CARD WRITE." And `touch_user`
        is the liveness heartbeat, so `last_seen` moves the section hash
        constantly — which keeps that path HOT rather than rare.
        """
        # Arrange
        owned_by_the_document = _SECTION_KEYS

        # Act
        rebuilt = set(owned_by_the_document)

        # Assert
        assert "users" not in rebuilt

    def test_no_section_is_owned_by_the_document_at_all(self):
        """The general form, so a THIRD section cannot be added silently.

        The two tests above name the sections that have already cost something.
        This one closes the class: `_sync_sections` is a no-op today, and any
        key added to it — named or not — re-arms a DELETE-and-reinsert whose
        gate cannot tell "I changed this" from "someone else did".

        Deliberately strict. If a section ever genuinely SHOULD be
        document-owned again, this test must be changed with a stated reason,
        which is the point of it failing.
        """
        # Arrange
        owned_by_the_document = _SECTION_KEYS

        # Act
        rebuilt = tuple(owned_by_the_document)

        # Assert
        assert rebuilt == ()

    def test_a_full_rebuild_does_not_clear_notifications(self):
        """The first-run path DELETEs every table it lists."""
        # Arrange
        cleared = _DOC_CLEAR_ORDER

        # Act
        tables = set(cleared)

        # Assert
        assert "notifications" not in tables

    def test_a_full_rebuild_does_not_clear_the_recipient_keys(self):
        """A drained inbox's key is data too, and the rail owns it now."""
        # Arrange
        cleared = _DOC_CLEAR_ORDER

        # Act
        tables = set(cleared)

        # Assert
        assert "inbox_recipients" not in tables


_DELIVERY_VALUES = [
    None,
    {"version": 1, "required": False},
    {"version": 1, "required": True, "generation": 1,
     "started_at": "2026-10-03T00:00:00Z", "checkpoints": {}},
]


@pytest.fixture
def delivery_mirror_store(new_store, env):
    """Real seeded stores: hashes come only from an ordinary unchanged row write."""
    def seed(namespace, *, incremental=False):
        cards = [
            {"id": "legacy", "title": "Legacy card", "status": "deferred",
             "assignee": "agent:test-suite"},
            {"id": "protected", "title": "Protected card", "status": "deferred",
             "assignee": "agent:test-suite", "_log_meta": {"delivery": deepcopy(namespace)}},
        ]
        db = new_store("cards_delivery_mirror", bootstrap=False)
        seed_db_from_doc({"tasks": cards}, db)
        env.set(ENV_STORE_DSN, db)
        if incremental:
            for task in cards:
                card, revision = read_card_or_raise(db, task["id"])
                write_card_or_raise(db, card, expected_revision=revision)
        return db
    return seed


def _canonical_pair(dsn):
    """Payload plus SQL revision for each of the two deliberately seeded rows."""
    return {tid: read_card_or_raise(dsn, tid) for tid in ("legacy", "protected")}


@pytest.mark.parametrize("namespace", _DELIVERY_VALUES, ids=["null", "optional", "enrolled"])
@pytest.mark.parametrize("incoming", ["retained", "namespace-removed", "card-omitted"])
def test_no_hash_rebuild_refuses_any_canonical_delivery_namespace(delivery_mirror_store, namespace, incoming):
    """Absence of a hash never licenses replacing the canonical enrolled store."""
    # Arrange -- seed_db_from_doc does not populate mirror_hashes.
    target = delivery_mirror_store(namespace)
    doc = export_doc(target)[0]
    if incoming == "namespace-removed":
        protected = next(task for task in doc["tasks"] if task["id"] == "protected")
        protected["_log_meta"].pop("delivery")
    elif incoming == "card-omitted":
        doc["tasks"] = [task for task in doc["tasks"] if task["id"] != "protected"]
    # Act
    attempt = lambda: mirror_doc_incremental(doc, target, store_path=target)
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("namespace", _DELIVERY_VALUES, ids=["null", "optional", "enrolled"])
def test_refused_no_hash_rebuild_preserves_omitted_canonical_card(delivery_mirror_store, namespace):
    # Arrange
    target = delivery_mirror_store(namespace)
    before = _canonical_pair(target)
    doc = export_doc(target)[0]
    doc["tasks"] = [task for task in doc["tasks"] if task["id"] == "legacy"]
    doc["tasks"][0]["title"] = "Must not land"
    # Act
    try:
        mirror_doc_incremental(doc, target, store_path=target)
    except TaskValidationError:
        pass
    # Assert
    assert _canonical_pair(target) == before


@pytest.mark.parametrize("namespace", _DELIVERY_VALUES, ids=["null", "optional", "enrolled"])
@pytest.mark.parametrize("change", ["remove", "replace"])
def test_incremental_mirror_cannot_change_reserved_namespace(delivery_mirror_store, namespace, change):
    # Arrange
    target = delivery_mirror_store(namespace, incremental=True)
    doc = export_doc(target)[0]
    proposed = next(task for task in doc["tasks"] if task["id"] == "protected")
    if change == "remove":
        proposed["_log_meta"].pop("delivery")
    else:
        proposed["_log_meta"]["delivery"] = {"injected": True}
    # Act
    attempt = lambda: mirror_doc_incremental(doc, target, store_path=target, touched_ids=["protected"])
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("namespace", _DELIVERY_VALUES, ids=["null", "optional", "enrolled"])
def test_explicit_mirror_deletion_refuses_any_delivery_namespace(delivery_mirror_store, namespace):
    # Arrange
    target = delivery_mirror_store(namespace, incremental=True)
    doc = export_doc(target)[0]
    doc["tasks"] = [task for task in doc["tasks"] if task["id"] != "protected"]
    # Act
    attempt = lambda: mirror_doc_incremental(doc, target, store_path=target, deleted_ids=["protected"])
    # Assert
    with pytest.raises(TaskValidationError):
        attempt()


@pytest.mark.parametrize("namespace", _DELIVERY_VALUES, ids=["null", "optional", "enrolled"])
def test_refused_explicit_deletion_leaves_earlier_legacy_edit_unwritten(delivery_mirror_store, namespace):
    """The removal preflight must precede every write in the caller's batch."""
    # Arrange
    target = delivery_mirror_store(namespace, incremental=True)
    before = _canonical_pair(target)
    doc = export_doc(target)[0]
    doc["tasks"] = [task for task in doc["tasks"] if task["id"] == "legacy"]
    doc["tasks"][0]["title"] = "Must not land"
    # Act
    try:
        mirror_doc_incremental(doc, target, store_path=target, deleted_ids=["protected"])
    except TaskValidationError:
        pass
    # Assert
    assert _canonical_pair(target) == before


# EOF
