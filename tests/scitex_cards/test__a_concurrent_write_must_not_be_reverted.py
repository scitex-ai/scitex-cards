#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A write to card A must not revert a concurrent write to card B.

THIS TEST FAILS TODAY, ON PURPOSE, and is marked ``xfail(strict=True)`` so the
day the fix lands it turns RED for passing unexpectedly and someone must delete
the marker. A defect with no test is a defect that gets re-argued; a defect with
an xfail is a defect with a receipt.

THE DEFECT, measured by figrecipe on the live store 2026-08-10 (three instances,
one a confirmed loss):

    complete_task(X)  ->  returned status=done
    ...writes to OTHER cards by other agents...
    list_tasks()      ->  X is back at status=blocked

A completion that reported success was reverted by writes to cards it had
nothing to do with. figrecipe: "there is no batching discipline a caller can
adopt to avoid it."

THE MECHANISM, established 2026-08-12 across three files:

    _store_comment.py:70   with _store_lock(tasks_path):
                               doc, tasks = _read_write_doc(...)   READ ALL
                               ...mutate one card...
                               _save_doc_unlocked(doc, ...)        WRITE ALL

    _store_write.py:51     the lock is an fcntl.flock on a sibling
                           `.<name>.lock` FILE

    _paths                 for a PostgreSQL store that path is the LOCAL ROOT
                           redirect, i.e. inside each agent's own container

The cards live in shared PostgreSQL; the mutual-exclusion lock does not. Two
agents each take their OWN lock, both succeed instantly, both rewrite the whole
document. An flock on a per-container file is not a weak version of a shared
lock — it is a different thing wearing the same name.

AND THE INCREMENTAL MIRROR DOES NOT SAVE US, which is the part worth having in a
test. `_db_mirror.mirror_doc_incremental` already writes only what CHANGED — but
computes it as:

    changed = [i for i, h in now_hashes.items() if prior.get(i) != h]

"differs from the database" INCLUDES "somebody else changed it and I hold an old
copy". The mirror is faithful to the writer's document, and that fidelity is the
bug: it cannot tell "I changed this" from "I am stale about this".

WHY THIS TEST IS DETERMINISTIC RATHER THAN THREADED. The failure needs one
specific interleaving — read, other-write, write — and a threaded test would
reproduce it only sometimes, which is worse than useless for a data-integrity
guarantee: an intermittently-green test teaches people to re-run. So the
interleaving is written out explicitly. Nothing here is a race; it is the race's
outcome, made repeatable.
"""

from __future__ import annotations

import pytest

from scitex_cards._model import load_tasks, save_tasks


@pytest.fixture()
def two_cards(tmp_path):
    """A store with two independent cards, written by the real verbs."""
    store = tmp_path / "cards.db"
    save_tasks(
        [
            {"id": "card-a", "title": "A", "status": "deferred"},
            {"id": "card-b", "title": "B", "status": "deferred"},
        ],
        store,
    )
    return store


@pytest.mark.xfail(
    strict=True,
    reason=(
        "cards-comment-task-whole-store-rmw-clobbers-concurrent-writes: a "
        "whole-store read-modify-write reverts any card changed between its "
        "read and its write. Delete this marker when the touched_ids fix lands."
    ),
)
def test_a_stale_writer_does_not_revert_another_agents_card(two_cards):
    """THE EXACT INTERLEAVING, spelled out.

    Agent A reads the store. Agent B then writes card-b and commits. Agent A —
    still holding its pre-B snapshot — writes card-a. A's document still carries
    the OLD card-b, so the whole-store write re-asserts it and B's change is
    gone, with both agents told their write succeeded.
    """
    # Arrange — A reads, B writes and commits, A is now stale about card-b
    store = two_cards
    a_doc = load_tasks(store)

    b_doc = load_tasks(store)
    for card in b_doc:
        if card["id"] == "card-b":
            card["status"] = "in_progress"
    save_tasks(b_doc, store)

    # Act — A writes its own card from the stale snapshot
    for card in a_doc:
        if card["id"] == "card-a":
            card["status"] = "in_progress"
    save_tasks(a_doc, store)

    # Assert — B's committed change must survive A's unrelated write
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_the_writer_own_change_lands(two_cards):
    """POSITIVE CONTROL, and it is not decoration.

    The assertion above can be satisfied by a store that refuses every write.
    This one proves the mechanism under test still does its job, so a green
    result there means "B survived", not "nothing happened".
    """
    # Arrange
    store = two_cards
    doc = load_tasks(store)
    for card in doc:
        if card["id"] == "card-a":
            card["status"] = "in_progress"
    # Act
    save_tasks(doc, store)
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-a"]["status"] == "in_progress"


def test_the_fixture_starts_from_a_known_state(two_cards):
    """CONTROL ON THE PRECONDITION: if card-b did not start at `deferred`, the
    xfail above could pass for a reason that has nothing to do with the defect.
    """
    # Arrange
    store = two_cards
    # Act
    before = {c["id"]: c for c in load_tasks(store)}
    # Assert
    assert before["card-b"]["status"] == "deferred"


# EOF
