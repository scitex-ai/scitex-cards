#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side CLI verbs: add, done, summary (+ registration wiring).

The ``update`` verb lives in the sibling ``_update.py`` (pure move; the
one-verb-per-file precedent) and is registered from ``register()`` below.

Wraps :mod:`scitex_cards._store` (the Python API). Each verb is a thin
click command that forwards keyword args to the store and prints either
a human-readable line or JSON via ``--json``. The store itself is
resolved from the ambient environment — no verb accepts a store path.

The agent-facing convention these verbs honor (per
``GITIGNORED/ARCHITECTURE.md`` Req 1):

- ``--scope LABEL`` / ``--assignee LABEL`` on read verbs respect
  ``$SCITEX_TODO_SCOPE`` as the default. Pass ``--scope ""`` to opt out.
- ``--by NAME`` on ``done`` overrides the
  ``$SCITEX_TODO_AGENT_ID`` → ``$USER`` precedence chain.

The ``sync`` verb is a deliberate Phase-1 no-op stub (Req 2 substrate
lands in Phase 2). The stable name + flag shape exist now so docs and
skills can reference them; the body just dry-prints the plan.
"""

from __future__ import annotations

import json

import click

from .. import _store
from .._db import resolve_db_path
from .._model import VALID_BLOCKERS, VALID_KINDS, VALID_STATUSES
from ._compat import spec_command_kwargs

# --------------------------------------------------------------------------- #
# Shared option decorators                                                    #
# --------------------------------------------------------------------------- #
# Closed-enum CLI validation (fail-fast at click-parse time) — mirrors
# the _model validators so typos raise BEFORE we touch the disk. Matches
# the operator's "fail loud, fail fast" rule (TG 9494) one layer earlier
# than save_tasks.
_STATUS_CHOICE = click.Choice(list(VALID_STATUSES), case_sensitive=True)
_KIND_CHOICE = click.Choice(list(VALID_KINDS), case_sensitive=True)
_BLOCKER_CHOICE = click.Choice(list(VALID_BLOCKERS), case_sensitive=True)


class _BlockerOrClearParamType(click.ParamType):
    """`--blocker` accepting closed enum values + clear-the-field sentinels.

    Dev-flagged gap (lead a2a `f5a54f85`): the strict `_BLOCKER_CHOICE`
    rejects `""` and `"none"` at parse time, so there was no CLI verb
    form for "clear this card's blocker field". Cards that needed to
    FLIP off a blocker (e.g. campaign-* cards once the blocker
    resolved) couldn't be closed from the CLI without hand-editing.

    Fix: a dedicated ParamType for the UPDATE verb that honours `""`
    and `"none"` (case-insensitive) as the clear-the-field sentinel —
    both convert to `""`, which the existing CLI-layer translation
    (`fields[key] = None if value == "" else value`) turns into
    `update_task(blocker=None)`, which the Python API treats as field
    deletion. Closed enum values pass through unchanged.

    Not used on the ADD verb — you cannot clear a field on insert.
    """

    name = "blocker_or_clear"

    def convert(self, value, param, ctx):
        if value is None:
            return None
        s = str(value)
        if s == "" or s.lower() == "none":
            return ""
        if s in VALID_BLOCKERS:
            return s
        self.fail(
            f"{s!r} is not one of {VALID_BLOCKERS}, '', or 'none'",
            param,
            ctx,
        )

    def get_metavar(self, param, ctx=None):
        # click >= 8.2 passes ctx as a keyword; older click passes only
        # param. Without the default, `scitex-todo update --help` crashed
        # with a TypeError on newer click (found by neurovista, 2026-07-11).
        return "[" + "|".join(list(VALID_BLOCKERS) + ["", "none"]) + "]"


_BLOCKER_OR_CLEAR = _BlockerOrClearParamType()


class _KindOrClearParamType(click.ParamType):
    """`--kind` accepting closed enum values + the `""` clear sentinel.

    Same gap as `_BlockerOrClearParamType`, one field over: the strict
    `_KIND_CHOICE` rejects `""` at parse time, so the "pass '' to CLEAR"
    contract every other surface documents had no CLI form for `kind` —
    a card mis-filed as `kind: status` could not be put back to the
    default (absent `kind` == `"task"`) without hand-editing the YAML.

    `""` converts to `""` and is passed VERBATIM to `update_task`, whose
    store layer owns the clear rule (`_store_enums`) and pops the key.

    Not used on the ADD verb — you cannot clear a field on insert.
    """

    name = "kind_or_clear"

    def convert(self, value, param, ctx):
        if value is None:
            return None
        s = str(value)
        if s == "":
            return ""
        if s in VALID_KINDS:
            return s
        self.fail(f"{s!r} is not one of {VALID_KINDS} or ''", param, ctx)

    def get_metavar(self, param, ctx=None):
        # click >= 8.2 passes ctx as a keyword; older click passes only
        # param — same compat shim as the blocker type above.
        return "[" + "|".join(list(VALID_KINDS) + [""]) + "]"


_KIND_OR_CLEAR = _KindOrClearParamType()


def _emit(payload, *, as_json: bool, human: str) -> None:
    """Print `payload` as JSON or `human` as text, per the --json flag."""
    if as_json:
        click.echo(json.dumps(payload, default=str))
    else:
        click.echo(human)


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #
@click.command(
    "add",
    **spec_command_kwargs(
        summary="Append a new task to the store.",
        examples=(
            (
                "{prog} add my-task 'Implement my-task' "
                '--agent "$SCITEX_TODO_AGENT_ID" --project scitex-todo',
                "",
            ),
        ),
    ),
)
@click.argument("id")
@click.argument("title")
@click.option(
    "--status",
    type=_STATUS_CHOICE,
    default="deferred",
    show_default=True,
    help="Initial status (closed enum — see VALID_STATUSES).",
)
# Operator-co-designed surface (TG 9667).
@click.option(
    "--task", default=None, help="The BIG board-card text (distinct from --title)."
)
@click.option(
    "--project", default=None, help="Project / repo basename (e.g. 'scitex-todo')."
)
@click.option("--host", default=None, help="Where the work happens (hostname).")
@click.option(
    "--agent", default=None, help="Owning agent (forward-compat alias for --assignee)."
)
@click.option(
    "--group",
    default=None,
    help=(
        "TRACK-1 dispatch cluster (lead a2a `74db4f2d`). Free-form "
        "non-empty string. The parallelism dispatcher queries "
        "`runnable(group=<G>)` so independent tasks in <G> run "
        "concurrently. Distinct from _groups.py's project Group "
        "(viewer aggregation)."
    ),
)
@click.option(
    "--goal", default=None, help="WHY (parent-goal text); 🎯 line on the card."
)
@click.option(
    "--last-activity",
    "last_activity",
    default=None,
    help="ISO-8601 UTC; drives recency color.",
)
@click.option(
    "--blocker",
    type=_BLOCKER_CHOICE,
    default=None,
    help="Closed enum (only valid when --status blocked).",
)
@click.option("--pr-url", "pr_url", default=None, help="GH/Gitea PR link.")
@click.option("--issue-url", "issue_url", default=None, help="GH/Gitea issue link.")
@click.option(
    "--kind",
    type=_KIND_CHOICE,
    default=None,
    help="Closed enum (absent ⇒ 'task').",
)
# Compute-kind metadata (ADR-0002).
@click.option(
    "--job-id", "job_id", default=None, help="kind=compute: scheduler job id."
)
@click.option("--command", default=None, help="kind=compute: command line.")
@click.option(
    "--started-at", "started_at", default=None, help="kind=compute: start ISO-8601 UTC."
)
@click.option(
    "--finished-at",
    "finished_at",
    default=None,
    help="kind=compute: finish ISO-8601 UTC.",
)
# Legacy fields (preserved — assignee stays primary today per ADR-0008 D2).
@click.option("--scope", default=None, help="Audience label (free-form string).")
@click.option(
    "--assignee",
    default=None,
    help="Who should act on this (PRIMARY linking field today).",
)
@click.option(
    "--priority", type=int, default=None, help="Integer priority (lower = earlier)."
)
@click.option(
    "--parent", default=None, help="Parent task id (nests this task under it)."
)
@click.option(
    "--note", default=None, help="Markdown note shown in the board detail drawer."
)
@click.option(
    "--depends-on",
    "depends_on",
    multiple=True,
    help="Task id this task depends on (repeatable).",
)
@click.option(
    "--blocks",
    "blocks",
    multiple=True,
    help="Task id this task blocks (repeatable).",
)
@click.option("--repo", default=None, help="Repo association (free-form string).")
@click.option(
    "--created-by",
    "created_by",
    default=None,  # hook-bypass: line-limit
    help="Creating USER (agent/human). Absent => $SCITEX_TODO_AGENT_ID -> $USER.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the inserted task as JSON.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be added and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — add is non-interactive; reserved for §2).",
)
def add_cmd(
    id,
    title,
    status,
    task,
    project,
    host,
    agent,
    group,
    goal,
    last_activity,
    blocker,
    pr_url,
    issue_url,
    kind,
    job_id,
    command,
    started_at,
    finished_at,
    scope,
    assignee,
    priority,
    parent,
    note,
    depends_on,
    blocks,
    repo,
    created_by,  # hook-bypass: line-limit
    as_json,
    dry_run,
    yes,
) -> None:
    """Append a new task. Raises ``TaskValidationError`` on a duplicate id."""
    _ = yes  # accepted for §2 compliance
    if dry_run:
        click.echo(
            f"# dry-run: would add id={id!r} title={title!r} status={status!r} "
            f"agent={agent!r} project={project!r} kind={kind!r} blocker={blocker!r}"
        )
        return
    try:
        inserted = _store.add_task(
            None,
            id=id,
            title=title,
            status=status,
            scope=scope,
            assignee=assignee,
            priority=priority,
            parent=parent,
            note=note,
            depends_on=list(depends_on) if depends_on else None,
            blocks=list(blocks) if blocks else None,
            repo=repo,
            created_by=created_by,  # hook-bypass: line-limit
            # Operator-co-designed + compute fields forwarded via **extras.
            task=task,
            project=project,
            host=host,
            agent=agent,
            group=group,
            goal=goal,
            last_activity=last_activity,
            blocker=blocker,
            pr_url=pr_url,
            issue_url=issue_url,
            kind=kind,
            job_id=job_id,
            command=command,
            started_at=started_at,
            finished_at=finished_at,
        )
    except _store.TaskValidationError as exc:
        raise click.ClickException(str(exc)) from None
    # Lead the success line with the created id so `add` can never look like a
    # silent failure (empty stdout). `--json` path stays JSON-only (`_emit`).
    # Card `todo-add-empty-stdout-on-success`. (hook-bypass: line-limit)
    _emit(
        inserted,
        as_json=as_json,
        human=f"✓ added {inserted.get('id') or id}  "
        f"({inserted.get('status', status)}) {inserted.get('title', title)}",
    )


# --------------------------------------------------------------------------- #
# update — extracted to `_update.py` (one-verb-per-file precedent; pure       #
# move to bring this module back under the file-size cap). Registered in      #
# `register()` below.                                                         #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# done                                                                        #
# --------------------------------------------------------------------------- #
@click.command(
    "done",
    **spec_command_kwargs(
        summary="Mark a task as done; stamps _log_meta.completed_{at,by}.",
        description=(
            "The SUCCESS terminal-state verb (doctrine §1d: exactly `done` "
            "for success and `close --reason` for non-success). Idempotent: "
            "re-doneing a `done` task keeps the original stamp."
        ),
        examples=(('{prog} done my-task --by "$SCITEX_TODO_AGENT_ID"', ""),),
    ),
)
@click.argument("task_id")
@click.option(
    "--by",
    default=None,
    help="Override completed_by (default: $SCITEX_TODO_AGENT_ID, then $USER).",
)
@click.option("--json", "as_json", is_flag=True)
def done_cmd(task_id, by, as_json) -> None:
    """Set status=done and stamp the completion meta."""
    try:
        done = _store.complete_task(None, task_id, by=by)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    stamp = done.get("_log_meta", {}).get("completed_at", "?")
    who = done.get("_log_meta", {}).get("completed_by", "?")
    _emit(
        done,
        as_json=as_json,
        human=f"done {done['id']}  (by {who} at {stamp})",
    )


# --------------------------------------------------------------------------- #
# summary                                                                     #
# --------------------------------------------------------------------------- #
@click.command(
    "summary",
    **spec_command_kwargs(
        summary="Print task counts by status / scope / assignee.",
        description=(
            "Numeric progress report over the resolved store, optionally "
            "restricted to one scope/assignee before counting."
        ),
        examples=(("{prog} summary --json", "Structured counts."),),
    ),
)
@click.option("--scope", default=None, help="Filter to this scope before counting.")
@click.option("--assignee", default=None)
@click.option("--json", "as_json", is_flag=True)
def summary_cmd(scope, assignee, as_json) -> None:
    """Counts by status, scope, assignee for the resolved store."""
    info = _store.summarize_tasks(None, scope=scope, assignee=assignee)
    if as_json:
        click.echo(json.dumps(info))
        return
    # `info["store"]` names the non-task sidecar container (see
    # `_paths.resolve_tasks_path`), not the store identity — the header
    # names the real store (the SQLite database) instead.
    click.echo(f"# {resolve_db_path(None)}  ({info['total']} tasks)")
    click.echo("by_status:")
    for s, n in info["by_status"].items():
        click.echo(f"  {s:<12} {n}")
    click.echo("by_scope:")
    for s, n in sorted(info["by_scope"].items()):
        click.echo(f"  {s or '(none)':<28} {n}")
    click.echo("by_assignee:")
    for s, n in sorted(info["by_assignee"].items()):
        click.echo(f"  {s or '(none)':<28} {n}")


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #
def register(main: click.Group) -> None:
    """Attach the Phase-1 mutation verbs (add / update / done / summary).

    The admin verbs (`resolve-store` / `init-store` / `sync-store`) and the
    `list_tasks_filtered` helper live in the sibling `_admin.py` module — see
    its `register()`. `list-tasks` itself is owned by `_cli/_main.py` (the
    filter flags from the old `list` verb were folded in there; the `list`
    Click verb was removed per audit §1 — bare transitive verb at top level).
    """
    from . import _admin, _close, _comment, _emit, _reassign, _stale, _update

    main.add_command(add_cmd, name="add")
    main.add_command(done_cmd, name="done")
    main.add_command(summary_cmd, name="summary")
    _update.register(main)
    _comment.register(main)
    _close.register(main)
    _reassign.register(main)
    _stale.register(main)
    # Generic producer verbs: `emit-event` (no-import shell-out emit seam) +
    # `find-card` (repo->card lookup; renamed from `resolve-card` in the
    # slice-6b verb-rename pilot). Consumed by scitex-dev's C7 `released` /
    # C8 `pulled` fleet producers.
    _emit.register(main)
    _admin.register(main)


# EOF
