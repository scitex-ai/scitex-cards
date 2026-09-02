#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The one-card comment path, on the engine it ships on.

Production is PostgreSQL and the SQLite suite cannot see a psycopg-only fault
(measured 2026-08-23: a positional row read passed ~7,500 SQLite tests and
broke every comment on the primary). So the four properties the SQLite file
pins are re-run here against a REAL PostgreSQL, in a private schema that is
created for the test and dropped after it. The store target under test is that
schema's DSN, pinned as ``$SCITEX_CARDS_DB``, so every guard and every write
resolves to it and never to the fleet's board.

Skip if UNDECLARED, fail if DECLARED-but-broken — the contract every
``SCITEX_CARDS_TEST_PG_DSN`` test in this suite uses, so a missing server
cannot quietly turn this into a green no-op.
"""

from __future__ import annotations

import cProfile
import os
import uuid

import pytest

from scitex_cards import _store
from scitex_cards._db import ENV_DB
from scitex_cards._store_errors import RevisionConflictError
from scitex_cards._store_single_card import read_card_or_raise, write_card_or_raise

_CARD = "zz-single-card-pg-fixture"


def _declared_dsn() -> str:
    declared = os.environ.get("SCITEX_CARDS_TEST_PG_DSN")
    if not declared:
        pytest.skip("SCITEX_CARDS_TEST_PG_DSN is not declared")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pytest.fail("SCITEX_CARDS_TEST_PG_DSN is set but psycopg is missing")
    return declared


@pytest.fixture
def pg_target(env):
    """A DSN scoped to a throwaway schema, pinned as the store for the test."""
    import psycopg

    base = _declared_dsn()
    schema = "cards_1c_" + uuid.uuid4().hex[:10]
    try:
        admin = psycopg.connect(base, autocommit=True)
    except Exception as exc:  # noqa: BLE001 -- declared-but-broken must FAIL
        pytest.fail(f"declared Postgres at {base!r} unreachable: {exc}")
    admin.execute(f"CREATE SCHEMA {schema}")
    joiner = "&" if "?" in base else "?"
    target = f"{base}{joiner}options=-csearch_path%3D{schema}"
    try:
        # PROVE THE ISOLATION BEFORE THE FIRST WRITE. Every write below goes
        # wherever this DSN's search_path points; if the option were ignored,
        # ``add_task`` would land a fixture card on the real board.
        with psycopg.connect(target) as probe:
            current = probe.execute("SELECT current_schema()").fetchone()[0]
        if current != schema:
            pytest.fail(
                f"search_path option not honoured: current_schema()={current!r}, "
                f"expected {schema!r}. Refusing to write anywhere."
            )
        env.set(ENV_DB, target)
        # Provision the private schema: the canonical read's existence guard
        # refuses a database with no `tasks` table (correctly), and add_task
        # goes through it. open_db runs init_schema, which is the one sanctioned
        # way a schema comes to exist.
        from scitex_cards._db import open_db

        open_db(target).close()
        _store.add_task(
            None,
            id=_CARD,
            title="one-card pg fixture",
            status="deferred",
            assignee="agent:test-suite",
        )
        yield target
    finally:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()


def _comment_texts(target: str) -> list[str]:
    import psycopg

    with psycopg.connect(target) as conn:
        rows = conn.execute(
            "SELECT text FROM task_comments WHERE task_id = %s ORDER BY seq", (_CARD,)
        ).fetchall()
    return [r[0] for r in rows]


def test_a_comment_lands_on_postgres(pg_target):
    # Arrange
    text = "hello from the one-card path"
    # Act
    _store.comment_task(None, _CARD, text, by="agent:test-suite")
    # Assert
    assert _comment_texts(pg_target) == [text]


def test_a_table_only_comment_row_survives_on_postgres(pg_target):
    # Arrange -- a row that reached the table without reaching card_json
    import psycopg

    with psycopg.connect(pg_target) as conn:
        conn.execute(
            "INSERT INTO task_comments(task_id, seq, author, ts, kind, text) "
            "VALUES (%s, 0, 'peer', '2026-09-01T01:00:00Z', NULL, 'arrived by sync')",
            (_CARD,),
        )
    # Act
    _store.comment_task(None, _CARD, "second", by="agent:test-suite")
    # Assert
    assert _comment_texts(pg_target) == ["arrived by sync", "second"]


def test_a_stale_revision_is_refused_on_postgres(pg_target):
    # Arrange
    card, revision = read_card_or_raise(pg_target, _CARD)
    card.setdefault("comments", []).append(
        {"author": "x", "ts": "2026-09-02T00:00:00Z", "text": "late"}
    )
    # Act
    stale = revision + 1
    # Assert
    with pytest.raises(RevisionConflictError):
        write_card_or_raise(pg_target, card, expected_revision=stale)


def test_a_comment_write_never_exports_the_board_on_postgres(pg_target):
    # Arrange
    profile = cProfile.Profile()
    # Act
    profile.enable()
    try:
        _store.comment_task(None, _CARD, "profiled", by="agent:test-suite")
    finally:
        profile.disable()
    # Assert
    names = {e.code.co_name for e in profile.getstats() if hasattr(e.code, "co_name")}
    assert names & {"export_doc", "_read_canonical_db_or_raise"} == set()


# EOF
