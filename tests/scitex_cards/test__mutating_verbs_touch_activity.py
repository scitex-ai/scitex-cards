#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every verb that mutates a card must advance `last_activity`.

WHY THIS FILE EXISTS — scitex-dev, 2026-08-10, reconciling three hosts
(laptop / scitex-04 / scitex-03) to 3719 cards each. Two cards could not be
ordered: both copies carried an IDENTICAL `last_activity`, because the act
that distinguished them was a COMPLETION and `complete_task` stamped
`_log_meta.completed_at` without touching `last_activity`.

That failure does not lose a CARD. It loses a COMPLETION, in the direction
that looks like ordinary reconciliation: the completed copy reads as the
STALE one whenever the other host touched the card later for any reason, so
last-writer-wins un-completes finished work and reports success.

An audit found FIVE such verbs, not one — `complete_task`, `resolve_task`,
`reopen_task`, `restore_task`, `set_edge`. That ratio is why the first test
below exists. The invariant was already written down in prose, and prose lost
5-to-1 against the habit of adding a verb, so the rule is enforced by
ENUMERATION instead: a new verb that persists a card without stamping fails
this file, whether or not its author ever read the prose.
"""

import ast
import os
import pathlib

from conftest import seed_db_from_doc

import scitex_cards
from scitex_cards._model import load_tasks
from scitex_cards._store_lifecycle import (
    complete_task,
    delete_task,
    reopen_task,
    resolve_task,
    restore_task,
)
from scitex_cards._store_relations import set_edge

#: A timestamp no real write will ever produce, so "did it change?" needs no
#: clock and cannot flake on a fast machine.
ANCIENT = "2000-01-01T00:00:00Z"

#: The single persist call. A function that reaches one of these WRITES a card.
_PERSIST = {"_save_doc_unlocked", "save_tasks", "write_doc_to_db"}

#: Functions that reach a persist call but are NOT card verbs. Each is exempt
#: on its own line with its own stated reason — never a blanket switch — so
#: adding an entry is a visible decision in review rather than a flag flip.
_NOT_A_VERB = {
    # The persist primitive itself. It receives an already-mutated document
    # and cannot tell a semantic edit from a rewrite; stamping here would
    # advance every card on any bulk write and silently reset the whole
    # board's stale-active nudge clock.
    "_save_doc_unlocked": "the persist primitive; sees a whole doc, not a change",
    # A thin back-compat wrapper that only recovers the non-`tasks` sections
    # and delegates straight to _save_doc_unlocked.
    "_save_tasks_unlocked": "thin wrapper over _save_doc_unlocked",
    # A generic locked read-modify-write CONTEXT MANAGER. It yields the task
    # list and the CALLER decides what changes; only that caller knows which
    # cards were really touched.
    "edit_tasks": "generic bulk-edit context manager; the caller owns semantics",
}


def _persisting_functions():
    """Yield (name, location) for every top-level package function that persists."""
    root = pathlib.Path(scitex_cards.__file__).parent
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            called = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Name):
                        called.add(fn.id)
                    elif isinstance(fn, ast.Attribute):
                        called.add(fn.attr)
            if called & _PERSIST:
                yield node, f"{path.name}:{node.lineno}"


def _stamps(node):
    """True if the body WRITES last_activity.

    TWO IDIOMS COUNT, because both are really in the tree and a checker that
    knew only one would be a gate that cannot fail for half the codebase:

      1. `touch_last_activity(card)` — the named helper, preferred for new code.
      2. `card["last_activity"] = ...` — the direct write, as `add_task`,
         `update_task`, `delete_task` and friends have always done it.

    Idiom 2 is NOT deprecated here. Rewriting eight working verbs to route
    through a new helper is churn that risks live behaviour to satisfy a
    checker's taste; the invariant is "the stamp is advanced", not "the stamp
    is advanced my way".
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name == "touch_last_activity":
                return True
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Subscript):
                    key = tgt.slice
                    if isinstance(key, ast.Constant) and key.value == "last_activity":
                        return True
        if isinstance(sub, ast.Dict):
            for key in sub.keys:
                if isinstance(key, ast.Constant) and key.value == "last_activity":
                    return True
        if isinstance(sub, ast.keyword) and sub.arg == "last_activity":
            return True
    return False


def _delinquents():
    """Every persisting function that does NOT stamp — exemptions NOT applied.

    The exemption is applied by the test, not hidden in here, so the barrier
    test reads as "these are the ones that persist without stamping, minus the
    three we deliberately excused" rather than trusting a filtered helper.
    """
    return sorted(
        (node.name, loc) for node, loc in _persisting_functions() if not _stamps(node)
    )


def _store(tasks):
    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _activity(store, task_id):
    tasks = load_tasks(store)
    return next(t for t in tasks if t["id"] == task_id).get("last_activity")


