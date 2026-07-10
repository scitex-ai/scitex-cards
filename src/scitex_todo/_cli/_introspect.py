#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§1a introspection command: ``list-python-apis``.

The ``mcp list-tools`` verb used to live here as a "no MCP yet" stub. Phase 1
ships a real MCP server (``scitex_todo._mcp_server``); the live ``mcp``
subgroup is now owned by ``_cli/_mcp.py``."""

from __future__ import annotations

import json

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand


def _describe_api(module, name: str) -> dict:
    """Introspect ``module.name`` into a name/signature/summary/source record."""
    import inspect

    obj = getattr(module, name)
    signature = ""
    if callable(obj):
        try:
            signature = str(inspect.signature(obj))
        except (TypeError, ValueError):
            signature = "(...)"
    doc = inspect.getdoc(obj) or ""
    summary = doc.splitlines()[0] if doc else ""
    try:
        source = inspect.getsourcefile(obj) or ""
    except TypeError:
        source = ""
    return {
        "name": name,
        "kind": type(obj).__name__,
        "signature": signature,
        "summary": summary,
        "doc": doc,
        "source": source,
    }


@click.command(
    "list-python-apis",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="List the public Python API (scitex_todo.__all__).",
        description=(
            "Verbosity is additive: -v adds signatures, -vv adds "
            "docstring summaries, -vvv adds source paths.",
        ),
        examples=(
            Example("{prog} list-python-apis -v", "List with signatures."),
            Example("{prog} list-python-apis --json", "Full records as JSON."),
        ),
    ),
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Add detail (repeatable): -v signatures, -vv summaries, -vvv sources.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the API list as JSON (rich records, independent of -v).",
)
def list_python_apis_cmd(verbose: int, as_json: bool) -> None:
    """List the public Python API surface of scitex_todo."""
    import scitex_todo

    names = [n for n in scitex_todo.__all__ if n != "__version__"]
    entries = [_describe_api(scitex_todo, n) for n in names]

    if as_json:
        click.echo(json.dumps(entries))
        return

    for entry in entries:
        if verbose == 0:
            click.echo(entry["name"])
            continue
        click.echo(f"{entry['name']}{entry['signature']}")
        if verbose >= 2 and entry["summary"]:
            click.echo(f"    {entry['summary']}")
        if verbose >= 3 and entry["source"]:
            click.echo(f"    [{entry['source']}]")


def register(group: click.Group) -> None:
    """Attach the introspection commands to the root ``group``.

    Note: the ``mcp`` subgroup that used to live here is now owned by
    :mod:`scitex_todo._cli._mcp` (real Phase-1 server). Importing this
    module no longer affects the ``mcp`` command surface.
    """
    group.add_command(list_python_apis_cmd)


# EOF
