#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card WRITE must refuse an unreadable row — never skip it.

THIS FILE EXISTS TO MAKE ONE PARTICULAR FIX UNSHIPPABLE.

`cards-one-malformed-row-must-not-refuse-the-whole-query-20260812` prescribes:

    "Refuse the RECORD, not the RESULT SET. A malformed row is skipped and
     REPORTED."

That is right for a PURE read and destructive here, because `list_tasks` and
`add_task` / `comment_task` reach the same `_read_canonical_db_or_raise`, and
on the write side the document that comes back is written back as the store::

    list_tasks         _store_list.py    load_tasks()        pure read
    add/comment/...    _store_mutate.py  _read_write_doc()   READ-MODIFY-WRITE
              both --> _model.load_doc --> _read_canonical_db_or_raise

MEASURED, by applying that prescription to `_db_export` and running a
`comment_task`::

    users loop made tolerant
        BEFORE users: ['u_000000000001', 'u_000000000002']
        WRITE:        SUCCEEDED — no refusal, no warning
        AFTER  users: ['u_000000000001']          <-- row DELETED

The write REPORTS SUCCESS while destroying a row. That is worse than the
outage the tolerance was meant to cure: an outage is loud and recoverable.

WHICH SECTIONS ARE ACTUALLY DANGEROUS — the useful part

Exactly those the write-back OWNS, and that is one named tuple:
`_db_mirror._SECTION_KEYS`, today `("users",)`. `_sync_sections` issues
`DELETE FROM <section>` and re-inserts from the doc, so a row missing from the
doc is a row deleted from the table.

The same mutation applied to the NOTIFICATIONS loop does NOT delete: measured,
the row survived, because `inboxes` was removed from `_SECTION_KEYS` in #780
precisely to stop ordinary card writes rebuilding the delivery rail. So the
hazard is not "any tolerated row is deleted" — it is scoped to `_SECTION_KEYS`,
and anyone widening that tuple widens this hazard with it.

WHY A TEST AND NOT A COMMENT

The export suite is 33 green with or without the tolerant change, because none
of its tests writes back a document containing a skipped row. Those tests are
not weak; they are calibrated for a different question. These three are the
assertion whose absence let the class hide.

The write attempt lives in a fixture that SUPPRESSES the refusal rather than
requiring it. That is deliberate: under the tolerant change the write SUCCEEDS,
so a fixture demanding a raise would error out and the guard would never run —
it would disappear at exactly the moment it was needed.

The tolerant read this card does want is a SEPARATE door, for callers that
never write the document back. Adding it must leave these three green.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest
from conftest import seed_db_from_doc

from scitex_cards._db import connect
from scitex_cards._db_export import ExportRefused

_UNREADABLE_USER = "u_000000000002"
_CARD = "t-one"
_STORE_ENV = "SCITEX_CARDS_DB"


def _user_ids(db: Path) -> set[str]:
    conn = connect(db)
    try:
        return {r["id"] for r in conn.execute("SELECT id FROM users")}
    finally:
        conn.close()


@pytest.fixture()
def store_with_one_unreadable_user(tmp_path: Path):
    """A store holding healthy rows plus ONE user record that cannot rebuild.

    Nulling `record_json` is enough here, unlike the notifications case: a
    rebuild rule exists only for `notifications` (`_db_export._REBUILDERS`), so
    `_record(repair=True)` cannot rescue a payload-less USER and goes straight
    to the refusal. `users` is also the section the write-back still owns,
    which is what makes this the table where tolerance destroys data.
    """
    doc = {
        "tasks": [
            {"id": _CARD, "title": "first", "status": "deferred", "agent": "a"},
        ],
        "users": [
            {"id": "u_000000000001", "kind": "agent", "names": ["a"]},
            {"id": _UNREADABLE_USER, "kind": "agent", "names": ["b"]},
        ],
        "inboxes": {},
    }
    db = tmp_path / "cards.db"
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


@pytest.fixture()
def store_after_an_attempted_write(store_with_one_unreadable_user: Path) -> Path:
    """The same store, after a card write has been ATTEMPTED against it.

    The refusal is SUPPRESSED, not required — see the module docstring. Under
    the tolerant change this call succeeds, and the point of the guard is to
    inspect what that success did to the store.
    """
    from scitex_cards import comment_task

    with contextlib.suppress(ExportRefused):
        comment_task(task_id=_CARD, text="a comment that must not land")
    return store_with_one_unreadable_user


def test_the_store_starts_with_the_unreadable_user_present(
    store_with_one_unreadable_user: Path,
):
    # Arrange
    # Act
    # Assert — the positive control. Without it, a guard that can never see the
    # row would pass for the wrong reason.
    assert _UNREADABLE_USER in _user_ids(store_with_one_unreadable_user)


def test_a_card_write_refuses_when_a_user_row_cannot_be_rebuilt(
    store_with_one_unreadable_user: Path,
):
    # Arrange
    from scitex_cards import comment_task

    # Act
    # Assert — refusing to write is recoverable; writing a document that is
    # missing a row is not.
    with pytest.raises(ExportRefused):
        comment_task(task_id=_CARD, text="a comment that must not land")


def test_a_refused_card_write_leaves_the_unreadable_user_intact(
    store_after_an_attempted_write: Path,
):
    # Arrange
    # Act
    # Assert — THE GUARD, and the one that caught the real hazard. Measured
    # under the tolerant change: the write succeeded and this row was gone.
    assert _UNREADABLE_USER in _user_ids(store_after_an_attempted_write)
