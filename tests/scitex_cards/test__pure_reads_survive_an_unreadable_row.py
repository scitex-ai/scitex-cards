#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A PURE read survives one unreadable row. A WRITE still refuses it.

THE DEFECT, measured 2026-08-17 before this change::

    store with 2 healthy cards + 1 users row whose payload cannot be rebuilt

    list_tasks()       -> ExportRefused: users row 'u_000000000002' …
    summarize_tasks()  -> ExportRefused: users row 'u_000000000002' …

Neither verb returns anything from `users`. Nothing either produces could be
made lossy by that row. They were refused solely because of the DOOR they
travel: `_read_canonical_db_or_raise`, which is the read half of a
read-modify-write and refuses so that a partial document is never written back
over the store.

That refusal is correct for the write door and wrong here, and the guard's own
comment says why it cannot simply be relaxed:

    "An export that silently under-reports is the total-loss case, BECAUSE THE
     DIFFERENCE IS DELETED ON WRITE-BACK."

A pure read performs no write-back, so the premise does not hold for it.

WHAT THIS FILE PINS, AND WHY THE PAIRING MATTERS

The two halves must hold TOGETHER. Tolerance on the read is only safe while the
write keeps refusing — measured the same day, a tolerant write reported SUCCESS
and left the row DELETED. So `test_a_write_still_refuses…` below is not a
courtesy duplicate of
`test__rmw_refusal_must_not_become_tolerance.py`; it is the assertion that this
change did not leak into the mutate path, run against this file's own fixture.
Delete it and a future edit could make both doors tolerant with nothing red.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import seed_db_from_doc

from scitex_cards._db import connect
from scitex_cards._db_export import ExportRefused

_STORE_ENV = "SCITEX_CARDS_DB"
_UNREADABLE_USER = "u_000000000002"


def _user_ids(db: Path) -> set[str]:
    conn = connect(db)
    try:
        return {r["id"] for r in conn.execute("SELECT id FROM users")}
    finally:
        conn.close()


@pytest.fixture()
def store_with_one_unreadable_user(tmp_path: Path, new_store):
    """Two healthy cards, two users, one of which cannot be rebuilt.

    `users` has no rebuild rule (`_db_export._REBUILDERS` covers only
    `notifications`), so nulling the payload is enough to reach the refusal
    rather than the repair.
    """
    doc = {
        "tasks": [
            {"id": "t-one", "title": "first", "status": "deferred", "agent": "a"},
            {"id": "t-two", "title": "second", "status": "done", "agent": "b"},
        ],
        "users": [
            {"id": "u_000000000001", "kind": "agent", "names": ["a"]},
            {"id": _UNREADABLE_USER, "kind": "agent", "names": ["b"]},
        ],
        "inboxes": {},
    }
    db = new_store()
    seed_db_from_doc(doc, db)

    conn = connect(db)
    conn.execute(
        "UPDATE users SET record_json = NULL WHERE id = ?", (_UNREADABLE_USER,)
    )
    conn.commit()
    conn.close()

    previous = os.environ.get(_STORE_ENV)
    os.environ[_STORE_ENV] = str(db)
    try:
        yield db
    finally:
        if previous is None:
            os.environ.pop(_STORE_ENV, None)
        else:
            os.environ[_STORE_ENV] = previous


def test_list_tasks_returns_the_cards_despite_an_unreadable_user_row(
    store_with_one_unreadable_user: Path,
):
    # Arrange
    from scitex_cards import list_tasks

    # Act
    tasks = list_tasks()

    # Assert — both healthy cards, not a blanked board. Before this change the
    # call raised ExportRefused over a row it does not return.
    assert len(tasks) == 2


def test_summarize_tasks_survives_an_unreadable_user_row(
    store_with_one_unreadable_user: Path,
):
    # Arrange
    from scitex_cards import summarize_tasks

    # Act
    summary = summarize_tasks()

    # Assert
    assert isinstance(summary, dict)


def test_a_write_still_refuses_an_unreadable_row(
    store_with_one_unreadable_user: Path,
):
    # Arrange
    from scitex_cards import comment_task

    # Act
    # Assert — THE PAIRING. Tolerance must not have reached the mutate path;
    # a write that omitted this row would DELETE it.
    with pytest.raises(ExportRefused):
        comment_task(task_id="t-one", text="must not land")


def test_a_tolerant_read_deletes_nothing(store_with_one_unreadable_user: Path):
    # Arrange
    from scitex_cards import list_tasks

    # Act
    list_tasks()

    # Assert — omitting a row from a RESULT must never remove it from the
    # STORE. The row stays, unreadable, until its payload is repaired.
    assert _UNREADABLE_USER in _user_ids(store_with_one_unreadable_user)


def test_the_skipped_row_is_named_rather_than_dropped_in_silence(
    store_with_one_unreadable_user: Path, caplog
):
    # Arrange
    from scitex_cards import list_tasks

    # Act
    with caplog.at_level("WARNING"):
        list_tasks()

    # Assert — "skip and report" is the contract; a skipped row nobody is told
    # about is the lossy read the guard exists to prevent, one level up.
    assert _UNREADABLE_USER in caplog.text
