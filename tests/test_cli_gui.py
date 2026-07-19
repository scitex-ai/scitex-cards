#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``gui`` verb group — the operator's startup script calls it.

Regression cover for the 2026-07-12 incident: his `scitex_start_gui_servers`
loop runs ``<pkg> gui serve &`` across every SciTeX tool, and scitex-todo
exited with "No such command 'gui'", so nothing ever bound :8051 and his board
never came up. The board itself was fine — the VERB did not exist.

The contract these tests pin:
  * `gui serve` EXISTS and is the blocking/headless one (his script backgrounds
    it with `&`; a browser-opening serve would be wrong there)
  * `gui` fronts the SAME board lifecycle — one pidfile, not two racing ones
  * the bare noun hard-errors rather than guessing
"""

from click.testing import CliRunner

from scitex_cards._cli import main


def _run(*args):
    return CliRunner().invoke(main, list(args))


#: The exact argv his startup loop backgrounds with `&`. Named once so every
#: `gui serve` property below is asserted against the SAME invocation.
SERVE_ARGV = ("gui", "serve", "--dry-run")


def test_gui_group_is_registered():
    """The whole incident in one assertion: `gui` must exist."""
    # Arrange
    noun = "gui"
    # Act
    commands = main.commands
    # Assert
    assert noun in commands


def test_gui_exposes_the_four_standard_verbs():
    # Arrange
    expected = ["open", "serve", "status", "stop"]
    # Act
    verbs = sorted(main.commands["gui"].commands)
    # Assert
    assert verbs == expected


def test_gui_serve_runs_without_error():
    """`scitex-todo gui serve` — the exact call his startup loop makes."""
    # Arrange
    argv = SERVE_ARGV
    # Act
    result = _run(*argv)
    # Assert
    assert result.exit_code == 0


def test_gui_serve_defaults_to_port_8051():
    """His script expects 8051; `board` already defaults there. Don't drift."""
    # Arrange
    argv = SERVE_ARGV
    # Act
    result = _run(*argv)
    # Assert
    assert "8051" in result.output


def test_gui_serve_does_not_open_a_browser():
    """`serve` is headless by contract — `open` is the browser one."""
    # Arrange
    argv = SERVE_ARGV
    # Act
    result = _run(*argv)
    # Assert
    assert "no browser" in result.output


def test_gui_serve_accepts_a_host():
    # Arrange
    argv = ("gui", "serve", "--host", "0.0.0.0", "--dry-run")
    # Act
    result = _run(*argv)
    # Assert
    assert result.exit_code == 0


def test_gui_serve_binds_loopback_by_default():
    """The board is UNAUTHENTICATED and serves every agent's cards."""
    # Arrange
    argv = SERVE_ARGV
    # Act
    result = _run(*argv)
    # Assert
    assert "127.0.0.1" in result.output


def test_bare_gui_noun_is_a_usage_error():
    """Noun-verb convention (operator directive TG 13316) — no guessing."""
    # Arrange
    argv = ("gui",)
    # Act
    result = _run(*argv)
    # Assert
    assert result.exit_code == 2


def test_bare_gui_noun_names_the_verbs():
    # Arrange
    argv = ("gui",)
    # Act
    result = _run(*argv)
    # Assert
    assert "gui serve" in result.output


def test_gui_status_shares_the_board_pidfile():
    """`gui` must FRONT the board lifecycle, never run a rival one.

    Two lifecycles racing for one port is the bug this aliasing avoids, so
    pin that the two verbs report from the same pidfile.
    """
    # Arrange
    verb = ("status", "--json")
    # Act
    gui_output = _run("gui", *verb).output
    board_output = _run("board", *verb).output
    # Assert
    assert gui_output == board_output


def test_gui_stop_is_safe_when_nothing_runs():
    # Arrange
    argv = ("gui", "stop", "--dry-run")
    # Act
    result = _run(*argv)
    # Assert
    assert result.exit_code == 0


def test_board_group_still_exists():
    """`gui` is an ALIAS, not a replacement — `board` stays canonical."""
    # Arrange
    noun = "board"
    # Act
    commands = main.commands
    # Assert
    assert noun in commands
