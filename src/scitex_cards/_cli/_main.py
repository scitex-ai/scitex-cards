#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Root ``scitex-cards`` group and core verbs (render-graph, list-tasks, board).

The §1a introspection / completion / skills groups live in sibling modules and
are attached to ``main`` at the bottom of this file.
"""

from __future__ import annotations

import json
import sys

import click

from .. import __version__
from .._currency import check_currency
from .._diagram import build_mermaid, render
from .._model import load_tasks
from .._paths import resolve_tasks_path
from .._store_target import store_label
from ._compat import spec_command_kwargs, spec_group_kwargs
from ._help_tree import _emit_help_recursive

# NAMES NO BACKEND AND NO DEFAULT PATH, ON PURPOSE.
#
# This block and the `summary` below are the two strings a human reads first
# from `scitex-cards --help`. They used to open "Canonical store: the SQLite
# database at $SCITEX_CARDS_DB (default ~/.scitex/cards/cards.db...)" and
# "Shared task store (SQLite canonical)".
#
# Both named a backend the code does not verify and the operator has banned,
# and the first also advertised a default file path — which is precisely the
# silent descent-to-a-file that an unresolvable target must NOT make.
#
# Substituting "PostgreSQL" would re-create the same defect with a different
# word: the deployment decides the target, the resolved target is the sole
# identity, and `resolve-store` is the only honest answer to "which one?".
# Guarded by tests/scitex_cards/_cli/test__help_names_no_backend.py.
_STORE_RESOLUTION = (
    "The cards database is whatever $SCITEX_CARDS_DB resolves to, and that",
    "resolved target is the SOLE identity — the deployment decides it, so do",
    "not assume a backend or a path. An unresolvable/absent target RAISES",
    "rather than standing in an empty board. Run `scitex-cards resolve-store`",
    "to see what you actually resolved to.",
)

# Doctrine §4a (10a_command-categories.md): fixed, ordered category headers.
# Every visible top-level command MUST be assigned — the auto `Other`
# catch-all is an audit finding and never renders at audit-clean.
_COMMAND_CATEGORIES = (
    (
        "Core",
        (
            "add",
            "update",
            "done",
            "close",
            "comment",
            "reassign",
            "list-tasks",
            "list-stale",
            "find-card",
            "next",
            "runnable",
            "triage",
            "summary",
            "render-graph",
            "emit-event",
            "help-wait",
            "help-clear",
            "hook",
            "migration",
            "index",
            "inbox",
            "init-store",
            "reconcile-merged-prs",
        ),
    ),
    # `deliver-notifications`, not `deliver` — the bare verb is a hidden
    # Phase-W alias now, and a deprecated name in the help advertises the
    # spelling we want callers to stop using.
    (
        "Data & Sync",
        ("db", "dm", "store", "sync-github", "sync-store", "deliver-notifications"),
    ),
    (
        "Service",
        ("board", "gui", "hub", "mcp", "notifyd", "serve", "watch", "watch-ci"),
    ),
    (
        "Diagnostics",
        (
            "blocked",
            "may-stop",
            "stop-hook",
            "install-stop-hook",
            "print-stats",
            # Renamed from `health` (audit §1: a noun leaf implies a transitive
            # action) using the ecosystem's canonical checking verb — §1f names
            # `validate`, not `check`. The old `health` is a hidden Phase-W
            # alias, so it is deliberately NOT listed here: a deprecated name in
            # the help text advertises the thing we want callers to stop using.
            "validate-health",
            "resolve-store",
            # Answers "who must restart for a fix to take effect". Sits beside
            # resolve-store because both diagnose the same class of question —
            # not "what does the config say" but "what is this process
            # actually using".
            "list-importers",
        ),
    ),
    ("Introspection", ("list-python-apis", "skills")),
    ("Maintenance", ("dev",)),  # hook-bypass: line-limit (pre-existing over-cap)
    ("Shell", ("install-shell-completion", "print-shell-completion")),
)


# --------------------------------------------------------------------------- #
# Top-level group (--help-recursive / --json universal flags)                 #
# --------------------------------------------------------------------------- #
def _run_currency_gate(check=check_currency) -> None:
    """Run the CURRENCY gate, surfacing a refusal as a CLEAN CLI error.

    A raw traceback is not a usable answer for an operator, and scitex-dev's
    message carries the remedy command — so the message is preserved verbatim
    inside the :class:`click.ClickException`. A ``ClickException`` raised by
    the gate itself is re-raised untouched rather than re-wrapped, which would
    double the prefix.

    ``check`` is a parameter so this translation can be driven directly with a
    hand-rolled gate (PA-306 §3). It is called from the GROUP CALLBACK, which
    is what makes the gate unconditional: every subcommand passes through it,
    not merely the ones some code path happens to reach.
    """
    try:
        check()
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    **spec_group_kwargs(
        summary="Shared card database + adapters for the agent fleet.",
        config_resolution=_STORE_RESOLUTION,
        version_of="scitex-cards",
        command_categories=_COMMAND_CATEGORIES,
    ),
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
@click.version_option(__version__, "-V", "--version", prog_name="scitex-cards")
@click.pass_context
def main(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    """scitex-cards CLI entry point."""
    # CURRENCY gate (operator directive: stale or broken installs ERROR, never
    # warn) — every CLI invocation is gated here, before any subcommand runs.
    # `check_currency()` is a no-op when scitex-dev is absent; when present it
    # raises with the exact remedy command, which we surface as a clean
    # ClickException rather than a raw traceback. See `_currency.py`.
    _run_currency_gate()
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
    **spec_command_kwargs(
        summary="Render the task dependency graph to a PNG.",
        description=(
            "Builds a mermaid dependency graph (depends_on / blocks "
            "edges) from the resolved store and renders it to a PNG via "
            "the mermaid CLI (or --print-mermaid to inspect the source "
            "without rendering)."
        ),
        examples=(
            (
                "{prog} render-graph -o tasks.png",
                "Render the resolved store to tasks.png.",
            ),
        ),
    ),
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
def render_graph_cmd(output: str, print_mermaid: bool) -> None:
    """Render the resolved task store to a dependency PNG."""
    resolved = resolve_tasks_path(None)
    tasks = load_tasks(resolved)
    mermaid_src = build_mermaid(tasks)

    if print_mermaid:
        sys.stdout.write(mermaid_src)
        return

    engine = render(mermaid_src, output)
    click.echo(f"{output}  (rendered via {engine}; source: {store_label(None)})")


# --------------------------------------------------------------------------- #
# list-tasks                                                                  #
# --------------------------------------------------------------------------- #
@main.command(
    "list-tasks",
    **spec_command_kwargs(
        summary="List tasks with optional filters.",
        description=(
            "Without any filter, prints the same plain-text table / JSON "
            "array as before (backward-compatible). With one or more "
            "filters, matches are AND-composed."
        ),
        examples=(
            ('{prog} list-tasks --assignee "$SCITEX_CARDS_AGENT_ID" --json', ""),
            (
                "{prog} list-tasks --project scitex-cards --status pending "
                "--status in_progress",
                "",
            ),
            ("{prog} list-tasks --blocking-me", ""),
            ("{prog} list-tasks --id-prefix scitex-", ""),
            ("{prog} list-tasks --blocker __none", "rows with no blocker"),
        ),
    ),
)
@click.option(
    "--scope",
    default=None,
    help="Match `scope` exactly (use '' to ignore $SCITEX_CARDS_SCOPE).",
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
    "--blocking-operator",
    "blocking_operator",
    is_flag=True,  # hook-bypass: line-limit
    help=(
        "The operator's decision queue: same predicate as --blocking-me but "
        "rendered as a glanceable, project-grouped view (title + why / "
        "how-to-unblock). --json emits the raw rows."
    ),
)
@click.option(
    "--overdue",
    is_flag=True,
    help=(
        "Predicate: tasks past their next deadline AND not in a closed "
        "lifecycle state (done / failed / cancelled / goal). Uses the "
        "deadline / deadlines schema + repeater rules from "
        "scitex_cards._model.is_overdue (PR #125, cards-p6-overdue-ui). "
        "This filter is the ONLY thing a deadline drives (that, and the "
        "board view) — a deadline NEVER sends a notification, so poll "
        "this yourself. Owner nudges key on inactivity, not deadlines. "
        "Matches NON-recurring deadlines only: a repeater (+1w) rolls "
        "the next occurrence into the future, so a recurring card is "
        "never overdue."
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
    scope: str | None,
    assignee: str | None,
    agent: str | None,
    project: str | None,
    host: str | None,
    blocker: str | None,
    kind: str | None,
    id_prefix: str | None,
    blocking_me: bool,
    blocking_operator: bool,  # hook-bypass: line-limit
    overdue: bool,
    statuses: tuple,
    as_json: bool,
) -> None:
    """Print the resolved task list (filtered or not)."""
    # The operator's decision queue is its OWN glanceable, project-grouped
    # rendering (not the flat filter table), so dispatch it first.
    if blocking_operator:
        from ._admin import list_blocking_operator

        list_blocking_operator(None, as_json)
        return
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
            None,
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
    resolved = resolve_tasks_path(None)
    tasks = load_tasks(resolved)
    if as_json:
        click.echo(json.dumps(tasks))
        return
    # Header names the STORE (the SQLite database) — NOT `resolved`, which is
    # the non-task sidecar container `load_tasks` takes only for naming in
    # error text (see `_paths.resolve_tasks_path`). Printing that sidecar
    # here mislabeled the header with a path the data never lived at.
    click.echo(f"# {store_label(None)}  ({len(tasks)} tasks)")
    for task in tasks:
        click.echo(f"{task['id']:<24} {task['status']:<12} {task['title']}")


# --------------------------------------------------------------------------- #
# Attach the §1a sub-groups (defined in sibling modules).                     #
# --------------------------------------------------------------------------- #
from . import (  # hook-bypass: line-limit (_main.py pre-existing over-cap; minimal wire)
    _board,
    _cardsync,
    _ci_watch,
    _completion,
    _deliver,
    _gui,
    _hooks,
    _inbox,
    _index,
    _introspect,
    _loop,
    _mcp,
    _migration_cli,
    _notifyd,
    _reconcile,
    _runnable,
    _skills,
    _stats,
    _triage,
    _undelivered,
    _write,
)  # noqa: E402

# board <verb> — dependency-graph board lifecycle (start/stop/restart/
# status). Extracted to _board.py to keep _main.py under the 512-line cap;
# behaviour + pidfile path (~/.scitex/cards/board.pid) are unchanged.
_board.register(main)
# gui <verb> — the ecosystem-standard GUI verbs (open/serve/status/stop),
# shared with figrecipe / scitex-writer / scitex-scholar so the operator's
# `scitex_start_gui_servers` loop can bring every SciTeX GUI up the same way.
# A thin front over the board lifecycle above; `board` stays canonical.
_gui.register(main)
# index <verb> — SQLite derived-index lifecycle (rebuild / info). Extracted
# to _index.py alongside the board split (same pure-move refactor).
_index.register(main)
# inbox <verb> — inbox storage-backend lifecycle (migrate-to-sqlite / info).
# Phase 1 of the store SQLite migration: moves the per-recipient inbox off the
# monolithic task document so a 5 s digest-poll no longer re-parses all cards.
_inbox.register(main)
# migration <verb> — directory-card enforcement migration (plan / apply).
# Extracted to _migration_cli.py alongside the board split.
_migration_cli.register(main)
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
# Phase 1 MCP subgroup — §3 required four (start / doctor / list-tools /
# install). The module itself loads cleanly without fastmcp installed;
# individual verbs print a clear install hint when fastmcp is missing.
_mcp.register(main)
# P3b + P3d (lead-approved 2026-06-12) — self-consuming board loop.
# `scitex-cards next` returns the top runnable task for an agent;
# `scitex-cards watch --push` is the push side that wakes agents on
# new/commented/changed tasks. See _skills/scitex-cards/32_*.md for the
# 7-step agent self-consumption pattern.
_loop.register(main)
# T1.2 (lead a2a `74db4f2d`, 2026-06-14) — the parallelism dispatcher's
# batch runnable view. Sister to `next` (single pick); respects
# depends_on closure. See _runnable.py for the predicate.
_runnable.register(main)
# Backlog-consumption payload (operator design 2026-07-10: deferred is debt;
# recency-weighted draw + age-based expiry). Read-only; the twin/owner
# decides and mutates via the existing verbs. See _backlog_triage.py.
_triage.register(main)
# Hook-consumer wire (lead a2a `6fff33d6` + `fbffb879`, 2026-06-14,
# operator-mandated). `scitex-cards hook push|done` verbs are the
# CLI twins of POST /hooks/push and POST /hooks/done — same canonical
# event-payload shape, same idempotency. See _hooks.py for the spec.
_hooks.register(main)
# watch-ci (record-only, decoupled-pollers lane per operator override
# via dev msg `96afacc7`, 2026-06-15). Server-side cron-style poller;
# logs per-repo CI transitions + updates the local state cache. NO
# bus emission for ci-result (SAC has its own independent poller for
# the delivery side). See _ci_watch.py.
_ci_watch.register(main)
# reconcile-merged-prs (card-freshness automation) — periodic auto-close of
# cards whose linked PR (pr_url) has MERGED. Pure decision core + gh/REST
# merge-state seam live in `_reconcile_prs.py`; DRY-RUN by default, --apply
# to mutate. Paired with the scitex-cards-reconcile-merged-prs JobSpec.
_reconcile.register(
    main
)  # hook-bypass: line-limit (pre-existing over-cap; minimal wire)
# deliver (slice 1 of the standalone notification-DELIVERY rail). One-shot
# delivery pass — reads each recipient's pending notifications (read-only,
# never touches the user's `seen` cursor) and hands them to the channels in
# recipients.json, recording outcomes in the delivery ledger. cron/loop-
# runnable; the daemon + systemd unit are a LATER slice. See
# src/scitex_cards/_delivery/.
_deliver.register(main)
# notifyd (slice 2 of the standalone notification-DELIVERY rail). The always-on
# daemon: bare `scitex-cards notifyd` runs the foreground loop (systemd
# ExecStart) ticking deliver_pending every --interval seconds, single-instance
# locked + signal-aware, re-surfacing standing terminal comm-misses on a
# throttle. `--once` is a single pass; `notifyd install-unit` writes the
# operator-gated systemd user-unit template (never runs systemctl). See
# src/scitex_cards/_delivery/_daemon.py + _systemd.py.
_notifyd.register(main)
_cardsync.register(main)  # hook-bypass: line-limit (pre-existing over-cap; minimal wire)
# dev list-undelivered — the restart/cadence check against the DURABLE channel
# rail, with a mandatory positive control so a filtered zero is trustworthy.
_undelivered.register(main)


if __name__ == "__main__":
    main()

# EOF
