#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A whole-document write must not revert cards it did not touch.

THE SILENT LOST-WRITE MECHANISM, reproduced deterministically. It needs a
STALE IN-MEMORY DOCUMENT, not a race, so a single process can arrange it
exactly — which is what makes it testable at all. Three field reports, none
of which could be reproduced on demand until this file:

    2026-08-10  figrecipe  a `complete_task` that RETURNED status=done was
                           later found back at `blocked`, reverted by WRITES
                           TO UNRELATED CARDS
    2026-08-18  dotfiles   a concurrently-written `parked` vanished, and
                           `last_activity` REVERTED to an older timestamp
    2026-08-18  sac        14 of 178 serial writes silently lost; all 14
                           persisted on retry with identical input

`last_activity` reverting to a specific EARLIER value is the tell that
separates this from a transaction rollback: a rollback leaves the previous
value in place, it does not move a field backwards to someone else's older
reading. Being overwritten by a stale copy does exactly that.

THE MECHANISM, at `_db_mirror.py:292-294`::

    if touched_ids is not None:
        wanted = {str(i) for i in touched_ids}
        changed = [i for i in changed if i in wanted]

That guard is the whole mitigation. Without it `changed` means "every card
whose content differs from the database", which — in that module's own words
— "silently includes 'somebody else changed this card and I am holding an old
copy'", and both callers are told they succeeded.
"""

from __future__ import annotations

import pytest

from scitex_cards._model import _store_lock, load_doc
from scitex_cards._store import get_task, update_task
from scitex_cards._store_write import _save_doc_unlocked

from conftest import seed_db_from_doc


@pytest.fixture()
def two_cards(tmp_path, env):
    """Two real cards in an isolated store, plus a STALE copy of the document.

    The stale copy is taken BEFORE the other agent's write, which is the whole
    arrangement: it is what any caller holds between its own read and its own
    write-back, and on this fleet that window spans hosts the store lock does
    not reach (`_store_lock` is an fcntl flock on a per-container file).
    """
    db_path = tmp_path / "cards.db"
    seed_db_from_doc(
        {
            "tasks": [
                {
                    "id": "ours",
                    "title": "the card we are editing",
                    "status": "deferred",
                    "assignee": "us",
                },
                {
                    "id": "theirs",
                    "title": "another agent's card",
                    "status": "deferred",
                    "assignee": "them",
                },
            ]
        },
        db_path,
    )
    env.set("SCITEX_CARDS_DB", str(db_path))

    stale = load_doc(db_path, validate=False)

    # ANOTHER AGENT COMMITS, naming only their own card. This is an ordinary,
    # correct write — it is the write that must survive.
    update_task(task_id="theirs", status="in_progress")

    return {"db_path": db_path, "stale": stale}


def _write_back(bundle, *, touched_ids):
    """Write our stale document back, having edited only OUR card."""
    stale = bundle["stale"]
    tasks = stale.get("tasks") or []
    for task in tasks:
        if task.get("id") == "ours":
            task["title"] = "edited by us"
    with _store_lock(bundle["db_path"]):
        _save_doc_unlocked(
            stale, bundle["db_path"], tasks=tasks, touched_ids=touched_ids
        )


def test_naming_the_touched_card_leaves_the_other_agents_write_intact(two_cards):
    # Arrange
    # Act — we write back a stale document, naming the one card we changed.
    _write_back(two_cards, touched_ids=["ours"])

    # Assert — their committed status survives our write.
    assert get_task(task_id="theirs")["status"] == "in_progress"


def test_naming_the_touched_card_still_applies_our_own_edit(two_cards):
    # Arrange
    # Act
    _write_back(two_cards, touched_ids=["ours"])

    # Assert — the guard must not cost us the write we actually intended.
    assert get_task(task_id="ours")["title"] == "edited by us"


def test_omitting_touched_ids_reverts_the_other_agents_card(two_cards):
    """THE DEFECT ITSELF, pinned so nobody reads omission as harmless.

    This is a characterisation test: it asserts what the primitive DOES today
    when `touched_ids` is omitted, which is to re-assert every differing card
    from the caller's copy. It is deliberately written as a statement about
    the PRIMITIVE rather than about any verb, because the fix is to thread the
    argument through the verbs — after which this test still holds and still
    documents why they must.

    If a future change makes omission safe (e.g. the argument becomes
    required, or the mirror stops inferring intent from difference), this test
    SHOULD fail and be deleted. That is the intended way for it to die.
    """
    # Arrange
    # Act — the same write, without naming what we touched.
    _write_back(two_cards, touched_ids=None)

    # Assert — their `in_progress` is gone, replaced by our older copy.
    assert get_task(task_id="theirs")["status"] == "deferred"
