#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The comment merge must read rows by NAME, which is all PostgreSQL allows.

THE GAP THIS FILLS. 0.49.0 shipped `_merge_unseen_comment_rows` reading result
rows by POSITION. The retired engine's row type supported `row[0]`; psycopg's dict row factory
does not, so `row[0]` looked up the integer key ``0`` and raised ``KeyError: 0``.
Production is PostgreSQL and the entire ~7,500-test suite ran on the retired engine, so the
defect was not missed — it was UNREACHABLE from the harness. Every card holding
comments became read-only in every direction for about an hour, fleet-wide.

The repo already had a `postgres-backend` CI job whose header names "dict-like
row access" as a behaviour it exists to validate. It passed on 0.49.0, because
its `PG_TEST_FILES` is a hand-maintained allowlist and the card-write funnel is
not on it. This file is the missing member.

WHY IT IS SAFE TO POINT AT A LIVE STORE. `_merge_unseen_comment_rows` only READS
`task_comments`; its output is a mutation of the in-memory card dict. And the
fixture creates a session-local ``TEMP`` table of that name, which precedes
``public`` on the search_path, so the SELECT resolves to the temporary rows and
the real table is never touched. Nothing here writes to the database.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._mirror_rows import _merge_unseen_comment_rows

_FALLBACK_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"

_CARD_ID = "zz-pgmerge-fixture-card"


@pytest.fixture
def pg_store_conn():
    """A package StoreConnection over Postgres, with a TEMP task_comments.

    Skip if UNDECLARED, fail if DECLARED-but-broken — the same contract the other
    Postgres-backed tests use, so a missing server cannot quietly turn this into
    a green no-op.

    The package wrapper is deliberate rather than a raw psycopg connection: the
    module under test writes `?` paramstyle, which only `StoreConnection`
    translates. A raw connection would fail for a reason unrelated to the defect.
    """
    declared = os.environ.get("SCITEX_CARDS_TEST_PG_DSN")
    dsn = declared or _FALLBACK_DSN
    try:
        import psycopg  # noqa: F401
    except ImportError:
        if declared:
            pytest.fail("SCITEX_CARDS_TEST_PG_DSN is set but psycopg is missing")
        pytest.skip("psycopg not installed")

    from scitex_cards._db import open_db

    try:
        conn = open_db(dsn)
    except Exception as exc:  # noqa: BLE001 -- see contract above
        if declared:
            pytest.fail(f"declared Postgres at {dsn!r} unreachable: {exc}")
        pytest.skip(f"no live Postgres: {type(exc).__name__}")

    conn.execute(
        "CREATE TEMP TABLE task_comments ("
        " task_id TEXT, seq INTEGER, author TEXT, ts TEXT, kind TEXT, text TEXT)"
    )
    yield conn
    conn.close()


def _insert(conn, seq: int, text: str) -> None:
    conn.execute(
        "INSERT INTO task_comments (task_id, seq, author, ts, kind, text)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (_CARD_ID, seq, "scitex-cards", "2026-08-23T00:0%d:00Z" % seq, "note", text),
    )


def test_a_row_the_document_cannot_see_is_absorbed_on_postgres(pg_store_conn):
    """The headline: the merge runs on a Postgres row without raising."""
    # Arrange
    _insert(pg_store_conn, 1, "in the doc")
    _insert(pg_store_conn, 2, "arrived by sync")
    card = {"id": _CARD_ID, "comments": [{"author": "scitex-cards", "ts": "2026-08-23T00:01:00Z", "kind": "note", "text": "in the doc"}]}
    # Act
    _merge_unseen_comment_rows(pg_store_conn, _CARD_ID, card)
    # Assert
    assert [c["text"] for c in card["comments"]] == ["in the doc", "arrived by sync"]


def test_the_recovered_count_is_reported_on_postgres(pg_store_conn):
    """One row was invisible to the document, so exactly one is recovered."""
    # Arrange
    _insert(pg_store_conn, 1, "in the doc")
    _insert(pg_store_conn, 2, "arrived by sync")
    card = {"id": _CARD_ID, "comments": [{"author": "scitex-cards", "ts": "2026-08-23T00:01:00Z", "kind": "note", "text": "in the doc"}]}
    # Act
    recovered = _merge_unseen_comment_rows(pg_store_conn, _CARD_ID, card)
    # Assert
    assert recovered == 1


def test_an_already_agreeing_card_recovers_nothing_on_postgres(pg_store_conn):
    """Over-reach guard: a merge wide enough to resurrect is wide enough to duplicate."""
    # Arrange
    _insert(pg_store_conn, 1, "only comment")
    card = {"id": _CARD_ID, "comments": [{"author": "scitex-cards", "ts": "2026-08-23T00:01:00Z", "kind": "note", "text": "only comment"}]}
    # Act
    recovered = _merge_unseen_comment_rows(pg_store_conn, _CARD_ID, card)
    # Assert
    assert recovered == 0


def test_a_card_with_no_rows_is_left_alone_on_postgres(pg_store_conn):
    """The early return that hid the 0.49.0 defect, pinned so it stays correct.

    This is the case a create-and-comment-once smoke test exercises, and passing
    it proves nothing about the others — which is exactly why it is here WITH
    them rather than instead of them.
    """
    # Arrange
    card = {"id": _CARD_ID, "comments": []}
    # Act
    recovered = _merge_unseen_comment_rows(pg_store_conn, _CARD_ID, card)
    # Assert
    assert recovered == 0
