#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``read_floor`` must answer, not raise, on every shape of store it can meet.

TWO SQLITE-SHAPED ASSUMPTIONS LIVED IN ONE SMALL FUNCTION, and both of them
fail CLOSED on PostgreSQL in a way that turns a no-op into a crash:

1. ``except sqlite3.OperationalError`` around the SELECT was how the function
   recognised "``schema_meta`` does not exist yet". PostgreSQL raises
   ``psycopg.errors.UndefinedTable`` for that condition, which the clause did
   not catch — so opening a BRAND-NEW store would raise out of a function whose
   documented contract is "no floor stamped, gate is a no-op".

2. ``row[0]`` is POSITIONAL. The previous driver's row accepted both ``row[0]``
   and ``row["value"]``; psycopg's ``dict_row`` accepts only the latter.
   :func:`scitex_cards._backend_connect.connect` deliberately declines to paper
   over that asymmetry so the port finds these call sites while they are cheap.

THIS FILE WAS HALF A COMPARISON AND IS NOW A CONTRACT. It ran each case twice —
once against a scratch file, once against a server — because the function had to
agree with itself across two engines. There is one engine, so the file-backed
half is gone; what is kept is the three answers, asserted where they matter.
Both former server tests ALSO never ran: they gated on ``$SCITEX_CARDS_TEST_PG``,
a private marker nothing sets any more, and fell back to a hardcoded
``127.0.0.1:5432`` nobody serves. So the surviving coverage was the half that
could not fail. They take the harness's store now, which FAILS rather than skips.
"""

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
    """The case the old ``except sqlite3.OperationalError`` could not catch.

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
