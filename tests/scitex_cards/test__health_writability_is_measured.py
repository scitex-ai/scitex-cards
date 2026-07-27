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


def _make_store(path):
    """A real, schema-complete SQLite store with one countable row."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO tasks (id) VALUES ('a')")
        conn.commit()
    finally:
        conn.close()
    return path


def test_a_writable_store_is_reported_writable(tmp_path):
    # ARRANGE — the ordinary healthy case.
    db = _make_store(tmp_path / "cards.db")

    # ACT
    result = _verify_db_store(db)

    # ASSERT
    assert result["ok"] is True
    assert "writable" in result["detail"]


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root bypasses permission bits, so the probe cannot fail"
)
def test_a_read_only_store_is_reported_not_writable(tmp_path):
    # ARRANGE — the store is readable and parses, but cannot be written.
    db = _make_store(tmp_path / "cards.db")
    db.chmod(0o444)

    # ACT
    result = _verify_db_store(db)

    # ASSERT — the whole point: this must FAIL, not claim "writable".
    assert result["ok"] is False


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root bypasses permission bits, so the probe cannot fail"
)
def test_a_read_only_store_says_what_to_do_about_it(tmp_path):
    # ARRANGE
    db = _make_store(tmp_path / "cards.db")
    db.chmod(0o444)

    # ACT
    result = _verify_db_store(db)

    # ASSERT — an error that only states what broke is half-written.
    assert "not writable" in result["detail"].lower()
    assert result["hint"] and str(db) in result["hint"]


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root bypasses permission bits, so the probe cannot fail"
)
def test_a_writable_store_in_a_read_only_directory_is_reported_not_writable(tmp_path):
    """SQLite writes `-wal` / `-journal` SIBLINGS, so the directory matters.

    A writable file in a read-only directory still fails every write — the
    file-permission check alone would report a healthy store that cannot
    actually take a card.
    """
    # ARRANGE
    store_dir = tmp_path / "cards"
    store_dir.mkdir()
    db = _make_store(store_dir / "cards.db")
    store_dir.chmod(0o555)
    try:
        # ACT
        result = _verify_db_store(db)

        # ASSERT
        assert result["ok"] is False
        assert str(store_dir) in result["detail"] or str(store_dir) in (
            result["hint"] or ""
        )
    finally:
        # Restore so tmp_path cleanup can remove the tree.
        store_dir.chmod(0o755)
