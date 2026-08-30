#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the store adapter + in-memory doc bootstrap (RFC #348).

Real stores + real in-memory doc fixtures — NO mocks. The database IS the store;
there is no YAML file to read and the ``import_from_yaml`` entry point is gone.
Tests build the same in-memory document the YAML used to hold and seed it into a
throwaway store via ``seed_db_from_doc`` (the surviving ``_rebuild_from_doc``
primitive), then assert the schema/columns/counts the RFC pins.

EVERY FIXTURE HOLDS A TARGET, NOT A PATH, and the rename is the point rather
than tidiness. A store is a PostgreSQL database now; the door refuses a filename
before it touches the filesystem, and a key called ``db_path`` is what invites
the next reader to call ``.parent`` or ``.exists()`` on it -- the assumption
this repository has already paid for twice (``_store_backend`` renamed its own
local for the same reason). ``bootstrap=False`` throughout: the harness's
per-test store is already schema-complete, so a fixture standing in for an OLD
store must start from an empty schema or the migration assertions are true
before the act.
"""

from __future__ import annotations

import os

import pytest
from conftest import seed_db_from_doc

from scitex_cards import _db, _db_bootstrap, _model
from scitex_cards._ddl import execute_ddl
from scitex_cards._schema_probe import column_names, table_names
from scitex_cards._schema_shape import observed_version
from scitex_cards._store_target import StoreTargetNotConfigured


def _stamped(conn) -> int | None:
    """The version the store SAYS it is. There is one stamp on this engine.

    ``PRAGMA user_version`` was the second one; ``_read_stamps`` returns
    ``stamped_pragma=None`` here by design, so ``schema_meta.schema_version`` is
    the whole answer.
    """
    return observed_version(conn).stamped_meta


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def store(new_store):
    """An in-memory tasks doc + threads map exercising every child collection.

    The database IS the store; there is no YAML file to read. Tests build the
    same in-memory document the YAML used to hold and seed it into the throwaway
    ``target`` via ``seed_db_from_doc``.
    """
    tasks_doc = {
        "tasks": [
            {
                "id": "c1",
                "title": "First card",
                "status": "in_progress",
                "task": "do the thing",
                "project": "scitex-cards",
                "repo": "scitex-cards",
                "agent": "agent:alice",
                "group": "core",
                "priority": 3,
                "depends_on": ["c2"],
                "blocks": ["c3"],
                "collaborators": ["bob"],
                "subscribers": ["carol", "bob"],
                "deadlines": ["2026-08-01", "2026-09-01 +1w"],
                "_log_meta": {"completed_by": "alice"},
                "comments": [
                    {"author": "alice", "ts": "2026-07-01T00:00:00Z", "text": "hi"},
                    {
                        "author": "bob",
                        "ts": "2026-07-02T00:00:00Z",
                        "text": "unblocked",
                        "kind": "unblock",
                    },
                ],
            },
            {"id": "c2", "title": "Second card", "status": "done"},
            {
                "id": "c3",
                "title": "Third card",
                "status": "blocked",
                "blocker": "dependency",
            },
        ],
        "users": [
            {
                "id": "u_aaaaaaaaaaaa",
                "kind": "agent",
                "names": ["alice", "proj-alice"],
                "host_at_name": "hostA@alice",
                "notify": {"telegram": True},
                "a2a_port": 7001,
                "created_at": "2026-06-01T00:00:00Z",
                "last_seen": "2026-07-01T00:00:00Z",
            },
            {"id": "u_bbbbbbbbbbbb", "kind": "human", "names": ["bob"]},
        ],
        "inboxes": {
            "u_aaaaaaaaaaaa": [
                {
                    "id": "n_111111111111",
                    "event_type": "reassigned",
                    "card_id": "c1",
                    "body": "Card c1 reassigned",
                    "actor": "bob",
                    "ts": "2026-07-03T00:00:00Z",
                    "seen": False,
                },
            ],
            "dave": [
                {
                    "id": "n_222222222222",
                    "event_type": "completed",
                    "card_id": "c2",
                    "body": "done",
                    "actor": "alice",
                    "ts": "2026-07-04T00:00:00Z",
                    "seen": True,
                },
            ],
        },
    }
    threads_doc = {
        "threads": {
            "dm:alice::bob": [
                {
                    "id": "m_111111111111",
                    "thread": "dm:alice::bob",
                    "from": "alice",
                    "to": "bob",
                    "body": "ping",
                    "ts": "2026-07-05T00:00:00Z",
                    "read": False,
                },
            ],
        },
    }
    return {
        "tasks_doc": tasks_doc,
        "threads": threads_doc["threads"],
        "target": new_store("cards_db_tests", bootstrap=False),
    }


@pytest.fixture
def imported(store):
    """The `store` doc seeded into the retired engine: summary + a live row-factory conn."""
    summary = seed_db_from_doc(
        store["tasks_doc"], store["target"], threads=store["threads"]
    )
    conn = _db.connect(store["target"])
    try:
        yield {"summary": summary, "conn": conn, "store": store}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #
def test_resolve_db_path_explicit_wins(tmp_path, env):
    # Arrange
    env.set(_db.ENV_DB, str(tmp_path / "env.db"))

    # Act
    got = _db.resolve_db_path(tmp_path / "explicit.db")

    # Assert
    assert got == (tmp_path / "explicit.db")


def test_resolve_db_path_env_over_userpath(tmp_path, env):
    # Arrange
    env.set(_db.ENV_DB, str(tmp_path / "env.db"))

    # Act
    got = _db.resolve_db_path()

    # Assert
    assert got == (tmp_path / "env.db")


def _resolve_with_delegated_user_path(tmp_path, env):
    """Neutralise both env tiers and let the REAL ecosystem resolver run.

    Returns ``(outcome, expected_path)``, where ``outcome`` is the raised
    exception rather than a path. It could not be a path since 2026-08-13:
    the final tier no longer RETURNS the delegated filename, it REFUSES and
    names it.

    NO fake. `scitex_config`'s `local_state.user_root()` reads $SCITEX_DIR on
    every call — its own docstring says "resolved per call so live SCITEX_DIR
    changes are honoured" — so setting that variable steers the real
    `user_path("cards", "cards.db")` to a known location under ``tmp_path``.

    Replacing that function with a recorder proved only that SOMETHING was
    called with those arguments. Running it for real proves the refusal names
    the path the ecosystem resolver ACTUALLY produces — which is the property
    a reader following the message depends on, and the one a recorder cannot
    check, because it supplied the answer it then asserted.
    """
    env.delete(_db.ENV_DB)
    env.set("SCITEX_DIR", str(tmp_path / "userscope"))
    expected = tmp_path / "userscope" / "cards" / "cards.db"
    try:
        outcome = _db.resolve_db_path()
    except StoreTargetNotConfigured as exc:
        outcome = exc
    return outcome, expected


def test_resolve_db_path_refuses_instead_of_returning_the_user_path(tmp_path, env):
    """Final tier REFUSES. It used to return the delegated filename.

    The abolished behaviour was returning a the retired engine path nobody chose, handed
    back with the same type as one somebody did choose. That is the whole
    defect, so this asserts the type of the outcome and not merely that
    something went wrong.
    """
    # Arrange
    # Act
    got, _expected = _resolve_with_delegated_user_path(tmp_path, env)

    # Assert
    assert isinstance(got, StoreTargetNotConfigured)


def test_resolve_db_path_delegates_with_the_cards_package_key(tmp_path, env):
    """The delegation still routes through the ecosystem resolver.

    UNCHANGED BY THE ABOLITION, and deliberately still pinned: the filename is
    still resolved through `local_state.user_path`, now to NAME the store in
    the refusal rather than to serve it. A refusal that guessed the path itself
    would send the reader to a file this package does not actually use.

    The old version asserted `calls == [("cards", ("cards.db",))]` against a
    recorder. That could not distinguish "the resolver produced this path" from
    "the test supplied this path" — the fake returned its own sentinel and the
    assertion checked the arguments it had just been handed. Asserting the
    ``<pkg>/cards.db`` SHAPE of the real resolver's output covers the same
    contract with the resolver actually in the loop.
    """
    # Arrange
    # Act
    got, expected = _resolve_with_delegated_user_path(tmp_path, env)

    # Assert — user_root()/cards/cards.db, i.e. the package key and filename.
    assert str(expected).endswith(f"{os.sep}cards{os.sep}cards.db")


def test_the_refusal_names_the_delegated_path(tmp_path, env):
    """And the name it reports is the one the real delegation produced."""
    # Arrange
    # Act
    got, expected = _resolve_with_delegated_user_path(tmp_path, env)

    # Assert
    assert str(expected) in str(got)


# --------------------------------------------------------------------------- #
# Schema                                                                      #
# --------------------------------------------------------------------------- #
# FOUR TESTS WERE DELETED HERE and they are worth naming: `connect` set
# `journal_mode=wal`, `foreign_keys=ON`, `busy_timeout=300000` and
# `synchronous=NORMAL`, and each had a test. All four are settings of a
# single-writer local FILE -- a write-ahead log beside the database, a
# per-connection lock wait, an fsync policy, and an opt-in for referential
# integrity that the previous engine shipped OFF by default. The engine that
# ships has no PRAGMA at all, journals and fsyncs by server configuration, and
# enforces foreign keys unconditionally. There is nothing to restate: these
# asserted that four knobs were turned, and the knobs are gone with the engine
# that had them. Deferrability -- the FK property this package DOES still choose
# -- has its own file, `test__foreign_keys_are_deferrable.py`.


@pytest.fixture
def opened(new_store):
    """A store provisioned through the ordinary door."""
    conn = _db.open_db(new_store("cards_db_schema", bootstrap=False))
    yield conn
    conn.close()


def _index_names(conn) -> set[str]:
    """Every index in THIS store's schema.

    Scoped to `current_schema()` for the same reason `_schema_probe` is: the
    store is a schema on a shared server, so an unscoped catalogue read answers
    about the whole database and would report a neighbour's index as this
    store's.
    """
    rows = conn.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
    ).fetchall()
    return {row["indexname"] for row in rows}


def test_open_db_creates_all_tables(opened):
    # Arrange
    # Act
    present = table_names(opened)

    # Assert
    assert set(_db.SCHEMA_TABLES) <= present


def test_open_db_creates_expected_indexes(opened):
    # Arrange
    expected = {
        "idx_tasks_status",
        "idx_tasks_agent",
        "idx_tasks_assignee",
        "idx_tasks_scope",
        "idx_tasks_kind",
        "idx_tasks_blocker",
        "idx_tasks_project",
        "idx_tasks_deadline",
        "idx_tasks_parent",
        "idx_tasks_pr_url",
        "idx_comments_task",
        "idx_edges_dst",
        "idx_roles_who",
        "idx_notif_recipient_seen",
        "idx_messages_thread",
        "idx_user_names_uid",
    }

    # Act
    idx = _index_names(opened)

    # Assert
    assert expected <= idx


def test_the_stamp_matches_the_schema_constant(opened):
    """The stamp and the constant must agree — whatever the version IS.

    Was ``test_user_version_is_1``, hard-coding the literal 1 in two places. That is
    a test of a NUMBER, not of a property: the schema went to v2 (``card_json``, the
    S2 read payload) and this failed with ``assert 2 == 1`` — telling us only that
    the number changed, which we knew, and nothing about whether the DB is coherent.

    The property worth pinning is that the stamp and the constant do not DRIFT APART,
    because a store whose stamp disagrees with the code's ``SCHEMA_VERSION``
    is exactly the "metadata that outlived its artifact" this migration keeps
    tripping over.
    """
    # Arrange
    conn = opened

    # Act
    stamped = _stamped(conn)

    # Assert
    assert stamped == _db.SCHEMA_VERSION


def test_schema_version_constant_is_at_least_the_payload_revision():
    """v2 is the revision that added ``tasks.card_json`` — the S2 read payload."""
    # Arrange
    # Act
    version = _db.SCHEMA_VERSION

    # Assert
    assert version >= 2, "v2 added tasks.card_json (the S2 read payload)"


# --------------------------------------------------------------------------- #
# The v1 -> v2 migration is ADDITIVE, idempotent, and does NOT back-fill.      #
# --------------------------------------------------------------------------- #
# ``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing table, so a DB created
# before v2 would keep the old shape forever unless something ALTERs it. It does —
# but the existing rows keep ``card_json = NULL``, and those NULLs are LOAD-BEARING:
# they are what makes the S2 read guard refuse a DB that has not been re-imported,
# instead of quietly serving cards with their unknown fields stripped.
#
# THE V1 DB IS BUILT BY SUBTRACTION FROM THE SCHEMA TEXT — NOT BY HAND, AND NOT WITH
# ``DROP COLUMN``. Two earlier drafts got this wrong, in opposite directions, and both
# are worth remembering:
#
# 1. The first hand-rolled ``CREATE TABLE tasks (id, title, status)`` as its "v1 shape".
#    ``open_db`` then died on ``CREATE INDEX ... ON tasks(agent)`` — no v1 DB ever had
#    only three columns, so the test was failing the CODE for not surviving a database
#    that HAS NEVER EXISTED.
#
# 2. The second built it by ``ALTER TABLE tasks DROP COLUMN card_json``. That passed
#    locally (the retired engine 3.45.1) and FAILED IN CI with ``no such column: agent`` on the
#    reopen — the rewritten table came back missing columns. ``DROP COLUMN`` is a
#    table-rewrite whose behaviour varies across the retired engine versions, so the fixture was
#    testing the runner's the retired engine as much as our migration. A TEST FIXTURE MUST NOT BE
#    BUILT OUT OF A FEATURE WHOSE SEMANTICS VARY BY ENVIRONMENT — it turns a green
#    local run into a red CI run and sends you hunting through the wrong code.
#
# So: take the REAL schema text and delete the one line v2 added. Every other column,
# every index, exactly as v1 had them — and no dependency on any ALTER at all. The
# fixture is a v1 DB because it was BUILT AS ONE, deterministically, on every the retired engine.
def _v1_schema_sql() -> str:
    """Today's schema text, minus every column added after v1.

    Derived by DROPPING NAMED LINES rather than by matching a multi-line block
    that happened to end the ``tasks`` table. The old form matched
    ``row_order ...,\\n    card_json ...\\n`` — which silently stopped matching
    the moment v6 appended ``revision`` after ``card_json``. The replace became
    a no-op and the fixture quietly carried the very column it exists to omit,
    so a test named "the v1 fixture omits the v2 column" was the thing that
    caught it. A fixture coupled to which column happens to be LAST is a fixture
    that breaks on every future column.
    """
    kept = [
        line
        for line in _db._SCHEMA_SQL.splitlines(keepends=True)
        if not line.strip().startswith(("card_json", "revision"))
    ]
    sql = "".join(kept)
    # Whatever column now ends the tasks table must not keep a trailing comma.
    return sql.replace(
        "    row_order      INTEGER,\n);", "    row_order      INTEGER\n);"
    )


def _build_v1_db(new_store, prefix: str = "cards_v1") -> str:
    """A deterministic v1 store carrying one pre-v2 row. Returns the TARGET.

    NO VERSION STAMP IS WRITTEN, and the omission is the honest half. The stamp
    that used to be set here was the second one, which this engine does not
    carry; and it was never what placed the store anyway -- ``observed_version``
    walks the PHYSICAL ladder, whose floor is v5's DM tables. This fixture
    installs the fresh script alone, so it has none of them and reads as
    genuinely below the floor rather than as a current store wearing an old
    label. That distinction is the one `test__migration_provenance` records
    having got wrong once already.
    """
    target = new_store(prefix, bootstrap=False)
    conn = _db.connect(target)
    try:
        execute_ddl(conn, _v1_schema_sql())
        conn.execute(
            "INSERT INTO tasks(id, title, status) VALUES ('old-1', 'v1', 'goal')"
        )
        conn.commit()
    finally:
        conn.close()
    return target


def _v1_task_columns(new_store):
    """The `tasks` columns of the freshly built v1 fixture, BEFORE any migration."""
    conn = _db.connect(_build_v1_db(new_store, "cards_v1_cols"))
    try:
        return _db.table_columns(conn, "tasks")
    finally:
        conn.close()


def test_the_v1_fixture_sql_omits_the_v2_payload_column():
    # Arrange
    # Act
    v1_sql = _v1_schema_sql()

    # Assert
    assert "card_json" not in v1_sql, "the v1 fixture must not contain the v2 column"


def test_the_v1_fixture_sql_keeps_every_v1_index():
    # Arrange
    # Act
    v1_sql = _v1_schema_sql()

    # Assert — this is what caught draft 1's three-column hand-rolled fixture.
    assert "idx_tasks_agent" in v1_sql


def test_the_v1_fixture_db_starts_without_the_payload_column(new_store):
    # Arrange
    # Act
    columns = _v1_task_columns(new_store)

    # Assert
    assert "card_json" not in columns


def test_the_v1_fixture_db_still_has_every_v1_column(new_store):
    # Arrange
    # Act
    columns = _v1_task_columns(new_store)

    # Assert
    assert "agent" in columns, "a real v1 DB HAS agent"


def _open_migrated_v1_db(new_store, prefix: str):
    """Build a v1 store and open it through `open_db` — the migration runs there."""
    return _db.open_db(_build_v1_db(new_store, prefix))


def test_a_v1_db_gains_the_payload_column_on_open(new_store):
    """A TRANSITION: the column is absent before the open and present after.

    The end state alone is satisfied by any current store, which is every store
    this harness hands out except the one built above.
    """
    # Arrange
    target = _build_v1_db(new_store, "cards_v1_gain")
    probe = _db.connect(target)
    before = "card_json" in _db.table_columns(probe, "tasks")
    probe.close()

    # Act
    conn = _db.open_db(target)

    # Assert
    try:
        assert (before, "card_json" in _db.table_columns(conn, "tasks")) == (
            False,
            True,
        )
    finally:
        conn.close()


def test_a_v1_db_is_restamped_to_the_current_schema_version(new_store):
    # Arrange
    conn = _open_migrated_v1_db(new_store, "cards_v1_stamp")

    # Act
    try:
        stamped = _stamped(conn)
    finally:
        conn.close()

    # Assert
    assert stamped == _db.SCHEMA_VERSION


def test_the_migration_does_not_back_fill_pre_existing_rows(new_store):
    """The NULLs are LOAD-BEARING — they make the S2 read guard refuse the DB."""
    # Arrange
    conn = _open_migrated_v1_db(new_store, "cards_v1_backfill")

    # Act
    try:
        row = conn.execute("SELECT card_json FROM tasks WHERE id='old-1'").fetchone()
    finally:
        conn.close()

    # Assert
    assert row["card_json"] is None, (
        "the pre-existing row must NOT be silently back-filled"
    )


# --------------------------------------------------------------------------- #
# Bootstrap — the import summary counts every child collection                #
# --------------------------------------------------------------------------- #
def test_import_counts_every_task(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert
    assert summary["tasks"] == 3


def test_import_counts_every_comment(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert
    assert summary["comments"] == 2


def test_import_counts_every_edge(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert — c1 depends_on c2 + blocks c3.
    assert summary["edges"] == 2


def test_import_counts_every_role(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert — 1 collaborator + 2 subscribers.
    assert summary["roles"] == 3


def test_import_counts_every_user(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert
    assert summary["users"] == 2


def test_import_counts_every_user_name_alias(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert — alice(2 aliases) + bob(1).
    assert summary["user_names"] == 3


def test_import_counts_every_notification(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert
    assert summary["notifications"] == 2


def test_import_counts_every_message(imported):
    # Arrange
    # Act
    summary = imported["summary"]

    # Assert
    assert summary["messages"] == 1


# --------------------------------------------------------------------------- #
# Bootstrap — the imported `tasks` row carries every mapped field              #
# --------------------------------------------------------------------------- #
def _card_row(imported, card_id: str = "c1"):
    return (
        imported["conn"]
        .execute("SELECT * FROM tasks WHERE id=?", (card_id,))
        .fetchone()
    )


def test_import_remaps_the_group_field_to_the_grp_column(imported):
    # Arrange
    # Act
    row = _card_row(imported)

    # Assert — `group` is a SQL keyword, so the column is `grp`.
    assert row["grp"] == "core"


def test_import_stores_the_repo_field(imported):
    # Arrange
    # Act
    row = _card_row(imported)

    # Assert
    assert row["repo"] == "scitex-cards"


def test_import_stores_the_priority_field(imported):
    # Arrange
    # Act
    row = _card_row(imported)

    # Assert
    assert row["priority"] == 3


def test_import_serialises_the_deadlines_list_to_json(imported):
    # Arrange
    # Act
    row = _card_row(imported)

    # Assert
    assert '"2026-08-01"' in row["deadlines_json"]


def test_import_serialises_the_log_meta_mapping_to_json(imported):
    # Arrange
    # Act
    row = _card_row(imported)

    # Assert
    assert "completed_by" in row["log_meta_json"]


def test_import_records_the_cards_position_in_the_store(imported):
    # Arrange
    # Act
    row = _card_row(imported)

    # Assert — c1 is the first card in the yaml.
    assert row["row_order"] == 0


# --------------------------------------------------------------------------- #
# Bootstrap — child collections                                               #
# --------------------------------------------------------------------------- #
def test_import_populates_both_edge_directions(imported):
    # Arrange
    # Act
    edges = {
        (r["edge_type"], r["dst_task_id"])
        for r in imported["conn"].execute(
            "SELECT * FROM task_edges WHERE src_task_id='c1'"
        )
    }

    # Assert
    assert edges == {("depends_on", "c2"), ("blocks", "c3")}


def test_import_populates_collaborator_and_subscriber_roles(imported):
    # Arrange
    # Act
    roles = {
        (r["role"], r["who"])
        for r in imported["conn"].execute("SELECT * FROM task_roles WHERE task_id='c1'")
    }

    # Assert
    assert roles == {
        ("collaborator", "bob"),
        ("subscriber", "carol"),
        ("subscriber", "bob"),
    }


def _c1_comments(imported):
    return (
        imported["conn"]
        .execute("SELECT * FROM task_comments WHERE task_id='c1' ORDER BY seq")
        .fetchall()
    )


def test_import_numbers_comments_in_store_order(imported):
    # Arrange
    # Act
    comments = _c1_comments(imported)

    # Assert
    assert [c["seq"] for c in comments] == [0, 1]


def test_import_preserves_a_comments_kind_discriminator(imported):
    # Arrange
    # Act
    comments = _c1_comments(imported)

    # Assert
    assert comments[1]["kind"] == "unblock"


def _user_names(imported):
    return {
        r["name"]: r["user_id"]
        for r in imported["conn"].execute("SELECT * FROM user_names")
    }


def test_import_indexes_a_users_primary_name(imported):
    # Arrange
    # Act
    names = _user_names(imported)

    # Assert
    assert names["alice"] == "u_aaaaaaaaaaaa"


def test_import_indexes_every_user_alias(imported):
    # Arrange
    # Act
    names = _user_names(imported)

    # Assert
    assert names["proj-alice"] == "u_aaaaaaaaaaaa"


def _alice(imported):
    return (
        imported["conn"]
        .execute("SELECT * FROM users WHERE id='u_aaaaaaaaaaaa'")
        .fetchone()
    )


def test_import_stores_a_users_a2a_port(imported):
    # Arrange
    # Act
    alice = _alice(imported)

    # Assert
    assert alice["a2a_port"] == 7001


def test_import_serialises_a_users_notify_prefs_to_json(imported):
    # Arrange
    # Act
    alice = _alice(imported)

    # Assert
    assert "telegram" in alice["notify_json"]


def _notification_for(imported, recipient: str):
    return (
        imported["conn"]
        .execute("SELECT * FROM notifications WHERE recipient_id=?", (recipient,))
        .fetchone()
    )


def test_import_keeps_an_unseen_notification_unseen(imported):
    # Arrange
    # Act
    notif = _notification_for(imported, "u_aaaaaaaaaaaa")

    # Assert
    assert notif["seen"] == 0


def test_import_stores_a_notifications_event_type(imported):
    # Arrange
    # Act
    notif = _notification_for(imported, "u_aaaaaaaaaaaa")

    # Assert
    assert notif["event_type"] == "reassigned"


def test_import_keeps_a_seen_notification_seen(imported):
    # Arrange
    # Act
    notif = _notification_for(imported, "dave")

    # Assert
    assert notif["seen"] == 1


def _only_message(imported):
    return imported["conn"].execute("SELECT * FROM messages").fetchone()


def test_import_stores_a_messages_thread_key(imported):
    # Arrange
    # Act
    msg = _only_message(imported)

    # Assert
    assert msg["thread_key"] == "dm:alice::bob"


def test_import_stores_a_messages_sender(imported):
    # Arrange
    # Act
    msg = _only_message(imported)

    # Assert
    assert msg["sender"] == "alice"


def test_import_stores_a_messages_recipient(imported):
    # Arrange
    # Act
    msg = _only_message(imported)

    # Assert
    assert msg["recipient"] == "bob"


def test_import_keeps_an_unread_message_unread(imported):
    # Arrange
    # Act
    msg = _only_message(imported)

    # Assert
    assert msg["read"] == 0


# --------------------------------------------------------------------------- #
# Bootstrap — idempotency                                                     #
# --------------------------------------------------------------------------- #
def _import_twice(store):
    """Seed the same doc twice; return both summaries.

    ``_rebuild_from_doc`` DELETEs every table before it inserts, so re-seeding is
    idempotent by construction — the same property the twice-run YAML import
    proved.
    """
    first = seed_db_from_doc(
        store["tasks_doc"], store["target"], threads=store["threads"]
    )
    second = seed_db_from_doc(
        store["tasks_doc"], store["target"], threads=store["threads"]
    )
    return first, second


def _count(store, table: str) -> int:
    conn = _db.connect(store["target"])
    try:
        # AN EXPLICIT ALIAS, because a COUNT has no column name of its own and
        # this driver's rows are addressed BY NAME. `fetchone()[0]` raised
        # `KeyError: 0` here.
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
    finally:
        conn.close()


def test_a_second_import_reports_the_same_per_table_counts(store):
    # Arrange
    # Act
    first, second = _import_twice(store)

    # Assert
    assert all(
        first[key] == second[key]
        for key in (
            "tasks",
            "comments",
            "edges",
            "roles",
            "users",
            "user_names",
            "notifications",
            "messages",
        )
    ), (first, second)


def test_a_second_import_does_not_multiply_task_rows(store):
    # Arrange
    _import_twice(store)

    # Act
    rows = _count(store, "tasks")

    # Assert
    assert rows == 3


def test_a_second_import_does_not_multiply_comment_rows(store):
    # Arrange
    _import_twice(store)

    # Act
    rows = _count(store, "task_comments")

    # Assert
    assert rows == 2


def test_a_second_import_does_not_multiply_edge_rows(store):
    # Arrange
    _import_twice(store)

    # Act
    rows = _count(store, "task_edges")

    # Assert
    assert rows == 2


def test_a_second_import_does_not_multiply_notification_rows(store):
    # Arrange
    _import_twice(store)

    # Act
    rows = _count(store, "notifications")

    # Assert
    assert rows == 2


# --------------------------------------------------------------------------- #
# verify()                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def verified(store):
    """The report `verify` gives for a freshly seeded store."""
    seed_db_from_doc(store["tasks_doc"], store["target"], threads=store["threads"])
    return _db.verify(store["target"])


def test_verify_reports_ok_after_import(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["ok"] is True


def test_verify_reports_the_observed_version(verified):
    """The ARTIFACT half of the report, and it replaces the second STAMP.

    This asserted ``report["user_version"]`` -- the other stamp, which this
    engine does not carry (``_read_stamps`` returns None for it by design). Two
    stamps agreeing with each other was never the interesting question anyway;
    the module's own docstring says what is: does the stamp agree with the
    SHAPE. ``observed_version`` walks the physical ladder, so that is what the
    field holds now.
    """
    # Arrange
    # Act
    report = verified

    # Assert — against the CONSTANT, not a literal: the schema keeps moving.
    assert report["observed_version"] == _db.SCHEMA_VERSION


def test_verify_reports_the_schema_version_as_a_string(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["schema_version"] == str(_db.SCHEMA_VERSION)


# `test_verify_runs_sqlites_own_integrity_quick_check` WAS DELETED HERE. It
# asserted `report["quick_check"] == "ok"`, i.e. that the verb had run the
# previous engine's page-level corruption scan over a local file. The shipping
# engine checksums its own pages and exposes no client-callable equivalent, so
# there is nothing to restate and nothing was invented in its place. `ok` did
# not get weaker for losing the term: the `observed_version` test above is new,
# and it makes `ok` require the stamp and the physical SHAPE to agree, which is
# strictly more than the two stamps agreeing with each other.


def test_verify_reports_no_error_on_a_healthy_store(verified):
    """The report must not be carrying a swallowed failure while saying ok."""
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["error"] is None


def test_verify_records_the_db_provenance_source(verified):
    """verify() surfaces the ``schema_meta`` 'source' stamp.

    The old 'yaml-import' provenance is gone with the import path; the seed
    helper stamps 'test-seed'. What still matters — and what this pins — is that
    verify reports whatever source the DB was actually stamped with, not a
    hard-coded literal.
    """
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["source"] == "test-seed"


def test_verify_counts_the_rows_of_each_table(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["tables"]["tasks"] == 3


def test_verify_reports_a_row_count_for_every_schema_table(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert set(report["tables"]) == set(_db.SCHEMA_TABLES)


def test_verify_reports_an_unprovisioned_store_as_not_existing(new_store):
    """"Absent" is now "there is no store there", not "there is no file there"."""
    # Arrange
    target = new_store("cards_verify_empty", bootstrap=False)

    # Act
    report = _db.verify(target)

    # Assert
    assert report["exists"] is False


def test_verify_reports_an_unprovisioned_store_as_not_ok(new_store):
    # Arrange
    target = new_store("cards_verify_empty2", bootstrap=False)

    # Act
    report = _db.verify(target)

    # Assert
    assert report["ok"] is False


def test_verify_says_why_an_unprovisioned_store_is_not_a_store(new_store):
    """The CLI printed "run init-store" for every failure on the way in.

    An unconfigured target, a refused one and an unreachable server all rendered
    as the same instruction, and it is the right one for only the first.
    """
    # Arrange
    target = new_store("cards_verify_empty3", bootstrap=False)

    # Act
    report = _db.verify(target)

    # Assert
    assert "schema_meta" in str(report["error"])


def test_verify_reports_a_refused_target_rather_than_raising(tmp_path):
    """A health verb that dies on a bad target tells the operator less.

    A filename is REFUSED by the door now, and `verify` used to reach that door
    through `resolve_db_path`, which raises on a DSN -- so this verb could not
    survive its own first statement against the store it reports on.
    """
    # Arrange
    target = str(tmp_path / "nope.db")

    # Act
    report = _db.verify(target)

    # Assert
    assert report["exists"] is False


# --------------------------------------------------------------------------- #
# repo-field round-trip (dataclass + DB column)                              #
# --------------------------------------------------------------------------- #
def test_repo_field_survives_from_dict_on_the_dataclass():
    # Arrange
    # Act
    task = _model.Task.from_dict({"id": "r1", "title": "t", "repo": "scitex-cards"})

    # Assert
    assert task.repo == "scitex-cards"


def test_repo_field_survives_to_dict_on_the_dataclass():
    # Arrange
    task = _model.Task.from_dict({"id": "r1", "title": "t", "repo": "scitex-cards"})

    # Act
    payload = task.to_dict()

    # Assert
    assert payload["repo"] == "scitex-cards"


def test_an_absent_repo_field_defaults_to_none():
    # Arrange
    # Act
    task = _model.Task.from_dict({"id": "r2", "title": "t"})

    # Assert
    assert task.repo is None


def test_an_absent_repo_field_stays_out_of_the_serialised_card():
    # Arrange — absent repo stays omitted so YAML stays compact.
    task = _model.Task.from_dict({"id": "r2", "title": "t"})

    # Act
    payload = task.to_dict()

    # Assert
    assert "repo" not in payload


def test_repo_field_round_trips_db_column(imported):
    # Arrange
    # Act
    row = imported["conn"].execute("SELECT repo FROM tasks WHERE id='c1'").fetchone()

    # Assert
    assert row["repo"] == "scitex-cards"


# --------------------------------------------------------------------------- #
# The upsert path must UPDATE, not DELETE-and-INSERT                          #
# --------------------------------------------------------------------------- #
# THESE TESTS USED TO READ THE SQL TEXT, through `the retired driver`'s trace callback, and
# asserted that the rebuild's `INTO tasks` statements carried no `OR REPLACE`.
# The measurement behind them stands: `INSERT OR REPLACE INTO tasks` cost
# 4,592 us/row against 110 us/row for a plain INSERT -- 42x, 6.3 s of the
# rebuild's 7.3 s on the live 1,370-card store -- because `tasks` is a parent
# with ON DELETE CASCADE children and REPLACE is a DELETE plus an INSERT.
#
# THE ASSERTION IS BEHAVIOURAL NOW, AND THAT IS AN UPGRADE RATHER THAN A
# WORKAROUND. There is no trace callback on this driver, but there is a better
# question to ask, and `_db_bootstrap` states it in its own first reason for the
# change: REPLACE fires the DELETE and INSERT triggers and NOT the AFTER UPDATE
# ones, so v7's `tasks_bump_revision` -- the optimistic lock -- was INERT for
# every upsert taking that path. A lock nobody fires is not a lock. So the
# upsert is now measured by whether the REVISION MOVES, which a DELETE+INSERT
# structurally cannot do and which no reading of the SQL string could have told
# us.
def _seeded(store):
    """The store, rebuilt from the doc through the real primitive."""
    conn = _db.connect(store["target"])
    _db.init_schema(conn)
    _db_bootstrap._rebuild_from_doc(conn, store["tasks_doc"])
    conn.commit()
    return conn


def test_the_rebuild_actually_inserts_into_tasks(store):
    # Arrange
    conn = _seeded(store)

    # Act
    rows = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()

    # Assert — guards the act itself; a rebuild that inserted nothing would make
    # every assertion below vacuous.
    try:
        assert rows["n"] == 3
    finally:
        conn.close()


def test_the_rebuild_leaves_every_card_at_revision_zero(store):
    """The rebuild DELETES first, so each row is new and has been written once.

    A revision above zero here would mean the rebuild had gone through the
    upsert path -- the one whose per-row cascade cost 42x.
    """
    # Arrange
    conn = _seeded(store)

    # Act
    rows = conn.execute("SELECT revision FROM tasks ORDER BY id").fetchall()

    # Assert
    try:
        assert [row["revision"] for row in rows] == [0, 0, 0]
    finally:
        conn.close()


# ...and the DEFAULT must stay an UPSERT, because the incremental mirror rewrites
# a changed card WITHOUT dropping its `tasks` row first. A plain INSERT there
# would violate the primary key on every card update.
def test_insert_tasks_defaults_to_upsert_over_a_live_row(store):
    # Arrange
    conn = _db.connect(store["target"])
    _db.init_schema(conn)
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v1", "status": "card"}])

    # Act — same id again, row still present: the incremental mirror's shape.
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v2", "status": "done"}])
    conn.commit()
    rows = conn.execute("SELECT id, title, status FROM tasks WHERE id='c9'").fetchall()
    conn.close()

    # Assert
    assert [(r["id"], r["title"], r["status"]) for r in rows] == [
        ("c9", "v2", "done")
    ]


def test_the_upsert_fires_the_revision_trigger(store):
    """THE PROPERTY `OR REPLACE` COULD NOT HAVE. A DELETE+INSERT fires the DELETE
    and INSERT triggers and not the AFTER UPDATE one, so the optimistic lock was
    inert on this path for as long as the clause was REPLACE. A true UPDATE
    bumps it, and this is the assertion that can tell the two apart.
    """
    # Arrange
    conn = _db.connect(store["target"])
    _db.init_schema(conn)
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v1", "status": "card"}])
    conn.commit()
    before = conn.execute("SELECT revision FROM tasks WHERE id='c9'").fetchone()

    # Act
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v2", "status": "done"}])
    conn.commit()

    # Assert
    try:
        after = conn.execute("SELECT revision FROM tasks WHERE id='c9'").fetchone()
        assert (before["revision"], after["revision"]) == (0, 1)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# ...and a plain INSERT must still tolerate a duplicate id, LAST-WINS          #
# --------------------------------------------------------------------------- #
# Last-wins is exactly what `OR REPLACE` used to give us; it is now done in
# Python (`_dedupe_last_wins`) so the SQL can stay plain. A duplicate id is a
# real data bug, so it is also logged LOUD rather than silently absorbed.
def _import_a_store_with_a_duplicate_id(new_store, caplog) -> dict:
    """Seed a doc carrying the same card id twice; return the observable state."""
    doc = {
        "tasks": [
            {
                "id": "dup",
                "title": "FIRST",
                "status": "card",
                "comments": [{"author": "a", "ts": "t", "text": "old"}],
            },
            {"id": "keep", "title": "Untouched", "status": "done"},
            {
                "id": "dup",
                "title": "SECOND",
                "status": "done",
                "comments": [{"author": "b", "ts": "t", "text": "new"}],
            },
        ]
    }
    target = new_store("cards_dupe", bootstrap=False)

    with caplog.at_level("ERROR"):
        summary = seed_db_from_doc(doc, target)

    conn = _db.connect(target)
    try:
        rows = [
            (r["id"], r["title"], r["status"])
            for r in conn.execute(
                "SELECT id, title, status FROM tasks ORDER BY id"
            ).fetchall()
        ]
        comments = [
            (r["task_id"], r["text"])
            for r in conn.execute(
                "SELECT task_id, text FROM task_comments ORDER BY task_id"
            ).fetchall()
        ]
    finally:
        conn.close()
    return {"summary": summary, "rows": rows, "comments": comments}


def test_a_duplicate_card_id_collapses_to_the_last_occurrence(new_store, caplog):
    # Arrange
    # Act
    state = _import_a_store_with_a_duplicate_id(new_store, caplog)

    # Assert — the duplicate collapses to ONE row, the LAST one, without raising.
    assert state["rows"] == [
        ("dup", "SECOND", "done"),
        ("keep", "Untouched", "done"),
    ]


def test_a_duplicate_card_id_is_counted_once_in_the_summary(new_store, caplog):
    # Arrange
    # Act
    state = _import_a_store_with_a_duplicate_id(new_store, caplog)

    # Assert
    assert state["summary"]["tasks"] == 2


def test_only_the_winning_duplicates_comments_survive(new_store, caplog):
    # Arrange
    # Act
    state = _import_a_store_with_a_duplicate_id(new_store, caplog)

    # Assert — `OR REPLACE` used to append BOTH cards' comments.
    assert state["comments"] == [("dup", "new")]


def test_a_duplicate_card_id_is_logged_with_the_offending_id(new_store, caplog):
    # Arrange
    # Act
    _import_a_store_with_a_duplicate_id(new_store, caplog)

    # Assert — the data bug is surfaced, not swallowed.
    assert "dup" in caplog.text


def test_a_duplicate_card_id_is_logged_at_error_level_by_name(new_store, caplog):
    # Arrange
    # Act
    _import_a_store_with_a_duplicate_id(new_store, caplog)

    # Assert
    assert "DUPLICATE CARD ID" in caplog.text


# EOF
