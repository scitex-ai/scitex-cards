#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``read_floor`` must answer, not raise, on every shape of store it can meet."""

import pytest

from scitex_cards._db import connect
from scitex_cards._min_client_version import (
    KEY_MIN_CLIENT_VERSION,
    read_floor,
)

_META = "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)"


@pytest.fixture
def empty(new_store):
    """A store with nothing on it — not even ``schema_meta``."""
    conn = connect(new_store("cards_floor", bootstrap=False))
    yield conn
    conn.close()


def test_absent_schema_meta_reads_as_no_floor(empty):
    """The case the old ``except the retired driver.OperationalError`` could not catch.

    A brand-new store has no ``schema_meta``, and the gate's documented contract
    for that is "no floor stamped, so this is a no-op" — not a raise out of the
    first connection anyone opens.
    """
    # Arrange
    conn = empty

    # Act
    floor = read_floor(conn)

    # Assert
    assert floor is None


def test_present_table_without_the_key_reads_as_no_floor(empty):
    """A provisioned store that has never been stamped is still ungated."""
    # Arrange
    empty.execute(_META)

    # Act
    floor = read_floor(empty)

    # Assert
    assert floor is None


def test_stamped_floor_is_read_by_column_name(empty):
    """The case ``row[0]`` could not survive: dict_row refuses position."""
    # Arrange
    empty.execute(_META)
    empty.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        (KEY_MIN_CLIENT_VERSION, "0.27.0"),
    )

    # Act
    floor = read_floor(empty)

    # Assert
    assert floor == "0.27.0"


# EOF
