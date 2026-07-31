#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NULL-safe comparison must survive the move to PostgreSQL.

The inbox dedups on ``(event_type, card_id, ts, actor)`` and every one of those
columns is genuinely nullable. SQLite spells the NULL-safe comparison ``x IS ?``;
PostgreSQL rejects that outright -- ``IS`` there accepts only
NULL / TRUE / FALSE / UNKNOWN / DISTINCT FROM / OF.

THE DANGEROUS FIX IS THE OBVIOUS ONE. Rewriting ``IS`` to ``=`` parses cleanly
on both engines and then silently stops deduplicating: ``actor = NULL`` is
UNKNOWN, never true, so a re-emitted notification never matches an existing row
and the recipient gets duplicates forever. No error, no crash -- just a
notification storm, and on the supersede path the "at most one pending digest
per recipient" invariant quietly dies too.

``IS NOT DISTINCT FROM`` is the portable spelling: standard SQL, native on
PostgreSQL, and supported by SQLite since 3.39 (2022). The first test pins that
floor, because on an older SQLite the new form is a syntax error rather than a
silent wrong answer -- loud, but still a break, and the CI images are not
something this package controls.
"""

from __future__ import annotations

import sqlite3

MINIMUM_SQLITE = (3, 39)


def _version_tuple() -> tuple[int, ...]:
    return tuple(int(p) for p in sqlite3.sqlite_version.split("."))


class TestTheEngineSupportsThePortableForm:
    def test_sqlite_is_new_enough(self):
        """A floor, not a snapshot: >= is the invariant, not today's version."""
        # Arrange
        floor = MINIMUM_SQLITE

        # Act
        actual = _version_tuple()[:2]

        # Assert
        assert actual >= floor

    def test_the_operator_parses(self):
        """Cheaper to assert directly than to infer from a version string."""
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")

        # Act
        conn.execute("SELECT 1 FROM t WHERE a IS NOT DISTINCT FROM ?", (None,))

        # Assert
        conn.close()
        assert True


class TestItMatchesNullToNull:
    """The behaviour the dedup depends on."""

    def test_it_matches_a_null_column_against_a_null_parameter(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES (NULL)")

        # Act
        rows = conn.execute(
            "SELECT COUNT(*) FROM t WHERE a IS NOT DISTINCT FROM ?", (None,)
        ).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 1

    def test_it_still_matches_a_real_value(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES ('x')")

        # Act
        rows = conn.execute(
            "SELECT COUNT(*) FROM t WHERE a IS NOT DISTINCT FROM ?", ("x",)
        ).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 1

    def test_it_does_not_match_a_different_value(self):
        """The negative must be reachable, or the positives prove nothing."""
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES ('x')")

        # Act
        rows = conn.execute(
            "SELECT COUNT(*) FROM t WHERE a IS NOT DISTINCT FROM ?", ("y",)
        ).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 0


class TestTheNaiveRewriteIsMeasurablyWrong:
    """POSITIVE CONTROL for the whole change.

    Without this the suite would pass just as happily against ``=``, and the
    reason this rewrite exists would be a claim rather than a measurement.
    """

    def test_equals_fails_to_match_null(self):
        # Arrange
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES (NULL)")

        # Act
        rows = conn.execute("SELECT COUNT(*) FROM t WHERE a = ?", (None,)).fetchone()[0]

        # Assert
        conn.close()
        assert rows == 0


class TestTheInboxUsesThePortableForm:
    """Pins the call sites, so a later 'simplification' back to IS is caught."""

    def test_no_bare_is_placeholder_remains(self):
        # Arrange
        from scitex_cards import _inbox_sqlite

        source = __import__("pathlib").Path(_inbox_sqlite.__file__).read_text()

        # Act
        bare = source.count("IS ?")

        # Assert
        assert bare == 0

    def test_the_portable_form_is_present(self):
        """Seven comparisons, counted from the call sites rather than guessed.

        I first wrote 6 here and the test failed with ``assert 7 == 6`` -- my
        arithmetic, not the code. The breakdown, so the next reader does not
        repeat it: the supersede DELETE compares ``event_type`` and ``card_id``
        (2), the msg_id dedup compares ``msg_id`` (1), and the fallback dedup
        compares ``event_type``, ``card_id``, ``ts`` and ``actor`` (4).
        """
        # Arrange
        from scitex_cards import _inbox_sqlite

        source = __import__("pathlib").Path(_inbox_sqlite.__file__).read_text()

        # Act
        portable = source.count("IS NOT DISTINCT FROM ?")

        # Assert
        assert portable == 7


# EOF
