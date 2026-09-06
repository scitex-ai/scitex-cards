#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Help text and agent-facing hints must say `scitex-cards`.

`_render_fallback_help` renders every `{prog}` placeholder on installs WITHOUT
scitex-dev's spec-help. Its default `prog` said `scitex-cards`, so on those
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
    # Arrange — the hint used to say `pip install 'scitex-cards[mcp]'`, which was
    # wrong twice over: it named the superseded DISTRIBUTION, and it named a
    # PARTIAL extra. The partial half is what took the fleet down on 2026-08-02
    # — the container defs pinned scitex-cards[mcp], which does not pull
    # psycopg, so every agent lost the store after the PostgreSQL cutover.
    # Remedies now name [all], so following a hint cannot under-install.
    from scitex_cards._cli._mcp import _INSTALL_HINT

    # Act
    # Assert
    assert "scitex-cards[all]" in _INSTALL_HINT


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
