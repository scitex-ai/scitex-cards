#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the S0 shadow-SQLite adapter + YAML bootstrap (RFC #348).

Real ``sqlite3`` + real ``tmp_path`` YAML fixtures — NO mocks. Every test
proves the S0 contract: the DB is a shadow bootstrapped from YAML, the schema
matches the RFC, and the import NEVER modifies the source YAML.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from scitex_cards import _db, _db_bootstrap, _model


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    """A tasks.yaml + threads.yaml pair exercising every child collection."""
    tasks_doc = {
        "tasks": [
            {
                "id": "c1",
                "title": "First card",
                "status": "in_progress",
                "task": "do the thing",
                "project": "scitex-todo",
                "repo": "scitex-todo",
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
    tasks_path = tmp_path / "tasks.yaml"
    threads_path = tmp_path / "threads.yaml"
    _write_yaml(tasks_path, tasks_doc)
    _write_yaml(threads_path, threads_doc)
    return {
        "tasks_path": tasks_path,
        "threads_path": threads_path,
        "db_path": tmp_path / "todo.db",
    }


@pytest.fixture
def imported(store):
    """The `store` fixture imported into SQLite: summary + a live row-factory conn."""
    summary = _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    conn = sqlite3.connect(str(store["db_path"]))
    conn.row_factory = sqlite3.Row
    try:
        yield {"summary": summary, "conn": conn, "store": store}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #
def test_resolve_db_path_explicit_wins(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setenv(_db.ENV_DB, str(tmp_path / "env.db"))

    # Act
    got = _db.resolve_db_path(tmp_path / "explicit.db")

    # Assert
    assert got == (tmp_path / "explicit.db")


def test_resolve_db_path_env_over_userpath(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setenv(_db.ENV_DB, str(tmp_path / "env.db"))

    # Act
    got = _db.resolve_db_path()

    # Assert
    assert got == (tmp_path / "env.db")


def _resolve_with_delegated_user_path(tmp_path, monkeypatch):
    """Neutralise both env tiers and record how `local_state.user_path` is called.

    Returns ``(resolved, calls, sentinel)``.
    """
    monkeypatch.delenv(_db.ENV_DB, raising=False)
    monkeypatch.delenv(_db.ENV_DB_DEPRECATED, raising=False)
    from scitex_config._ecosystem import local_state

    calls = []
    sentinel = tmp_path / "delegated" / "cards.db"

    def fake_user_path(pkg_short, *parts):
        calls.append((pkg_short, parts))
        return sentinel

    monkeypatch.setattr(local_state, "user_path", fake_user_path)
    return _db.resolve_db_path(), calls, sentinel


def test_resolve_db_path_returns_the_delegated_user_path(tmp_path, monkeypatch):
    """Final tier DELEGATES to local_state.user_path — no re-rolled precedence."""
    # Arrange
    # Act
    got, _calls, sentinel = _resolve_with_delegated_user_path(tmp_path, monkeypatch)

    # Assert
    assert got == sentinel


def test_resolve_db_path_delegates_with_the_cards_package_key(tmp_path, monkeypatch):
    """The delegation passes the package short-name and the db filename."""
    # Arrange
    # Act
    _got, calls, _sentinel = _resolve_with_delegated_user_path(tmp_path, monkeypatch)

    # Assert
    assert calls == [("cards", ("cards.db",))]


def _resolve_from_legacy_env_only(tmp_path, monkeypatch, caplog):
    """Set ONLY the pre-rename env name and resolve, capturing warnings."""
    monkeypatch.delenv(_db.ENV_DB, raising=False)
    monkeypatch.setenv(_db.ENV_DB_DEPRECATED, str(tmp_path / "legacy.db"))
    with caplog.at_level("WARNING", logger="scitex_cards._db"):
        return _db.resolve_db_path()


def test_resolve_db_path_still_honours_the_legacy_env_name(
    tmp_path, monkeypatch, caplog
):
    """SCITEX_TODO_DB (pre-rename) still resolves when it is the only export."""
    # Arrange
    # Act
    got = _resolve_from_legacy_env_only(tmp_path, monkeypatch, caplog)

    # Assert
    assert got == (tmp_path / "legacy.db")


def test_resolve_db_path_warns_that_the_legacy_env_name_is_deprecated(
    tmp_path, monkeypatch, caplog
):
    """...and it resolves LOUDLY, so the export gets migrated."""
    # Arrange
    # Act
    _resolve_from_legacy_env_only(tmp_path, monkeypatch, caplog)

    # Assert
    assert any("deprecated" in r.message for r in caplog.records)


def test_resolve_db_path_new_env_wins_over_legacy(tmp_path, monkeypatch):
    """When both names are set, SCITEX_CARDS_DB wins."""
    # Arrange
    monkeypatch.setenv(_db.ENV_DB, str(tmp_path / "new.db"))
    monkeypatch.setenv(_db.ENV_DB_DEPRECATED, str(tmp_path / "legacy.db"))

    # Act
    got = _db.resolve_db_path()

    # Assert
    assert got == (tmp_path / "new.db")


# --------------------------------------------------------------------------- #
# Schema + PRAGMAs                                                            #
# --------------------------------------------------------------------------- #
def _pragma(tmp_path, name: str):
    """Open a fresh connection and read back one PRAGMA."""
    conn = _db.connect(tmp_path / "p.db")
    try:
        return conn.execute(f"PRAGMA {name}").fetchone()[0]
    finally:
        conn.close()


def test_connect_sets_journal_mode_to_wal(tmp_path):
    # Arrange
    # Act
    mode = _pragma(tmp_path, "journal_mode")

    # Assert
    assert str(mode).lower() == "wal"


def test_connect_enables_foreign_keys(tmp_path):
    # Arrange
    # Act
    enabled = _pragma(tmp_path, "foreign_keys")

    # Assert
    assert enabled == 1


def test_connect_sets_a_generous_busy_timeout(tmp_path):
    # Arrange
    # Act
    timeout = _pragma(tmp_path, "busy_timeout")

    # Assert
    assert timeout == 300000


def test_connect_sets_synchronous_to_normal(tmp_path):
    # Arrange
    # Act
    synchronous = _pragma(tmp_path, "synchronous")

    # Assert
    assert synchronous == 1  # NORMAL


def _schema_objects(tmp_path, object_type: str) -> set[str]:
    """Open a fresh DB and return the names of every table/index it created."""
    conn = _db.open_db(tmp_path / "s.db")
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type=?", (object_type,)
            )
        }
    finally:
        conn.close()


def test_open_db_creates_all_tables(tmp_path):
    # Arrange
    # Act
    present = _schema_objects(tmp_path, "table")

    # Assert
    assert set(_db.SCHEMA_TABLES) <= present


def test_open_db_creates_expected_indexes(tmp_path):
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
    idx = _schema_objects(tmp_path, "index")

    # Assert
    assert expected <= idx


def test_user_version_matches_the_schema_constant(tmp_path):
    """The PRAGMA stamp and the constant must agree — whatever the version IS.

    Was ``test_user_version_is_1``, hard-coding the literal 1 in two places. That is
    a test of a NUMBER, not of a property: the schema went to v2 (``card_json``, the
    S2 read payload) and this failed with ``assert 2 == 1`` — telling us only that
    the number changed, which we knew, and nothing about whether the DB is coherent.

    The property worth pinning is that the stamp and the constant do not DRIFT APART,
    because a DB whose ``user_version`` disagrees with the code's ``SCHEMA_VERSION``
    is exactly the "metadata that outlived its artifact" this migration keeps
    tripping over.
    """
    # Arrange
    conn = _db.open_db(tmp_path / "s.db")

    # Act
    try:
        stamped = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

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
#    locally (SQLite 3.45.1) and FAILED IN CI with ``no such column: agent`` on the
#    reopen — the rewritten table came back missing columns. ``DROP COLUMN`` is a
#    table-rewrite whose behaviour varies across SQLite versions, so the fixture was
#    testing the runner's SQLite as much as our migration. A TEST FIXTURE MUST NOT BE
#    BUILT OUT OF A FEATURE WHOSE SEMANTICS VARY BY ENVIRONMENT — it turns a green
#    local run into a red CI run and sends you hunting through the wrong code.
#
# So: take the REAL schema text and delete the one line v2 added. Every other column,
# every index, exactly as v1 had them — and no dependency on any ALTER at all. The
# fixture is a v1 DB because it was BUILT AS ONE, deterministically, on every SQLite.
def _v1_schema_sql() -> str:
    """Today's schema text, minus the single column v2 introduced."""
    return _db._SCHEMA_SQL.replace(
        "    row_order      INTEGER,\n    card_json      TEXT\n",
        "    row_order      INTEGER\n",
    )


def _build_v1_db(tmp_path: Path) -> Path:
    """A deterministic v1 DB carrying one pre-v2 row."""
    db = tmp_path / "v1.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_v1_schema_sql())
        conn.execute("PRAGMA user_version=1")
        conn.execute(
            "INSERT INTO tasks(id, title, status) VALUES ('old-1', 'v1', 'goal')"
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _v1_task_columns(tmp_path: Path):
    """The `tasks` columns of the freshly built v1 fixture, BEFORE any migration."""
    conn = sqlite3.connect(str(_build_v1_db(tmp_path)))
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


def test_the_v1_fixture_db_starts_without_the_payload_column(tmp_path):
    # Arrange
    # Act
    columns = _v1_task_columns(tmp_path)

    # Assert
    assert "card_json" not in columns


def test_the_v1_fixture_db_still_has_every_v1_column(tmp_path):
    # Arrange
    # Act
    columns = _v1_task_columns(tmp_path)

    # Assert
    assert "agent" in columns, "a real v1 DB HAS agent"


def _open_migrated_v1_db(tmp_path: Path):
    """Build a v1 DB and open it through `open_db` — the migration runs there."""
    return _db.open_db(_build_v1_db(tmp_path))


def test_a_v1_db_gains_the_payload_column_on_open(tmp_path):
    # Arrange
    conn = _open_migrated_v1_db(tmp_path)

    # Act
    try:
        columns = _db.table_columns(conn, "tasks")
    finally:
        conn.close()

    # Assert
    assert "card_json" in columns


def test_a_v1_db_is_restamped_to_the_current_schema_version(tmp_path):
    # Arrange
    conn = _open_migrated_v1_db(tmp_path)

    # Act
    try:
        stamped = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    # Assert
    assert stamped == _db.SCHEMA_VERSION


def test_the_migration_does_not_back_fill_pre_existing_rows(tmp_path):
    """The NULLs are LOAD-BEARING — they make the S2 read guard refuse the DB."""
    # Arrange
    conn = _open_migrated_v1_db(tmp_path)

    # Act
    try:
        row = conn.execute("SELECT card_json FROM tasks WHERE id='old-1'").fetchone()
    finally:
        conn.close()

    # Assert
    assert row[0] is None, "the pre-existing row must NOT be silently back-filled"


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
    assert row["repo"] == "scitex-todo"


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
    """Import the same yaml twice; return both summaries."""
    first = _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    second = _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    return first, second


def _count(store, table: str) -> int:
    conn = sqlite3.connect(str(store["db_path"]))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
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
    """The report `verify` gives for a freshly imported store."""
    _db_bootstrap.import_from_yaml(
        tasks_path=store["tasks_path"], db_path=store["db_path"]
    )
    return _db.verify(store["db_path"])


def test_verify_reports_ok_after_import(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["ok"] is True


def test_verify_reports_the_stamped_user_version(verified):
    # Arrange
    # Act
    report = verified

    # Assert — against the CONSTANT, not a literal: the schema is at v2 now
    # (card_json) and will move again. What matters is that the two stamps
    # agree with the code.
    assert report["user_version"] == _db.SCHEMA_VERSION


def test_verify_reports_the_schema_version_as_a_string(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["schema_version"] == str(_db.SCHEMA_VERSION)


def test_verify_runs_sqlites_own_integrity_quick_check(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["quick_check"] == "ok"


def test_verify_records_that_the_db_came_from_a_yaml_import(verified):
    # Arrange
    # Act
    report = verified

    # Assert
    assert report["source"] == "yaml-import"


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


def test_verify_reports_an_absent_db_as_not_existing(tmp_path):
    # Arrange
    # Act
    report = _db.verify(tmp_path / "nope.db")

    # Assert
    assert report["exists"] is False


def test_verify_reports_an_absent_db_as_not_ok(tmp_path):
    # Arrange
    # Act
    report = _db.verify(tmp_path / "nope.db")

    # Assert
    assert report["ok"] is False


# --------------------------------------------------------------------------- #
# repo-field round-trip (dataclass + DB column)                              #
# --------------------------------------------------------------------------- #
def test_repo_field_survives_from_dict_on_the_dataclass():
    # Arrange
    # Act
    task = _model.Task.from_dict({"id": "r1", "title": "t", "repo": "scitex-todo"})

    # Assert
    assert task.repo == "scitex-todo"


def test_repo_field_survives_to_dict_on_the_dataclass():
    # Arrange
    task = _model.Task.from_dict({"id": "r1", "title": "t", "repo": "scitex-todo"})

    # Act
    payload = task.to_dict()

    # Assert
    assert payload["repo"] == "scitex-todo"


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
    val = imported["conn"].execute("SELECT repo FROM tasks WHERE id='c1'").fetchone()[0]

    # Assert
    assert val == "scitex-todo"


# --------------------------------------------------------------------------- #
# SAFETY: import never modifies the source YAML                              #
# --------------------------------------------------------------------------- #
def test_import_does_not_modify_the_source_tasks_yaml(store):
    # Arrange
    before = store["tasks_path"].read_bytes()

    # Act — twice, to be extra sure a second pass also leaves YAML untouched.
    _import_twice(store)

    # Assert
    assert store["tasks_path"].read_bytes() == before


def test_import_does_not_modify_the_source_threads_yaml(store):
    # Arrange
    before = store["threads_path"].read_bytes()

    # Act — twice, to be extra sure a second pass also leaves YAML untouched.
    _import_twice(store)

    # Assert
    assert store["threads_path"].read_bytes() == before


# --------------------------------------------------------------------------- #
# PERF REGRESSION: the REBUILD must not pay for `OR REPLACE`                  #
# --------------------------------------------------------------------------- #
# `INSERT OR REPLACE INTO tasks` cost 4,592 us/row against 110 us/row for a plain
# INSERT — 42x, and 6.3 s of the rebuild's 7.3 s (live store, 1,370 cards).
# `tasks` is a parent with ON DELETE CASCADE children, so under
# `PRAGMA foreign_keys=ON` a REPLACE runs the full cascade/FK machinery on every
# row — to resolve a conflict `_rebuild_from_doc` has already made IMPOSSIBLE by
# deleting every row first. Asserts the MECHANISM, not a wall-clock number, so it
# cannot flake on a busy box.
def _sql_trace(conn, fn):
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        conn.set_trace_callback(None)
    return seen


def _traced_rebuild_inserts(store) -> list[str]:
    """Run `_rebuild_from_doc` under a SQL trace; return the `INTO tasks` statements."""
    doc = _model.load_doc(store["tasks_path"], validate=False)
    conn = _db.connect(store["db_path"])
    _db.init_schema(conn)

    def _go():
        conn.execute("BEGIN IMMEDIATE")
        _db_bootstrap._rebuild_from_doc(conn, doc)
        conn.commit()

    seen = _sql_trace(conn, _go)
    conn.close()
    return [s for s in seen if "INTO tasks" in s]


def test_the_rebuild_actually_inserts_into_tasks(store):
    # Arrange
    # Act
    into_tasks = _traced_rebuild_inserts(store)

    # Assert — guards the trace itself; an empty trace would pass the next test
    # vacuously.
    assert into_tasks, "expected the rebuild to insert into `tasks`"


def test_rebuild_inserts_tasks_without_or_replace(store):
    # Arrange
    # Act
    into_tasks = _traced_rebuild_inserts(store)
    offenders = [s for s in into_tasks if "OR REPLACE" in s.upper()]

    # Assert
    assert not offenders, (
        "the rebuild must use a plain INSERT — it deletes every row first, so "
        "`INSERT OR REPLACE` only buys per-row FK cascade checks (42x slower). "
        f"Offending SQL: {offenders[0]!r}"
    )


# ...but the DEFAULT must stay REPLACE, because the incremental mirror upserts a
# changed card WITHOUT dropping its `tasks` row first. A plain INSERT there would
# raise `UNIQUE constraint failed: tasks.id` on every card update.
def test_insert_tasks_defaults_to_upsert_over_a_live_row(store):
    # Arrange
    conn = _db.connect(store["db_path"])
    _db.init_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v1", "status": "todo"}])

    # Act — same id again, row still present: the incremental mirror's shape.
    _db_bootstrap._insert_tasks(conn, [{"id": "c9", "title": "v2", "status": "done"}])
    conn.commit()
    rows = conn.execute("SELECT id, title, status FROM tasks WHERE id='c9'").fetchall()
    conn.close()

    # Assert
    assert [tuple(r) for r in rows] == [("c9", "v2", "done")]


# --------------------------------------------------------------------------- #
# ...and a plain INSERT must still tolerate a duplicate id, LAST-WINS          #
# --------------------------------------------------------------------------- #
# Last-wins is exactly what `OR REPLACE` used to give us; it is now done in
# Python (`_dedupe_last_wins`) so the SQL can stay plain. A duplicate id is a
# real data bug, so it is also logged LOUD rather than silently absorbed.
def _import_a_store_with_a_duplicate_id(tmp_path, caplog) -> dict:
    """Import a yaml carrying the same card id twice; return the observable state."""
    tasks_path = tmp_path / "tasks.yaml"
    _write_yaml(
        tasks_path,
        {
            "tasks": [
                {
                    "id": "dup",
                    "title": "FIRST",
                    "status": "todo",
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
        },
    )
    db_path = tmp_path / "todo.db"

    with caplog.at_level("ERROR"):
        summary = _db_bootstrap.import_from_yaml(tasks_path=tasks_path, db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT id, title, status FROM tasks ORDER BY id"
        ).fetchall()
        comments = conn.execute(
            "SELECT task_id, text FROM task_comments ORDER BY task_id"
        ).fetchall()
    finally:
        conn.close()
    return {"summary": summary, "rows": rows, "comments": comments}


def test_a_duplicate_card_id_collapses_to_the_last_occurrence(tmp_path, caplog):
    # Arrange
    # Act
    state = _import_a_store_with_a_duplicate_id(tmp_path, caplog)

    # Assert — the duplicate collapses to ONE row, the LAST one, without raising.
    assert state["rows"] == [
        ("dup", "SECOND", "done"),
        ("keep", "Untouched", "done"),
    ]


def test_a_duplicate_card_id_is_counted_once_in_the_summary(tmp_path, caplog):
    # Arrange
    # Act
    state = _import_a_store_with_a_duplicate_id(tmp_path, caplog)

    # Assert
    assert state["summary"]["tasks"] == 2


def test_only_the_winning_duplicates_comments_survive(tmp_path, caplog):
    # Arrange
    # Act
    state = _import_a_store_with_a_duplicate_id(tmp_path, caplog)

    # Assert — `OR REPLACE` used to append BOTH cards' comments.
    assert state["comments"] == [("dup", "new")]


def test_a_duplicate_card_id_is_logged_with_the_offending_id(tmp_path, caplog):
    # Arrange
    # Act
    _import_a_store_with_a_duplicate_id(tmp_path, caplog)

    # Assert — the data bug is surfaced, not swallowed.
    assert "dup" in caplog.text


def test_a_duplicate_card_id_is_logged_at_error_level_by_name(tmp_path, caplog):
    # Arrange
    # Act
    _import_a_store_with_a_duplicate_id(tmp_path, caplog)

    # Assert
    assert "DUPLICATE CARD ID" in caplog.text


# EOF
