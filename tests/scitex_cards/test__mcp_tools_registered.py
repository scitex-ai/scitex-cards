#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every tool the server DECLARES must actually be REGISTERED.

WHY THIS FILE EXISTS (2026-07-14): `mcp` was hoisted out of `_mcp_server` into
the `_mcp_app` leaf to break an import cycle (`_mcp_write` imported the server;
the server imported `_mcp_write` at its tail). That refactor is only correct if
`_mcp_server` STILL imports the satellite modules — because importing them is
what runs their `@mcp.tool` decorators.

THE FAILURE MODE THIS GUARDS IS SILENT. Break that tail import and nothing
raises: the server starts, reports healthy, and simply does not have `add_task`
or `update_task`. An ImportError is loud and gets fixed in a minute; a missing
tool looks like the agent "choosing" not to write. Trading a loud bug for a
quiet one is the worst possible refactor outcome, so the loudness is restored
here as a test.

This also pins the earlier lesson (`_mcp_server` split, 2026-07-13): a split must
leave the original module re-exporting its public API, or it is a rename with
extra steps.
"""

import asyncio

import pytest

pytest.importorskip("fastmcp", reason="MCP tools require the [mcp] extra")


def _registered_names() -> set[str]:
    from scitex_cards import _mcp_server

    tools = asyncio.run(_mcp_server.mcp.list_tools())
    return {t.name for t in tools}


def test_every_declared_tool_is_registered():
    """TOOL_NAMES is the contract; the registry must satisfy it exactly."""
    # Arrange
    from scitex_cards._mcp_server import TOOL_NAMES

    declared = set(TOOL_NAMES)

    # Act
    registered = _registered_names()

    # Assert
    missing = sorted(declared - registered)
    assert not missing, (
        f"declared in TOOL_NAMES but NOT registered with the MCP server: {missing}. "
        "A tool module was probably dropped from the import tail of _mcp_server — "
        "importing it is what runs its @mcp.tool decorators."
    )


def test_no_tool_is_registered_without_being_declared():
    """The reverse drift: a tool the introspection surfaces do not know about."""
    # Arrange
    from scitex_cards._mcp_server import TOOL_NAMES

    declared = set(TOOL_NAMES)

    # Act
    registered = _registered_names()

    # Assert
    undeclared = sorted(registered - declared)
    assert not undeclared, (
        f"registered but missing from TOOL_NAMES: {undeclared}. TOOL_NAMES exists so "
        "`mcp list-tools` need not introspect FastMCP's drifting internals — add it."
    )


@pytest.mark.parametrize("name", ["add_task", "update_task"])
def test_the_write_tools_specifically_survived_the_leaf_split(name):
    """The two tools the _mcp_app extraction could have silently unregistered.

    They live in `_mcp_write`, the module on the far side of the broken cycle.
    Named explicitly, not just covered by the contract test above, because these
    are the ones a bad fix actually drops.
    """
    # Arrange
    expected = name

    # Act
    registered = _registered_names()

    # Assert
    assert expected in registered
