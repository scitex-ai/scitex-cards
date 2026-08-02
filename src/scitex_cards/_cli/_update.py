#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI ``update`` verb: mutate fields of an existing task by id.

Extracted from ``_write.py`` (pure move; the one-verb-per-file precedent
of ``_comment.py`` / ``_close.py`` / ``_reassign.py``) to bring that
module back under the file-size cap. Shares the option plumbing
(closed-enum choices / ``_emit``) with its siblings via ``._write``.
"""

from __future__ import annotations

import click

from .. import _store
from .._store_enums import CLEARABLE_ENUM_FIELDS, UNCLEARABLE_ENUM_FIELDS
from ._compat import spec_command_kwargs
from ._write import (
    _BLOCKER_OR_CLEAR,
    _KIND_OR_CLEAR,
    _STATUS_CHOICE,
    _emit,
)

# Closed-enum fields — sourced from the store so the two cannot drift.
_ENUM_FIELDS: frozenset[str] = frozenset(
    CLEARABLE_ENUM_FIELDS + UNCLEARABLE_ENUM_FIELDS
)


@click.command(
    "update",
    **spec_command_kwargs(
        summary="Mutate fields of an existing task by id.",
        description=(
            "Pass an empty string (e.g. --scope '') to CLEAR a field. "
            "--depends-on / --blocks REPLACE the list (repeat the flag "
            "per id; pass once with '' to clear; +/- delta semantics "
            "are a follow-up)."
        ),
        examples=(
            (
                "{prog} update my-task --status in_progress --priority 1 "
                '--agent "$SCITEX_TODO_AGENT_ID"',
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
@click.option(
    "--agent", default=None, help="Owning agent (forward-compat alias for --assignee)."
)
@click.option(
    "--group",
    default=None,
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
@click.option(
    "--parked",
    default=None,
    help=(
        "WHY this card deliberately stands (free text), OR '' to UN-PARK it. "
        "A park exempts the card from the backlog nudge and from auto-expiry, "
        "so a card that could be parked but not un-parked was a state you "
        "could enter and not leave — while invisible to the sweep that exists "
        "to catch exactly that. Every other clearable field had a CLI form; "
        "this one was simply never wired."
    ),
)
@click.option("--pr-url", "pr_url", default=None)
@click.option("--issue-url", "issue_url", default=None)
@click.option(
    "--kind",
    type=_KIND_OR_CLEAR,
    default=None,
    help="Closed enum, OR '' to CLEAR (absent kind ⇒ 'task').",
)
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
    parked,
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
) -> None:
    """Apply each provided field to the matching task."""
    _ = yes  # accepted for §2 compliance
    fields: dict = {}
    # Pass through only the fields the user actually provided (click's
    # `None` default = "not passed"). Empty string is the explicit
    # "clear this field" signal — translate to None for `update_task` so
    # the key is popped rather than stored as `""`.
    #
    # EXCEPT the closed-enum fields, which go through VERBATIM: the store
    # owns what `""` means on them (`_store_enums` — blocker/kind: delete
    # the key; status: refuse loudly, since a card must carry a decision).
    # `--status ''` cannot even be typed (click's closed Choice rejects it
    # at parse time, naming the valid set), so the CLI never expresses a
    # status clear — deliberate, matching the store's rule.
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
        # Free text, NOT a closed enum — so `--parked ''` reaches the store as
        # None and un-parks the card, via the generic ""-clears rule below.
        ("parked", parked),
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
        if key in _ENUM_FIELDS:
            fields[key] = value
        else:
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
        click.echo(f"# dry-run: would update task_id={task_id!r} fields={fields!r}")
        return
    try:
        merged = _store.update_task(None, task_id, **fields)
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except _store.TaskValidationError as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        merged,
        as_json=as_json,
        human=f"updated {merged['id']}  ({merged['status']}) {merged['title']}",
    )


def register(main: click.Group) -> None:
    """Attach the `update` verb to the top-level CLI group."""
    main.add_command(update_cmd, name="update")


# EOF
