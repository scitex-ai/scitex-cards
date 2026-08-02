#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board read handlers must go through the mtime-keyed cache, not `load_tasks`.

Regression cover for the 2026-07-12 slow-board incident. The operator said the
GUI was slow and asked for the SQLite migration. Measured on his live store
(1,352 cards), /timeline was ~6 s per request and broke down as:

    load_tasks()     1.22 s   <- 99% of it, re-parsing the whole 5 MB YAML
    _build_payload   0.01 s   <- the actual timeline work

...on EVERY request, and the front end polls every 30 s. `services.get_board`
already had exactly the right cache (keyed on MAX mtime across the store and
every lane, so it re-reads precisely when something was written); these handlers
simply bypassed it. Routing them through it took steady-state /timeline from
5.96 s to 0.15 s.

These tests pin the CALL, not the timing — a timing assertion would be flaky in
CI. If someone reintroduces a bare `load_tasks` in a read handler, the store gets
re-parsed on every poll again and these fail.
"""

import inspect

from scitex_cards._django.handlers import chat, runnable, timeline
from scitex_cards._django.handlers.fleet import timing_view


def _src(fn) -> str:
    return inspect.getsource(fn)


#: The cache entry point every read handler must route through, and the bare
#: re-parse none of them may reintroduce. Named once so each test below reads
#: as "this handler, that property" rather than repeating the literals.
CACHED_READ = "get_board"
BARE_LOAD = "tasks = load_tasks("


def test_timeline_reads_through_the_cache():
    # Arrange
    view = timeline.timeline_view
    # Act
    src = _src(view)
    # Assert
    assert CACHED_READ in src


def test_timeline_does_not_call_load_tasks_directly():
    """A bare load_tasks here re-parses 5 MB on every 30 s poll."""
    # Arrange
    view = timeline.timeline_view
    # Act
    src = _src(view)
    # Assert
    assert BARE_LOAD not in src


def test_runnable_reads_through_the_cache():
    # Arrange
    view = runnable.runnable_view
    # Act
    src = _src(view)
    # Assert
    assert CACHED_READ in src


def test_runnable_does_not_call_load_tasks_directly():
    # Arrange
    view = runnable.runnable_view
    # Act
    src = _src(view)
    # Assert
    assert BARE_LOAD not in src


def test_blocked_batch_reads_through_the_cache():
    # Arrange
    view = runnable.blocked_batch_view
    # Act
    src = _src(view)
    # Assert
    assert CACHED_READ in src


def test_chat_reads_through_the_cache():
    # Arrange
    view = chat.chat_view
    # Act
    src = _src(view)
    # Assert
    assert CACHED_READ in src


def test_fleet_timing_reads_through_the_cache():
    # Arrange
    view = timing_view.fleet_timing_view
    # Act
    src = _src(view)
    # Assert
    assert CACHED_READ in src


def test_timeline_still_reports_a_store_path():
    """The payload contract keeps store_path — the FE footer shows it."""
    # Arrange
    view = timeline.timeline_view
    # Act
    src = _src(view)
    # Assert
    assert "store_path" in src
