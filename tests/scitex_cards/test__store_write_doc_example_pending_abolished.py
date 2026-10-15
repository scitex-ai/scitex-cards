#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ``edit_tasks`` doc-example abolished-status gap.

``edit_tasks`` in ``_store_write.py`` is the sanctioned bulk-edit
primitive, and its docstring example is what a future migration author
copies first. It still taught the 2026-07-10 ``pending``-to-``deferred``
migration shape — matching the abolished ``pending`` status, which no
live store mints anymore, so a copied loop is a silent no-op. The
example now matches a LIVE status (unblocked ``deferred`` backlog rows
claimed to ``in_progress``) and the docstring names the abolition so
the history survives without staying a pattern.

No store, no PostgreSQL: source-text pins plus an in-memory run of the
taught predicate, so they hold in containers where the store-backed
suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from scitex_cards import _store_write


def _example_block() -> str:
    # Arrange helper, not a test: the docstring from the usage example
    # to its end, so the abolition note below the example is in scope
    # but the implementation prose above it is not.
    doc = _store_write.edit_tasks.__doc__ or ""
    return doc[doc.index(">>> with edit_tasks") :]


def test_example_mentions_no_abolished_status_literal():
    # Arrange
    block = _example_block()
    # Act
    holders = [line for line in block.splitlines() if '"pending"' in line]
    # Assert — a copied example must never match the abolished status.
    assert holders == []


def test_example_predicate_is_the_live_backlog_shape():
    # Arrange
    block = _example_block()
    # Act / Assert — the taught predicate claims unblocked backlog rows.
    assert 't.get("status") == "deferred"' in block
    assert 't["status"] = "in_progress"' in block


def test_example_body_promotes_only_unblocked_backlog_rows():
    # Arrange — the loop body the example teaches, run against memory rows.
    rows = [
        {"id": "a", "status": "deferred"},
        {"id": "b", "status": "deferred", "blocker": "operator-decision"},
        {"id": "c", "status": "blocked", "blocker": "operator-decision"},
        {"id": "d", "status": "done"},
        {"id": "legacy", "status": "pending"},
    ]
    # Act — same predicate the docstring example teaches:
    for t in rows:
        if t.get("status") == "deferred" and not t.get("blocker"):
            t["status"] = "in_progress"
    # Assert — only the unblocked backlog row moves; the legacy
    # abolished-status row is (correctly) not matched.
    assert {t["id"]: t["status"] for t in rows} == {
        "a": "in_progress",
        "b": "deferred",
        "c": "blocked",
        "d": "done",
        "legacy": "pending",
    }


def test_doc_names_the_abolition_so_history_survives():
    # Arrange
    doc = _store_write.edit_tasks.__doc__ or ""
    # Act
    flat = " ".join(doc.split())
    # Assert — the note keeps the date and the pointer to the enum.
    assert "abolished" in flat and "ABOLISHED_STATUSES" in flat


def test_detector_is_not_vacuous():
    # Arrange — the literal scan above must be capable of finding the
    # shape it looks for.
    fixture = '...     if t.get("status") == "pending":\n'
    # Act
    holders = [line for line in fixture.splitlines() if '"pending"' in line]
    # Assert
    assert holders != []
