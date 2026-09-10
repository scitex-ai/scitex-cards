#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/tasks`` joins the stale-while-revalidate set, and nothing that writes does not.

The SWRV mechanism (serve the pre-write snapshot, rebuild in the background)
lives in ``services.get_board(allow_stale=True)`` and is behaviour-tested with a
real store in ``test__board_stale_while_revalidate.py``. What is NOT pinned there
is WHICH endpoints opt in — that decision is the single frozenset
``STALE_OK_ENDPOINTS`` in ``views.py``, and a silent membership change there is
exactly the kind of drift this package's gates exist to catch.

The read-your-own-writes line it draws (services.get_board docstring): a caller
that opts in WITHOUT a "write-then-read-back" property is a bug. The chat POST
is the live counter-example — it reads its own message back through the board,
so it must see its write and must NOT be stale-OK. ``/tasks`` qualifies because
it is GET-only (``handle_tasks`` ignores the method; there is no ``/tasks``
write) and the SPA renders the board from ``/graph``, not ``/tasks``, so nothing
writes and then reads it back. A stale grid is invisible while the multi-MB
rebuild wait is not — the same property ``graph`` and ``timeline`` have.

This file pins the membership: the read-only views in, every write endpoint out.
It is hermetic — no store, no request, no template — so it is the guaranteed-red
regression the behaviour test (which needs a store) cannot be in this
environment.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from scitex_cards._django import views  # noqa: E402


def test_tasks_is_stale_ok_alongside_its_read_only_siblings() -> None:
    # Arrange / Act
    stale_ok = views.STALE_OK_ENDPOINTS
    # Assert
    assert {"graph", "timeline", "tasks"} <= stale_ok, (
        f"STALE_OK_ENDPOINTS={stale_ok!r} — the read-only board views must all "
        "serve the pre-write snapshot and rebuild in the background; a stale "
        "answer is invisible while the full-store rebuild wait is not"
    )


def test_no_write_endpoint_is_stale_ok() -> None:
    """The read-your-own-writes invariant: a caller that must reflect a write it
    just made (the chat POST is the live example) must NOT be listed, or its own
    write vanishes until the next refresh.

    Derives the write set from the live HANDLERS dispatch table minus the known
    read-only board views, so the check self-maintains: add a new write handler
    and it is checked here automatically, and no future edit that adds
    ``"comment"`` or ``"create"`` to STALE_OK passes silently.
    """
    # Arrange — the real partition, from the dispatch table itself.
    from scitex_cards._django.handlers import HANDLERS

    read_only_views = {"graph", "timeline", "tasks", "ping", "rev"}
    write_endpoints = set(HANDLERS) - read_only_views
    # Act
    stale_ok = views.STALE_OK_ENDPOINTS
    # Assert
    leaked = write_endpoints & stale_ok
    assert not leaked, (
        f"write endpoints {sorted(leaked)!r} are in STALE_OK_ENDPOINTS="
        f"{stale_ok!r} — a read-your-own-writes bug: a caller that writes and "
        "then reads its own change back would serve the stale snapshot and "
        "lose the write until the next refresh"
    )
