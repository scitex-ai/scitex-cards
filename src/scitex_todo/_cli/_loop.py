#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verbs for the self-consuming board loop: ``next`` and ``watch``.

P3b + P3d (lead-approved 2026-06-12). Two verbs together realize the
fleet's central-command loop:

  scitex-todo next [--mine|--assignee X] [--auto-claim] [--json]
    The single canonical "what to pick up next" predicate, used by
    every agent's harness on wake. See ``_next.next_task`` for the
    filter + sort rules.

  scitex-todo watch --push [--interval N] [--once]
    The push side: polls the store, detects new/commented/changed
    tasks, POSTs ``/v1/turn`` to the owning agent's a2a port. See
    ``_wake_watcher`` for the wire shape + debounce.

Both are registered via :func:`register` from ``_main.py`` like the
other sub-modules in this package.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand


def register(main: click.Group) -> None:
    """Attach the ``next`` and ``watch`` verbs to the root group."""
    main.add_command(next_cmd)
    main.add_command(watch_cmd)


@click.command(
    "next",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Print the next runnable task for an agent (single canonical predicate).",
        description=(
            "Used by every agent's harness on wake: pick the top task, "
            "flip to in_progress, work it, comment progress, mark done. "
            "See the 'agent self-consumption loop' sub-skill (32) for "
            "the 7-step pattern.",
        ),
        examples=(Example("{prog} next --mine --json", "Pick my next task, as JSON."),),
    ),
)
@click.option(
    "--tasks", "tasks_path", default=None,
    help="Path to tasks.yaml (default: resolver chain).",
)
@click.option(
    "--assignee", default=None,
    help="Agent name to filter on. Mutually exclusive with --mine.",
)
@click.option(
    "--mine", "use_mine", is_flag=True,
    help="Filter on SCITEX_TODO_AGENT env var.",
)
@click.option(
    "--project", default=None,
    help="Scope to one project.",
)
@click.option(
    "--auto-claim", is_flag=True,
    help=(
        "ATOMIC: also flip status to 'in_progress' + stamp a "
        "'starting (auto-claim)' comment in one write. Race-free for "
        "parallel agents on the same queue."
    ),
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit the full task dict as JSON (machine consumption).",
)
def next_cmd(
    tasks_path: str | None,
    assignee: str | None,
    use_mine: bool,
    project: str | None,
    auto_claim: bool,
    as_json: bool,
) -> None:
    """Print the next runnable task for an agent."""
    from .._next import next_task
    from .._paths import resolve_tasks_path
    from .._model import load_tasks

    if assignee and use_mine:
        raise click.ClickException(
            "Pass --assignee OR --mine, not both."
        )
    if use_mine:
        env = os.environ.get("SCITEX_TODO_AGENT")
        if not env:
            raise click.ClickException(
                "--mine needs SCITEX_TODO_AGENT to be set in the env."
            )
        assignee = env

    path = resolve_tasks_path(tasks_path)
    tasks = load_tasks(path)

    pick = next_task(tasks, assignee=assignee, project=project)
    if pick.task is None:
        if as_json:
            click.echo(json.dumps({"task": None, "candidate_count": 0}))
        else:
            click.echo(
                f"# no runnable task for assignee={assignee!r}"
                f"{(' project=' + project) if project else ''}",
                err=True,
            )
        sys.exit(1)

    if auto_claim:
        _auto_claim(path, pick.task["id"], assignee=assignee or "<unknown>")

    if as_json:
        click.echo(json.dumps(pick.task, default=str))
    else:
        prio = pick.task.get("priority")
        prio_str = f"#{prio}" if isinstance(prio, int) else "—"
        deadline = pick.task.get("deadline") or "—"
        click.echo(
            f"{pick.task['id']} | {prio_str} | "
            f"{pick.task.get('title','')} | {deadline}"
        )


def _auto_claim(path, task_id: str, *, assignee: str) -> None:
    """Atomic flip → in_progress + stamp a starting comment.

    Reads the store, mutates in memory, re-saves under the writer's
    file-lock so two parallel agents calling --auto-claim race on
    the lock (not the task).
    """
    from .._model import load_tasks, save_tasks

    tasks = load_tasks(path)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    stamp = {
        "ts": now, "author": assignee, "text": f"starting (auto-claim by {assignee})",
    }
    for t in tasks:
        if t.get("id") == task_id:
            t["status"] = "in_progress"
            comments = list(t.get("comments") or [])
            comments.append(stamp)
            t["comments"] = comments
            t["last_activity"] = now
            break
    save_tasks(tasks, path)


@click.command(
    "watch",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Watch tasks.yaml and POST /v1/turn to the owning agent on change.",
        description=(
            "Wakes on new/commented/status-changed tasks (the push side "
            "of the self-consuming board loop). Pairs with `next` (the "
            "pull side) — see the 'agent self-consumption loop' "
            "sub-skill (32).",
        ),
        examples=(Example("{prog} watch --push --interval 2", "Poll every 2s and push wakes."),),
    ),
)
@click.option(
    "--push", is_flag=True, default=True,
    help=(
        "Forward wakes to each owning agent's a2a /v1/turn. (Reserved "
        "off-by-default mode is planned for dry-run / logging-only.)"
    ),
)
@click.option(
    "--tasks", "tasks_path", default=None,
    help="Path to tasks.yaml (default: resolver chain).",
)
@click.option(
    "--interval", "interval_s", type=float, default=2.0, show_default=True,
    help="Polling interval in seconds.",
)
@click.option(
    "--min-wake-interval", "min_wake_interval_s",
    type=float, default=30.0, show_default=True,
    help="Per-agent debounce window in seconds.",
)
@click.option(
    "--once", is_flag=True,
    help="Run a single diff tick and exit (handy for tests).",
)
def watch_cmd(
    push: bool,
    tasks_path: str | None,
    interval_s: float,
    min_wake_interval_s: float,
    once: bool,
) -> None:
    """Drive the wake-watcher loop."""
    from .._paths import resolve_tasks_path
    from .._wake_watcher import (
        WatcherState,
        run_watcher_forever,
        run_watcher_once,
    )

    path = resolve_tasks_path(tasks_path)
    if once:
        state = WatcherState()
        # First tick seeds; second tick reports any changes that landed
        # in between (rare in --once mode; useful for the test path).
        wakes = run_watcher_once(
            path, state,
            min_wake_interval_s=min_wake_interval_s,
            post=push,
        )
        for w in wakes:
            click.echo(
                f"WAKE {w.agent} {w.trigger_kind} {w.task_id} :: {w.summary}"
            )
        return
    click.echo(
        f"[scitex-todo] watch --push tracking {path} "
        f"(interval={interval_s}s, debounce={min_wake_interval_s}s)",
        err=True,
    )
    run_watcher_forever(
        path,
        interval_s=interval_s,
        min_wake_interval_s=min_wake_interval_s,
    )


# EOF
