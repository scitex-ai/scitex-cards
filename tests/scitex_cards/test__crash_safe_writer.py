#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crash-safe writer guarantees on `_model.save_tasks` — SQLite store.

Pins the safety contract the lead requested in a2a `d5809cd3` after
recovering the 2026-06-13 corruption episode. The store is SQLite now, so
the atomicity boundary is the SQLite TRANSACTION rather than the old YAML
tmp + `os.replace` promotion — but the CONTRACT the caller relies on is
unchanged:

1. Pre-write validation raises before anything is committed.
2. A failed pre-write leaves the store's prior state intact (nothing written).
3. A SIGKILL DURING the write leaves the store UNCHANGED and loadable — the
   killed transaction never commits, so the store is neither corrupted nor
   partially applied. `mirror_doc_incremental` commits ONCE at the end, so the
   single commit is the atomicity guarantee `os.replace` used to provide.
4. The happy path round-trips through load_tasks.
5. Non-`tasks` sections (the `users:` registry, inbox recipients) survive a
   card mutation — the write LOADS the whole doc and carries them through, so
   dropping them would silently destroy the user registry.

No mocks (STX-NM / PA-306). The SIGKILL test uses a real subprocess that calls
save_tasks with a giant payload then kills itself mid-write, mirroring the
existing no-mocks subprocess pattern in test__store.py.

STORE-PATH RULE: reads ignore the path argument and read the canonical DB, but
a WRITE stamps the DB with the path passed, and the next read refuses a DB
stamped for a different store. So every call here passes the PINNED store
identity (`SCITEX_CARDS_TASKS_YAML_SHARED`), never a test-local tmp path, and
prior rows are SEEDED into the canonical DB (`SCITEX_CARDS_DB`) via
`seed_db_from_doc`. Under SQLite `users` round-trips as a LIST of records and
`inboxes` as a `{recipient: [notification]}` map (the DB table shapes) — not
the old YAML dict-of-users shape.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import seed_db_from_doc

from scitex_cards._model import (
    TaskValidationError,
    load_doc,
    load_tasks,
    save_tasks,
)


def _store() -> str:
    """The PINNED store identity path — what save/load are addressed to."""
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


# === fixtures ===============================================================


@pytest.fixture()
def good_store() -> Path:
    """A pre-existing valid store (in the canonical DB) with two tasks."""
    seed_db_from_doc(
        {
            "tasks": [
                {"id": "t-a", "title": "task a", "status": "pending"},
                {"id": "t-b", "title": "task b", "status": "pending"},
            ]
        },
        os.environ["SCITEX_CARDS_DB"],
    )
    return Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])


# === 1. Pre-write validation raises BEFORE committing anything ==============


def test_invalid_in_memory_structure_raises_before_write():
    # Arrange
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    # Assert
    with pytest.raises(TaskValidationError):
        save_tasks(bad, _store())


def test_invalid_in_memory_does_not_persist_anything():
    # Arrange — the bootstrapped store is empty; a failed save must not write.
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    try:
        save_tasks(bad, _store())
    except TaskValidationError:
        pass

    # Assert — nothing was committed; the store is still empty.
    assert load_tasks(_store()) == []


def test_invalid_in_memory_leaves_existing_store_unchanged(good_store: Path):
    # Arrange
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    try:
        save_tasks(bad, good_store)
    except TaskValidationError:
        pass

    # Assert — the existing store is unchanged: still the good two tasks.
    assert [t["id"] for t in load_tasks(good_store)] == ["t-a", "t-b"]


# === 2. SIGKILL mid-write leaves the store consistent (txn atomicity) =======


_KILL_MID_WRITE_SCRIPT = textwrap.dedent(
    """
    import os, signal, sys, time
    sys.path.insert(0, sys.argv[2])
    from scitex_cards._model import save_tasks
    # Schedule a SIGKILL on ourselves shortly after we start — it lands DURING
    # the SQLite write for a payload this large, before the single commit that
    # makes the write durable.
    pid = os.getpid()
    if os.fork() == 0:
        time.sleep(0.02)
        os.kill(pid, signal.SIGKILL)
        os._exit(0)
    # Build a beefy payload so the write takes well over the kill delay; the
    # killer fires before the transaction commits.
    huge = [
        {"id": f"t-{i}", "title": "X" * 200, "status": "pending",
         "note": "Y" * 1000}
        for i in range(5000)
    ]
    save_tasks(huge, sys.argv[1])
    """
)


