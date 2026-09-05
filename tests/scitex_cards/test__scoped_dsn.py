#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scoped-DSN gate: a search_path the DSN asks for must be the one in force.

THE SERVER HALF RUNS AGAINST A REAL SERVER AND DOES NOT SKIP. The defect this
gate answers (2026-09-05: a transaction-mode pooler dropped
``options=-csearch_path`` silently and the harness sat on the live board) is
invisible to a mock by construction — a mock returns whatever search_path it
is told to. The throwaway schema the root conftest pins is the server, and its
absence is a failure.

WHAT CANNOT BE TESTED HERE, said plainly: no pooler sits between this suite and
its PostgreSQL, so the end-to-end refusal through ``connect()`` cannot be
provoked by the environment. The comparison is exercised instead by asking the
gate about a DSN that names a DIFFERENT schema from the one the session holds —
the same inputs the pooler produces (a request the session does not carry),
reached from the other side.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._backend_connect import connect
from scitex_cards._scoped_dsn import (
    SearchPathNotApplied,
    assert_search_path_applied,
    requested_search_path,
    search_path_in_force,
)


@pytest.fixture
def store_dsn() -> str:
    dsn = os.environ.get("SCITEX_CARDS_DB", "")
    if "search_path" not in dsn:
        pytest.fail(
            "the root conftest did not pin $SCITEX_CARDS_DB to a throwaway "
            f"PostgreSQL schema; it holds {dsn!r}. A failure, not a skip: a "
            "skipped gate test and a passing one look identical.",
            pytrace=False,
        )
    return dsn


# --------------------------------------------------------------------------- #
# The parser reads what libpq reads                                            #
# --------------------------------------------------------------------------- #
def test_a_dsn_without_options_asks_for_nothing():
    # Arrange
    dsn = "postgresql://scitex-primary:55433/scitex"
    # Act
    wanted = requested_search_path(dsn)
    # Assert
    assert wanted == ""


def test_a_single_options_names_its_schema():
    # Arrange
    dsn = "postgresql://h:55433/db?options=-csearch_path%3Dcards_tests_aaa"
    # Act
    wanted = requested_search_path(dsn)
    # Assert
    assert wanted == "cards_tests_aaa"


def test_the_last_of_two_options_wins_as_libpq_has_it():
    # Arrange: an xdist worker's DSN - the controller's scope, then its own
    dsn = (
        "postgresql://h:55433/db?options=-csearch_path%3Dcards_tests_controller"
        "&options=-csearch_path%3Dcards_tests_worker"
    )
    # Act
    wanted = requested_search_path(dsn)
    # Assert
    assert wanted == "cards_tests_worker"


def test_the_spaced_form_and_the_conninfo_form_are_read_too():
    # Arrange
    spaced = "postgresql://h:55433/db?options=-c%20search_path%3Dws_one"
    conninfo = "host=h port=55433 dbname=db options='-csearch_path=ws_two'"
    # Act
    got = (requested_search_path(spaced), requested_search_path(conninfo))
    # Assert
    assert got == ("ws_one", "ws_two")


def test_only_the_first_schema_of_a_list_is_the_scope():
    # Arrange
    dsn = "postgresql://h:55433/db?options=-csearch_path%3D%22ws_q%22%2Cpublic"
    # Act
    wanted = requested_search_path(dsn)
    # Assert
    assert wanted == "ws_q"


# --------------------------------------------------------------------------- #
# The server half                                                              #
# --------------------------------------------------------------------------- #
def test_the_pinned_schema_is_the_one_in_force(store_dsn):
    # Arrange
    wanted = requested_search_path(store_dsn)
    # Act
    with connect(store_dsn) as conn:
        in_force = search_path_in_force(conn.raw)
    # Assert
    assert wanted in in_force


def test_connect_accepts_a_dsn_whose_scope_the_server_applied(store_dsn):
    # Arrange
    # Act
    with connect(store_dsn) as conn:
        row = conn.fetchone("SELECT 1 AS one")
    # Assert
    assert row is not None


@pytest.fixture
def a_request_the_session_does_not_carry(store_dsn) -> tuple[str, str]:
    """The pinned DSN rewritten to ask for a schema the session was never given.

    The pooler's failure from the other side: same session, a request it does
    not carry. Returns ``(the schema in force, the schema asked for)``.
    """
    wanted = requested_search_path(store_dsn)
    other = store_dsn.replace(wanted, "cards_tests_not_this_session")
    return wanted, other


def test_a_request_the_session_does_not_carry_is_refused(
    store_dsn, a_request_the_session_does_not_carry
):
    # Arrange
    _, other = a_request_the_session_does_not_carry
    # Act
    with connect(store_dsn) as conn:
        # Assert
        with pytest.raises(SearchPathNotApplied):
            assert_search_path_applied(conn.raw, other)


@pytest.fixture
def refusal_message(store_dsn, a_request_the_session_does_not_carry) -> str:
    _, other = a_request_the_session_does_not_carry
    with connect(store_dsn) as conn:
        try:
            assert_search_path_applied(conn.raw, other)
        except SearchPathNotApplied as exc:
            return str(exc)
    pytest.fail("the gate accepted a search_path the session does not carry")


def test_the_refusal_names_the_schema_asked_for(refusal_message):
    # Arrange
    asked_for = "cards_tests_not_this_session"
    # Act
    named = asked_for in refusal_message
    # Assert
    assert named, refusal_message


def test_the_refusal_names_the_schema_in_force(
    refusal_message, a_request_the_session_does_not_carry
):
    # Arrange
    in_force, _ = a_request_the_session_does_not_carry
    # Act
    named = in_force in refusal_message
    # Assert
    assert named, refusal_message


def test_the_refusal_names_the_remedy(refusal_message):
    # Arrange
    remedy = "55433"
    # Act
    named = remedy in refusal_message
    # Assert
    assert named, refusal_message


def test_a_dsn_that_asks_for_nothing_is_not_checked(store_dsn):
    # Arrange
    unscoped = store_dsn.split("?", 1)[0]
    # Act
    with connect(store_dsn) as conn:
        assert_search_path_applied(conn.raw, unscoped)
    # Assert: no exception is the assertion
    assert requested_search_path(unscoped) == ""


# EOF
