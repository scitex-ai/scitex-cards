#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The two flags every mutating CLI verb owes its caller, and the guard behind them.

``--dry-run`` (say what would happen, change nothing) and ``--yes`` (do not ask
me) are required of mutating verbs by the CLI audit's §2, and by the
constitution's rule that any change whose blast radius you cannot enumerate in
advance runs first as a dry run. They live here rather than being re-declared
per verb so the wording, the short flag and the confirmation semantics cannot
drift apart across the CLI.

WHY THE PROMPT IS TTY-GATED, which is the whole design decision in this file.

A ``--yes`` that gates nothing is a flag that lies, and the constitution is
explicit that a choice you do not intend to support should not be offered. But
a ``--yes`` that is REQUIRED off-TTY turns every cron, CI step and agent that
already shells out to these verbs into an immediate breakage — and those
callers are exactly the ones who cannot answer a prompt.

So the prompt appears only where somebody can actually answer it. Off-TTY the
verb proceeds exactly as it did before these flags existed. That keeps the flag
honest — it really does skip a confirmation you would otherwise be shown —
while adding no new way for existing automation to fail.

That is deliberately NOT the shape sac's CLI chose (it refuses without ``--yes``
even off-TTY). The difference is who calls: sac's lifecycle verbs are driven by
an operator or a supervisor that can be taught the flag, whereas these are
already wired into hooks and crons whose invocation this package does not own.
"""

from __future__ import annotations

import sys

import click

#: What a dry run prints in front of every line describing an unmade change,
#: so a reader scanning a terminal cannot mistake a preview for a result.
DRY_RUN_PREFIX = "[dry-run]"


def dry_run_option(fn):
    """Attach ``--dry-run`` to a command."""
    return click.option(
        "--dry-run",
        is_flag=True,
        default=False,
        help="Report what would change and exit without changing it.",
    )(fn)


def yes_option(fn):
    """Attach ``--yes`` / ``-y`` to a command, bound to ``assume_yes``."""
    return click.option(
        "--yes",
        "-y",
        "assume_yes",
        is_flag=True,
        default=False,
        help="Skip the confirmation prompt (no effect when not on a terminal).",
    )(fn)


def mutating_options(fn):
    """Attach BOTH flags. Use on any verb that changes state."""
    return dry_run_option(yes_option(fn))


def confirm_or_abort(action: str, *, assume_yes: bool = False) -> None:
    """Ask before ``action``, unless told not to or nobody can answer.

    Returns normally when the caller may proceed; raises click's ``Abort`` when
    an interactive user declines. ``action`` is phrased as the question, e.g.
    ``"Install the notifyd unit?"``.

    Silent in three cases, and only one of them is a preference:

    * ``assume_yes`` — the caller said so.
    * stdin is not a terminal — there is nobody to ask. See the module
      docstring; this is the choice that keeps existing crons working.
    * stdin is closed or detached, which raises on ``isatty()`` in some
      sandboxes and is treated as "not a terminal" rather than as a failure —
      a confirmation helper must never be the thing that breaks the verb.
    """
    if assume_yes:
        return
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        interactive = False
    if not interactive:
        return
    # The prompt IS the intended UX here, and it is already gated on
    # `sys.stdin.isatty()` above, so it cannot fire in CI, cron, a hook or an
    # agent shell-out. The rule's suggested remedy — refuse-without-`--yes` —
    # is the shape sac's CLI chose; this package deliberately did not, because
    # these verbs are already wired into hooks and crons whose invocation it
    # does not own, and refusing would break them the day the flag landed. The
    # module docstring carries the full argument.
    # audit-cli: interactive-ok — TTY-gated; refusing would break existing crons
    click.confirm(action, abort=True)


__all__ = [
    "DRY_RUN_PREFIX",
    "confirm_or_abort",
    "dry_run_option",
    "mutating_options",
    "yes_option",
]

# EOF
