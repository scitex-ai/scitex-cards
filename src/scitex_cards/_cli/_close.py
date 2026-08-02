#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI ``close`` verb: close a card WITH the reason recorded.

Operator + lead directive (board-reconciliation, 2026-06-13): we needed
a way to retire a stale card and *preserve the reason*. ``delete`` drops
the row, losing context; the closed-enum ``VALID_STATUSES`` has no
``"closed"`` slot, and adding one would cascade through the model /
board / docs. So the ergonomic gap is filled here:

  scitex-todo close TASK_ID --reason TEXT [--by AUTHOR] [--json] \\
      [--dry-run] [-y]

Semantics (composition over invention):

  1. Append a structured comment ``[CLOSED] {reason}`` via
     :func:`scitex_cards._store.comment_task` — the reason is now in
     ``task.comments[]`` (Gitea-shaped activity log).
  2. Flip ``status`` to ``"cancelled"`` — the real terminal "closed as not
     planned" state. This verb previously wrote ``"deferred"`` because no
     closed-ish status existed; that overload made ``deferred`` mean both
     "closed" and "not now" at once, and TERMINAL_STATUSES resolved the
     ambiguity by treating every deferred card as closed. 354 open cards
     silently left the active board. Operator ruling 2026-07-10: "deferred
     は終了ではない" — ``deferred`` is OPEN. ``close`` writes ``cancelled``.
  3. Stamp ``_log_meta.closed_{at,by}`` (UTC ISO + author precedence
     chain identical to ``comment`` / ``done``).

Reason is REQUIRED (``click.UsageError`` if missing or empty) — the
whole point of this verb is honest reconciliation.
"""

from __future__ import annotations

import click

from .. import _store
from ._compat import spec_command_kwargs
from ._write import _emit


@click.command(
    "close",
    **spec_command_kwargs(
        summary="Close a task WITH a reason (preserves context in comments[]).",
        description=(
            "The NON-SUCCESS terminal-state verb (doctrine §1d: exactly "
            "`done` for success and `close --reason` for everything else). "
            "Composes `comment_task` + `update_task(status=cancelled)` and "
            "stamps _log_meta.closed_{at,by}. Reason is REQUIRED."
        ),
        examples=(
            (
                "{prog} close stale-card --reason 'superseded by PR #142' "
                '--by "$SCITEX_TODO_AGENT_ID"',
                "",
            ),
        ),
    ),
)
@click.argument("task_id")
@click.option(
    "--reason",
    default=None,
    help="WHY this card is being closed (required; recorded in comments[]).",
)
@click.option(
    "--by",
    default=None,
    help="Override closed_by / comment.author (default: $SCITEX_TODO_AGENT_ID, then $USER).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the close payload as JSON.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the intended close and exit 0 without mutating the store.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — close is non-interactive; reserved for §2).",
)
def close_cmd(task_id, reason, by, as_json, dry_run, yes) -> None:
    """Close ``task_id`` with ``reason`` recorded in ``comments[]``."""
    _ = yes  # accepted for §2 compliance
    if reason is None or not str(reason).strip():
        raise click.UsageError("--reason is required and must be non-empty")

    reason_text = str(reason).strip()
    comment_text = f"[CLOSED] {reason_text}"

    if dry_run:
        click.echo(
            f"# dry-run: would close task_id={task_id!r} reason={reason_text!r} "
            f"by={by!r}  (append comment + status=cancelled + stamp closed_at/by)"
        )
        return

    try:
        comment_payload = _store.comment_task(None, task_id, comment_text, by=by)
        # The comment carries the canonical UTC timestamp + resolved author
        # chain we used; reuse them so the close stamp is consistent.
        entry = comment_payload["comment"]
        existing = _store.get_task(None, task_id)
        log_meta = dict(existing.get("_log_meta") or {})
        log_meta["closed_at"] = entry["ts"]
        log_meta["closed_by"] = entry["author"]
        merged = _store.update_task(
            None,
            task_id,
            status="cancelled",
            _log_meta=log_meta,
        )
    except _store.TaskNotFoundError as exc:
        raise click.ClickException(str(exc)) from None
    except (_store.TaskValidationError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None

    payload = {
        "task_id": merged["id"],
        "status": merged["status"],
        "reason": reason_text,
        "comment": entry,
        "closed_at": log_meta["closed_at"],
        "closed_by": log_meta["closed_by"],
    }
    _emit(
        payload,
        as_json=as_json,
        human=(
            f"closed {merged['id']}  (by {log_meta['closed_by']} "
            f"at {log_meta['closed_at']}) reason={reason_text!r}"
        ),
    )


def register(main: click.Group) -> None:
    """Attach the `close` verb to the top-level CLI group."""
    main.add_command(close_cmd, name="close")


# EOF
