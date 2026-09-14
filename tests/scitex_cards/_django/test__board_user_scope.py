#!/usr/bin/env python3
"""Per-user board scoping at the view layer (the tenancy P1 read-path slice).

The board cache in ``services.get_board`` is keyed by STORE, not by USER —
scoping inside it would cache user A's slice and serve it to user B (a
tenancy hole). So the scope is applied per-request in
``views._scope_board_for_request``: a pure in-memory filter over an in-memory
task list, producing a COPY (or returning the shared board unchanged when
scoping is off or the principal is staff). No store, no cursor, no Django
settings — therefore unit-testable hermetically, on a node with no writable
PostgreSQL. The predicate itself (creator/assignee/agent) is covered by
``test__user_row_scope.py``.

Semantics (Hub decision, 2026-09-14): a user sees a card they filed
(``created_by``), own (``assignee``), or act on (``agent``); staff see the
full set; a principal that owns no rows sees an empty board (fail closed).
Behind the ``SCITEX_CARDS_USER_SCOPE`` flag, OFF by default.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scitex_cards._django import views
from scitex_cards._django.services import BoardState


def _board(*rows):
    return BoardState(
        tasks=list(rows),
        store_path=Path("/scratch/tasks.yaml"),
        mtime=0.0,
        sig=(0, (0, 0, 0)),
    )


def _request(user):
    """A request object shaped for the boundary: an authenticated user (or not)."""
    req = SimpleNamespace()
    req.user = user
    req.scitex_store = None
    return req


def _user(name, *, is_staff=False):
    return SimpleNamespace(
        is_authenticated=True, is_staff=is_staff, get_username=lambda: name
    )


def test_flag_off_returns_the_board_unchanged(env):
    # Arrange: scoping disabled (the default); a board with a foreign row.
    env.set("SCITEX_CARDS_USER_SCOPE", "")
    board = _board({"id": "a1", "created_by": "alice"}, {"id": "b1", "created_by": "bob"})
    req = _request(_user("alice"))
    # Act: scope a request.
    out = views._scope_board_for_request(req, board)
    # Assert: the shared board is returned by identity, unmodified.
    assert out is board


def test_flag_on_scopes_to_the_users_own_cards(env):
    # Arrange: scoping enabled; alice owns a1 (creator) + a2 (agent), not b1.
    env.set("SCITEX_CARDS_USER_SCOPE", "1")
    board = _board(
        {"id": "a1", "created_by": "alice"},
        {"id": "b1", "created_by": "bob", "assignee": "bob"},
        {"id": "a2", "agent": "alice"},
    )
    req = _request(_user("alice"))
    # Act: scope a request.
    out = views._scope_board_for_request(req, board)
    # Assert: only alice's rows survive, in order.
    assert [t["id"] for t in out.tasks] == ["a1", "a2"]


def test_flag_on_does_not_mutate_the_shared_board(env):
    # Arrange: scoping enabled; the shared board carries a foreign row.
    env.set("SCITEX_CARDS_USER_SCOPE", "1")
    board = _board({"id": "a1", "created_by": "alice"}, {"id": "b1", "created_by": "bob"})
    req = _request(_user("alice"))
    # Act: scope a request.
    out = views._scope_board_for_request(req, board)
    # Assert: the scoped copy is distinct and the original board is untouched.
    assert out is not board and len(board.tasks) == 2


def test_staff_see_the_full_set(env):
    # Arrange: scoping enabled; the principal is staff.
    env.set("SCITEX_CARDS_USER_SCOPE", "1")
    board = _board({"id": "a1", "created_by": "alice"}, {"id": "b1", "created_by": "bob"})
    req = _request(_user("alice", is_staff=True))
    # Act: scope a request.
    out = views._scope_board_for_request(req, board)
    # Assert: staff bypass the predicate (the control Hub mirrors).
    assert out is board


def test_principal_that_owns_no_rows_sees_an_empty_board(env):
    # Arrange: scoping enabled; the principal matches no ownership field.
    env.set("SCITEX_CARDS_USER_SCOPE", "1")
    board = _board({"id": "a1", "created_by": "bob"}, {"id": "b1", "agent": "carol"})
    req = _request(_user("dave"))
    # Act: scope a request.
    out = views._scope_board_for_request(req, board)
    # Assert: fail closed — no cards, and empty_store is set accordingly.
    assert out.tasks == [] and out.empty_store


def test_flag_values_parse_to_the_right_boolean(env):
    # Arrange: the flag is a plain env string.
    env.set("SCITEX_CARDS_USER_SCOPE", "0")
    # Act: parse the flag.
    # Assert: falsy values are OFF.
    assert views._user_scope_enabled() is False


def test_flag_true_value_parses_on(env):
    # Arrange: the flag is a plain env string.
    env.set("SCITEX_CARDS_USER_SCOPE", "true")
    # Act: parse the flag.
    # Assert: truthy values are ON.
    assert views._user_scope_enabled() is True


# EOF
