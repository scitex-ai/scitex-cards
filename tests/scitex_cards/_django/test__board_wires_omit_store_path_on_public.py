#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board's JSON endpoints must not leak the store path on a public deployment.

COMPASS §13 L478/L479 (corroborated by §21 L643 "current UI visibly exposes
internal/operator concepts"). ``store_path`` is the deployment's database /
identity location — internal information. The 2026-09-10 evidence audit found
the board leaks it on TWO surfaces: (1) the server-rendered footer (closed by
the ``show_internal_chrome`` gate, see
``test__board_hides_internal_chrome_from_public.py``) and (2) the JSON wire
payloads of ``/graph``, ``/tasks`` and ``/rev`` — which carried
``"store_path": <path>`` to EVERY client regardless of deployment.

THE FIX THIS TEST PINS. Each of those three handlers now puts ``store_path`` in
its payload only when ``settings.DEBUG`` is true (the loopback board, where the
operator wants to see WHICH store answered). On a public deployment
(``settings.py:86`` forces ``DEBUG`` off under ``SCITEX_CARDS_PUBLIC_HOST``) the
key is omitted from the dict entirely, so the wire carries nothing. This is the
same operator-vs-external split ``_store_errors.public_summary`` uses.

HERMETIC. Builds a minimal fake ``board`` (only the attributes the handlers read:
``store_path``, ``tasks``, ``mtime``, ``sig``, ``empty_store``) and calls the
three handlers directly with a ``RequestFactory`` request — no store, no DB, no
template render. ``override_settings(DEBUG=...)`` is re-read at call time, so
both directions are asserted against the real handler code.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory, override_settings  # noqa: E402

from scitex_cards._django.handlers import graph as graph_handlers  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_graph_cache():
    """handle_graph caches by (store_path, mtime, sig) — NOT by DEBUG. That is
    safe in production (settings.py makes DEBUG process-constant: a given board
    process is always either loopback or public, never both), but this test
    flips DEBUG between calls to the SAME fake board, so a payload built under
    one DEBUG would poison the next. Reset the cache around every test."""
    graph_handlers._graph_cache_reset()
    yield
    graph_handlers._graph_cache_reset()


def _fake_board() -> SimpleNamespace:
    """A board object carrying exactly the attributes the handlers read.

    ``tasks`` is a list of dicts (the handlers only len() / iterate it);
    ``sig`` is a 2-tuple (the handlers read ``board.sig[0]``); ``mtime`` is a
    float (``/rev`` reads it straight); ``groups``/``ts`` are the remaining
    attributes ``_build_graph`` touches (a user-defined cluster list and a
    store timestamp — both may be empty/None on a minimal board). No store is
    opened.
    """
    return SimpleNamespace(
        store_path="/home/operator/.scitex/cards/cards.db",
        tasks=[{"id": "a", "title": "Task A", "status": "pending"}],
        mtime=1234.5,
        ts=1234.5,
        sig=("7", 1),
        groups=[],
        empty_store=False,
    )


def _handler_payload(handler) -> dict:
    """Call one read handler with a bare request and return its JSON body."""
    request = RequestFactory().get("/x")
    resp = handler(request, _fake_board())
    return json.loads(resp.content.decode("utf-8"))


def test_graph_omits_store_path_on_public() -> None:
    # Arrange
    body = None
    with override_settings(DEBUG=False):
        body = _handler_payload(graph_handlers.handle_graph)
    # Assert — the key is gone, not present-and-null.
    assert "store_path" not in body, (
        "public /graph still leaks store_path on the wire — compass §13 "
        "L479: a stranger's browser network tab shows the deployment's store"
    )
    assert body["task_count"] == 1, "the /graph payload lost its data"


def test_tasks_omits_store_path_on_public() -> None:
    # Arrange
    with override_settings(DEBUG=False):
        body = _handler_payload(graph_handlers.handle_tasks)
    # Assert
    assert "store_path" not in body, "public /tasks still leaks store_path"
    assert body["tasks"], "the /tasks payload lost its data"


def test_rev_omits_store_path_on_public() -> None:
    # Arrange
    with override_settings(DEBUG=False):
        body = _handler_payload(graph_handlers.handle_rev)
    # Assert
    assert "store_path" not in body, "public /rev still leaks store_path"
    assert body["count"] == 1, "the /rev payload lost its data"


def test_graph_keeps_store_path_for_operator() -> None:
    """The loopback board (DEBUG=true) still names the store — the operator's
    diagnosis surface is preserved, not just hidden."""
    # Arrange
    with override_settings(DEBUG=True):
        body = _handler_payload(graph_handlers.handle_graph)
    # Assert
    assert body["store_path"] == "/home/operator/.scitex/cards/cards.db"


def test_store_path_for_wire_is_the_single_seam() -> None:
    """The helper is the one place the decision lives; both branches are pure."""
    board = _fake_board()
    with override_settings(DEBUG=True):
        assert graph_handlers._store_path_for_wire(board) == board.store_path
    with override_settings(DEBUG=False):
        assert graph_handlers._store_path_for_wire(board) is None
