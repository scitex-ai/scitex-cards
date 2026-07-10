#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo print-stats`` CLI verb.

Compute per-(agent | project | host) throughput from the canonical
tasks.yaml: created / completed / delta / ratio / velocity. Optionally
pushes per-agent notify bodies via scitex-todo's self-contained HTTP
push wire — operator's standing direction that "agents see their own
numbers every hour and self-correct."

Shares the aggregator in :mod:`scitex_todo._throughput` with the
``sync-github`` verb (sibling module ``_sync_github.py``) so the
WIP-validation gate, the board's compact `Δ delta` pill, and the
``print-stats --notify`` body all derive from one shared definition of
"open" / "stale" / "completed".

``sync-github`` was extracted to ``_sync_github.py`` to keep this file
under the project's 512-line convention — same split shape every other
verb-group in this package already uses. ``register()`` here also wires
the sibling module's verb so `_cli/_main.py` keeps a single import.
"""

from __future__ import annotations

import json

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand

from .._model import load_tasks
from .._paths import resolve_tasks_path
from .._throughput import (
    GroupStats,
    aggregate,
    build_notify_body,
)


# --------------------------------------------------------------------------- #
# stats                                                                       #
# --------------------------------------------------------------------------- #


def _format_text(rows: list[GroupStats]) -> str:
    """Plain text table — terse but readable in a terminal."""
    if not rows:
        return "(no rows)"
    header = f"{'name':30}  {'open':>5}  {'stale':>5}  {'done':>5}  {'created':>7}  {'delta':>6}  {'ratio':>6}  {'vel/day':>8}"
    body = []
    for r in rows:
        body.append(
            f"{r.name[:30]:30}  {r.open_count:5d}  {r.stale_count:5d}  "
            f"{r.completed:5d}  {r.created:7d}  {r.delta:+6d}  "
            f"{r.ratio*100:5.1f}%  {r.velocity_per_day:8.2f}"
        )
    return "\n".join([header] + body)


def _format_json(rows: list[GroupStats]) -> str:
    return json.dumps(
        [
            {
                "name": r.name,
                "open": r.open_count,
                "stale": r.stale_count,
                "completed": r.completed,
                "created": r.created,
                "delta": r.delta,
                "ratio": round(r.ratio, 4),
                "velocity_per_day": round(r.velocity_per_day, 4),
            }
            for r in rows
        ],
        indent=2,
        ensure_ascii=False,
    )


def _push_notify(agent: str, body: str) -> str:
    """Push the notify body to ``agent`` via scitex-todo's self-contained
    push wire (:func:`scitex_todo._push.deliver`).

    Operator standing direction via lead a2a `8e51b1e07` + `ffc6629c8`
    (2026-06-12): no `sac` CLI dependency — scitex-todo owns its push
    delivery. Result label is the wire used (`http` / `dry-run`) or
    an error tag (`no-turn-url-configured` / `http-error` / etc.).
    """
    from .._push import deliver

    result = deliver(agent, body, kind="notify")
    if result.get("ok"):
        return result.get("wire") or "ok"
    return result.get("reason") or "error"


# §1f (audit-cli, WARN-only): 'print' is a non-canonical synonym for
# 'show' per doctrine 06_noun-verb-catalog.md. NOT renamed in this pass
# — renaming is a breaking change requiring the 3-phase deprecation
# ladder (doctrine 11_deprecation.md: Warn+forward -> Error -> Removed),
# out of scope for the mechanical §4b CliHelp migration this pass made.
# TODO(Phase-W): introduce `show-stats` as the canonical name, register
# `print-stats` as a hidden warn-forward alias via
# scitex_dev._ecosystem.click_compat.deprecated_alias() once that
# helper ships, then retire `print-stats` in a later minor per the
# ladder. 2026-07-10 CLI-standardization audit pass.
@click.command(
    name="print-stats",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Print throughput stats (per-agent / project / host).",
        description=(
            "Operator standing direction (lead a2a "
            "4b23ebc177944deaa7549e256e9a375a, 2026-06-12): every agent "
            "must measure its own creation vs completion rate so "
            "completion > creation discipline holds across the fleet. "
            "--notify pushes the per-agent summary so receivers "
            "self-correct hourly.",
        ),
        examples=(
            Example("{prog} print-stats --by agent --since 2026-06-01", "Windowed stats."),
            Example("{prog} print-stats --by agent --notify", "Push per-agent summaries."),
            Example("{prog} print-stats --by agent --notify --nudge-quiet", "Also nudge stalled agents."),
            Example("{prog} print-stats --by project --format json", "Machine-readable, by project."),
        ),
    ),
)
@click.option(
    "--by",
    type=click.Choice(["agent", "project", "host"]),
    default="agent",
    help="Group throughput rows by this field.",
)
@click.option(
    "--since",
    default=None,
    help="ISO-8601 date (YYYY-MM-DD). Scopes created/completed to this window.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format. text = aligned table; json = list of objects.",
)
@click.option(
    "--notify",
    is_flag=True,
    help=(
        "After printing the stats, push a per-agent notify body via "
        "scitex-todo's self-contained HTTP push wire (_push.deliver). "
        "The body lists each agent's open tasks (RUNNABLE first, "
        "then BLOCKED + reason), ⚠ on stale in_progress, and "
        "recently-completed lines so the receiver can self-correct."
    ),
)
@click.option(
    "--nudge-quiet",
    is_flag=True,
    help=(
        "Per-agent structural nudge: if the agent has open in_progress "
        "tasks AND no recent activity within SCITEX_TODO_NUDGE_QUIET_MIN "
        "(default 10 minutes), push an additional nudge body. Designed "
        "for the hourly / 10-min cron entry — operator's standing "
        "direction is that 'silence + in_progress = escalation', not "
        "a manual lead intervention. Implies --notify=agent push; "
        "the quiet nudge piggybacks on the same wire."
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, or $SCITEX_TODO_TASKS).",
)
def stats_cmd(
    by: str, since: str | None, fmt: str, notify: bool,
    nudge_quiet: bool, tasks_path: str | None,
) -> None:
    """Print throughput stats (per-agent / project / host).

    Operator standing direction (lead a2a ``4b23ebc177944deaa7549e256e9a375a``
    2026-06-12): every agent must measure its own creation vs
    completion rate so completion > creation discipline holds across
    the fleet. ``--notify`` pushes the per-agent summary so receivers
    self-correct hourly.

    \b
    Example:
      $ scitex-todo print-stats --by agent --since 2026-06-01
      $ scitex-todo print-stats --by agent --notify
      $ scitex-todo print-stats --by agent --notify --nudge-quiet
      $ scitex-todo print-stats --by project --format json
    """
    path = resolve_tasks_path(tasks_path)
    tasks = load_tasks(path)
    rows = aggregate(tasks, by=by, since=since)
    out = _format_json(rows) if fmt == "json" else _format_text(rows)
    click.echo(out)
    if (notify or nudge_quiet) and by == "agent":
        click.echo("")
        click.echo(f"# Notify push → {len(rows)} agents")
        for r in rows:
            if r.name == "(unassigned)":
                continue
            if notify:
                body = build_notify_body(r.name, tasks, since=since)
                wire = _push_notify(r.name, body)
                click.echo(f"  {wire:>6}  {r.name}  ({len(body)} chars)")
        if nudge_quiet:
            click.echo("")
            click.echo(f"# Quiet-nudge sweep (SCITEX_TODO_NUDGE_QUIET_MIN)")
            _emit_quiet_nudges(tasks, rows)
    elif notify or nudge_quiet:
        click.echo(
            "WARN: --notify / --nudge-quiet ignored when --by != agent "
            "(push target needs an agent id).",
            err=True,
        )


def _emit_quiet_nudges(tasks: list[dict], rows: list) -> None:
    """Per-agent structural nudge (PR (h) — lead a2a `19d575415a` +
    revision `9e710ab0` 2026-06-12). For each agent lane, if any
    in_progress task has not been touched in
    ``SCITEX_TODO_NUDGE_QUIET_MIN`` minutes (default 10), push a
    nudge body via :func:`scitex_todo._push.deliver`.

    Why per-task quiet check (rather than per-agent-only)? Because a
    single agent with 5 open tasks would otherwise quietly stall on
    1 while the others mask it. We nudge if ANY in_progress is quiet.
    The nudge body lists ALL open tasks for the agent (same
    ``build_notify_body`` as --notify) so the recipient sees the
    full picture, not just the stalled row.
    """
    import os

    from .._push import deliver

    quiet_min = float(os.environ.get("SCITEX_TODO_NUDGE_QUIET_MIN", "10"))
    quiet_seconds = quiet_min * 60.0

    by_agent: dict[str, list[dict]] = {}
    for t in tasks:
        a = (t.get("agent") or "").strip()
        if not a or a == "(unassigned)":
            continue
        if t.get("status") == "in_progress":
            by_agent.setdefault(a, []).append(t)

    import datetime as _dt
    now = _dt.datetime.now(tz=_dt.timezone.utc)

    def _quiet_age_s(t: dict) -> float | None:
        ts = t.get("last_activity") or t.get("created_at")
        if not ts:
            return None
        try:
            parsed = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (now - parsed).total_seconds()

    pushed = 0
    for agent, in_prog in sorted(by_agent.items()):
        worst_age = max(
            (_quiet_age_s(t) or 0.0 for t in in_prog),
            default=0.0,
        )
        if worst_age <= quiet_seconds:
            continue
        body = build_notify_body(agent, tasks)
        body += (
            "\n————————\n"
            f"QUIET NUDGE: at least one in_progress task has not been "
            f"touched for {int(worst_age // 60)} min (threshold "
            f"{int(quiet_min)} min). Push a commit / comment / status "
            f"flip — or mark BLOCKED with a concrete reason."
        )
        result = deliver(agent, body, kind="quiet-nudge")
        ok_label = "✓" if result.get("ok") else "✗"
        click.echo(
            f"  {ok_label}  {agent:30}  quiet {int(worst_age//60)}m  "
            f"wire={result.get('wire')}  reason={result.get('reason')}"
        )
        if result.get("ok"):
            pushed += 1

    click.echo(f"# {pushed} quiet-nudge push(es) sent")


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #


def register(group: click.Group) -> None:
    from . import _sync_github

    group.add_command(stats_cmd)
    _sync_github.register(group)


__all__ = ["register", "stats_cmd"]