def _card(**over):
    # `blocker` is set because a blocked card that names no gate is a validator
    # warning on every read, and 15 lines of tolerated-warning noise is how a
    # real warning goes unread.
    card = {
        "id": "a",
        "title": "A",
        "status": "blocked",
        "blocker": "dependency",
        "last_activity": ANCIENT,
    }
    card.update(over)
    return card


def test_the_enumerator_finds_a_verb_it_is_known_to_find():
    """POSITIVE CONTROL. If the AST walk silently matches nothing, the real
    test below passes for the wrong reason — an empty set has no delinquents.
    `delete_task` has persisted-and-stamped since 2026-07-21, so it must
    appear among the functions this walk classifies."""
    # Arrange
    expected = "delete_task"
    # Act
    found = {node.name for node, _loc in _persisting_functions()}
    # Assert
    assert expected in found


def test_every_card_mutating_verb_stamps_last_activity():
    """THE BARRIER. Add a verb that writes a card without advancing its
    activity stamp and this fails, naming the verb and its line."""
    # Arrange
    exempt = set(_NOT_A_VERB)
    # Act
    missing = [f"{n} ({loc})" for n, loc in _delinquents() if n not in exempt]
    # Assert
    assert missing == []


def test_complete_task_advances_last_activity():
    """The originally reported defect: a completion left no mark on the field
    every reconciler orders by."""
    # Arrange
    store = _store([_card()])
    # Act
    complete_task(store, "a", by="tester")
    # Assert
    assert _activity(store, "a") != ANCIENT


def test_resolve_task_advances_last_activity():
    # Arrange
    store = _store([_card(blocker="operator-decision")])
    # Act
    resolve_task(store, "a", actor="tester")
    # Assert
    assert _activity(store, "a") != ANCIENT


def test_reopen_task_advances_last_activity():
    """An UN-completion is as much an act as a completion; without the stamp a
    reconciler re-applies the done copy and undoes the reopen."""
    # Arrange
    store = _store([_card(status="done")])
    # Act
    reopen_task(store, "a", by="tester")
    # Assert
    assert _activity(store, "a") != ANCIENT


def test_restore_task_advances_last_activity():
    """`delete_task` stamps when it tombstones, so a restore replaying the
    pre-delete snapshot verbatim would be OLDER than the tombstone it
    reverses — an Undo that a reconciler undoes."""
    # Arrange
    store = _store([_card()])
    removed = delete_task(store, "a")["removed"]
    # Act
    restore_task(store, removed)
    # Assert
    assert _activity(store, "a") != ANCIENT


def test_restore_task_does_not_mutate_the_callers_undo_payload():
    """The Undo payload must stay replayable, so the stamp goes on the COPY
    that is written, never on the dict the caller still holds."""
    # Arrange
    store = _store([_card()])
    removed = delete_task(store, "a")["removed"]
    before = removed.get("last_activity")
    # Act
    restore_task(store, removed)
    # Assert
    assert removed.get("last_activity") == before


def test_delete_task_clears_the_blocker_it_tombstones():
    """A `cancelled` card that still names an unresolved gate is incoherent, and
    the validator refuses the WHOLE document over it — so deleting any blocked
    card that named its gate failed outright. `complete_task` learned this rule
    on 2026-08-01; `delete_task` had not.

    Asserting on the STORED row, not on the returned payload: `removed` is the
    pre-tombstone snapshot and carries the blocker either way. Reading the row
    back also proves the write happened at all — before this fix
    `_validate_tasks` rejected the document and nothing was persisted.
    """
    # Arrange
    store = _store([_card(blocker="operator-decision")])
    # Act
    delete_task(store, "a")
    # Assert
    assert "blocker" not in next(t for t in load_tasks(store) if t["id"] == "a")


def test_set_edge_advances_last_activity_on_the_source():
    # Arrange
    store = _store([_card(), _card(id="b", title="B")])
    # Act
    set_edge(store, "add", "depends_on", "a", "b")
    # Assert
    assert _activity(store, "a") != ANCIENT


def test_set_edge_advances_last_activity_on_the_awaited_card():
    """Adding the edge subscribes the waiter's owner to the awaited card, so
    the awaited card changed too — and it is the one whose completion fires
    the notification."""
    # Arrange
    store = _store([_card(agent="owner"), _card(id="b", title="B")])
    # Act
    set_edge(store, "add", "depends_on", "a", "b")
    # Assert
    assert _activity(store, "b") != ANCIENT


def test_set_edge_leaves_last_activity_alone_on_a_no_op_re_add():
    """PRECISION GUARD, the mirror-image failure: stamping a card that did not
    change ages it for nothing, which is the same lie pointing the other way."""
    # Arrange
    store = _store([_card(depends_on=["b"]), _card(id="b", title="B")])
    unchanged = _activity(store, "a")
    # Act
    set_edge(store, "add", "depends_on", "a", "b")
    # Assert
    assert _activity(store, "a") == unchanged


# EOF
