#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the stale-backlog detector prose (pending abolition).

``_stale/active.py``'s module docstring named ``status=pending`` as the
backlog scan set after ``pending`` was abolished 2026-07-10. The code was
already correct (``BACKLOG_STATUSES == {"deferred"}``, with
``PENDING_STATUSES`` kept only as a deprecated alias for out-of-tree
importers, and ``detect_pending_backlog`` passing that set) — only the
prose lied, inviting the next copy-paste to reintroduce a dead filter.
The prose now names ``deferred``; these pins keep code, docs, and the
status enum aligned.

These pins use in-memory dicts plus docstrings only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import datetime as _dt

import scitex_cards._stale.active as active_mod
from scitex_cards._backlog_triage import BACKLOG_STATUS
from scitex_cards._stale.active import (
    BACKLOG_STATUSES,
    PENDING_STATUSES,
    detect_pending_backlog,
)
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def _utc(*args):
    return _dt.datetime(*args, tzinfo=_dt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = _utc(2026, 6, 25, 12, 0, 0)


def _backlog_card(cid, status, *, entry_hours_ago, agent="a"):
    ts = _iso(NOW - _dt.timedelta(hours=entry_hours_ago))
    return {
        "id": cid,
        "status": status,
        "title": cid,
        "agent": agent,
        "deferred_at": ts,
        "created_at": ts,
    }


def test_backlog_statuses_are_deferred_with_pending_alias():
    assert BACKLOG_STATUSES == frozenset({"deferred"})
    # Deprecated alias for out-of-tree importers — same set, not a live
    # second scan set.
    assert PENDING_STATUSES == BACKLOG_STATUSES


def test_deferred_row_detected_legacy_pending_row_ignored():
    tasks = [
        _backlog_card("d1", "deferred", entry_hours_ago=48),
        _backlog_card("leg", "pending", entry_hours_ago=48),
    ]
    out = detect_pending_backlog(tasks, now=NOW, pending_hours=24.0)
    assert [c.id for c in out["a"]] == ["d1"]


def test_module_docstring_names_deferred_scan_set():
    doc = active_mod.__doc__ or ""
    assert "``status=deferred``" in doc
    assert "owned ``status=pending`` cards" not in doc


def test_backlog_mirror_matches_triage_ssot():
    assert BACKLOG_STATUS == "deferred"
    assert BACKLOG_STATUS in VALID_STATUSES
    assert BACKLOG_STATUS not in ABOLISHED_STATUSES
    assert "pending" in ABOLISHED_STATUSES
