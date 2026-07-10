#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side CLI verbs: add, update.

Wraps :mod:`scitex_todo._store` (the Python API). Each verb is a thin
click command that resolves the store path through the usual precedence
chain (CLI ``--tasks`` → ``$SCITEX_TODO_TASKS`` → project → user →
bundled example), forwards keyword args, and prints either a human-
readable line or JSON via ``--json``.

The agent-facing convention these verbs honor (per
``GITIGNORED/ARCHITECTURE.md`` Req 1):

- ``--scope LABEL`` / ``--assignee LABEL`` on read verbs respect
  ``$SCITEX_TODO_SCOPE`` as the default. Pass ``--scope ""`` to opt out.

``done`` and ``summary`` — the two short, self-contained verbs — live in
the sibling ``_done_summary.py`` module (extracted to keep this file
under the project's 512-line convention; same split shape every other
verb-group in this package already uses).
"""

from __future__ import annotations

import json

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand

from .. import _store
from .._model import VALID_BLOCKERS, VALID_KINDS, VALID_STATUSES
from .._paths import resolve_tasks_path

# --------------------------------------------------------------------------- #
# Shared option decorators                                                    #
# --------------------------------------------------------------------------- #
_TASKS_OPTION = click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)

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
            param, ctx,
        )

    def get_metavar(self, param):
        return "[" + "|".join(list(VALID_BLOCKERS) + ["", "none"]) + "]"


_BLOCKER_OR_CLEAR = _BlockerOrClearParamType()


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
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Append a new task to the store.",
        description=(
            "Inserts a new row identified by the positional ID + TITLE. "
            "Raises on a duplicate id. The many optional flags mirror "
            "every field on the task schema (agent/project/host, "
            "kind=compute metadata, graph edges, …) — see the options "
            "list below for the full set.",
        ),
        examples=(
            Example(
                "{prog} add my-task 'Implement my-task' "
                "--agent proj-scitex-todo --project scitex-todo",
                "Create a task owned by an agent.",
            ),
        ),
    ),
)
@click.argument("id")
@click.argument("title")
@click.option(
    "--status",
    type=_STATUS_CHOICE,
    default="pending",
    show_default=True,
    help="Initial status (closed enum — see VALID_STATUSES).",
)
# Operator-co-designed surface (TG 9667).
@click.option("--task", default=None, help="The BIG board-card text (distinct from --title).")
@click.option("--project", default=None, help="Project / repo basename (e.g. 'scitex-todo').")
@click.option("--host", default=None, help="Where the work happens (hostname).")
@click.option("--agent", default=None, help="Owning agent (forward-compat alias for --assignee).")
@click.option(
    "--group", default=None,
    help=(
        "TRACK-1 dispatch cluster (lead a2a `74db4f2d`). Free-form "
        "non-empty string. The parallelism dispatcher queries "
        "`runnable(group=<G>)` so independent tasks in <G> run "
        "concurrently. Distinct from _groups.py's project Group "
        "(viewer aggregation)."
    ),
)
@click.option("--goal", default=None, help="WHY (parent-goal text); 🎯 line on the card.")
@click.option("--last-activity", "last_activity", default=None, help="ISO-8601 UTC; drives recency color.")
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
@click.option("--job-id", "job_id", default=None, help="kind=compute: scheduler job id.")
@click.option("--command", default=None, help="kind=compute: command line.")
@click.option("--started-at", "started_at", default=None, help="kind=compute: start ISO-8601 UTC.")
@click.option("--finished-at", "finished_at", default=None, help="kind=compute: finish ISO-8601 UTC.")
# Legacy fields (preserved — assignee stays primary today per ADR-0008 D2).
@click.option("--scope", default=None, help="Audience label (free-form string).")
@click.option(
    "--assignee", default=None, help="Who should act on this (PRIMARY linking field today)."
)
@click.option("--priority", type=int, default=None, help="Integer priority (lower = earlier).")
@click.option("--parent", default=None, help="Parent task id (nests this task under it).")
@click.option("--note", default=None, help="Markdown note shown in the board detail drawer.")
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
    "--created-by", "created_by", default=None,  # hook-bypass: line-limit
    help="Creating USER (agent/human). Absent => $SCITEX_TODO_AGENT -> $USER.",
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
@_TASKS_OPTION
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
    tasks_path,
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
            tasks_path,
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
    _emit(
        inserted,
        as_json=as_json,
        human=f"added {inserted['id']}  ({inserted['status']}) {inserted['title']}",
    )


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #
@click.command(
    "update",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Mutate fields of an existing task by id.",
        description=(
            "Pass an empty string (e.g. --scope '') to CLEAR a field. "
            "--depends-on / --blocks REPLACE the list (repeat the flag "
            "per id; pass once with '' to clear; +/- delta semantics "
            "are a follow-up).",
        ),
        examples=(
            Example(
                "{prog} update my-task --status in_progress --priority 1 "
                "--agent proj-scitex-todo",
                "Flip status + reprioritize.",
            ),
        ),
    ),
)
@click.argument("task_id")
@click.option("--title", default=None)
@click.option("--status", type=_STATUS_CHOICE, default=None)
# Operator-co-designed surface (TG 9667).
@click.option("--task", default=None, help="The BIG board-card text.")
@click.option("--project", default=None)
@click.option("--host", default=None)
@click.option("--agent", default=None, help="Owning agent (forward-compat alias for --assignee).")
@click.option(
    "--group", default=None,
    help="TRACK-1 dispatch cluster. Use '' to CLEAR.",
)
@click.option("--goal", default=None)
@click.option("--last-activity", "last_activity", default=None)
@click.option(
    "--blocker",
    type=_BLOCKER_OR_CLEAR,
    default=None,
    help=(
        "Closed enum (when status=blocked), OR '' / 'none' to CLEAR an "
        "existing blocker on the card. Dev-flagged gap fix: previously "
        "the strict closed-enum rejected '' / 'none' so there was no "
        "CLI form for clearing the field."
    ),
)
@click.option("--pr-url", "pr_url", default=None)
@click.option("--issue-url", "issue_url", default=None)
@click.option("--kind", type=_KIND_CHOICE, default=None)
# Compute-kind metadata.
@click.option("--job-id", "job_id", default=None)
@click.option("--command", default=None)
@click.option("--started-at", "started_at", default=None)
@click.option("--finished-at", "finished_at", default=None)
# Graph wiring (now supported on update, not just add).
@click.option(
    "--depends-on",
    "depends_on",
    multiple=True,
    help="REPLACE depends_on list. Repeat the flag per id; pass once with '' to clear.",
)
@click.option(
    "--blocks",
    "blocks",
    multiple=True,
    help="REPLACE blocks list. Repeat the flag per id; pass once with '' to clear.",
)
# Legacy fields.
@click.option("--scope", default=None, help="New scope (use '' to clear).")
@click.option("--assignee", default=None, help="New assignee (use '' to clear).")
@click.option("--priority", type=int, default=None)
@click.option("--parent", default=None)
@click.option("--note", default=None)
@click.option("--repo", default=None)
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print which fields would change and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — update is non-interactive; reserved for §2).",
)
@_TASKS_OPTION
def update_cmd(
    task_id,
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
    depends_on,
    blocks,
    scope,
    assignee,
    priority,
    parent,
    note,
    repo,
    as_json,
    dry_run,
    yes,
    tasks_path,
) -> None:
    """Apply each provided field to the matching task."""
    _ = yes  # accepted for §2 compliance
    fields: dict = {}
    # Pass through only the fields the user actually provided (click's
    # `None` default = "not passed"). Empty string is the explicit
    # "clear this field" signal — translate to None for `update_task` so
    # the key is popped rather than stored as `""`.
    for key, value in (
        ("title", title),
        ("status", status),
        ("task", task),
        ("project", project),
        ("host", host),
        ("agent", agent),
        ("goal", goal),
        ("last_activity", last_activity),
        ("blocker", blocker),
        ("pr_url", pr_url),
        ("issue_url", issue_url),
        ("kind", kind),
        ("job_id", job_id),
        ("command", command),
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("scope", scope),
        ("assignee", assignee),
        ("group", group),
        ("priority", priority),
        ("parent", parent),
        ("note", note),
        ("repo", repo),
    ):
        if value is None:
            continue
        fields[key] = None if value == "" else value

    # --depends-on / --blocks: click's `multiple=True` returns a tuple.
    # Empty tuple = flag not passed → don't touch. Tuple of one empty
    # string = explicit "clear list". Otherwise REPLACE the list.
    for key, multi in (("depends_on", depends_on), ("blocks", blocks)):
        if not multi:
            continue
        if len(multi) == 1 and multi[0] == "":
            fields[key] = None
        else:
            fields[key] = [v for v in multi if v != ""]

    if not fields:
        raise click.ClickException(
            "no fields to update; pass at least one field flag (see --help)"
        )

    if dry_run:
        click.echo(
            f"# dry-run: would update task_id={task_id!r} fields={fields!r}"
        )
        return
    try:
        merged = _store.update_task(tasks_path, task_id, **fields)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except _store.TaskValidationError as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        merged,
        as_json=as_json,
        human=f"updated {merged['id']}  ({merged['status']}) {merged['title']}",
    )


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
    `done` / `summary` live in the sibling `_done_summary.py` module.
    """
    from . import _admin, _close, _comment, _done_summary, _stale

    main.add_command(add_cmd, name="add")
    main.add_command(update_cmd, name="update")
    _done_summary.register(main)
    _comment.register(main)
    _close.register(main)
    _stale.register(main)
    _admin.register(main)


# EOF
