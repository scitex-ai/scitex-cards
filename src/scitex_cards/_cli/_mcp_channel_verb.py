#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards mcp channel` verb — the standalone channel server entry.

Extracted from ``_cli/_mcp.py`` (which is at its size cap) so the verb can be
wired into BOTH the scitex-dev-helper path and the hand-rolled fallback
``mcp`` group. The actual server lives in :mod:`scitex_cards._mcp_channel`;
this is the thin click wrapper.
"""

from __future__ import annotations

import click


def attach_channel_verb(mcp_group: click.Group) -> None:
    """Attach the ``channel`` verb to an existing ``mcp`` click group.

    Runs scitex-cards's OWN long-running MCP stdio server (foreground) that
    pushes unsolicited ``notifications/claude/channel`` messages (rendered
    ``<- scitex-cards``) into the Claude session, draining this agent's
    standalone inbox. ZERO external-runtime dependency. This is what an
    ``.mcp.json`` entry execs.
    """

    @mcp_group.command(
        "channel",
        help=(
            "Run the standalone channel-notification server (stdio).\n\n"
            "Pushes `notifications/claude/channel` (rendered `<- stodo`)\n"
            "into the Claude session, draining this agent's inbox. ZERO sac\n"
            "dependency. Agent id resolves from $SCITEX_TODO_AGENT_ID (or\n"
            "--agent). --name/--interval fall back to $SCITEX_TODO_CHANNEL_SOURCE\n"
            "/$SCITEX_TODO_CHANNEL_INTERVAL then the defaults, so the .mcp.json\n"
            "entry can carry zero config args.\n\n"
            "Example:\n"
            "  scitex-cards mcp channel --name stodo --interval 5"
        ),
    )
    @click.option(
        "--name",
        default=None,
        help=(
            "Sets meta.source (drives the `<- stodo` render). "
            "Default: $SCITEX_TODO_CHANNEL_SOURCE, then 'stodo'."
        ),
    )
    @click.option(
        "--interval",
        type=float,
        default=None,
        help=(
            "Seconds between inbox drains. "
            "Default: $SCITEX_TODO_CHANNEL_INTERVAL, then 5.0."
        ),
    )
    @click.option(
        "--agent",
        default=None,
        help="Override the agent id (default: $SCITEX_TODO_AGENT_ID, fail-loud).",
    )
    def channel(name, interval, agent) -> None:
        try:
            from .._mcp_channel import main as channel_main
        except ImportError as exc:  # pragma: no cover — mcp SDK missing
            raise click.ClickException(
                "scitex-cards mcp channel needs the MCP SDK: "
                f"pip install 'scitex-cards[mcp]' ({exc})"
            ) from None
        try:
            channel_main(name=name, interval=interval, agent=agent)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from None


__all__ = ["attach_channel_verb"]

# EOF
