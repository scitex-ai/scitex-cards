#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards forget` — the forgetting sweep's terminal twin.

Operator doctrine 2026-10-22: forgetting is a feature. Triaging stale cards
one by one costs more than recreating them on demand, so AGE ALONE is a
valid reason to close a blocked/deferred/goal card with no recent activity.
DRY-RUN by default (report only); ``--apply`` performs the mutation, reusing
the ``close`` verb per card so every close carries its reason in
``comments[]`` and the ``_log_meta.closed_{at,by}`` stamp (mirrors
scitex-dev's board-mutation pattern and the `reconcile-merged-prs` verb —
no silent auto-close).

The horizon resolves flag > env > config file > default (see
:mod:`scitex_cards._forget.load_forget_config`). ``--days none`` (or config
``null``, or ``SCITEX_CARDS_FORGET_DAYS=none``) disables the sweep — the
OFF switch is one word, not a code change. ``in_progress`` cards are NEVER
candidates, whatever their age.
"""

from __future__ import annotations

import json

import click

from .. import _model
from .._forget import (
    ENV_FORGET_DAYS,
    ForgetConfig,
    forget_reason,
    load_forget_config,
    select_forget_candidates,
)
from .._paths import resolve_tasks_path
from ._compat import spec_command_kwargs
from ._mutating import confirm_or_abort, mutating_options


@click.command(
    "forget",
    **spec_command_kwargs(
        summary="Close stale blocked/deferred/goal cards by age alone.",
        description=(
            "The forgetting sweep: blocked/deferred/goal cards untouched "
            "longer than the horizon are closed with an age reason stamped "
            "in comments[]. in_progress is never touched. DRY-RUN by "
            "default; --apply mutates. The OFF switch is --days none "
            "(or forgetting.days=null in config, or "
            "SCITEX_CARDS_FORGET_DAYS=none)."
        ),
        examples=(
            ("{prog} forget", ""),
            ("{prog} forget --days 7", ""),
            ("{prog} forget --days none", ""),
            ("{prog} forget --apply", ""),
        ),
    ),
)
@click.option(
    "--days",
    "days",
    default="unset",
    help=(
        "Forgetting horizon in days (flag beats env beats config beats the "
        f"{ForgetConfig.days}-day default). 'none' disables the sweep."
    ),
)
@click.option(
    "--apply",
    "apply",
    is_flag=True,
    help=(
        "Actually close the stale cards (one `close` per card, reason "
        "stamped). Without this flag the verb is DRY-RUN (report only)."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the summary as JSON (machine-readable).",
)
@mutating_options
def forget_cmd(
    days: str, apply: bool, as_json: bool, dry_run: bool, assume_yes: bool
) -> None:
    """Run the forgetting sweep (report by default, close with ``--apply``)."""
    if dry_run:
        apply = False
    config = load_forget_config(explicit_days=days)

    if not config.enabled:
        message = (
            "# forget: DISABLED (horizon is none — set --days N, "
            f"{ENV_FORGET_DAYS}=N, or forgetting.days=N in config to arm)"
        )
        if as_json:
            click.echo(json.dumps({"enabled": False, "would_close": []}))
            return
        click.echo(message)
        return

    resolved = resolve_tasks_path(None)
    tasks = _model.load_tasks(resolved)
    candidates = select_forget_candidates(tasks, config)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "enabled": True,
                    "days": config.days,
                    "would_close": [t["id"] for t in candidates],
                },
                indent=2,
            )
        )
        if not apply:
            return
    else:
        if not candidates:
            click.echo(
                f"# forget (DRY-RUN): 0 stale card(s) older than "
                f"{config.days}d — nothing to close"
            )
            return
        click.echo(
            f"# forget ({'APPLIED' if apply else 'DRY-RUN'}): "
            f"{len(candidates)} stale card(s) older than {config.days}d"
            + ("" if apply else " (pass --apply to close)")
        )
        for task in candidates:
            click.echo(f"  {'closed' if apply else 'would-close'} {task['id']}")

    if not apply:
        return

    confirm_or_abort(
        f"Close {len(candidates)} stale card(s) older than {config.days}d?",
        assume_yes=assume_yes,
    )
    reason = forget_reason(config.days if config.days is not None else 0)
    from .. import _store

    for task in candidates:
        comment_payload = _store.comment_task(None, task["id"], f"[CLOSED] {reason}")
        entry = comment_payload["comment"]
        existing = _store.get_task(None, task["id"])
        log_meta = dict(existing.get("_log_meta") or {})
        log_meta["closed_at"] = entry["ts"]
        log_meta["closed_by"] = entry["author"]
        _store.update_task(
            None,
            task["id"],
            status="cancelled",
            blocker="",
            _log_meta=log_meta,
        )
    if not as_json:
        click.echo(f"# forget (APPLIED): closed {len(candidates)} card(s)")


def register(main: click.Group) -> None:
    """Attach the `forget` verb to the top-level CLI group."""
    main.add_command(forget_cmd, name="forget")


# EOF
