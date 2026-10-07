#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the database-migration family subpackage."""

from __future__ import annotations

import pickle

from scitex_cards import _db_dm_idempotency as old_idem_mod
from scitex_cards import _db_notification_exchange as old_exch_mod
from scitex_cards._database import schema_rungs as new_mod
from scitex_cards._database.schema_rungs import (
    _migrate_v13_to_v14 as new_v13,
    _migrate_v14_to_v15 as new_v14,
)
from scitex_cards._db_dm_idempotency import _migrate_v14_to_v15 as old_v14
from scitex_cards._db_notification_exchange import (
    _migrate_v13_to_v14 as old_v13,
)


def test_old_exchange_path_is_the_new_object():
    # Arrange
    old, new = old_v13, new_v13
    # Act
    identical = old is new
    # Assert
    assert identical


def test_old_idempotency_path_is_the_new_object():
    # Arrange
    old, new = old_v14, new_v14
    # Act
    identical = old is new
    # Assert
    assert identical


def test_exchange_module_attribution_survives_the_move():
    # Arrange
    module = new_v13.__module__
    # Act
    expected = "scitex_cards._db_notification_exchange"
    # Assert
    assert module == expected


def test_idempotency_module_attribution_survives_the_move():
    # Arrange
    module = new_v14.__module__
    # Act
    expected = "scitex_cards._db_dm_idempotency"
    # Assert
    assert module == expected


def test_exchange_pickle_round_trip_resolves_through_the_old_path():
    # Arrange
    blob = pickle.dumps(old_v13)
    # Act
    revived = pickle.loads(blob)  # noqa: S301 - intentional: the pinned contract
    # Assert
    assert revived is old_v13


def test_idempotency_pickle_round_trip_resolves_through_the_old_path():
    # Arrange
    blob = pickle.dumps(old_v14)
    # Act
    revived = pickle.loads(blob)  # noqa: S301 - intentional: the pinned contract
    # Assert
    assert revived is old_v14


def test_old_all_contracts_unchanged_and_new_exposes_both():
    # Arrange
    old_exch_all = old_exch_mod.__all__
    old_idem_all = old_idem_mod.__all__
    new_all = new_mod.__all__
    # Act
    contracts = (old_exch_all, old_idem_all, new_all)
    # Assert
    assert contracts == (
        ["_migrate_v13_to_v14"],
        ["_migrate_v14_to_v15"],
        ["_migrate_v13_to_v14", "_migrate_v14_to_v15"],
    )

# EOF
