#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crash-safe writer guarantees on `_model.save_tasks`.

Pins the safety contract the lead requested in a2a `d5809cd3` after
recovering the 2026-06-13 corruption episode (canonical
`~/.scitex/todo/tasks.yaml` ended mid-string at line ~2784):

1. Pre-write validation raises before any disk write.
2. A failed pre-write leaves no canonical file behind.
3. A SIGKILL DURING the dump leaves the canonical file UNCHANGED
   (atomic-rename guarantee — POSIX `os.replace` only fires after
   the tmp is fully written + post-dump-reparsed + count-checked).
4. The happy-path round-trips through load_tasks.

No mocks (STX-NM / PA-306). The SIGKILL test uses a real subprocess
that calls save_tasks with a giant payload then kills itself mid-flow,
mirroring the existing
`test__store.py::test_two_concurrent_writers_serialize_via_flock`
no-mocks subprocess pattern. AAA, one assertion per test.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scitex_cards._model import (
    TaskValidationError,
    load_tasks,
    save_tasks,
)

# === fixtures ===============================================================


@pytest.fixture()
def good_store(tmp_path: Path) -> Path:
    """A pre-existing valid store with two tasks."""
    store = tmp_path / "tasks.yaml"
    save_tasks(
        [
            {"id": "t-a", "title": "task a", "status": "pending"},
            {"id": "t-b", "title": "task b", "status": "pending"},
        ],
        store,
    )
    return store


# === 1. Pre-write validation raises BEFORE touching disk ====================


