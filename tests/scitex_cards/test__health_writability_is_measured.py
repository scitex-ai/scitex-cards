#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` must MEASURE writability, never assert it.

`_verify_db_store` opens the canonical database `mode=ro` — a read-only
connection learns nothing about writing — and then reported the store
"readable, writable". That word was a hardcoded literal in an f-string, so it
could never be false: "a gate that cannot fail is not a gate ... the same as
deleting it, except worse: the config still lists it and everyone believes it
is working" (constitution §2).

It is not hypothetical. On 2026-07-28 every card CREATE refused for any agent
without `$SCITEX_CARDS_DB` while `health` reported that same store readable AND
writable — the check that should have caught the outage was the reason it stayed
invisible. Reported by scitex-ui.

These tests pin the claim to reality: if the store cannot be written, the check
must FAIL and say so.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from scitex_cards._health import _verify_db_store

_ROOT_BYPASSES_PERMISSIONS = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root bypasses permission bits, so the probe cannot fail",
)


def _make_store(path):
    """A real, schema-complete store with one countable row."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO tasks (id) VALUES ('a')")
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def writable_store(tmp_path):
    """The ordinary healthy case: a store this process can write."""
    return _make_store(tmp_path / "cards.db")


@pytest.fixture
def read_only_store(tmp_path):
    """A store that reads and parses fine but cannot be written."""
    db = _make_store(tmp_path / "cards.db")
    db.chmod(0o444)
    return db


@pytest.fixture
def store_in_read_only_dir(tmp_path):
    """A WRITABLE file inside a read-only directory.

    A file-backed engine creates `-wal` / `-journal` SIBLINGS, so the directory
    matters: a
    file-permission check alone would report a healthy store that cannot
    actually take a card.
    """
    store_dir = tmp_path / "cards"
    store_dir.mkdir()
    db = _make_store(store_dir / "cards.db")
    store_dir.chmod(0o555)
    yield db
    # Restore so tmp_path cleanup can remove the tree.
    store_dir.chmod(0o755)


def test_a_writable_store_passes_the_check(writable_store):
    # Arrange — a store this process can write.
    # Act
    result = _verify_db_store(writable_store)

    # Assert
    assert result["ok"] is True


def test_a_writable_store_is_described_as_writable(writable_store):
    # Arrange — a store this process can write.
    # Act
    result = _verify_db_store(writable_store)

    # Assert
    assert "writable" in result["detail"]


@_ROOT_BYPASSES_PERMISSIONS
def test_a_read_only_store_fails_the_check(read_only_store):
    # Arrange — readable and parseable, but unwritable.
    # Act
    result = _verify_db_store(read_only_store)

    # Assert — the whole point: this must FAIL, not claim "writable".
    assert result["ok"] is False


@_ROOT_BYPASSES_PERMISSIONS
def test_a_read_only_store_is_described_as_not_writable(read_only_store):
    # Arrange — readable and parseable, but unwritable.
    # Act
    result = _verify_db_store(read_only_store)

    # Assert
    assert "not writable" in result["detail"].lower()


@_ROOT_BYPASSES_PERMISSIONS
def test_a_read_only_store_hint_names_the_offending_path(read_only_store):
    # Arrange — readable and parseable, but unwritable.
    # Act
    result = _verify_db_store(read_only_store)

    # Assert — an error that only states what broke is half-written.
    assert str(read_only_store) in (result["hint"] or "")


@_ROOT_BYPASSES_PERMISSIONS
def test_a_store_in_a_read_only_directory_fails_the_check(store_in_read_only_dir):
    # Arrange — writable file, unwritable directory: writes still fail.
    # Act
    result = _verify_db_store(store_in_read_only_dir)

    # Assert
    assert result["ok"] is False


@_ROOT_BYPASSES_PERMISSIONS
def test_a_store_in_a_read_only_directory_names_the_directory(store_in_read_only_dir):
    # Arrange — writable file, unwritable directory: writes still fail.
    store_dir = store_in_read_only_dir.parent

    # Act
    result = _verify_db_store(store_in_read_only_dir)

    # Assert
    assert str(store_dir) in f"{result['detail']}{result['hint'] or ''}"