def test_sigkill_mid_write_leaves_store_unchanged(good_store: Path):
    # Arrange
    script = good_store.parent / "killer.py"
    script.write_text(_KILL_MID_WRITE_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")

    # Act — run the subprocess; it SIGKILLs itself mid-write.
    subprocess.run(
        [sys.executable, str(script), str(good_store), src],
        capture_output=True,
        timeout=30,
    )

    # Assert — the killed transaction never committed, so the store still holds
    # exactly the original two tasks (never a partial write).
    assert [t["id"] for t in load_tasks(good_store)] == ["t-a", "t-b"]


def test_sigkill_mid_write_leaves_store_loadable(good_store: Path):
    # Arrange
    script = good_store.parent / "killer.py"
    script.write_text(_KILL_MID_WRITE_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")

    # Act
    subprocess.run(
        [sys.executable, str(script), str(good_store), src],
        capture_output=True,
        timeout=30,
    )

    # Assert — the canonical store still loads cleanly. A corrupted / half-
    # written DB would raise here.
    tasks = load_tasks(good_store)
    assert len(tasks) == 2  # the original two tasks


# === 3. Happy path still round-trips ========================================


def test_clean_save_round_trips_through_load():
    # Arrange
    tasks_in = [
        {"id": "t-a", "title": "task a", "status": "pending"},
        {"id": "t-b", "title": "task b", "status": "done"},
    ]

    # Act
    save_tasks(tasks_in, _store())
    loaded = load_tasks(_store())

    # Assert
    assert [t["id"] for t in loaded] == ["t-a", "t-b"]


# === 4. Non-`tasks` top-level sections survive the write ====================
#
# The write path LOADS the existing doc to carry through EVERY non-`tasks`
# top-level section — notably the `users:` registry and the inbox recipients
# the live store carries. Replacing only `doc["tasks"]` and dropping the rest
# would silently destroy the user registry on the next card mutation.


def _seed_store_with_extra_sections() -> Path:
    """Seed the canonical DB with tasks PLUS a users registry and an inbox.

    Returns the PINNED store path (per the STORE-PATH RULE). Users are a LIST
    of records and inboxes a ``{recipient: [notification]}`` map — the shapes
    the DB stores and round-trips (the old YAML dict-of-users shape is gone).
    """
    seed_db_from_doc(
        {
            "tasks": [
                {"id": "t-a", "title": "task a", "status": "pending"},
            ],
            "users": [
                {
                    "id": "alice",
                    "kind": "agent",
                    "telegram_id": 123,
                    "display": "Alice",
                },
                {"id": "bob", "kind": "agent", "telegram_id": 456, "display": "Bob"},
            ],
            "inboxes": {
                "default": [
                    {
                        "id": "n1",
                        "event_type": "assigned",
                        "card_id": "t-a",
                        "body": "hi",
                        "actor": "bob",
                        "ts": "2026-01-01T00:00:00Z",
                        "seen": False,
                    }
                ]
            },
        },
        os.environ["SCITEX_CARDS_DB"],
    )
    return Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])


def test_save_preserves_users_section():
    # Arrange — a store with a populated `users:` registry.
    store = _seed_store_with_extra_sections()
    tasks = load_tasks(store)

    # Act — mutate + save via the write path under test.
    tasks[0]["status"] = "done"
    save_tasks(tasks, store)

    # Assert — the `users:` registry is intact.
    after = load_doc(store)
    assert after["users"] == [
        {"id": "alice", "kind": "agent", "telegram_id": 123, "display": "Alice"},
        {"id": "bob", "kind": "agent", "telegram_id": 456, "display": "Bob"},
    ]


def test_save_preserves_all_top_level_keys():
    # Arrange
    store = _seed_store_with_extra_sections()
    tasks = load_tasks(store)

    # Act
    tasks[0]["title"] = "task a (edited)"
    save_tasks(tasks, store)

    # Assert — every top-level section survives (membership + order).
    after = load_doc(store)
    assert list(after.keys()) == ["tasks", "users", "inboxes"]


def test_save_preserves_custom_inboxes_section_value():
    # Arrange
    store = _seed_store_with_extra_sections()
    tasks = load_tasks(store)

    # Act
    save_tasks(tasks, store)

    # Assert — the inbox section is preserved verbatim through the write.
    after = load_doc(store)
    assert after["inboxes"] == {
        "default": [
            {
                "id": "n1",
                "event_type": "assigned",
                "card_id": "t-a",
                "body": "hi",
                "actor": "bob",
                "ts": "2026-01-01T00:00:00Z",
                "seen": False,
            }
        ]
    }


# === 5. Tasks round-trip exactly (load -> save -> load equals) ==============


def test_save_round_trips_tasks_exactly():
    # Arrange — a store with rich task fields.
    tasks_in = [
        {
            "id": "t-a",
            "title": "task a",
            "status": "pending",
            "priority": 1,
            "note": "multi\nline\nnote",
            "tags": ["x", "y"],
        },
        {"id": "t-b", "title": "タスク b", "status": "done"},
    ]
    save_tasks(tasks_in, _store())

    # Act — load, then save unchanged, then load again.
    loaded_once = load_tasks(_store())
    save_tasks(loaded_once, _store())
    loaded_twice = load_tasks(_store())

    # Assert — load -> save -> load is a fixed point (values equal).
    assert loaded_twice == loaded_once


# === 6. Crash-safe write applies the WHOLE doc (no partial store) ===========


def _append_task_and_save() -> Path:
    """Seed a multi-section store, append one card, and save it back."""
    store = _seed_store_with_extra_sections()
    tasks = load_tasks(store)
    tasks.append({"id": "t-new", "title": "new", "status": "pending"})
    save_tasks(tasks, store)
    return store


def test_append_save_writes_a_complete_task_list():
    # Act
    store = _append_task_and_save()

    # Assert — a partial write would fail to load or lose the appended card.
    assert [t["id"] for t in load_tasks(store)] == ["t-a", "t-new"]


def test_append_save_keeps_the_users_section_present():
    # Act
    store = _append_task_and_save()

    # Assert — a partial write would miss the non-`tasks` sections.
    after = load_doc(store)
    assert "users" in after
