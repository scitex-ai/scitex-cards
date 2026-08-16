#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards mcp` subgroup — §3 required four verbs (+ channel).

Verbs:
    start          Launch the FastMCP server (stdio by default).
    doctor         Self-diagnose the MCP install.
    list-tools     Enumerate registered tools (with `-v|-vv|-vvv`/`--json`).
    install        Print the snippet to paste into a Claude Code MCP config.
    install-fleet  Apply the entry to every agent's to_home/.mcp.json.
    channel        Run the standalone channel-notification server (stdio).

We prefer ``scitex_dev._mcp_cli.attach_mcp_subcommands`` when available
(keeps every scitex package's `mcp` group identical) and fall back to a
hand-rolled group when scitex-dev isn't installed (so a fresh
``pip install scitex-cards[all]`` still works). The ``channel`` verb is
scitex-cards' OWN feature (no scitex-dev parallel) and is wired onto the
group in BOTH paths.

The ``install`` / ``install-fleet`` verbs and the FastMCP tool-introspection
helpers were extracted to ``_mcp_install`` / ``_mcp_tools`` to keep this
orchestrator under the module size budget; their public names are re-exported
here so historical import paths keep working.
"""

from __future__ import annotations

import json
import sys

import click

from .._currency import check_currency
from ._compat import spec_command_kwargs, spec_group_kwargs
from ._mcp_channel_verb import attach_channel_verb
from ._mcp_install import _fleet_apply_one, attach_install_verbs  # noqa: F401
from ._mcp_tools import (  # noqa: F401
    _list_tool_names,
    _list_tool_records,
    _tool_record,
    _tools_dict,
)

_SERVER_PATH = "scitex_cards._mcp_server:mcp"

# What this surface CALLS ITSELF. Used for three agent-visible things: the
# `mcpServers` KEY in the emitted install snippet (so tools namespace as
# `mcp__scitex-cards__*`), the `command` the entry execs, and the `{prog}` in
# scitex-dev-rendered help. All three must say the CURRENT name.
#
# This is NOT the same decision as the console script: `scitex-cards` stays an
# installed alias forever (the operator's running units invoke it). Renaming
# what we ANNOUNCE is safe; renaming what we PUBLISH is not. See
_CLI_NAME = "scitex-cards"

_INSTALL_HINT = (
    "scitex-cards MCP tools require the [mcp] extra:\n  pip install 'scitex-cards[all]'"
)


def _try_import_mcp():
    """Import the FastMCP instance. Returns (mcp_obj, None) or (None, hint)."""
    try:
        from .._mcp_server import mcp as mcp_obj

        return mcp_obj, None
    except ImportError:
        return None, _INSTALL_HINT


def _run_unified_server() -> None:
    """Run the ONE scitex-cards MCP server over stdio: it serves the card TOOLS
    AND pushes this agent's digest (``notifications/claude/channel``).

    This merges what used to be two servers — the FastMCP tool server
    (``mcp start``) and the standalone channel server (``mcp channel``) — into a
    single ``scitex-cards`` MCP integration (one ``.mcp.json`` entry). It reuses
    FastMCP's underlying low-level server (``mcp._mcp_server``, which already
    has every ``@mcp.tool()`` registered) and drives it with the channel
    module's own-the-session serve loop so the poll loop can push.

    The agent id is OPTIONAL: with ``$SCITEX_CARDS_AGENT_ID`` set the digest is
    pushed; without it the server still serves tools (push disabled) rather than
    failing — the tools surface must work even with no identity configured.
    """
    import asyncio

    from .. import _mcp_channel
    from .._mcp_server import mcp  # FastMCP instance (tools registered)

    agent_id = _mcp_channel.resolve_agent_id_optional(None)

    asyncio.run(
        _mcp_channel._run(
            agent_id=agent_id,
            source=_mcp_channel._resolve_source(None),
            interval=_mcp_channel._resolve_interval(None),
            server=mcp._mcp_server,
        )
    )


def _attach_unified_start(group: click.Group) -> None:
    """Register (or override) the ``start`` verb with the UNIFIED server.

    ``add_command`` keyed by name replaces any existing ``start`` — so this
    overrides the scitex-dev-provided ``start`` (which runs tools-only) with the
    unified tools+push server on the default stdio path. ``--http`` keeps the
    plain FastMCP transport (HTTP cannot carry the claude/channel push, so it is
    tools-only by nature).
    """

    @group.command(
        "start",
        **spec_command_kwargs(
            summary="Launch the MCP server (stdio): tools + this agent's digest push.",
            examples=(
                ("{prog} mcp start", "stdio (tools + digest)."),
                ("{prog} mcp start --http --port 7700", "HTTP, tools only."),
            ),
        ),
    )
    @click.option(
        "--http", is_flag=True, help="Use HTTP transport (tools only, no digest push)."
    )
    @click.option("--host", default="127.0.0.1", show_default=True)
    @click.option("--port", type=int, default=0, help="HTTP port (0 = auto).")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Print what would happen (transport/host/port) and exit 0 without launching.",
    )
    @click.option(
        "-y",
        "--yes",
        is_flag=True,
        help="Skip confirmation (no-op for the default stdio path; reserved for HTTP mode).",
    )
    def start(http, host, port, dry_run, yes) -> None:
        _ = yes  # accepted for §2 compliance; no interactive prompt today
        if dry_run:
            transport = "http" if http else "stdio"
            click.echo(
                f"# dry-run: would launch MCP server transport={transport} "
                f"host={host} port={port or 'auto'} "
                f"({'tools only' if http else 'tools + digest push'})"
            )
            return
        # CURRENCY gate, first line of the REAL launch path (operator
        # directive: stale/broken installs ERROR, never warn). Already
        # covered transitively by `main()`'s group callback in `_cli/
        # _main.py` — every `scitex-cards` invocation is gated there before
        # any subcommand runs — but called again here, explicitly, at the
        # actual server-start call site: this is the one place that matches
        # `_SERVER_PATH` (the module a client could also launch directly via
        # `fastmcp run scitex_cards._mcp_server:mcp`, bypassing this CLI
        # entirely — see `_mcp_server.py`'s note on that gap). No-op when
        # scitex-dev is absent.
        check_currency()
        mcp_obj, hint = _try_import_mcp()
        if mcp_obj is None:
            raise click.ClickException(hint)
        if http:
            # HTTP transport can't carry the server-initiated claude/channel
            # push; serve tools only via FastMCP's own runner.
            try:
                mcp_obj.run(transport="http", host=host, port=port or None)
            except TypeError:
                mcp_obj.run_http(host=host, port=port or 0)
            return
        _run_unified_server()


def _fallback_mcp_group() -> click.Group:
    """Hand-rolled `mcp` group used when scitex-dev's helper isn't present.

    Implements §3's required four (``start``, ``doctor``, ``list-tools``,
    ``install``) plus ``install-fleet`` and the ``channel`` verb. Keeps
    behavior parity with the scitex-dev helper so users see the same
    surface either way.
    """

    @click.group(
        "mcp",
        **spec_group_kwargs(
            summary="MCP server subcommands (SciTeX §3 required four).",
            description="Required: start, doctor, list-tools, install.",
            command_categories=(
                (
                    "Core",
                    (
                        "start",
                        "doctor",
                        "list-tools",
                        "install",
                        "install-fleet",
                        "channel",
                    ),
                ),
            ),
        ),
    )
    def mcp_group() -> None:
        pass

    # ── start (unified: tools + digest push) ─────────────────────────── #
    _attach_unified_start(mcp_group)

    # ── doctor ────────────────────────────────────────────────────────── #
    @mcp_group.command(
        "doctor",
        **spec_command_kwargs(
            summary="Self-diagnose the MCP install.",
            examples=(("{prog} mcp doctor --json", "Structured diagnostic."),),
        ),
    )
    @click.option("--json", "as_json", is_flag=True)
    def doctor(as_json) -> None:
        diag = {
            "package": "scitex-cards",
            "server_path": _SERVER_PATH,
            "fastmcp": None,
            "tools": 0,
            "status": "unknown",
            "hint": None,
        }
        try:
            import fastmcp

            diag["fastmcp"] = getattr(fastmcp, "__version__", "(unknown)")
        except ImportError:
            diag["status"] = "critical"
            diag["hint"] = _INSTALL_HINT
            if as_json:
                click.echo(json.dumps(diag))
            else:
                click.echo(f"status: critical\n{_INSTALL_HINT}")
            sys.exit(2)

        mcp_obj, hint = _try_import_mcp()
        if mcp_obj is None:
            diag["status"] = "critical"
            diag["hint"] = hint
            if as_json:
                click.echo(json.dumps(diag))
            else:
                click.echo(f"status: critical\n{hint}")
            sys.exit(2)

        tool_names = _list_tool_names(mcp_obj)
        diag["tools"] = len(tool_names)
        diag["status"] = "ok" if tool_names else "degraded"
        if as_json:
            click.echo(json.dumps(diag))
        else:
            click.echo(f"status: {diag['status']}")
            click.echo(f"fastmcp: {diag['fastmcp']}")
            click.echo(f"tools:   {diag['tools']}")
        sys.exit(0 if diag["status"] == "ok" else 1)

    # ── list-tools ────────────────────────────────────────────────────── #
    @mcp_group.command(
        "list-tools",
        **spec_command_kwargs(
            summary="Enumerate registered MCP tools.",
            description="Verbosity is additive (-v|-vv|-vvv), matching the §1a ladder.",
            examples=(("{prog} mcp list-tools -vv", "Names + full descriptions."),),
        ),
    )
    @click.option("-v", "verbosity", count=True, help="Repeat for more detail.")
    @click.option("--json", "as_json", is_flag=True)
    def list_tools(verbosity, as_json) -> None:
        mcp_obj, hint = _try_import_mcp()
        if mcp_obj is None:
            raise click.ClickException(hint)
        items = _list_tool_records(mcp_obj, verbosity=verbosity)
        if as_json:
            click.echo(json.dumps(items))
            return
        if verbosity == 0:
            for it in items:
                click.echo(it["name"])
        else:
            for it in items:
                click.echo(f"- {it['name']}")
                if verbosity >= 1 and it.get("description"):
                    click.echo(f"    {it['description'].splitlines()[0]}")
                if verbosity >= 2 and it.get("description"):
                    for line in it["description"].splitlines()[1:]:
                        click.echo(f"    {line}")
                if verbosity >= 3:
                    click.echo(f"    full: {it}")

    # ── install / install-fleet (extracted) ───────────────────────────── #
    attach_install_verbs(mcp_group)
    # ── channel (scitex-cards' own standalone server) ─────────────────── #
    attach_channel_verb(mcp_group)
    return mcp_group


def register(main: click.Group) -> None:
    """Attach the `mcp` subgroup to `main`. Prefers the scitex-dev helper."""
    try:
        from scitex_dev._mcp_cli import attach_mcp_subcommands  # type: ignore

        @click.group(
            "mcp",
            **spec_group_kwargs(
                summary="MCP server subcommands (SciTeX §3 required four).",
                command_categories=(
                    (
                        "Core",
                        (
                            "start",
                            "doctor",
                            "list-tools",
                            "install",
                            "install-fleet",
                            "channel",
                        ),
                    ),
                ),
            ),
        )
        def mcp_group() -> None:
            pass

        attach_mcp_subcommands(mcp_group, server_path=_SERVER_PATH, cli_name=_CLI_NAME)
        # Override scitex-dev's tools-only `start` with scitex-cards' UNIFIED
        # server (tools + digest push) — one `scitex-cards` MCP integration.
        _attach_unified_start(mcp_group)
        # scitex-cards' OWN channel verb has no scitex-dev parallel — wire it
        # on regardless of which path built the group (kept for back-compat).
        attach_channel_verb(mcp_group)
        main.add_command(mcp_group, name="mcp")
        return
    except ImportError:
        # scitex-dev not available — use the hand-rolled fallback.
        main.add_command(_fallback_mcp_group(), name="mcp")


# EOF
