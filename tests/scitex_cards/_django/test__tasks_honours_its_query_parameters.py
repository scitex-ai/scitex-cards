#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/tasks`` filters server-side instead of accepting parameters and ignoring them.

THE DEFECT WAS SILENT ACCEPTANCE. A caller passing ``?status=in_progress`` got
the entire board and no indication its filter did nothing — worse than a 400,
because nothing distinguishes "your filter matched everything" from "your
filter was never read".

MEASURED 2026-09-06 by scitex-agent-container against the live board on 8051,
after the operator reported the page never finishing loading:

    GET /            200 in 0.044s      the page itself is fine
    GET /tasks       200 in 2.50s       55,418,279 bytes (20 MB gzipped)

    ?limit=50                        -> 55,418,279
    ?status=in_progress              -> 55,418,279
    ?limit=50&status=in_progress     -> 55,418,279
    ?assignee=scitex-agent-container -> 55,418,279

Four arms, one byte count. That is what "the server never looked" looks like
from outside, and it is a better proof than reading the handler — a handler can
be read wrongly, but four identical sizes cannot be argued with.

THE DEFAULT IS DELIBERATELY UNCHANGED. With no parameters the whole board is
still returned, so no existing caller's answer moves. Narrowing the default is
a separate decision with its own risk (the board renders closed columns), and
mixing it in here would make a purely additive change into a behavioural one.
"""

import json

import pytest
from django.test import RequestFactory

from scitex_cards._django.handlers.graph import handle_tasks


class _Board:
    """The slice of BoardState this handler touches, and nothing more."""

    def __init__(self, tasks, generation="gen-1"):
        self.tasks = tasks
        self.store_path = "postgresql://example/store"
        self.empty_store = not tasks
        # (generation, stat_sig) — the shape services.get_board builds.
        self.sig = (generation, (0, 0, 0))


@pytest.fixture()
def board():
    return _Board(
        [
            {"id": "a", "status": "in_progress", "assignee": "alice"},
            {"id": "b", "status": "done", "assignee": "alice"},
            {"id": "c", "status": "blocked", "assignee": "bob"},
            {"id": "d", "status": "in_progress", "assignee": "bob"},
        ]
    )


def _ids(response) -> list:
    return [t["id"] for t in json.loads(response.content)["tasks"]]


def test_no_parameters_returns_the_whole_board(board):
    """THE DEFAULT IS UNCHANGED, and it runs first because it is the promise.

    Every filter test below is only safe if this holds: the change may add
    selectivity, never remove a card from a caller who asked for nothing.
    """
    # Arrange
    request = RequestFactory().get("/tasks")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["a", "b", "c", "d"]


def test_status_selects_only_that_status(board):
    # Arrange
    request = RequestFactory().get("/tasks?status=in_progress")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["a", "d"]


def test_status_accepts_a_comma_separated_set(board):
    """A board column set is one request, not one per column."""
    # Arrange
    request = RequestFactory().get("/tasks?status=in_progress,blocked")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["a", "c", "d"]


def test_assignee_selects_only_that_owner(board):
    # Arrange
    request = RequestFactory().get("/tasks?assignee=bob")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["c", "d"]


def test_limit_truncates_the_returned_list(board):
    # Arrange
    request = RequestFactory().get("/tasks?limit=2")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["a", "b"]


def test_the_filters_compose(board):
    """The measured arm that returned the whole board: both together."""
    # Arrange
    request = RequestFactory().get("/tasks?status=in_progress&assignee=bob")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["d"]


def test_an_unparseable_limit_is_ignored_rather_than_fatal(board):
    """A malformed integer must not replace a slow page with NO page.

    This endpoint feeds a browser grid; refusing the whole board over a typo
    in a query string would be a worse outage than the one being fixed.
    """
    # Arrange
    request = RequestFactory().get("/tasks?limit=banana")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == ["a", "b", "c", "d"]


def test_a_status_nobody_has_returns_an_empty_list(board):
    """Selectivity, not silent pass-through — the defect's own signature."""
    # Arrange
    request = RequestFactory().get("/tasks?status=nonexistent")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert _ids(response) == []


def _etag(response) -> str:
    return response["ETag"]


def test_an_unchanged_board_answers_304_to_a_matching_etag(board):
    """THE CACHE THE OPERATOR ASKED FOR: an unchanged poll costs ~0 bytes.

    He directed 2026-09-06 that the GUI be made fast with diffs and caching.
    The server already cached its own work; what it never had was a way to
    tell a POLLING client "nothing changed", so every poll paid the full
    payload — 20 MB gzipped on the live board.
    """
    # Arrange
    first = handle_tasks(RequestFactory().get("/tasks"), board)
    request = RequestFactory().get("/tasks", HTTP_IF_NONE_MATCH=_etag(first))
    # Act
    second = handle_tasks(request, board)
    # Assert
    assert second.status_code == 304


def test_a_changed_board_does_not_answer_304(board):
    """The generation is the store's mutation stamp, so a moved board cannot
    report fresh. This is the half that makes the cache safe rather than fast."""
    # Arrange
    first = handle_tasks(RequestFactory().get("/tasks"), board)
    moved = _Board(board.tasks, generation="gen-2")
    request = RequestFactory().get("/tasks", HTTP_IF_NONE_MATCH=_etag(first))
    # Act
    second = handle_tasks(request, moved)
    # Assert
    assert second.status_code == 200


def test_a_filtered_etag_does_not_satisfy_an_unfiltered_request(board):
    """THE TRAP, and the reason the filters are in the key.

    Without this, a client that fetched ?status=in_progress and then asked for
    the WHOLE board would present the narrow ETag, match, and be told 304 —
    quietly holding a filtered board while believing it had all of it. A stale
    hit indistinguishable from a fresh one, on the fleet's source of truth.
    """
    # Arrange
    narrow = handle_tasks(RequestFactory().get("/tasks?status=in_progress"), board)
    request = RequestFactory().get("/tasks", HTTP_IF_NONE_MATCH=_etag(narrow))
    # Act
    whole = handle_tasks(request, board)
    # Assert
    assert whole.status_code == 200


def test_the_response_states_which_generation_it_was_built_from(board):
    """A reader must be able to tell WHICH board state it is holding.

    sac's caveat, adopted as a requirement rather than a note: a cache that
    cannot distinguish a stale hit from a fresh one looks exactly like a fast
    correct answer.
    """
    # Arrange
    request = RequestFactory().get("/tasks")
    # Act
    response = handle_tasks(request, board)
    # Assert
    assert json.loads(response.content)["generation"] == "gen-1"


# EOF
