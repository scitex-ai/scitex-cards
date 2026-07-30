#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards db set-min-client-version`` — the deliberate admin verb.

Setting the floor is what makes it enforceable at all (see
``../test__min_client_version.py`` for the enforcement side, at the
``_db.connect`` chokepoint). This file covers the CLI verb itself: it must
refuse a floor above its own running version (that would brick the very
client setting it), and a valid call must actually persist the floor.

``db_group`` is invoked directly (the same pattern as
``test__db_snapshot_freshness_guard.py``), relying on the suite-wide
``$SCITEX_CARDS_DB`` pin (``tests/conftest.py``) for the target database.
"""

from __future__ import annotations

import os

from click.testing import CliRunner

from scitex_cards._cli._db import db_group
from scitex_cards._db import connect
from scitex_cards._min_client_version import read_floor, resolve_running_version


def _db_path() -> str:
    return os.environ["SCITEX_CARDS_DB"]


def _read_floor_now() -> str | None:
    conn = connect(_db_path())
    try:
        return read_floor(conn)
    finally:
        conn.close()


def test_set_min_client_version_refuses_a_floor_above_the_running_client():
    # Arrange
    running = resolve_running_version()
    too_high = "9999.0.0"
    assert too_high != running  # sanity: this must actually exercise "too high"

    # Act
    result = CliRunner().invoke(db_group, ["set-min-client-version", too_high])

    # Assert
    assert result.exit_code != 0


def test_refusing_a_high_floor_names_both_versions_in_the_error():
    # Arrange
    running = resolve_running_version()
    too_high = "9999.0.0"

    # Act
    result = CliRunner().invoke(db_group, ["set-min-client-version", too_high])

    # Assert
    assert running in result.output
    assert too_high in result.output


def test_refusing_a_high_floor_leaves_no_floor_stamped():
    # Arrange
    too_high = "9999.0.0"

    # Act
    CliRunner().invoke(db_group, ["set-min-client-version", too_high])

    # Assert
    assert _read_floor_now() is None


def test_set_min_client_version_accepts_a_floor_below_the_running_client():
    # Arrange
    low_floor = "0.0.1"

    # Act
    result = CliRunner().invoke(db_group, ["set-min-client-version", low_floor])

    # Assert
    assert result.exit_code == 0, result.output


def test_accepting_a_floor_persists_it_to_schema_meta():
    # Arrange
    low_floor = "0.0.1"

    # Act
    CliRunner().invoke(db_group, ["set-min-client-version", low_floor])

    # Assert
    assert _read_floor_now() == "0.0.1"


def test_set_min_client_version_accepts_a_floor_exactly_equal_to_the_running_client():
    # Arrange
    running = resolve_running_version()

    # Act
    result = CliRunner().invoke(db_group, ["set-min-client-version", running])

    # Assert
    assert result.exit_code == 0, result.output


def test_accepting_an_equal_floor_persists_the_exact_version_string():
    # Arrange
    running = resolve_running_version()

    # Act
    CliRunner().invoke(db_group, ["set-min-client-version", running])

    # Assert
    assert _read_floor_now() == running


# EOF
