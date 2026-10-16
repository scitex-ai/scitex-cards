#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the idle-guard backlog prose (pending abolition).

``_idle_guard``'s module docstring named the ``PENDING backlog`` as the
set the stop guard deliberately ignores after ``pending`` was abolished
2026-07-10. The code was already correct (``stale_in_progress`` keeps
only ``status == "in_progress"``, so both ``deferred`` and legacy
``pending`` rows are excluded) — only the prose lied, inviting the next
copy-paste to reintroduce a dead ``pending`` filter. The prose now names
the deferred backlog; these pins keep code, docs, and the status enum
aligned.

These pins use in-memory dicts plus docstrings only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import datetime as _dt

import scitex_cards._idle_guard as guard_mod
from scitex_cards import _idle_guard
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_NOW = _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _t(*, id, status, hours_ago, owner="alice"):
    last = (_NOW - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": id,
        "title": f"card {id}",
        "status": status,
        "agent": owner,
        "last_activity": last,
    }


def test_doc_names_deferred_backlog():
    doc = guard_mod.__doc__ or ""
    assert "Deferred backlog is surfaced" in doc
    assert "PENDING backlog is surfaced" not in doc


def test_deferred_backlog_excluded_from_abandonment_set():
    tasks = [
        _t(id="hot", status="in_progress", hours_ago=10),
        _t(id="backlog", status="deferred", hours_ago=99),
    ]
    cards = _idle_guard.stale_in_progress("alice", tasks, now=_NOW, stale_hours=2.0)
    assert [c.id for c in cards] == ["hot"]


def test_legacy_pending_row_also_excluded():
    tasks = [
        _t(id="hot", status="in_progress", hours_ago=10),
        _t(id="leg", status="pending", hours_ago=99),
    ]
    cards = _idle_guard.stale_in_progress("alice", tasks, now=_NOW, stale_hours=2.0)
    assert [c.id for c in cards] == ["hot"]


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_pins_are_nonvacuous():
    assert (guard_mod.__doc__ or "").strip() != ""
    assert callable(_idle_guard.stale_in_progress)
