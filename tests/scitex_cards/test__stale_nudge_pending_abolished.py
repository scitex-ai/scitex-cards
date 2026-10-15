#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the pending-backlog nudge prose (pending abolition).

``_stale/active_nudge.py``'s module docstring described the
``pending-backlog`` nudge as "start or triage the pending cards you
accepted but never began" after ``pending`` was abolished 2026-07-10.
The code was already correct (the detector scans ``BACKLOG_STATUSES ==
{"deferred"}`` and :func:`pending_backlog_nudge_line` renders
``"N deferred card(s) ..."``) — only the docstring lied, inviting the
next copy-paste to reintroduce a dead filter. The prose now names the
deferred backlog; these pins keep code, docs, and the status enum
aligned.

These pins use in-memory StaleCards plus docstrings only: no store, no
PostgreSQL — they run in containers where the store-backed suite refuses
via the fail-LOUD guard.
"""

from __future__ import annotations

import scitex_cards._stale.active_nudge as nudge_mod
from scitex_cards._stale.active import StaleCard
from scitex_cards._stale.active_lines import pending_backlog_nudge_line
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES


def _card(cid: str) -> StaleCard:
    return StaleCard(
        id=cid, title=cid, status="deferred", age_hours=48.0,
    )


def test_nudge_doc_names_deferred_backlog():
    doc = nudge_mod.__doc__ or ""
    assert "deferred cards you accepted but never began" in doc
    assert "pending cards you accepted but never began" not in doc


def test_nudge_line_renders_deferred():
    line = pending_backlog_nudge_line("a", [_card("d1")], pending_hours=24.0)
    assert "deferred card(s)" in line
    assert "pending card" not in line
    assert "d1" in line


def test_doc_and_line_agree_on_deferred():
    doc = nudge_mod.__doc__ or ""
    line = pending_backlog_nudge_line("a", [_card("d1")], pending_hours=24.0)
    assert "deferred" in doc and "deferred" in line


def test_pending_stays_abolished():
    assert "pending" in ABOLISHED_STATUSES
    assert "pending" not in VALID_STATUSES
    assert "deferred" in VALID_STATUSES


def test_pins_are_nonvacuous():
    assert (nudge_mod.__doc__ or "").strip() != ""
    assert callable(pending_backlog_nudge_line)
