#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The mirror-rail seed must not carry the abolished `pending` status.

``status: pending`` was abolished 2026-07-10 (operator directive;
:data:`ABOLISHED_STATUSES` in :mod:`scitex_cards._task`): a card in
``pending`` carried NO decision, so every open card must now state its
disposition (``in_progress`` / ``blocked`` + blocker / ``deferred``).
The ``test__db_mirror.py`` suite pins that an ordinary card write never
rebuilds the delivery rail (notification row, receipts, dedupe key, seen
flag) — none of its assertions touches the card's status, so its
``_write_a_card`` helper seeded ``status: pending`` while proving nothing
about it, teaching the abolished value as a live example. Same
dead-fixture class as the group-field (5), org (18), deadline (11),
deadline_multi_recurring (3), is_overdue (19), mermaid (2), idle-guard
(4), write-blocker-clear (1) and throughput (12) repoints: repointed to
``deferred`` (the successor default — :class:`Task` dataclass default
and ``add_task(status="deferred")``), no src change
(``mirror_doc_incremental`` performs no status validation; the seed
value never reaches an assertion).

Deliberately NOT repointed anywhere else in that file: the single seed
was the only ``pending`` in it.

PG-free by construction (in-memory dicts + file-text scans only): no
``postgres_dsn`` / ``new_store`` fixture, no store touch. The
store-backed rail tests themselves refuse via the fail-LOUD guard
without a writable cluster, by design.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import scitex_cards
from scitex_cards._model import Task, _validate_tasks
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_MIRROR_TEST = (
    Path(scitex_cards.__file__).parent.parent.parent
    / "tests"
    / "scitex_cards"
    / "test__db_mirror.py"
)


def test_mirror_seed_is_the_successor_default_not_pending():
    # Arrange — the repointed helper shape: ordinary card + live status.
    tasks = [{"id": "card-1", "title": "a card", "status": "deferred"}]
    # Act — must validate SILENTLY (a live status warns about nothing).
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _validate_tasks(tasks, source="<test>")
    # Assert — reaching here IS the assertion (no warning, no raise).
    assert tasks[0]["status"] == "deferred"


def test_legacy_pending_row_still_tolerated_never_rejected():
    # Arrange — a legacy `pending` row of the same shape.
    tasks = [{"id": "card-1", "title": "a card", "status": "pending"}]
    # Act — the read side tolerates abolished values (warns), never raises,
    # so surviving legacy rows keep validating while new code writes `deferred`.
    _validate_tasks(tasks, source="<test>")
    # Assert — reaching here IS the assertion (no raise on legacy rows).
    assert tasks == [{"id": "card-1", "title": "a card", "status": "pending"}]


def test_task_dataclass_default_is_the_successor():
    # Arrange / Act.
    t = Task(id="card-1", title="a card")
    # Assert — a fresh row carries a decision out of the box, never `pending`.
    assert t.status == "deferred"


def test_enum_mirror_pending_abolished_deferred_valid():
    # Arrange / Act / Assert — the single source both sides mirror.
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_mirror_seed_text_carries_no_pending_status_seed():
    # Arrange.
    text = _MIRROR_TEST.read_text(encoding="utf-8")
    # Act / Assert — no card-status seed of the abolished value remains.
    # (Matches the `"status": "pending"` / `'status': 'pending'` seed
    # shapes only: prose mentions of the abolition live in comments.)
    assert '"status": "pending"' not in text
    assert "'status': 'pending'" not in text
    assert "status='pending'" not in text


def test_mirror_seed_text_carries_the_deferred_seed():
    # Arrange.
    text = _MIRROR_TEST.read_text(encoding="utf-8")
    # Act — the helper under test must exist (vacuity guard: the sweep
    # above cannot pass on an unread or emptied file).
    match = re.search(
        r"def _write_a_card\(.*?\n(?:.*\n)*?.*?\"status\": \"deferred\"",
        text,
    )
    # Assert.
    assert match is not None, "_write_a_card must seed status deferred"
