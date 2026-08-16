#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every ``comments[]`` element is BORN with a globally-unique id.

Card ``cards-comments-need-globally-unique-ids-before-append-20260814``, half 1
(minting only — the backfill of id-less historical comments is half 2 and is
deliberately not touched here).

``comments[]`` can only be declared under ``MergeRule.APPEND`` for multi-host
replication (ADR-0018 D2) if its elements carry their own ids: APPEND unions by
element id, and an element with no id can only be matched by POSITION — exactly
what diverges when two hosts append at once.

THE FIRST TEST IS THE ONE THAT MATTERS. Twelve call sites append a comment
today; a test that enumerates those twelve stops guarding the moment someone
adds a thirteenth. So the guard reads the PACKAGE SOURCE instead and asserts
that
no comment-shaped dict literal anywhere escapes the one minting helper — a new
append site that forgets the id turns it red without anyone remembering to
extend a list. Its companion asserts the scanner still SEES those sites, so a
detector that quietly stops matching cannot make the guard vacuously green.

Real fixtures, no mocks (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards import _store
from scitex_cards._comment_ids import (
    COMMENT_ID_PREFIX,
    is_comment_id,
    new_comment_id,
    stamp_comment_id,
)
from scitex_cards._model import load_doc
from scitex_cards._store_wip import enforce_wip_gate

# === Source scan: the guard that catches the NEXT append site ==============

#: The package tree actually imported — never a hard-coded relative path, so
#: the scan follows whichever source the suite is really running against.
_PACKAGE_ROOT = Path(scitex_cards.__file__).resolve().parent

#: The keys that make a dict literal a ``comments[]`` ELEMENT rather than one
#: of the many other mappings in the package. Every append site writes all
#: three; the bus/event payloads next door carry ``body`` / ``created_at``
#: instead and are correctly ignored.
_COMMENT_ENTRY_KEYS = frozenset({"author", "ts", "text"})

#: The ONE helper allowed to mint the id (``scitex_cards._comment_ids``).
_STAMP_HELPER = "stamp_comment_id"

#: How many comment-entry literals the scan is known to reach. A floor, not an
#: equality: a legitimate new append site should not have to edit this file,
#: but a detector that stops matching must not pass silently either.
_KNOWN_APPEND_SITES = 12


def _called_name(func: ast.expr) -> str | None:
    """The bare name of the function a Call node invokes, module path aside."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _string_keys(node: ast.Dict) -> set[str]:
    """The literal string keys of a dict display (``**spread`` keys are None)."""
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _stamped_argument_ids(tree: ast.Module) -> set[int]:
    """``id()`` of every node passed directly to the minting helper."""
    wrapped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node.func) == _STAMP_HELPER:
            wrapped.update(id(arg) for arg in node.args)
    return wrapped


def _comment_entry_sites() -> tuple[list[str], list[str]]:
    """Scan the package: ``(all_sites, unstamped_sites)`` as ``file:line``."""
    all_sites: list[str] = []
    unstamped: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        wrapped = _stamped_argument_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            if not _COMMENT_ENTRY_KEYS <= _string_keys(node):
                continue
            where = f"{path.relative_to(_PACKAGE_ROOT)}:{node.lineno}"
            all_sites.append(where)
            if id(node) not in wrapped:
                unstamped.append(where)
    return all_sites, unstamped


def test_no_comment_entry_is_built_without_the_minting_helper():
    # Arrange
    _, unstamped = _comment_entry_sites()
    # Act
    offenders = sorted(unstamped)
    # Assert — each entry is a `<module>:<line>` that must wrap its dict
    # literal in `stamp_comment_id(...)`.
    assert offenders == []


def test_the_source_scan_still_reaches_the_known_append_sites():
    # Arrange
    all_sites, _ = _comment_entry_sites()
    # Act
    found = len(all_sites)
    # Assert — a detector that matched nothing would make the guard above
    # pass while guarding nothing at all.
    assert found >= _KNOWN_APPEND_SITES


# === The minted token itself ==============================================


def test_a_minted_comment_id_has_the_house_shape():
    # Arrange
    prefix_and_hex = r"c_ + 12 lowercase hex, as for u_ / n_ / m_ / dmr_"
    # Act
    minted = new_comment_id()
    # Assert
    assert is_comment_id(minted), f"{minted!r} is not {prefix_and_hex}"


def test_a_minted_comment_id_carries_the_established_prefix():
    # Arrange
    expected = COMMENT_ID_PREFIX
    # Act
    minted = new_comment_id()
    # Assert
    assert minted.startswith(expected)


def test_many_minted_comment_ids_are_all_distinct():
    # Arrange — a counter would collide across hosts silently, so the token
    # must be random; 20k draws is a cheap smoke test of that randomness.
    draws = 20_000
    # Act
    minted = {new_comment_id() for _ in range(draws)}
    # Assert
    assert len(minted) == draws


# === Stamping is additive, and never overwrites ===========================


def test_stamping_gives_an_id_to_an_entry_that_has_none():
    # Arrange
    entry = {"author": "agent-a", "ts": "2026-08-14T00:00:00Z", "text": "hi"}
    # Act
    stamp_comment_id(entry)
    # Assert
    assert is_comment_id(entry["id"])


def test_stamping_preserves_an_id_the_entry_already_carries():
    # Arrange — the live store is MIXED: 8,359 comments already carry a `c_`
    # id and 1,010 carry none (measured 2026-08-14).
    entry = {"id": "c_0123456789ab", "author": "a", "ts": "t", "text": "hi"}
    # Act
    stamp_comment_id(entry)
    # Assert
    assert entry["id"] == "c_0123456789ab"


def test_stamping_preserves_a_foreign_shaped_id_verbatim():
    # Arrange — an id is an ADDRESS. Rewriting one written by an older or
    # foreign writer orphans whatever already refers to that comment.
    entry = {"id": "legacy-42", "author": "a", "ts": "t", "text": "hi"}
    # Act
    stamp_comment_id(entry)
    # Assert
    assert entry["id"] == "legacy-42"


def test_stamping_leaves_every_other_field_untouched():
    # Arrange
    entry = {"author": "agent-a", "ts": "2026-08-14T00:00:00Z", "text": "hi"}
    before = dict(entry)
    # Act
    stamped = stamp_comment_id(entry)
    # Assert
    assert {k: v for k, v in stamped.items() if k != "id"} == before


# === The real store verbs, end to end =====================================


def _card(store: str, task_id: str) -> dict:
    """The raw stored card — ``load_doc`` so a TOMBSTONED row is visible too."""
    doc = load_doc(store, validate=False)
    return next(t for t in doc["tasks"] if t["id"] == task_id)


def _run_comment(store: str, task_id: str) -> None:
    _store.comment_task(store, task_id, text="a remark")


def _run_resolve(store: str, task_id: str) -> None:
    _store.resolve_task(store, task_id=task_id)


def _run_reopen(store: str, task_id: str) -> None:
    _store.reopen_task(store, task_id=task_id)


def _run_delete(store: str, task_id: str) -> None:
    _store.delete_task(store, task_id=task_id)


def _run_rescore(store: str, task_id: str) -> None:
    _store.rescore_task(store, task_id=task_id, urgency=3, importance=4)


def _run_reassign(store: str, task_id: str) -> None:
    _store.reassign_task(store, task_id=task_id, new_owner="agent-successor")


def _run_reassign_all(store: str, task_id: str) -> None:
    card = _card(store, task_id)
    _store.reassign_all(
        store,
        old_owner=card.get("agent") or card.get("assignee"),
        new_owner="agent-successor",
    )


@pytest.mark.parametrize(
    "verb",
    [
        _run_comment,
        _run_resolve,
        _run_reopen,
        _run_delete,
        _run_rescore,
        _run_reassign,
        _run_reassign_all,
    ],
    ids=[
        "comment_task",
        "resolve_task",
        "reopen_task",
        "delete_task",
        "rescore_task",
        "reassign_task",
        "reassign_all",
    ],
)
def test_every_store_verb_stamps_the_comment_it_appends(verb):
    # Arrange
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    task_id = f"card-{verb.__name__}"
    _store.add_task(store, id=task_id, title="A", assignee="agent:test-suite")
    # Act
    verb(store, task_id)
    # Assert
    appended = _card(store, task_id)["comments"]
    assert [c for c in appended if not is_comment_id(c.get("id"))] == []


def test_the_wip_override_audit_stamp_carries_a_comment_id(env):
    # Arrange — limit 1 makes the refuse threshold 2, so two in-flight cards
    # push the third over the cap; priority 0 admits it via the emergency
    # exemption, which is the branch that writes the audit comment.
    env.set("SCITEX_CARDS_WIP_LIMIT", "1")
    in_flight = [
        {"id": "w1", "title": "W1", "agent": "agent-busy", "status": "in_progress"},
        {"id": "w2", "title": "W2", "agent": "agent-busy", "status": "in_progress"},
    ]
    admitted = {
        "id": "w3",
        "title": "W3",
        "agent": "agent-busy",
        "status": "in_progress",
        "priority": 0,
    }
    # Act
    enforce_wip_gate(admitted, in_flight, now_iso="2026-08-14T00:00:00Z")
    # Assert
    assert is_comment_id(admitted["comments"][0]["id"])

# EOF
