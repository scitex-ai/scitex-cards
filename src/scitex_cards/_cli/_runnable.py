#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-todo runnable`` — the parallelism dispatcher's
view of "what's runnable right now."

Sister to ``scitex-todo next`` (single agent-pickup), but BATCH
(returns the FULL list, optionally filtered by agent / group) and
respects ``depends_on`` (transitive upstream closure). Lead a2a
``74db4f2d``, 2026-06-14 — TRACK 1 (dependency-aware tickets)
dispatch backbone.

See :mod:`scitex_cards._runnable` for the predicate.
"""

from __future__ import annotations

import json
import os
import sys

import click

from ._compat import spec_command_kwargs


def register(main: click.Group) -> None:
    """Attach the ``runnable`` + ``blocked`` verbs to the root group."""
    main.add_command(runnable_cmd)
    main.add_command(blocked_cmd)


@click.command(
    "runnable",
    **spec_command_kwargs(
        summary="List ALL runnable tasks the dispatcher can pick up right now.",
        description=(
            "Respects depends_on closure. Sister to `next` (which picks "
            "one) — `runnable` is the batch view the lead-side "
            "parallelism dispatcher consumes to fan out work across "
            "agents/groups. Filters: --agent restricts to one agent's "
            "queue; --group restricts to a dispatch cluster ('' = "
            "ungrouped only); --mine is shorthand for --agent "
            "$SCITEX_TODO_AGENT_ID.",
        ),
        examples=(
            (
                "{prog} runnable --group ci-recovery-wave --json",
                "One dispatch cluster, as JSON.",
            ),
        ),
    ),
)
@click.option(
    "--agent",
    default=None,
    help="Agent name. Mutually exclusive with --mine.",
)
@click.option(
    "--mine",
    "use_mine",
    is_flag=True,
    help="Filter on $SCITEX_TODO_AGENT_ID.",
)
@click.option(
    "--group",
    default=None,
    help=(
        "Dispatch-cluster name (the T1.1 `group` field). Pass an "
        "empty string ('') to ask for ungrouped-only tasks."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit full task dicts + diagnostic counts as JSON.",
)
def runnable_cmd(
    agent: str | None,
    use_mine: bool,
    group: str | None,
    as_json: bool,
) -> None:
    """List ALL runnable tasks for the dispatcher."""
    from .._model import load_tasks
    from .._paths import resolve_tasks_path
    from .._runnable import runnable_tasks

    if agent and use_mine:
        raise click.ClickException("Pass --agent OR --mine, not both.")
    if use_mine:
        env = os.environ.get("SCITEX_TODO_AGENT_ID")
        if not env:
            raise click.ClickException(
                "--mine needs SCITEX_TODO_AGENT_ID to be set in the env."
            )
        agent = env

    path = resolve_tasks_path(None)
    tasks = load_tasks(path)

    result = runnable_tasks(tasks, agent=agent, group=group)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "tasks": result.tasks,
                    "candidate_count": result.candidate_count,
                    "blocked_by_deps_count": result.blocked_by_deps_count,
                },
                default=str,
            )
        )
        return

    if not result.tasks:
        # No runnable tasks — emit a "queue depth" footer on stderr so
        # the dispatcher can tell "0 runnable but K candidates waiting
        # on upstream deps" from "0 runnable, queue is genuinely empty."
        click.echo(
            f"# 0 runnable (candidates={result.candidate_count}, "
            f"blocked_by_deps={result.blocked_by_deps_count})",
            err=True,
        )
        sys.exit(1)

    for t in result.tasks:
        prio = t.get("priority")
        prio_str = f"#{prio}" if isinstance(prio, int) else "—"
        deadline = t.get("deadline") or "—"
        click.echo(
            f"{t.get('id', '')} | {prio_str} | {t.get('title', '')} | {deadline}"
        )
    click.echo(
        f"# runnable={len(result.tasks)} candidates={result.candidate_count} "
        f"blocked_by_deps={result.blocked_by_deps_count}",
        err=True,
    )


@click.command(
    "blocked",
    **spec_command_kwargs(
        summary="List ALL not-runnable tasks + WHY (the inverse of `runnable`).",
        description=(
            "For each task in {pending, in_progress, blocked} that the "
            "dispatcher can NOT pick up, names the reason "
            "(explicit-blocker / manual-block / depends-on / "
            "reverse-blocks) and the chain of upstream ids keeping it "
            "parked. Use this to surface 'you can unblock K tasks by "
            "finishing X' insight when the queue stalls. Filters: "
            "--agent, --group, --mine (same semantics as `runnable`).",
        ),
        examples=(
            ("{prog} blocked --group paper-portfolio --json", "One cluster, as JSON."),
        ),
    ),
)
@click.option(
    "--agent",
    default=None,
    help="Agent name. Mutually exclusive with --mine.",
)
@click.option(
    "--mine",
    "use_mine",
    is_flag=True,
    help="Filter on $SCITEX_TODO_AGENT_ID.",
)
@click.option(
    "--group",
    default=None,
    help="Dispatch-cluster name (T1.1 `group`); '' = ungrouped only.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the full payload (tasks + by-reason histogram) as JSON.",
)
def blocked_cmd(
    agent: str | None,
    use_mine: bool,
    group: str | None,
    as_json: bool,
) -> None:
    """List ALL not-runnable tasks + the reason."""
    from .._model import load_tasks
    from .._paths import resolve_tasks_path
    from .._runnable import blocked_tasks

    if agent and use_mine:
        raise click.ClickException("Pass --agent OR --mine, not both.")
    if use_mine:
        env = os.environ.get("SCITEX_TODO_AGENT_ID")
        if not env:
            raise click.ClickException(
                "--mine needs SCITEX_TODO_AGENT_ID to be set in the env."
            )
        agent = env

    path = resolve_tasks_path(None)
    tasks = load_tasks(path)

    result = blocked_tasks(tasks, agent=agent, group=group)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": bt.id,
                            "title": bt.title,
                            "reason": bt.reason,
                            "chain": list(bt.chain),
                        }
                        for bt in result.tasks
                    ],
                    "total": result.total,
                    "by_reason": result.by_reason,
                },
                default=str,
            )
        )
        return

    if not result.tasks:
        click.echo("# 0 blocked tasks (queue is clear).", err=True)
        return

    for bt in result.tasks:
        chain_str = ",".join(bt.chain) if bt.chain else "—"
        click.echo(f"{bt.id} | {bt.reason} | {chain_str} | {bt.title}")
    by_reason_str = " ".join(f"{k}={v}" for k, v in result.by_reason.items() if v)
    click.echo(f"# blocked={result.total} ({by_reason_str})", err=True)


# EOF
