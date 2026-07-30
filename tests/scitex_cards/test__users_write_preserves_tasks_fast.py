#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A liveness/users write must preserve the tasks payload — via the FAST path.

WHY THIS FILE EXISTS (2026-07-15): `_users/_store.py::_save_users_unlocked` is the
liveness-HEARTBEAT write — every `update_task`/`add_task`/`comment` by a registered
agent stamps `last_seen` and lands here. It used a ruamel ROUND-TRIP loader+dumper
to preserve the `tasks:` list's comments while replacing only `users:`. But the
TASK write path (`_store_write._save_doc_unlocked`) had already abandoned comment
preservation and writes with the fast safe loader/dumper — so this path was
ruamel-round-tripping the ENTIRE 6.5 MB store to preserve comments the task path
already dropped.

MEASURED cost of that: 46 s/write on 0.9.4, 171 s/write on 0.13.x, on the live
store, for any registered agent. It is the root cause of the board's per-write
latency. The fix swaps ruamel for the fast safe path.

These tests pin the INVARIANT the ruamel path actually provided — the tasks
payload survives a users-only write — so the fast path cannot silently drop it,
AND pin that the slow ruamel round-trip does not creep back in.
"""

import inspect

import pytest
import yaml

from scitex_cards._users import _store as users_store
from scitex_cards._users._model import UserValidationError
from scitex_cards._users._store import _save_users_unlocked

#: The user row a heartbeat write lands. Copied at every call site — the
#: writer must never see a shared mutable instance.
USER_ROW = {
    "id": "u_a",
    "kind": "agent",
    "names": ["alice"],
    "last_seen": "2026-07-15T00:00:00Z",
}

#: A row missing BOTH `id` and `names`: it fails `validate_user` before a
#: single byte is written. The three crash-safety tests below all drive this
#: same failure and each pins one consequence of it.
INVALID_ROW = {"kind": "agent"}


def _read(path):
    return yaml.safe_load(path.read_text())


def _store_with_tasks(tmp_path, task_count: int):
    """A real store carrying `task_count` task rows and an empty users list."""
    store = tmp_path / "tasks.yaml"
    tasks = [
        {"id": f"t{i}", "title": f"task {i}", "status": "deferred"}
        for i in range(task_count)
    ]
    store.write_text(yaml.safe_dump({"tasks": tasks, "users": []}))
    return store


def _attempt_invalid_users_write(store) -> None:
    """Drive the validation failure, swallowing the expected raise."""
    with pytest.raises(UserValidationError):
        _save_users_unlocked([dict(INVALID_ROW)], store)


def test_users_write_preserves_the_entire_tasks_payload(tmp_path):
    """Replacing users: must leave every task row untouched."""
    # Arrange
    store = _store_with_tasks(tmp_path, 50)
    expected_ids = [f"t{i}" for i in range(50)]
    # Act
    _save_users_unlocked([dict(USER_ROW)], store)
    # Assert
    doc = _read(store)
    assert [t["id"] for t in doc["tasks"]] == expected_ids, "task payload changed"


def test_users_write_replaces_the_users_section(tmp_path):
    # Arrange
    store = _store_with_tasks(tmp_path, 50)
    # Act
    _save_users_unlocked([dict(USER_ROW)], store)
    # Assert
    doc = _read(store)
    assert [u["id"] for u in doc["users"]] == ["u_a"]


def test_users_first_write_seeds_an_empty_tasks_list(tmp_path):
    """A users-FIRST write (no tasks yet) must leave a valid `tasks: []`.

    `_model.load_tasks` hard-requires a top-level `tasks:` list; a file carrying
    only `users:` would make a later `add_task` fail-loud.
    """
    # Arrange
    store = tmp_path / "tasks.yaml"
    store.write_text(yaml.safe_dump({"users": []}))
    # Act
    _save_users_unlocked([dict(USER_ROW)], store)
    # Assert
    doc = _read(store)
    assert doc.get("tasks") == []


def test_users_first_write_still_persists_the_user(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    store.write_text(yaml.safe_dump({"users": []}))
    # Act
    _save_users_unlocked([dict(USER_ROW)], store)
    # Assert
    doc = _read(store)
    assert [u["id"] for u in doc["users"]] == ["u_a"]


def test_an_invalid_user_row_is_refused_outright(tmp_path):
    """Validation aborts BEFORE any write — the premise the two tests below
    depend on."""
    # Arrange
    store = _store_with_tasks(tmp_path, 1)
    # Act
    # Assert
    with pytest.raises(UserValidationError):
        _save_users_unlocked([dict(INVALID_ROW)], store)


def test_a_failed_users_write_leaves_the_canonical_file_intact(tmp_path):
    """A mid-write failure must leave the canonical file untouched."""
    # Arrange
    store = _store_with_tasks(tmp_path, 1)
    original = _read(store)
    # Act
    _attempt_invalid_users_write(store)
    # Assert
    assert _read(store) == original, "canonical file was mutated on a failed write"


def test_a_failed_users_write_leaves_no_tmp_sidecar(tmp_path):
    """...and no .tmp litter next to it."""
    # Arrange
    store = _store_with_tasks(tmp_path, 1)
    # Act
    _attempt_invalid_users_write(store)
    # Assert
    assert not (store.parent / f".{store.name}.tmp").exists(), "left a .tmp turd"


def test_the_slow_ruamel_round_trip_did_not_creep_back():
    """PIN THE FIX: the heartbeat write must NOT ruamel round-trip the store.

    A ruamel round-trip of the whole tasks blob is what cost 46–171 s/write. This
    asserts the function's source does not reintroduce it. Implementation-coupled
    on purpose — the performance cliff is invisible to a behavioural test on a
    small fixture, so it must be pinned structurally.
    """
    # Arrange
    banned = ("from ruamel", "YAML()")
    # Act
    src = inspect.getsource(_save_users_unlocked)
    # Assert
    assert not [token for token in banned if token in src], (
        "the users/heartbeat write reintroduced a ruamel round-trip — that cost "
        "46–171 s PER card write on the live store. Use the fast safe path."
    )


def test_the_heartbeat_write_uses_the_fast_safe_path():
    """The other half of the fix: it must round-trip through safe_load/dump."""
    # Arrange
    required = ("safe_dump", "safe_load")
    # Act
    src = inspect.getsource(_save_users_unlocked)
    # Assert
    assert [token for token in required if token in src] == list(required)


def test_module_still_exposes_the_helper():
    """Guard the import surface used by the liveness heartbeat."""
    # Arrange
    name = "_save_users_unlocked"
    # Act
    exposed = hasattr(users_store, name)
    # Assert
    assert exposed
