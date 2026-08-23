#!/usr/bin/env python3
"""Every reassign write is narrowed to the cards it actually touched.

THE DEFECT. `_db_mirror` documents a lost-write mechanism distinct from the
deadlock rollback: a caller writing card A re-asserts its STALE copy of card B
over another agent's committed change, "and both are told they succeeded".
Measured on the live board 2026-08-10 — a `complete_task` that RETURNED
status=done was later found back at `blocked`, reverted by writes to UNRELATED
cards.

`touched_ids` is the built mitigation: it narrows the write to the cards the
caller actually touched, so untouched cards are never re-asserted. Until
2026-08-19 the reassign verbs were the ONLY card verbs missing it — and they
are the worst ones to miss, because a stale-copy overwrite there reverts
another agent's OWNERSHIP change rather than a field.

WHY THIS TEST IS STRUCTURAL RATHER THAN BEHAVIOURAL. Demonstrating the race
needs two concurrent writers interleaved at a chosen point, which this suite
cannot arrange without patching production internals (forbidden package-wide,
STX-NM002). What CAN be pinned exactly, and is what actually regressed, is that
each write declares its touched set. A behavioural test that never interleaves
would pass against the unfixed code, which is the failure mode this file exists
to avoid.

THE TWO SITES ARE DIFFERENT VERBS AND TAKE DIFFERENT SETS, which is the part a
careless fix gets wrong:

    reassign_all    BULK    every card owned by old_owner   -> touched_ids=moved
    reassign_task   SINGLE  one card, task_id in scope      -> touched_ids=[task_id]

The card prescribing this fix said "pass `touched_ids=[task_id]` at both sites".
That is right for one and WRONG for the other: `reassign_all` has no `task_id`,
and narrowing it to a single id would persist ONE ownership change and silently
drop the other N-1 — worse than the broad write it replaces, which at least
keeps everything it touched. Hence the second test below, which pins the bulk
site's argument to the accumulated list rather than merely to "some value".
"""

import ast
import inspect

import pytest

from scitex_cards import _store_reassign

#: The verbs under test, and the touched-set each one must declare.
SAVE_CALL = "_save_doc_unlocked"


def _save_calls(module):
    """Every ``_save_doc_unlocked`` call node in ``module``'s source."""
    tree = ast.parse(inspect.getsource(module))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        == SAVE_CALL
    ]


def test_reassign_has_write_sites_to_check():
    """The positive control: a scan that finds nothing proves nothing.

    Without this, deleting both writes — or renaming the function — would make
    every assertion below vacuously true.
    """
    # Arrange
    module = _store_reassign
    # Act
    calls = _save_calls(module)
    # Assert
    assert len(calls) == 2


def test_every_reassign_write_declares_its_touched_set():
    """No reassign write may re-assert the caller's whole in-memory copy."""
    # Arrange
    module = _store_reassign
    # Act
    calls = _save_calls(module)
    unguarded = [
        call.lineno
        for call in calls
        if "touched_ids" not in {kw.arg for kw in call.keywords}
    ]
    # Assert
    assert unguarded == []


def test_the_bulk_verb_narrows_to_every_card_it_moved():
    """`reassign_all` must pass the ACCUMULATED list, never a single id.

    This is the one a careless fix gets backwards. `reassign_all` modifies N
    cards; narrowing its write to one id would drop N-1 ownership changes while
    looking like the same one-line guard every other verb uses.
    """
    # Arrange
    calls = _save_calls(_store_reassign)
    # Act
    bulk = [
        kw.value
        for call in calls
        for kw in call.keywords
        if kw.arg == "touched_ids" and isinstance(kw.value, ast.Name)
    ]
    # Assert
    assert [node.id for node in bulk] == ["moved"]


def test_the_single_card_verb_narrows_to_that_card():
    """`reassign_task` touches exactly one card, so its set is that id."""
    # Arrange
    calls = _save_calls(_store_reassign)
    # Act
    singles = [
        kw.value
        for call in calls
        for kw in call.keywords
        if kw.arg == "touched_ids" and isinstance(kw.value, ast.List)
    ]
    # Assert
    assert [
        [getattr(el, "id", None) for el in node.elts] for node in singles
    ] == [["task_id"]]


@pytest.mark.parametrize("verb", ["reassign_task", "reassign_all"])
def test_the_verb_is_still_exported(verb):
    """Guard against the tests above passing because the verb was removed."""
    # Arrange
    module = _store_reassign
    # Act
    found = hasattr(module, verb)
    # Assert
    assert found is True
