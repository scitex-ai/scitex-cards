#!/usr/bin/env python3
"""Hermetic tests for the per-user ROW scope predicate (the tenancy fix unit).

No DSN, no database, no Django — the predicate operates on already-loaded row
dicts. These pin the semantics the hub's boundary regression test mirrors:
user A must see zero cards they neither filed nor act on; staff see the full
set.
"""

from __future__ import annotations

from scitex_cards._user_row_scope import (
    OWNED_FIELDS,
    foreign_row_ids,
    scope_rows_for_user,
    user_owns_row,
)


def _row(task_id, *, created_by=None, assignee=None, agent=None):
    return {"id": task_id, "created_by": created_by, "assignee": assignee, "agent": agent}


def test_owned_fields_are_exactly_creator_assignee_agent():
    # Arrange: none — the constant is the subject.
    # Act: none — reading a constant.
    # Assert: the ownership fields are exactly the three operator-co-designed ones.
    assert OWNED_FIELDS == ("created_by", "assignee", "agent")


def test_user_owns_row_by_created_by():
    # Arrange: a row created by alice.
    row = _row("t1", created_by="alice")
    # Act: ask whether alice owns it.
    owns = user_owns_row(row, "alice")
    # Assert: the creator owns their own row.
    assert owns


def test_user_owns_row_by_assignee():
    # Arrange: a row assigned to alice.
    row = _row("t1", assignee="alice")
    # Act: ask whether alice owns it.
    owns = user_owns_row(row, "alice")
    # Assert: the assignee owns their own row.
    assert owns


def test_user_owns_row_by_agent():
    # Arrange: a row owned by alice via the `agent` field.
    row = _row("t1", agent="alice")
    # Act: ask whether alice owns it.
    owns = user_owns_row(row, "alice")
    # Assert: the agent owns their own row.
    assert owns


def test_user_does_not_own_a_foreign_row():
    # Arrange: a row that is entirely bob's.
    row = _row("t1", created_by="bob", assignee="bob", agent="bob")
    # Act: ask whether alice owns it.
    owns = user_owns_row(row, "alice")
    # Assert: alice must not own a foreign row.
    assert not owns


def test_row_with_no_ownership_fields_is_not_owned_by_anyone():
    # Arrange: a row with no creator / assignee / agent.
    row = _row("t1")
    # Act: ask whether alice owns it.
    owns = user_owns_row(row, "alice")
    # Assert: an unowned row matches no principal.
    assert not owns


def test_empty_principal_matches_nothing():
    # Arrange: a row that has an owner, but an empty principal.
    row = _row("t1", created_by="alice")
    # Act: ask whether the empty principal owns it.
    owns = user_owns_row(row, "")
    # Assert: an unresolvable user fails closed (sees no cards).
    assert not owns


def test_scope_rows_returns_only_the_users_own_cards():
    # Arrange: a mixed set of alice's and others' rows.
    rows = [
        _row("a1", created_by="alice"),
        _row("b1", created_by="bob", assignee="bob"),
        _row("a2", agent="alice"),
        _row("c1", assignee="carol"),
    ]
    # Act: scope the set to alice.
    visible = scope_rows_for_user(rows, "alice")
    # Assert: only alice's rows survive, in input order.
    assert [r["id"] for r in visible] == ["a1", "a2"]


def test_scope_rows_preserves_input_order():
    # Arrange: alice's rows interleaved with a foreign one.
    rows = [
        _row("x", agent="alice"),
        _row("y", created_by="bob"),
        _row("z", created_by="alice"),
    ]
    # Act: scope the set to alice.
    visible = scope_rows_for_user(rows, "alice")
    # Assert: the order of the surviving rows is unchanged.
    assert [r["id"] for r in visible] == ["x", "z"]


def test_scope_rows_staff_sees_the_full_set():
    # Arrange: rows owned by different users plus an unowned one.
    rows = [
        _row("a1", created_by="alice"),
        _row("b1", created_by="bob"),
        _row("c1"),
    ]
    # Act: scope the set to alice as staff.
    visible = scope_rows_for_user(rows, "alice", is_staff=True)
    # Assert: staff bypass the predicate and see everything.
    assert [r["id"] for r in visible] == ["a1", "b1", "c1"]


def test_foreign_row_ids_is_empty_for_a_user_who_owns_everything():
    # Arrange: rows alice owns in different ways.
    rows = [_row("a1", created_by="alice"), _row("a2", assignee="alice")]
    # Act: compute the foreign ids for alice.
    foreign = foreign_row_ids(rows, "alice")
    # Assert: nothing is foreign to her.
    assert foreign == []


def test_foreign_row_ids_lists_only_foreign_cards():
    # Arrange: alice's row plus bob's and carol's.
    rows = [
        _row("a1", created_by="alice"),
        _row("b1", created_by="bob"),
        _row("c1", agent="carol"),
    ]
    # Act: compute the foreign ids for alice.
    foreign = foreign_row_ids(rows, "alice")
    # Assert: exactly the non-alice rows are foreign (the hub regression shape).
    assert sorted(foreign) == ["b1", "c1"]


def test_foreign_row_ids_is_empty_for_staff():
    # Arrange: rows that belong only to other users.
    rows = [_row("b1", created_by="bob"), _row("c1", agent="carol")]
    # Act: compute the foreign ids for alice as staff.
    foreign = foreign_row_ids(rows, "alice", is_staff=True)
    # Assert: staff see all cards, so none are foreign to them.
    assert foreign == []
