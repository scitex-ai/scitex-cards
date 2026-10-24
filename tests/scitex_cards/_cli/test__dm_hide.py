#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards dm hide|unhide`` contract (real PostgreSQL, no mocks).

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture: every
hide below targets the ambient per-test store, which is refused without a
writable cluster. The no-store tests (help text, unresolved identity) run
anywhere.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import main


def _invoke(*args: str):
    return CliRunner().invoke(main, ["dm", *args])


def test_help_registers_the_hide_and_unhide_verbs():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["dm", "--help"])
    # Assert
    assert (
        result.exit_code == 0
        and "hide" in result.output
        and "unhide" in result.output
    )


def test_hide_help_names_the_peer_and_the_append_only_boundary():
    # Arrange
    # Act
    result = _invoke("hide", "--help")
    words = " ".join(result.output.split())
    # Assert
    assert "--sender" in words and "append-only" in words


def test_unresolved_sender_is_a_clean_error(env):
    # Arrange
    env.delete("SCITEX_CARDS_AGENT_ID")
    # Act
    result = _invoke("hide", "operator")
    # Assert
    assert result.exit_code == 1 and "SCITEX_CARDS_AGENT_ID" in result.output


def test_hide_marks_thread_hidden(postgres_dsn, env):
    # Arrange
    env.set("SCITEX_CARDS_AGENT_ID", "hide-cli")
    _invoke("send", "operator", "hello", "--sender", "hide-cli")
    # Act
    hid = _invoke("hide", "operator", "--sender", "hide-cli", "--json")
    # Assert
    assert json.loads(hid.stdout)["hidden"] is True


def test_unhide_marks_thread_shown(postgres_dsn, env):
    # Arrange
    env.set("SCITEX_CARDS_AGENT_ID", "hide-cli")
    _invoke("send", "operator", "hello", "--sender", "hide-cli")
    _invoke("hide", "operator", "--sender", "hide-cli", "--json")
    # Act
    shown = _invoke("unhide", "operator", "--sender", "hide-cli", "--json")
    # Assert
    assert json.loads(shown.stdout)["hidden"] is False


def test_hide_unknown_peer_is_a_clean_error(postgres_dsn, env):
    # Arrange
    env.set("SCITEX_CARDS_AGENT_ID", "hide-cli")
    # Act
    result = _invoke("hide", "agent-ghost", "--sender", "hide-cli")
    # Assert
    assert result.exit_code == 1 and "unknown thread" in result.output


# EOF
