#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The inbox rail's table, column and order names, as one object.

WHAT THIS MODULE USED TO BE, AND WHY MOST OF IT IS GONE. It was a per-backend
SEAM: two shapes, and a ``shape_for(conn)`` that picked between them by looking
at the live connection. Its load-bearing test asserted that one of those two
spellings was byte-identical to what production ran, so the other could be
rewired without changing behaviour.

There is one storage engine now. A function that chooses between two engines'
spellings has nothing left to choose, and a constant describing the retired
engine's table names describes nothing that exists -- so ``shape_for`` and the
second shape are gone from ``_inbox_shape``, and the tests that pinned the
DISPATCH went with them. They are listed here rather than silently dropped:

    the retired shape's table / recipient / order clause   (3 tests)
    "the two shapes differ in all three fields"            (1 test)
    "the connection selects the shape"                     (2 tests)

The last pair is worth one more sentence, because deleting a test that used to
pass deserves an argument rather than a shrug. Its subject was that the shape
is read from WHAT IS ACTUALLY OPEN rather than from a caller's belief -- a good
rule. But with a single engine there is no second answer for the resolver to
get wrong: the property is now enforced by there being nothing to resolve,
which is stronger than a test asserting the resolver resolves correctly.

WHAT SURVIVES IS THE REASON THE OBJECT EXISTS AT ALL. The ordering is bundled
with the names, and that is why this is one frozen object and not three loose
constants: a table rename plus a column rename produces SQL that is valid and
silently loses delivery order. Bundling the order expression with the names
makes "renamed the table but kept the old order column" unrepresentable rather
than merely discouraged.
"""

from __future__ import annotations

from scitex_cards._inbox_shape import POSTGRES_SHAPE, InboxShape


class TestTheShapeNamesTheCanonicalStore:
    def test_the_table_is_notifications(self):
        # Arrange
        shape = POSTGRES_SHAPE

        # Act
        table = shape.table

        # Assert
        assert table == "notifications"

    def test_the_recipient_column_is_recipient_id(self):
        # Arrange
        shape = POSTGRES_SHAPE

        # Act
        column = shape.recipient

        # Assert
        assert column == "recipient_id"

    def test_the_order_clause_uses_the_v9_column(self):
        """NOT ``rowid``, which does not exist here, and NOT ``ts``."""
        # Arrange
        shape = POSTGRES_SHAPE

        # Act
        clause = shape.order()

        # Assert
        assert clause == "ORDER BY seq"


class TestTheShapeIsImmutable:
    """A shared module-level constant that a caller can mutate is a footgun."""

    def test_a_shape_cannot_be_reassigned(self):
        # Arrange
        shape = InboxShape(table="t", recipient="r", order_by="o")

        # Act
        try:
            shape.table = "other"
            raised = None
        except Exception as exc:
            raised = exc

        # Assert
        assert raised is not None


# EOF
