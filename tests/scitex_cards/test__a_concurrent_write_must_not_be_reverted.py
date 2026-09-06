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
from scitex_cards._store_comment import comment_task
from scitex_cards._store_mutate import add_task, update_task
from scitex_cards._store_lifecycle import (
    complete_task,
    delete_task,
    reopen_task,
    resolve_task,
    restore_task,
)


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


def test_a_comment_on_one_card_does_not_rewrite_another(two_cards):
    """THE CONVERTED VERB, and the guarantee `touched_ids` buys.

    `comment_task` names the single card it touched, so the write can no longer
    re-assert the caller's snapshot of every OTHER card. This is the narrow,
    checkable half of the fix: not "concurrency is solved" but "a comment on A
    cannot write B".

    Asserted through the REAL verb rather than through the mirror, because the
    defect was never in the mirror alone — it was in what the verb handed it.
    """
    # Arrange — B is committed after the store is seeded
    store = two_cards
    doc = load_tasks(store)
    for card in doc:
        if card["id"] == "card-b":
            card["status"] = "in_progress"
    save_tasks(doc, store)
    # Act — comment on card-a only (first positional is the STORE, not the id)
    comment_task(store, "card-a", "hello", by="tester")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_the_comment_itself_still_lands(two_cards):
    """POSITIVE CONTROL for the converted verb: narrowing the write must not
    narrow it to nothing."""
    # Arrange
    store = two_cards
    # Act
    comment_task(store, "card-a", "hello", by="tester")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-a"]["comments"][-1]["text"] == "hello"


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


def _commit_b_in_progress(store):
    """Commit a change to card-b so an unrelated write has something to revert."""
    doc = load_tasks(store)
    for card in doc:
        if card["id"] == "card-b":
            card["status"] = "in_progress"
    save_tasks(doc, store)


def test_completing_one_card_does_not_rewrite_another(two_cards):
    """THE VERB FROM THE CONFIRMED LOSS.

    figrecipe 2026-08-10: a complete_task that RETURNED status=done was later
    found back at status=blocked, reverted by writes to unrelated cards. This is
    the converse guarantee -- completing card-a must not revert card-b.
    """
    # Arrange
    store = two_cards
    _commit_b_in_progress(store)
    # Act
    complete_task(store, "card-a")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_the_completion_itself_still_lands(two_cards):
    """POSITIVE CONTROL: narrowing the write must not narrow it to nothing."""
    # Arrange
    store = two_cards
    # Act
    complete_task(store, "card-a")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-a"]["status"] == "done"


def test_resolving_one_card_does_not_rewrite_another(two_cards):
    # Arrange
    store = two_cards
    doc = load_tasks(store)
    for card in doc:
        if card["id"] == "card-a":
            card["status"] = "blocked"
            card["blocker"] = "operator-decision"
    save_tasks(doc, store)
    _commit_b_in_progress(store)
    # Act
    resolve_task(store, "card-a", actor="tester")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_reopening_one_card_does_not_rewrite_another(two_cards):
    # Arrange
    store = two_cards
    complete_task(store, "card-a")
    _commit_b_in_progress(store)
    # Act
    reopen_task(store, "card-a", by="tester")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_restoring_one_card_does_not_rewrite_another(two_cards):
    # Arrange
    store = two_cards
    payload = delete_task(store, "card-a")["removed"]
    _commit_b_in_progress(store)
    # Act
    restore_task(store, payload)
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_deleting_one_card_does_not_rewrite_another(two_cards):
    # Arrange
    store = two_cards
    _commit_b_in_progress(store)
    # Act
    delete_task(store, "card-a")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_deleting_a_card_still_scrubs_inbound_references(two_cards):
    """THE TEST THAT WOULD HAVE CAUGHT THE OBVIOUS-BUT-WRONG CONVERSION.

    delete_task tombstones its target AND scrubs references to it from other
    cards, so its intent spans several rows. `touched_ids=[task_id]` — the
    conversion every other verb takes — would have silently dropped the scrubs,
    leaving card-b pointing at a tombstone. That is a NEW defect introduced by
    the fix for an old one, and only this assertion distinguishes them.
    """
    # Arrange
    store = two_cards
    doc = load_tasks(store)
    for card in doc:
        if card["id"] == "card-b":
            card["depends_on"] = ["card-a"]
    save_tasks(doc, store)
    # Act
    delete_task(store, "card-a")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert "card-a" not in (after["card-b"].get("depends_on") or [])


def test_updating_one_card_does_not_rewrite_another(two_cards):
    """THE VERB THAT RUNS MOST, and the last one to get the guard.

    `comment_task`, `complete_task`, `resolve_task`, `reopen_task`,
    `delete_task` and `rescore_task` all declared `touched_ids`; `update_task`
    did not, so every other verb's protection was undone by the one that runs
    constantly. Six converted siblings and the two central verbs left out is
    the fix-never-reached-its-second-site shape, at the worst possible site.
    """
    # Arrange
    store = two_cards
    _commit_b_in_progress(store)
    # Act
    update_task(store, "card-a", status="blocked", blocker="operator-decision")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_the_update_itself_still_lands(two_cards):
    """POSITIVE CONTROL: narrowing the write must not narrow it to nothing.

    Not decoration. `touched_ids` filters which rows reach the database, so the
    failure mode of over-narrowing is a verb that reports success and writes
    NOTHING — indistinguishable from working, exactly like the defect it fixes.
    """
    # Arrange
    store = two_cards
    # Act
    update_task(store, "card-a", status="blocked", blocker="operator-decision")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-a"]["status"] == "blocked"


def test_adding_a_card_does_not_rewrite_another(two_cards):
    """`add_task` had the same gap, and an insert is not exempt from it.

    The new row is what the caller MEANT to write, but the whole-document save
    carried its entire stale snapshot alongside — so creating a card could
    revert an unrelated one committed since the read.
    """
    # Arrange
    store = two_cards
    _commit_b_in_progress(store)
    # Act
    add_task(store, id="card-c", title="C", status="deferred", assignee="tester")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert after["card-b"]["status"] == "in_progress"


def test_the_added_card_itself_lands(two_cards):
    """POSITIVE CONTROL for the insert path."""
    # Arrange
    store = two_cards
    # Act
    add_task(store, id="card-c", title="C", status="deferred", assignee="tester")
    # Assert
    after = {c["id"]: c for c in load_tasks(store)}
    assert "card-c" in after


# EOF
