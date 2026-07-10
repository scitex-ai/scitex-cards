#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Root ``scitex-todo`` group and core verbs (render-graph, list-tasks).

The §1a introspection / completion / skills groups, and each noun-verb
group (``board``, ``index``, ``migration``, ``mcp``, ``hook``, …), live
in sibling modules and are attached to ``main`` at the bottom of this
file. This module itself stays a thin orchestrator (root group +
render-graph + list-tasks + the --help-recursive/--json machinery) —
``board``/``index``/``migration`` were extracted to their own modules
(``_board.py`` / ``_index.py`` / ``_migration_cli.py``) to keep this file
under the project's 512-line convention, mirroring the split every other
verb-group in this package already uses.
"""

from __future__ import annotations

import json
import sys

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand, SpecGroup

from .. import __version__
from .._diagram import build_mermaid, render
from .._model import load_tasks
from .._paths import resolve_tasks_path

# The canonical seven help categories (§4a 10a_command-categories.md):
# fixed names + order ecosystem-wide. Names below MUST match the actual
# registered top-level command names; anything not listed falls through
# to the "Other" section in --help, which stays empty at audit-clean.
COMMAND_CATEGORIES = [
    (
        "Core",
        [
            "render-graph",
            "list-tasks",
            "add",
            "update",
            "done",
            "close",
            "comment",
            "summary",
            "stale-list",
            "next",
            "runnable",
            "blocked",
        ],
    ),
    (
        "Data & Sync",
        ["resolve-store", "init-store", "sync-store", "sync-github", "index", "migration"],
    ),
    ("Service", ["board", "mcp", "watch", "hook", "ci-watch", "help-wait", "help-clear"]),
    ("Introspection", ["list-python-apis", "skills", "print-stats"]),
    ("Shell", ["print-shell-completion", "install-shell-completion"]),
]

_ROOT_HELP_SPEC = CliHelp(
    summary="Canonical YAML task store + adapters for the SciTeX agent fleet.",
    version_of="scitex-todo",
    description=(
        "A shared task board that agents and humans read/write through "
        "the CLI, a Python API, an MCP server, and a Django web board — "
        "all backed by one YAML file (plus a SQLite derived-index for "
        "fast reads).",
    ),
    examples=(
        Example("{prog} add my-task 'Implement X' --agent proj-scitex-todo", "Create a task."),
        Example("{prog} next --mine --json", "Pick the next runnable task."),
        Example("{prog} list-tasks --status pending --json", "List tasks as JSON."),
    ),
    config_resolution=(
        "Task store resolution (first existing wins): an explicit --tasks "
        "path, then $SCITEX_TODO_TASKS, then the project store "
        "<git-root>/.scitex/todo/tasks.yaml, then the user store "
        "~/.scitex/todo/tasks.yaml (relocatable via $SCITEX_DIR), then "
        "the bundled generic example. See the README 'Where your task "
        "data lives' section.",
    ),
)


# --------------------------------------------------------------------------- #
# Top-level group (--help-recursive / --json universal flags)                 #
# --------------------------------------------------------------------------- #
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


@click.group(
    cls=SpecGroup,
    help_spec=_ROOT_HELP_SPEC,
    command_categories=COMMAND_CATEGORIES,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--help-recursive",
    "help_recursive",
    is_flag=True,
    help="Show help for every subcommand, flattened.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON (the command tree for the top level).",
)
@click.version_option(__version__, "-V", "--version", prog_name="scitex-todo")
@click.pass_context
def main(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    """scitex-todo CLI entry point."""
    if help_recursive or as_json:
        _emit_help_recursive(ctx, as_json=as_json)
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


# --------------------------------------------------------------------------- #
# render-graph                                                                #
# --------------------------------------------------------------------------- #
@main.command(
    "render-graph",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Render the task dependency graph to a PNG.",
        description=(
            "Builds a mermaid dependency graph (depends_on / blocks "
            "edges) from the resolved store and renders it to a PNG via "
            "the mermaid CLI (or --print-mermaid to inspect the source "
            "without rendering).",
        ),
        examples=(
            Example(
                "{prog} render-graph --tasks ./.scitex/todo/tasks.yaml -o tasks.png",
                "Render the project store to tasks.png.",
            ),
        ),
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "-o",
    "--output",
    default="tasks.png",
    show_default=True,
    help="Output PNG path.",
)
@click.option(
    "--print-mermaid",
    is_flag=True,
    help="Print the generated mermaid source to stdout and exit (no render).",
)
def render_graph_cmd(tasks_path: str | None, output: str, print_mermaid: bool) -> None:
    """Render the resolved task store to a dependency PNG."""
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    mermaid_src = build_mermaid(tasks)

    if print_mermaid:
        sys.stdout.write(mermaid_src)
        return

    engine = render(mermaid_src, output)
    click.echo(f"{output}  (rendered via {engine}; source: {resolved})")


# --------------------------------------------------------------------------- #
# list-tasks                                                                  #
# --------------------------------------------------------------------------- #
@main.command(
    "list-tasks",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="List tasks with optional filters.",
        description=(
            "Without any filter, prints the same plain-text table / "
            "JSON array as before (backward-compatible). With one or "
            "more filters, matches are AND-composed.",
        ),
        examples=(
            Example("{prog} list-tasks --assignee proj-scitex-todo --json", "Filter by assignee."),
            Example(
                "{prog} list-tasks --project scitex-todo --status pending --status in_progress",
                "Multi-status filter.",
            ),
            Example("{prog} list-tasks --blocking-me", "The BLOCKING-YOU predicate."),
            Example("{prog} list-tasks --id-prefix proj-scitex-", "Prefix match on id."),
            Example("{prog} list-tasks --blocker __none", "Rows with no blocker."),
        ),
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "--scope",
    default=None,
    help="Match `scope` exactly (use '' to ignore $SCITEX_TODO_SCOPE).",
)
@click.option(
    "--assignee",
    default=None,
    help="Match `assignee` exactly (PRIMARY linking field today).",
)
@click.option(
    "--agent",
    default=None,
    help="Match `agent` exactly (forward-compat alias for --assignee).",
)
@click.option("--project", default=None, help="Match `project` exactly.")
@click.option("--host", default=None, help="Match `host` exactly.")
@click.option(
    "--blocker",
    default=None,
    help="Match `blocker` exactly; `__none` matches rows with no blocker.",
)
@click.option(
    "--kind",
    default=None,
    help="Match `kind` exactly; `task` matches both explicit and absent rows.",
)
@click.option(
    "--id-prefix",
    "id_prefix",
    default=None,
    help="Match the front of `id` (cheap project-rollup lookup).",
)
@click.option(
    "--blocking-me",
    "blocking_me",
    is_flag=True,
    help="Predicate: status=blocked AND blocker=operator-decision (BLOCKING-YOU panel).",
)
@click.option(
    "--overdue",
    is_flag=True,
    help=(
        "Predicate: tasks past their next deadline AND not in a terminal "
        "lifecycle state (done / deferred / failed / goal). Uses the "
        "deadline / deadlines schema + repeater rules from "
        "scitex_todo._model.is_overdue (PR #125, todo-p6-overdue-ui)."
    ),
)
@click.option(
    "--status",
    "statuses",
    multiple=True,
    help="Match `status` exactly. Repeat for multi-status filter.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the resolved tasks as a JSON array.",
)
def list_tasks_cmd(
    tasks_path: str | None,
    scope: str | None,
    assignee: str | None,
    agent: str | None,
    project: str | None,
    host: str | None,
    blocker: str | None,
    kind: str | None,
    id_prefix: str | None,
    blocking_me: bool,
    overdue: bool,
    statuses: tuple,
    as_json: bool,
) -> None:
    """Print the resolved task list (filtered or not)."""
    # Normalize: click's multiple=True returns a tuple; the helper
    # signature takes a list[str] | None. Empty tuple = no constraint.
    statuses_list: list[str] | None = list(statuses) if statuses else None
    # Did the caller pass ANY filter? Drive the dispatch off this.
    has_filter = (
        any(
            v is not None
            for v in (
                scope,
                assignee,
                agent,
                project,
                host,
                blocker,
                kind,
                id_prefix,
            )
        )
        or bool(statuses_list)
        or blocking_me
        or overdue
    )

    if has_filter:
        from ._admin import list_tasks_filtered

        list_tasks_filtered(
            scope,
            assignee,
            # Legacy positional `status` (single) is None when --status
            # is empty / multi; the multi case feeds `statuses=`.
            None,
            as_json,
            tasks_path,
            statuses=statuses_list,
            agent=agent,
            project=project,
            host=host,
            blocker=blocker,
            kind=kind,
            id_prefix=id_prefix,
            blocking_me=blocking_me,
            overdue=overdue,
        )
        return
    # Plain path — backward-compatible plain table / JSON array.
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    if as_json:
        click.echo(json.dumps(tasks))
        return
    click.echo(f"# {resolved}  ({len(tasks)} tasks)")
    for task in tasks:
        click.echo(f"{task['id']:<24} {task['status']:<12} {task['title']}")


# --------------------------------------------------------------------------- #
# Attach the noun-verb groups + §1a sub-groups (defined in sibling modules).  #
# --------------------------------------------------------------------------- #
from . import (
    _board,
    _ci_watch,
    _completion,
    _hooks,
    _index,
    _introspect,
    _loop,
    _mcp,
    _migration_cli,
    _runnable,
    _skills,
    _stats,
    _write,
)  # noqa: E402

_introspect.register(main)
_completion.register(main)
_skills.register(main)
# `stats` + `sync-github` (operator standing direction via lead a2a
# `4b23ebc1` / `7489ac31` / `6f24a752` / `5263c8d9` / `02b71bd0` /
# `130cc5ac`, 2026-06-12). Shared aggregator in `_throughput.py`.
_stats.register(main)
# Phase 1 mutation/admin verbs: add / update / done / list / summary /
# where / init / sync(stub). See GITIGNORED/ARCHITECTURE.md.
_write.register(main)
# `board` — start/stop/restart/status pidfile lifecycle. Extracted to
# _board.py to keep this file under the 512-line convention.
_board.register(main)
# `index` — SQLite derived-index rebuild/info. Extracted to _index.py.
_index.register(main)
# `migration` — directory-card plan/apply. Extracted to _migration_cli.py.
_migration_cli.register(main)
# Phase 1 MCP subgroup — §3 required four (start / doctor / list-tools /
# install). The module itself loads cleanly without fastmcp installed;
# individual verbs print a clear install hint when fastmcp is missing.
_mcp.register(main)
# P3b + P3d (lead-approved 2026-06-12) — self-consuming board loop.
# `scitex-todo next` returns the top runnable task for an agent;
# `scitex-todo watch --push` is the push side that wakes agents on
# new/commented/changed tasks. See _skills/scitex-todo/32_*.md for the
# 7-step agent self-consumption pattern.
_loop.register(main)
# T1.2 (lead a2a `74db4f2d`, 2026-06-14) — the parallelism dispatcher's
# batch runnable view. Sister to `next` (single pick); respects
# depends_on closure. See _runnable.py for the predicate.
_runnable.register(main)
# Hook-consumer wire (lead a2a `6fff33d6` + `fbffb879`, 2026-06-14,
# operator-mandated). `scitex-todo hook push|done` verbs are the
# CLI twins of POST /hooks/push and POST /hooks/done — same canonical
# event-payload shape, same idempotency. See _hooks.py for the spec.
_hooks.register(main)
# ci-watch (record-only, decoupled-pollers lane per operator override
# via dev msg `96afacc7`, 2026-06-15). Server-side cron-style poller;
# logs per-repo CI transitions + updates the local state cache. NO
# bus emission for ci-result (SAC has its own independent poller for
# the delivery side). See _ci_watch.py.
_ci_watch.register(main)


if __name__ == "__main__":
    main()

# EOF
