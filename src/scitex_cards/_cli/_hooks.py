#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verbs ``scitex-cards hook push|done`` — the producer-side wire
for shell-driven event producers.

Equivalent to the HTTP endpoints `/hooks/push` and `/hooks/done` but
callable from shell scripts that already have direct CLI access.
Lead a2a `6fff33d6` + `fbffb879`, 2026-06-14.
"""

from __future__ import annotations

import json
import sys

import click

from ._compat import spec_command_kwargs, spec_group_kwargs


def register(main: click.Group) -> None:
    """Attach the ``hook`` subgroup to the root CLI."""
    main.add_command(hook_group)


@click.group(
    "hook",
    **spec_group_kwargs(
        summary="Hook-consumer wire for shell-driven event producers.",
        description=(
            "Loose-coupling contract for SAC's push-hook + dev's "
            "merge-Action. Two verbs: push (record a git push event) + "
            "done (record a PR-merge done event). Both idempotent. See "
            "also the HTTP twins: POST /hooks/push, POST /hooks/done.",
        ),
        command_categories=[("Core", ["push", "done"])],
    ),
)
def hook_group() -> None:
    """The ``hook`` noun group."""


@hook_group.command(
    "push",
    **spec_command_kwargs(
        summary="Record a git-push event on the board.",
        description=(
            "Reads a canonical push-event JSON payload from --payload "
            "<FILE> or stdin (when --payload is '-'). Idempotently "
            "appends a comment to each named card.",
        ),
        examples=(("{prog} hook push --payload event.json", "Record from a file."),),
    ),
)
@click.option(
    "--payload",
    "payload_path",
    required=True,
    type=click.Path(exists=False),
    help="Path to a JSON file with the push event payload, or '-' for stdin.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate + print the event WITHOUT recording it on the board.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help=(
        "Accepted for CLI-convention uniformity (mutating verbs offer"
        " --yes); `hook push` is idempotent + non-interactive, so there"
        " is no prompt to skip."
    ),
)
def hook_push_cmd(payload_path: str, dry_run: bool, yes: bool) -> None:
    # `yes` is intentionally unused — hook push is idempotent and runs
    # non-interactively (git hooks / CI), so there is no destructive
    # prompt to gate; the flag exists for convention uniformity.
    del yes
    _run_hook(payload_path, expected_kind="push", dry_run=dry_run)


@hook_group.command(
    "done",
    **spec_command_kwargs(
        summary="Record a PR-merge / done event on the board.",
        description=(
            "Reads a canonical done-event JSON payload from --payload "
            "<FILE> or stdin (when --payload is '-'). Idempotently "
            "flips each named card to status=done with pr_url stamped.",
        ),
        examples=(("{prog} hook done --payload event.json", "Record from a file."),),
    ),
)
@click.option(
    "--payload",
    "payload_path",
    required=True,
    type=click.Path(exists=False),
    help="Path to a JSON file with the done event payload, or '-' for stdin.",
)
def hook_done_cmd(payload_path: str) -> None:
    _run_hook(payload_path, expected_kind="done")


def _run_hook(payload_path: str, *, expected_kind: str, dry_run: bool = False) -> None:
    """Shared body for the push / done verbs."""
    from .._hooks import HookEventError, dispatch_event, event_validate

    if payload_path == "-":
        text = sys.stdin.read()
    else:
        with open(payload_path, encoding="utf-8") as handle:
            text = handle.read()
    try:
        body = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"invalid JSON: {exc}") from None
    if not isinstance(body, dict):
        raise click.ClickException("payload must be a JSON object")
    body.setdefault("kind", expected_kind)
    if body.get("kind") != expected_kind:
        raise click.ClickException(
            f"verb expects kind={expected_kind!r}; payload declared "
            f"kind={body.get('kind')!r}"
        )
    try:
        event = event_validate(body)
    except HookEventError as exc:
        raise click.ClickException(str(exc)) from None
    if dry_run:
        click.echo(json.dumps({"dry_run": True, "would_dispatch": event}, default=str))
        return
    summary = dispatch_event(event)
    click.echo(json.dumps(summary, default=str))


# EOF
