#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card JSON cannot carry is REFUSED at the write, not stored as a NULL.

THE DEFECT THIS CLOSES, measured 2026-08-17 on the unfixed tree::

    add_task(..., note="x")            -> payload OK
    add_task(..., note=datetime(...))  -> row stored, card_json NULL
    add_task(..., note={1, 2})         -> ExportRefused naming 't-datetime'

The third call never got to fail on its own value. It was refused by the row the
SECOND call had just planted. One `add_task` with an ordinary Python value, and
the next unrelated operation by ANY agent is dead — that is the tasks-variant
outage first seen 2026-08-11 and unexplained for five days.

`card_payload_json` answers `None` for such a card and `_db_bootstrap` stored
the row anyway. The `NULL` is documented as load-bearing, and it is — for a
payload that is ALREADY MISSING, it is what makes the read refuse rather than
serve a card whose fields changed shape. It is not a defensible WRITE policy:
the writer has already discovered the payload cannot be serialised, and stores
a row it knows to be unreadable.

    refusing the write   the caller gets one message naming the field and its
                         type, and fixes their own call
    storing the NULL     everyone else loses the whole board until somebody
                         unrelated happens to rewrite that row

Same information, same instant. Only who pays differs.

NOT A LICENCE TO TOLERATE ON READ — the opposite. This refuses EARLIER, on the
way in. `test__rmw_refusal_must_not_become_tolerance.py` pins that the
read-modify-write door must keep refusing, because skipping a row THERE deletes
it. Refuse on write; refuse on read-modify-write; tolerate only on a pure read
that never writes the document back.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
from conftest import seed_db_from_doc

from scitex_cards._db import connect
from scitex_cards._db_payload import CardNotSerialisableError

_STORE_ENV = "SCITEX_CARDS_DB"
_BAD = "t-carries-a-datetime"


def _card_json_state(db: Path, task_id: str) -> str:
    conn = connect(db)
    try:
        rows = list(
            conn.execute("SELECT card_json FROM tasks WHERE id = ?", (task_id,))
        )
    finally:
        conn.close()
    if not rows:
        return "absent"
    return "null-payload" if rows[0]["card_json"] is None else "payload-present"


@pytest.fixture()
def store(tmp_path: Path, new_store):
    """An ordinary healthy store, with $SCITEX_CARDS_DB pinned at it."""
    db = new_store()
    seed_db_from_doc(
        {
            "tasks": [
                {"id": "t-seed", "title": "seed", "status": "deferred", "agent": "a"}
            ],
            "users": [],
            "inboxes": {},
        },
        db,
    )
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
def store_after_a_refused_write(store: Path) -> Path:
    """The store, after one `add_task` carrying a datetime was refused."""
    from scitex_cards import add_task

    with pytest.raises(CardNotSerialisableError):
        add_task(
            id=_BAD,
            title="probe",
            assignee="a",
            note=datetime.datetime(2026, 8, 17, 11, 0, 0),
        )
    return store


def test_a_card_carrying_a_datetime_is_refused(store: Path):
    # Arrange
    from scitex_cards import add_task

    # Act
    # Assert
    with pytest.raises(CardNotSerialisableError):
        add_task(
            id=_BAD,
            title="probe",
            assignee="a",
            note=datetime.datetime(2026, 8, 17, 11, 0, 0),
        )


@pytest.fixture()
def refusal_text(store: Path) -> str:
    """The message a caller actually sees when their card cannot be stored."""
    from scitex_cards import add_task

    with pytest.raises(CardNotSerialisableError) as excinfo:
        add_task(
            id=_BAD,
            title="probe",
            assignee="a",
            note=datetime.datetime(2026, 8, 17, 11, 0, 0),
        )
    return str(excinfo.value)


def test_the_refusal_names_the_offending_field_and_its_type(refusal_text: str):
    # Arrange
    # Act
    # Assert — `json.dumps`'s own message names the TYPE and not the KEY, and a
    # caller with a dozen fields cannot act on "datetime is not serializable".
    assert "note (datetime)" in refusal_text


def test_the_refusal_says_nothing_was_written(refusal_text: str):
    # Arrange
    # Act
    # Assert — the caller's first question is whether they now have a mess to
    # clean up. The answer is no, and the message has to say so.
    assert "Nothing was written" in refusal_text


def test_a_refused_write_plants_no_row(store_after_a_refused_write: Path):
    # Arrange
    # Act
    # Assert — not merely "no NULL payload": NO ROW. A refused write must leave
    # nothing to clean up.
    assert _card_json_state(store_after_a_refused_write, _BAD) == "absent"


def test_the_store_still_works_after_a_refused_write(store_after_a_refused_write):
    # Arrange
    from scitex_cards import add_task

    # Act
    add_task(id="t-after", title="after", assignee="a", note="plain text")

    # Assert — THE POINT OF THE WHOLE CHANGE. Before this fix the refused card
    # was stored with a NULL payload and THIS call raised ExportRefused naming
    # the earlier row: one bad value disabled the board for everyone.
    assert _card_json_state(store_after_a_refused_write, "t-after") == "payload-present"


def test_an_ordinary_card_is_unaffected(store: Path):
    # Arrange
    from scitex_cards import add_task

    # Act
    add_task(id="t-plain", title="plain", assignee="a", note="just text")

    # Assert
    assert _card_json_state(store, "t-plain") == "payload-present"