def test_invalid_in_memory_structure_raises_before_write(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    # Assert
    with pytest.raises(TaskValidationError):
        save_tasks(bad, store)


def test_invalid_in_memory_does_not_create_canonical_file(tmp_path: Path):
    # Arrange — store didn't exist; a failed save must NOT create it.
    store = tmp_path / "tasks.yaml"
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    try:
        save_tasks(bad, store)
    except TaskValidationError:
        pass

    # Assert
    assert not store.exists()


def test_invalid_in_memory_leaves_existing_canonical_unchanged(good_store: Path):
    # Arrange — capture the existing valid canonical bytes.
    before = good_store.read_bytes()
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    try:
        save_tasks(bad, good_store)
    except TaskValidationError:
        pass

    # Assert — bytes are exactly the original.
    assert good_store.read_bytes() == before


# === 2. SIGKILL mid-write leaves canonical untouched (atomic-rename) ========


_KILL_MID_WRITE_SCRIPT = textwrap.dedent(
    """
    import os, signal, sys, time
    sys.path.insert(0, sys.argv[2])
    from scitex_cards._model import save_tasks
    # Schedule a SIGKILL on ourselves after a tiny delay — should hit DURING
    # the YAML dump for a payload this large.
    pid = os.getpid()
    if os.fork() == 0:
        time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os._exit(0)
    # Build a beefy payload — 5000 tasks with a long note each so the dump
    # takes well over 50ms; the killer fires mid-stream.
    huge = [
        {{"id": f"t-{{i}}", "title": "X" * 200, "status": "pending",
          "note": "Y" * 1000}}
        for i in range(5000)
    ]
    save_tasks(huge, sys.argv[1])
    """
)


def test_sigkill_mid_dump_leaves_canonical_unchanged(good_store: Path):
    # Arrange — snapshot the canonical bytes BEFORE we crash-write.
    before = good_store.read_bytes()
    script = good_store.parent / "killer.py"
    script.write_text(_KILL_MID_WRITE_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")

    # Act — run the subprocess; expect a non-zero exit (SIGKILL on the
    # parent process). subprocess.run returncode will be negative.
    result = subprocess.run(
        [sys.executable, str(script), str(good_store), src],
        capture_output=True,
        timeout=10,
    )

    # Assert — canonical file is byte-for-byte the original. The atomic-
    # rename guarantee means os.replace never fired (we got killed before
    # reaching it), so the canonical bytes are untouched.
    assert good_store.read_bytes() == before


def test_sigkill_mid_dump_does_not_leave_stale_tmp_in_canonical_slot(good_store: Path):
    # Arrange
    script = good_store.parent / "killer.py"
    script.write_text(_KILL_MID_WRITE_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")

    # Act
    subprocess.run(
        [sys.executable, str(script), str(good_store), src],
        capture_output=True,
        timeout=10,
    )

    # Assert — the canonical path still loads cleanly. A truncated /
    # half-written canonical file would raise here.
    tasks = load_tasks(good_store)
    assert len(tasks) == 2  # the original two tasks


# === 3. Happy path still works (no regression on the post-dump-validate) ===


def test_clean_save_round_trips_through_load(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks_in = [
        {"id": "t-a", "title": "task a", "status": "pending"},
        {"id": "t-b", "title": "task b", "status": "done"},
    ]

    # Act
    save_tasks(tasks_in, store)
    loaded = load_tasks(store)

    # Assert
    assert [t["id"] for t in loaded] == ["t-a", "t-b"]


def test_clean_save_leaves_no_stale_tmp_sidecar(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tmp = store.parent / f".{store.name}.tmp"

    # Act
    save_tasks([{"id": "t-only", "title": "only", "status": "pending"}], store)

    # Assert — successful save deletes the tmp via os.replace; no sidecar.
    assert not tmp.exists()


# === 4. Non-`tasks` top-level sections survive the fast write ===============
#
# The fast write (safe dump, replacing the ruamel round-trip) must still
# LOAD the existing doc to carry through EVERY non-`tasks` top-level key —
# notably the `users:` registry and the `inboxes:` section the live store
# carries. Replacing only `doc["tasks"]` and dropping `users`/`inboxes`
# would silently destroy the user registry on the next card mutation.


def _write_store_with_extra_sections(store: Path) -> None:
    """Write a store that has `tasks` PLUS `users` + a custom top-level key."""
    from scitex_cards._yaml import safe_dump

    doc = {
        "tasks": [
            {"id": "t-a", "title": "task a", "status": "pending"},
        ],
        "users": {
            "alice": {"telegram_id": 123, "display": "Alice"},
            "bob": {"telegram_id": 456, "display": "Bob"},
        },
        "inboxes": {"default": ["t-a"]},
    }
    with store.open("w", encoding="utf-8") as handle:
        safe_dump(doc, handle)


def test_save_preserves_users_section(tmp_path: Path):
    from scitex_cards._yaml import safe_load

    # Arrange — a store with a populated `users:` registry.
    store = tmp_path / "tasks.yaml"
    _write_store_with_extra_sections(store)
    tasks = load_tasks(store)

    # Act — mutate + save via the write path under test.
    tasks[0]["status"] = "done"
    save_tasks(tasks, store)

    # Assert — the `users:` registry is intact byte-for-value.
    with store.open(encoding="utf-8") as handle:
        after = safe_load(handle)
    assert after["users"] == {
        "alice": {"telegram_id": 123, "display": "Alice"},
        "bob": {"telegram_id": 456, "display": "Bob"},
    }


def test_save_preserves_all_top_level_keys(tmp_path: Path):
    from scitex_cards._yaml import safe_load

    # Arrange
    store = tmp_path / "tasks.yaml"
    _write_store_with_extra_sections(store)
    tasks = load_tasks(store)

    # Act
    tasks[0]["title"] = "task a (edited)"
    save_tasks(tasks, store)

    # Assert — every top-level section survives (order + membership).
    with store.open(encoding="utf-8") as handle:
        after = safe_load(handle)
    assert list(after.keys()) == ["tasks", "users", "inboxes"]


def test_save_preserves_custom_inboxes_section_value(tmp_path: Path):
    from scitex_cards._yaml import safe_load

    # Arrange
    store = tmp_path / "tasks.yaml"
    _write_store_with_extra_sections(store)
    tasks = load_tasks(store)

    # Act
    save_tasks(tasks, store)

    # Assert — the non-`tasks`, non-`users` section is preserved verbatim.
    with store.open(encoding="utf-8") as handle:
        after = safe_load(handle)
    assert after["inboxes"] == {"default": ["t-a"]}


# === 5. Tasks round-trip exactly (load -> save -> load equals) ==============


def test_save_round_trips_tasks_exactly(tmp_path: Path):
    # Arrange — a store with rich task fields.
    store = tmp_path / "tasks.yaml"
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
    save_tasks(tasks_in, store)

    # Act — load, then save unchanged, then load again.
    loaded_once = load_tasks(store)
    save_tasks(loaded_once, store)
    loaded_twice = load_tasks(store)

    # Assert — load -> save -> load is a fixed point (values equal).
    assert loaded_twice == loaded_once


# === 6. Crash-safe path uses tmp + os.replace (no partial canonical) ========


def _append_task_and_save(store: Path) -> None:
    """Seed a multi-section store, append one card, and save it back."""
    _write_store_with_extra_sections(store)
    tasks = load_tasks(store)
    tasks.append({"id": "t-new", "title": "new", "status": "pending"})
    save_tasks(tasks, store)


def test_append_save_leaves_no_tmp_sidecar_behind(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tmp = store.parent / f".{store.name}.tmp"

    # Act
    _append_task_and_save(store)

    # Assert — os.replace consumed the tmp; nothing stale remains.
    assert not tmp.exists()


def test_append_save_writes_a_complete_task_list(tmp_path: Path):
    from scitex_cards._yaml import safe_load

    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _append_task_and_save(store)

    # Assert — a partial write would fail to parse or lose the appended card.
    with store.open(encoding="utf-8") as handle:
        after = safe_load(handle)
    assert [t["id"] for t in after["tasks"]] == ["t-a", "t-new"]


def test_append_save_keeps_the_users_section_present(tmp_path: Path):
    from scitex_cards._yaml import safe_load

    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _append_task_and_save(store)

    # Assert — a partial write would miss the non-`tasks` sections.
    with store.open(encoding="utf-8") as handle:
        after = safe_load(handle)
    assert "users" in after
