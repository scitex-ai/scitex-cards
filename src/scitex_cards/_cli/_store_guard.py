#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One refusal, shared by every verb that binds a port.

WHY THIS IS A MODULE AND NOT A LINE IN EACH CLI FILE. On 2026-08-09 the
operator's board served a cards database frozen eight days earlier while the
fleet wrote to PostgreSQL. It rendered perfectly. Nothing raised, nothing
warned, and the only instrument that caught it was him saying that nothing was
arriving.

`gui serve` was guarded that day because it was the surface that burned him.
`board start` -- the OTHER door onto the same Django app -- was not, so for
three days whether he was protected depended on which verb he happened to type.
A guard that exists once per door is a guard that will be missing from the next
door somebody adds. This module is the door-independent half; each caller still
states its own reason for calling it, because the reasons genuinely differ.

WHAT IT DOES NOT DO, and the restraint is deliberate: it does not make
``resolve_store_target`` itself raise on the default tier. That would break
every zero-config install and every test that relies on one. The rule -- fail
fast, fail loud, no silent fallbacks -- is enforced at the doors where a guess
does damage, one door at a time, each with a written reason. A one-shot CLI
landing on the default is a fresh install behaving correctly; a SERVER landing
there is a deployment that lost its target and will serve whatever sits at that
filename, to everyone, for days.
"""

from __future__ import annotations

import click

__all__ = ["refuse_unconfigured_store"]


def refuse_unconfigured_store() -> None:
    """Raise ``click.ClickException`` unless somebody actually chose a store.

    CALL THIS BEFORE THE DRY-RUN BRANCH, not after. ``--dry-run`` reports what
    WOULD happen, and "would start board on port 8051" for a store nobody
    configured is a confident answer to the question the operator is asking
    precisely because he is unsure.

    It also keeps the positive control honest. A guard placed after the dry-run
    exit is never reached by the configured-store test, so that test passes
    whether the guard works or refuses everything -- a control that cannot fail,
    which is the defect class this guard exists to answer.

    CALLERS MUST DEFINE THEIR WRAPPER ABOVE THE DECORATOR STACK, never between
    it and the command. Placing a ``def`` inside a click decorator chain
    silently rebinds every decorator onto the wrong function, leaving the
    command undecorated and the verb UNREGISTERED ENTIRELY. That happened while
    writing the `gui serve` guard and was caught only by the positive control.

    ``ClickException`` rather than the raw error so the CLI prints the remedy
    instead of a traceback; the message already names every variable to set and
    the verb that reports what this process resolved.
    """
    from .._store_target import (
        StoreTargetNotConfigured,
        require_configured_store_target,
    )

    try:
        require_configured_store_target()
    except StoreTargetNotConfigured as exc:
        raise click.ClickException(str(exc)) from exc


# EOF
