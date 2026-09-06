#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for backend selection and paramstyle translation.

The literal-handling tests are the ones that matter. A naive
``sql.replace("?", "%s")`` passes every "does it translate a placeholder" test
and silently corrupts any string literal containing a question mark -- and card
titles, message bodies and notes contain them constantly. That class of defect
produces WRONG DATA rather than an error, so it is pinned from both directions.
"""

import pytest

from scitex_cards._store_url import (
    BACKEND_POSTGRES,
    BACKEND_UNSUPPORTED,
    backend_of,
    is_postgres_url,
    to_paramstyle,
)


class TestBackendSelection:
    @pytest.mark.parametrize(
        "url",
        [
            "postgresql://user@host/db",
            "postgres://user@host/db",
            "POSTGRESQL://user@host/db",
            "  postgresql://user@host/db  ",
        ],
        ids=["canonical", "short", "uppercase", "padded"],
    )
    def test_postgres_urls_select_postgres(self, url):
        # Arrange
        target = url
        # Act
        backend = backend_of(target)
        # Assert
        assert backend == BACKEND_POSTGRES

    @pytest.mark.parametrize(
        "path",
        [
            "/home/ywatanabe/.scitex/cards/cards.db",
            "cards.db",
            "./relative/cards.db",
            "/tmp/postgres/cards.db",
        ],
        ids=["absolute", "bare", "relative", "path-mentioning-postgres"],
    )
    def test_filesystem_paths_are_unsupported(self, path):
        # Arrange
        target = path
        # Act
        backend = backend_of(target)
        # Assert
        assert backend == BACKEND_UNSUPPORTED

    def test_a_path_containing_the_word_postgres_is_still_unsupported(self):
        # Arrange: the scheme is what decides, not the presence of a substring.
        target = "/var/lib/postgresql/cards.db"
        # Act
        backend = backend_of(target)
        # Assert
        assert backend == BACKEND_UNSUPPORTED

    def test_non_string_is_not_a_url(self):
        # Arrange
        target = None
        # Act
        result = is_postgres_url(target)
        # Assert
        assert result is False

    def test_non_string_still_gets_an_answer(self):
        # Arrange: backend_of is total -- every input resolves.
        target = None
        # Act
        backend = backend_of(target)
        # Assert
        assert backend == BACKEND_UNSUPPORTED

    def test_empty_string_is_unsupported(self):
        # Arrange
        target = ""
        # Act
        backend = backend_of(target)
        # Assert
        assert backend == BACKEND_UNSUPPORTED


class TestParamstyleNonPostgresIsUntouched:
    def test_non_postgres_sql_is_returned_unchanged(self):
        # Arrange
        sql = "SELECT * FROM tasks WHERE id = ? AND agent = ?"
        # Act
        result = to_paramstyle(sql, BACKEND_UNSUPPORTED)
        # Assert
        assert result == sql

    def test_non_postgres_leaves_percent_signs_alone(self):
        # Arrange
        sql = "SELECT * FROM tasks WHERE title LIKE '%urgent%'"
        # Act
        result = to_paramstyle(sql, BACKEND_UNSUPPORTED)
        # Assert
        assert result == sql


class TestParamstylePostgresPlaceholders:
    def test_a_single_placeholder_becomes_percent_s(self):
        # Arrange
        sql = "SELECT * FROM tasks WHERE id = ?"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT * FROM tasks WHERE id = %s"

    def test_every_placeholder_is_translated(self):
        # Arrange
        sql = "INSERT INTO t(a, b, c) VALUES(?, ?, ?)"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "INSERT INTO t(a, b, c) VALUES(%s, %s, %s)"

    def test_no_placeholders_leaves_sql_alone(self):
        # Arrange
        sql = "SELECT COUNT(*) FROM tasks"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == sql


class TestAQuestionMarkInsideALiteralIsNotAPlaceholder:
    """The defect a naive replace() would ship: card titles and message bodies
    contain question marks constantly, and corrupting one produces wrong data
    rather than an error.
    """

    def test_a_question_mark_in_a_literal_survives(self):
        # Arrange
        sql = "SELECT * FROM tasks WHERE title = 'is it done?'"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT * FROM tasks WHERE title = 'is it done?'"

    def test_a_naive_replace_would_have_corrupted_it(self):
        # Arrange: pins WHY this module is not one line of str.replace.
        sql = "SELECT * FROM tasks WHERE title = 'is it done?'"
        # Act
        naive = sql.replace("?", "%s")
        # Assert
        assert naive != to_paramstyle(sql, BACKEND_POSTGRES)

    def test_placeholders_outside_a_literal_still_translate(self):
        # Arrange
        sql = "SELECT * FROM t WHERE title = 'really?' AND id = ?"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT * FROM t WHERE title = 'really?' AND id = %s"

    def test_an_escaped_quote_does_not_end_the_literal(self):
        # Arrange: SQL escapes a quote by doubling it, so the literal here is
        # it's fine? -- and the ? inside must survive.
        sql = "SELECT * FROM t WHERE note = 'it''s fine?' AND id = ?"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT * FROM t WHERE note = 'it''s fine?' AND id = %s"

    def test_multiple_literals_each_protect_their_contents(self):
        # Arrange
        sql = "SELECT ? WHERE a = 'x?' AND b = 'y?' AND c = ?"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT %s WHERE a = 'x?' AND b = 'y?' AND c = %s"


class TestPercentSignsMustBeDoubledForPostgres:
    def test_a_like_pattern_has_its_percents_doubled(self):
        # Arrange: with %s paramstyle an undoubled % is a format specifier and
        # raises at execution time.
        sql = "SELECT * FROM tasks WHERE title LIKE '%urgent%'"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT * FROM tasks WHERE title LIKE '%%urgent%%'"

    def test_a_percent_outside_a_literal_is_also_doubled(self):
        # Arrange
        sql = "SELECT 100 % 7"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT 100 %% 7"

    def test_percents_and_placeholders_coexist(self):
        # Arrange
        sql = "SELECT * FROM t WHERE title LIKE '%x%' AND id = ?"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result == "SELECT * FROM t WHERE title LIKE '%%x%%' AND id = %s"


class TestRealStatementsFromThisPackage:
    def test_the_dm_message_insert_translates_correctly(self):
        # Arrange: the actual statement from _dm_write.insert_message.
        sql = (
            "INSERT OR IGNORE INTO dm_messages"
            "(id, thread_id, sender, body, ts, seq, origin_host, record_json)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)"
        )
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result.endswith("VALUES(%s, %s, %s, %s, %s, %s, %s, %s)")

    def test_translation_does_not_invent_or_lose_placeholders(self):
        # Arrange
        sql = "INSERT INTO t VALUES(?, ?, ?, ?, ?, ?, ?, ?)"
        # Act
        result = to_paramstyle(sql, BACKEND_POSTGRES)
        # Assert
        assert result.count("%s") == sql.count("?")


# EOF
