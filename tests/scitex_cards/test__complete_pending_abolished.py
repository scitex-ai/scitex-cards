#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the complete_task transition prose (pending abolition).

``src/scitex_cards/_store_lifecycle.py`` drove the active-unblock emit
(ADR-0009) on every real non-``done`` → ``done`` transition — the code was
already correct (``transitioned`` is set for any non-``done`` row, and an
idempotent re-complete returns early with no emit) — but the inline
comment named it a ``pending→done`` transition after ``pending`` was
abolished 2026-07-10. Same dead-prose class as the runnable / reconcile /
stale fixes: the prose invites the next copy-paste to treat ``pending`` as
a live source state. The comment now names the true non-``done``→``done``
transition with the abolition note; no behavior change.

These pins use file text, ``inspect`` source, and the status enum only:
no store, no PostgreSQL — they run in containers where the store-backed
suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import scitex_cards._store_lifecycle as lifecycle_mod
from scitex_cards._store_lifecycle import complete_task
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_SOURCE = (
    Path(lifecycle_mod.__file__).resolve().read_text(encoding="utf-8")
)


def test_comment_names_the_true_transition():
    assert "non-done→done transition" in _SOURCE
    assert "Only on a real pending→done transition." not in _SOURCE


def test_comment_records_the_abolition():
    assert "``pending`` was abolished 2026-07-10" in _SOURCE


def test_emit_still_gated_on_a_real_transition():
    src = inspect.getsource(complete_task)
    # Idempotent re-complete returns early — no emit on a non-transition.
    assert 'if task.get("status") == "done":' in src
    # The unblock drive + completed event fire only when a row moved.
    assert "if transitioned:" in src
    assert "_emit_unblock_for_dependents(resolved, task_id" in src


def test_status_enum_mirror():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "done" in VALID_STATUSES
    # Non-vacuity: the abolished entry names the successor decision states,
    # so the mirror above is not checking an empty string.
    hint = ABOLISHED_STATUSES["pending"]
    assert "deferred" in hint
    assert "in_progress" in hint
    assert "blocked" in hint
