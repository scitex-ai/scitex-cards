#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the stale-active detector (pending abolition).

``tests/scitex_cards/test__stale_active.py`` seeded ``status="pending"``
as the live backlog in two fixtures AFTER ``pending`` was abolished
2026-07-10. The detector was already correct (``STALE_ACTIVE_STATUSES``
is ``{"in_progress", "blocked"}``, so both ``deferred`` and legacy
``pending`` rows are excluded) — only the fixtures lied, inviting the
next copy-paste to reintroduce a dead ``pending`` seed. The fixtures
now seed ``deferred`` (the successor backlog state); these pins keep
code, fixtures, and the status enum aligned.

These pins use in-memory dicts plus module constants/docstrings only:
no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import datetime as _dt

import scitex_cards._stale.active as active_mod
from scitex_cards._stale.active import (
    BACKLOG_STATUSES,
    STALE_ACTIVE_STATUSES,
    detect_stale_active,
    is_stale_active,
)
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

_NOW = _dt.datetime(2026, 6, 25, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _card(cid, status, *, hours_ago, agent="a"):
    last = (_NOW - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": cid,
        "status": status,
        "title": cid,
        "agent": agent,
        "last_activity": last,
    }


def test_deferred_backlog_never_stale_active():
    t = _card("x", "deferred", hours_ago=99)
    assert is_stale_active(t, now=_NOW, stale_hours=2.0) is False


def test_legacy_pending_row_also_excluded():
    tasks = [
        _card("p", "pending", hours_ago=99, agent="a"),
        _card("ip", "in_progress", hours_ago=99, agent="a"),
    ]
    out = detect_stale_active(tasks, now=_NOW, stale_hours=2.0)
    assert sorted(c.id for c in out["a"]) == ["ip"]


def test_active_set_is_in_progress_and_blocked_only():
    assert STALE_ACTIVE_STATUSES == frozenset({"in_progress", "blocked"})
    assert BACKLOG_STATUSES == frozenset({"deferred"})


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_pins_are_nonvacuous():
    assert (active_mod.__doc__ or "").strip() != ""
    assert callable(is_stale_active)
    assert callable(detect_stale_active)
