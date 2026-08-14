#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards dev cardsync`` — endpoint parsing and command wiring.

The endpoint parser is pure, so it is tested directly. The command itself is
exercised through Click's runner against ``--help``, which proves it is
mounted and documented without needing two live databases.

Assertions read ``result.stdout`` rather than ``result.output``: CliRunner
folds stderr into ``output`` by default, so asserting on it passes when the
text arrives on the wrong stream.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli._main import main
from scitex_cards._cli._cardsync import parse_endpoint


def _parse_error(spec: str) -> str:
    """The parser's complaint, as a value, so a test keeps one assertion."""
    try:
        parse_endpoint(spec)
    except ValueError as exc:
        return str(exc)
    return ""


# -- parsing NAME=DSN -----------------------------------------------------
def test_the_name_is_taken_from_before_the_equals():
    # Arrange
    spec = "laptop=postgresql://u@h:5432/db"
    # Act
    name, _ = parse_endpoint(spec)
    # Assert
    assert name == "laptop"


def test_the_dsn_is_taken_from_after_the_equals():
    # Arrange
    spec = "laptop=postgresql://u@h:5432/db"
    # Act
    _, dsn = parse_endpoint(spec)
    # Assert
    assert dsn == "postgresql://u@h:5432/db"


def test_a_libpq_dsn_keeps_its_own_equals_signs():
    # Arrange — key=value DSNs are legal and must not be split on every '='
    spec = "peer=host=127.0.0.1 port=55432 dbname=scitex_cards"
    # Act
    _, dsn = parse_endpoint(spec)
    # Assert
    assert dsn == "host=127.0.0.1 port=55432 dbname=scitex_cards"


def test_surrounding_whitespace_is_stripped():
    # Arrange
    spec = "  laptop  =  postgresql://u@h/db  "
    # Act
    name, _ = parse_endpoint(spec)
    # Assert
    assert name == "laptop"


def test_a_bare_dsn_without_a_name_is_refused():
    # Arrange — the name is what the operator reads in the report
    spec = "postgresql://u@h/db"
    # Act
    message = _parse_error(spec)
    # Assert
    assert "NAME=DSN" in message


def test_an_empty_name_is_refused():
    # Arrange
    spec = "=postgresql://u@h/db"
    # Act
    message = _parse_error(spec)
    # Assert
    assert "NAME=DSN" in message


def test_an_empty_dsn_is_refused():
    # Arrange
    spec = "laptop="
    # Act
    message = _parse_error(spec)
    # Assert
    assert "NAME=DSN" in message


def test_the_refusal_shows_a_working_example():
    # Arrange — an error naming the rule but not the shape still costs a lookup
    spec = "nonsense"
    # Act
    message = _parse_error(spec)
    # Assert
    assert "postgresql://" in message


# -- the command is mounted and documented --------------------------------
def test_cardsync_is_mounted_under_the_dev_group():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dev", "cardsync", "--help"])
    # Assert
    assert result.exit_code == 0


def test_the_group_help_names_the_report_verb():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dev", "cardsync", "--help"])
    # Assert
    assert "report" in result.stdout


def test_the_report_help_says_it_writes_nothing():
    # Arrange — read-only is the load-bearing property; it belongs in --help
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dev", "cardsync", "report", "--help"])
    # Assert
    assert "--fail-on-diverged" in result.stdout


def test_a_malformed_endpoint_fails_before_any_connection():
    # Arrange — an unreachable DSN would hang; parsing must reject first
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dev", "cardsync", "report", "nonsense", "also-nonsense"])
    # Assert
    assert result.exit_code != 0

# EOF
