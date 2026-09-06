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

    def __init__(self, tasks):
        self.tasks = tasks
        self.store_path = "postgresql://example/store"
        self.empty_store = not tasks


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


# EOF
