#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An unexpanded shell variable is not a store target and must be refused.

Measured 2026-08-18: ``SCITEX_CARDS_DB='${SCITEX_CARDS_DB}'`` -- the literal --
resolved to a legitimate target, because it is non-empty (so the zero-config
refusal never fires) and not DSN-shaped (so the DSN refusal never inspects it).
Eight agents then shared one database file named after the variable, and four
operator messages were written into it and delivered to nobody.
"""

import pytest

from scitex_cards._db import connect
from scitex_cards._store_url import (
    UnrecognisedStoreTarget,
    is_unexpanded_variable,
    reject_unexpanded_variable,
)

THE_FIELD_LITERAL = "${SCITEX_CARDS_DB}"


def test_the_braced_literal_that_was_measured_in_the_field_is_detected():
    # Arrange
    target = THE_FIELD_LITERAL

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is True


def test_a_braced_variable_inside_a_longer_path_is_detected():
    # Arrange
    target = "/home/agent/${PROJ}/cards.db"

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is True


def test_a_command_substitution_is_detected():
    # Arrange
    target = "$(cat /etc/store-path)"

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is True


def test_a_real_absolute_path_is_not_detected():
    # A POSITIVE CONTROL IN THE OTHER DIRECTION: this guard must never refuse a
    # store that works today. Every deployment in service holds a path.
    # Arrange
    target = "/home/agent/.scitex/cards/cards.db"

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is False


def test_a_postgres_dsn_is_not_detected():
    # Arrange
    target = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is False


def test_a_bare_dollar_name_is_deliberately_not_detected():
    # THE CHOSEN GAP, ASSERTED SO IT STAYS CHOSEN. "$" is legal in a POSIX
    # filename, so matching a bare "$FOO" could refuse a store that works.
    # If this test ever fails the decision was changed -- read the predicate's
    # docstring before deciding that is an improvement.
    # Arrange
    target = "/data/$FOO/cards.db"

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is False


def test_a_non_string_target_is_not_detected():
    # Arrange
    target = None

    # Act
    detected = is_unexpanded_variable(target)

    # Assert
    assert detected is False


def test_rejecting_the_field_literal_raises_unrecognised_store_target():
    # Arrange
    target = THE_FIELD_LITERAL

    # Act
    # Assert
    with pytest.raises(UnrecognisedStoreTarget):
        reject_unexpanded_variable(target)


def test_the_refusal_names_the_offending_value_so_it_is_actionable():
    # Arrange
    captured = ""

    # Act
    try:
        reject_unexpanded_variable(THE_FIELD_LITERAL)
    except UnrecognisedStoreTarget as refusal:
        captured = str(refusal)

    # Assert
    assert THE_FIELD_LITERAL in captured


def test_rejecting_a_real_path_returns_without_raising():
    # Arrange
    target = "/home/agent/.scitex/cards/cards.db"

    # Act
    outcome = reject_unexpanded_variable(target)

    # Assert
    assert outcome is None


def test_connect_refuses_the_literal_instead_of_creating_a_database(tmp_path):
    # THE DOOR THAT MATTERS. Opening is where a wrong string stops being a
    # wrong string and becomes a real, empty board that answers every query.
    # Arrange
    target = str(tmp_path / THE_FIELD_LITERAL)

    # Act
    # Assert
    with pytest.raises(UnrecognisedStoreTarget):
        connect(target)


def test_connect_refusing_the_literal_leaves_no_file_behind(tmp_path):
    # Arrange
    target = str(tmp_path / THE_FIELD_LITERAL)

    # Act
    try:
        connect(target)
    except UnrecognisedStoreTarget:
        pass

    # Assert
    assert list(tmp_path.iterdir()) == []
