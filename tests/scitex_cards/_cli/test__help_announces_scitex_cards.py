#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Help text and agent-facing hints must say `scitex-cards`.

`_render_fallback_help` renders every `{prog}` placeholder on installs WITHOUT
scitex-dev's spec-help. Its default `prog` said `scitex-todo`, so on those
installs the whole CLI taught the old name in every example -- while the
scitex-dev path (`_main.py`'s `version_of` / `prog_name`) already said
`scitex-cards`. The two must not disagree.

No mocks (STX-NM / PA-306). One assertion per test (TQ002 / TQ007).
"""

from __future__ import annotations

import importlib.util

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._compat import _render_fallback_help

#: The [mcp] extra is optional, so gate the FastMCP-instance test at COLLECTION
#: time rather than branching inside the test body.
_HAS_FASTMCP = importlib.util.find_spec("fastmcp") is not None


def _fallback_example() -> str:
    return _render_fallback_help(
        "summary",
        (),
        (("{prog} list-tasks", "note"),),
        (),
        None,
    )


def test_fallback_help_renders_the_current_prog():
    # Arrange
    # Act
    text = _fallback_example()
    # Assert
    assert "scitex-cards list-tasks" in text


def test_fallback_help_does_not_teach_the_old_prog():
    # Arrange
    # Act
    text = _fallback_example()
    # Assert
    assert "scitex-todo" not in text


def test_board_redirect_error_names_the_current_cli():
    # Arrange
    runner = CliRunner()
    # Act — the bare-noun hard error is pure remediation text.
    result = runner.invoke(main, ["board"])
    # Assert
    assert "scitex-cards board start" in result.output


def test_gui_redirect_error_names_the_current_cli():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["gui"])
    # Assert
    assert "scitex-cards gui serve" in result.output


def test_mcp_install_hint_names_an_installable_extra():
    # Arrange — the hint used to say `pip install 'scitex-todo[mcp]'`, which is
    # not merely stale: the [mcp] extra is declared by scitex-cards, so the old
    # hint pointed at the superseded dist.
    from scitex_cards._cli._mcp import _INSTALL_HINT

    # Act
    # Assert
    assert "scitex-cards[mcp]" in _INSTALL_HINT


@pytest.mark.skipif(not _HAS_FASTMCP, reason="the [mcp] extra is not installed")
def test_mcp_server_instance_announces_scitex_cards():
    # Arrange — this is the name agents see the server by.
    from scitex_cards._mcp_app import mcp

    # Act
    # Assert
    assert mcp.name == "scitex-cards"


def test_mcp_instructions_open_with_the_current_name():
    # Arrange
    from scitex_cards._mcp_instructions import build_instructions

    # Act — the single most-read sentence the package ships.
    text = build_instructions("some-agent")
    # Assert
    assert text.startswith("scitex-cards:")


# EOF
