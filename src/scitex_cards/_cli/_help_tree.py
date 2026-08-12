#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walk a click command tree for ``--help-recursive`` and ``--json``.

Extracted from ``_main`` because it is a distinct responsibility: ``_main``
DEFINES the root group and its verbs, whereas this module only READS a
command tree that already exists. Nothing here knows anything about
scitex-cards — hand it any ``click.Group`` and it will walk it.

The functions stay private (leading underscore) and ``_main`` re-exports the
names it uses, so this split moves code without widening the public surface.
"""

from __future__ import annotations

import json

import click


def _iter_commands(cmd, ctx, prefix):
    """Yield ``(prefix, command, context)`` for ``cmd`` and every descendant."""
    yield prefix, cmd, ctx
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            yield from _iter_commands(sub, sub_ctx, f"{prefix} {name}")


def _command_tree(cmd, ctx):
    """Return a JSON-serializable ``{name, help, options, commands}`` tree."""
    node = {
        "name": ctx.info_name,
        "help": (cmd.help or "").strip(),
        "options": [p.opts[-1] for p in cmd.params if isinstance(p, click.Option)],
        "commands": {},
    }
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            node["commands"][name] = _command_tree(sub, sub_ctx)
    return node


def _emit_help_recursive(ctx, as_json):
    """Print flattened help (or the command tree as JSON) for every subcommand."""
    if as_json:
        click.echo(json.dumps(_command_tree(ctx.command, ctx), indent=2))
        return
    blocks: list[str] = []
    for prefix, cmd, sub_ctx in _iter_commands(ctx.command, ctx, ctx.info_name):
        blocks.append(f"### {prefix}\n{cmd.get_help(sub_ctx)}")
    click.echo("\n\n".join(blocks))


# EOF
