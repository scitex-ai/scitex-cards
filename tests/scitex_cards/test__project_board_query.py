#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The project board's queries: is the tenancy IN the SQL, and is it BOUNDED?

These are the cheap assertions that keep the performance fix from silently
regressing to "load everything and filter in Python" — which is what this page
did first, and what measured 72.7s cold / 32.7s warm against the shared store.
They read the SQL text, so they need no database and no network; the round trip
against a real store is tested in test__project_board_tenancy.py, where the
harness hands out a throwaway one.
"""

from __future__ import annotations

from scitex_cards import _project_board_query as pbq
from scitex_cards._user_row_scope import OWNED_FIELDS


def test_the_rows_query_carries_the_tenancy_predicate():
    """Every owned field must be a WHERE term, or the query returns a fleet."""
    # Arrange
    sql = pbq.rows_sql()
    # Act
    missing = [field for field in OWNED_FIELDS if f"{field} = ?" not in sql]
    # Assert
    assert missing == []


def test_the_projects_query_carries_the_same_predicate():
    """The picker must not be able to NAME a project the viewer has no card in."""
    # Arrange
    sql = pbq.projects_sql()
    # Act
    missing = [field for field in OWNED_FIELDS if f"{field} = ?" not in sql]
    # Assert
    assert missing == []


def test_the_predicate_is_built_from_the_one_owned_fields_definition():
    """SQL and the in-memory predicate must not drift into two different
    answers to "whose card is this"."""
    # Arrange
    clause = pbq._tenancy_clause()
    # Act
    terms = clause.count(" = ?")
    # Assert
    assert terms == len(OWNED_FIELDS)


def test_the_rows_query_is_bounded_by_a_limit():
    """A board page must not be able to grow with the fleet."""
    # Arrange
    sql = pbq.rows_sql()
    # Act
    bounded = "LIMIT ?" in sql
    # Assert
    assert bounded is True


def test_the_rows_query_selects_only_the_fields_a_card_shows():
    """`note` and the comment bodies are most of the 61.6 MB document; a board
    card shows none of them, so the query must not ask for them."""
    # Arrange
    sql = pbq.rows_sql()
    # Act
    heavy = [word for word in ("note", "comments", "card_json", "goal") if word in sql]
    # Assert
    assert heavy == []


def test_the_rows_query_is_scoped_to_one_project():
    """One project per query, or the bound is a page size over the whole fleet."""
    # Arrange
    sql = pbq.rows_sql()
    # Act
    scoped = "WHERE project = ?" in sql
    # Assert
    assert scoped is True


def test_staff_reads_drop_the_predicate_entirely():
    """The operator's control: staff see the whole board, and it costs them no
    extra parameters."""
    # Arrange
    sql = pbq.rows_sql(is_staff=True)
    # Act
    forbidden = [field for field in OWNED_FIELDS if f"{field} = ?" in sql]
    # Assert
    assert forbidden == []


def test_rows_parameters_match_the_order_the_sql_expects():
    """project first, then the principal per owned field, then the limit, then the
    offset — the order a positional driver requires, asserted rather than assumed."""
    # Arrange
    expected = ("proj-alpha", "alice", "alice", "alice", 7, 21)
    # Act
    params = pbq.rows_params("proj-alpha", "alice", limit=7, offset=21)
    # Assert
    assert params == expected


def test_the_rows_query_pages_in_the_database_not_in_python():
    """OFFSET belongs to the SQL: fetching 500 rows to show the last 50 has still
    paid for 500, which is the cost this module exists to remove."""
    # Arrange
    sql = pbq.rows_sql()
    # Act
    paged = "LIMIT ? OFFSET ?" in sql
    # Assert
    assert paged is True


def test_the_count_query_is_a_count_not_a_page():
    """The total must come from the store's own COUNT: a page reporting its own
    length as the total says "500 of 500" for a project holding 1,200 cards."""
    # Arrange
    sql = pbq.count_sql()
    # Act
    shape = ("COUNT(*)" in sql, "LIMIT" in sql, "OFFSET" in sql)
    # Assert
    assert shape == (True, False, False)


def test_the_count_carries_the_same_tenancy_predicate_as_the_rows():
    """A count that ignored the predicate would leak another tenant's card COUNT —
    the leak the page refuses to make even in aggregate."""
    # Arrange
    sql = pbq.count_sql()
    # Act
    missing = [field for field in OWNED_FIELDS if f"{field} = ?" not in sql]
    # Assert
    assert missing == []


def test_the_count_query_takes_no_limit_parameter():
    """A count is not paged; a stray limit parameter would be a driver error."""
    # Arrange
    expected = ("proj-alpha", "alice", "alice", "alice")
    # Act
    params = pbq.count_params("proj-alpha", "alice")
    # Assert
    assert params == expected


def test_asking_for_a_count_without_a_project_asks_the_store_nothing():
    """No project means no count, and no query for one."""
    # Arrange
    expected = 0
    # Act
    total = pbq.count_for("", "alice")
    # Assert
    assert total == expected


def test_the_comments_query_is_keyed_by_the_card_and_bounded():
    """The child table is indexed (task_id, seq) for exactly this read, and a card
    with hundreds of comments must not make the page grow without limit."""
    # Arrange
    sql = pbq.comments_sql()
    # Act
    shape = ("WHERE task_id = ?" in sql, "ORDER BY seq" in sql, "LIMIT ?" in sql)
    # Assert
    assert shape == (True, True, True)


def test_the_comments_query_skips_deleted_entries():
    """A deleted comment must not reappear on the page that deleted it."""
    # Arrange
    sql = pbq.comments_sql()
    # Act
    filtered = "deleted_at IS NULL" in sql
    # Assert
    assert filtered is True


def test_asking_for_comments_without_a_card_asks_the_store_nothing():
    """An empty id is a bug upstream, not a query for every comment."""
    # Arrange
    expected = []
    # Act
    comments = pbq.comments_for("")
    # Assert
    assert comments == expected


def test_projects_parameters_repeat_the_principal_per_owned_field():
    """A DISTINCT query needs the same predicate parameters, in the same order."""
    # Arrange
    expected = tuple("alice" for _ in OWNED_FIELDS)
    # Act
    params = pbq.projects_params("alice")
    # Assert
    assert params == expected


def test_a_staff_projects_query_takes_no_parameters():
    """No predicate means no parameters — a stray one would be a driver error."""
    # Arrange
    expected = ()
    # Act
    params = pbq.projects_params("", is_staff=True)
    # Assert
    assert params == expected


def test_asking_for_rows_without_a_project_asks_the_store_nothing():
    """An empty selection is not a query for every card with an empty project."""
    # Arrange
    expected = []
    # Act
    rows = pbq.rows_for("", "alice")
    # Assert
    assert rows == expected


def test_deleted_rows_are_excluded():
    """A soft-deleted card must not reappear on a project board."""
    # Arrange
    sql = pbq.rows_sql()
    # Act
    excluded = "COALESCE(is_deleted, false) = false" in sql
    # Assert
    assert excluded is True


def test_the_row_fields_are_the_ones_the_template_renders():
    """The query's projection and the card's markup are the same list of facts;
    a field added to one and not the other shows as a blank cell."""
    # Arrange
    rendered = {"id", "title", "status", "project", "assignee", "priority"}
    # Act
    missing = rendered - set(pbq.BOARD_ROW_FIELDS)
    # Assert
    assert missing == set()
