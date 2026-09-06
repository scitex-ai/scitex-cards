#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Carry tolerated-value warnings back to the caller who caused them.

THE DEFECT THIS EXISTS FOR, measured on the live board 2026-08-16.
`_validate._warn_tolerated` shouts about an unknown or abolished value and keeps
going -- correctly, by operator ruling 2026-07-10 (「カードが書けないということは
なしで大丈夫です、warning で十分です」): a status value must never cost someone
their card, and refusing would make one legacy row fail every other agent's write.

But the shout goes to the MCP SERVER process's stderr and to `warnings`. An agent
calling `add_task` over MCP receives the tool result and nothing else. So the one
mechanism that notices is invisible to the only party who can act on it.

What that permitted, measured rather than imagined:

  * three cards carrying the ABOLISHED status `pending` were CREATED after its
    abolition, all by the maintainer of the package that abolished it, inside
    36 hours;
  * a several-hundred-card sweep set `archived`, a status no build has ever
    known, and it stood roughly six hours before being reversed.

Every one of those fired the warning. Nobody saw one.

WHY A COLLECTOR AND NOT A RAISE. Refusing is ruled out above and the reasoning is
sound. Returning the warning costs nothing and stops a sweep at card ONE rather
than card 293 -- the barrier belongs where the information is missing, not where
the write is (constitution s7).

WHY A ContextVar. The store lock is held across the write and the fleet runs
concurrent writers in threads; a module-level list would leak one caller's
warnings into another's result. A ContextVar is per-context by construction, so
collection cannot bleed across callers even if two writes interleave.

INACTIVE BY DEFAULT. With no collector open `record()` is a no-op, so every
existing caller of `_warn_tolerated` -- including the READ path, which fires on
every load of a store holding legacy rows -- is unchanged and pays nothing.
"""

from __future__ import annotations

import contextlib
import contextvars

#: Active collector for THIS context, or ``None`` when nobody is collecting.
#: Never read directly outside this module.
_COLLECTOR: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "scitex_cards_tolerated_collector", default=None
)


def record(message: str, side: str = "read-side", task_id: str | None = None) -> None:
    """Offer ``message`` to the active collector, if there is one and it wants it.

    A no-op when nothing is collecting, which is the default and the case for
    every read-path warning outside a write.
    """
    state = _COLLECTOR.get()
    if state is None:
        return
    sink, wanted_id = state
    # WRITE-SIDE ONLY. A write re-reads the store, so the read-side warning for
    # the SAME card fires again on the way back with different wording -- the
    # caller would get the same problem described twice, once accusing them and
    # once explaining that somebody else may be ahead of them.
    if side != "write-side":
        return
    # AND ONLY THIS CALLER'S CARD. `save_tasks` validates the WHOLE task list, so
    # an unscoped collector hands the writer a warning for every off-enum row in
    # the document -- ELEVEN of them on the live board today, about cards they
    # have never touched, with theirs buried among them. That is worse than the
    # silence this exists to fix: silence is a check nobody reads, noise is a
    # check everybody learns to ignore.
    if wanted_id is not None and f"task {wanted_id!r}" not in message:
        return
    sink.append(message)


@contextlib.contextmanager
def collect(task_id: str | None = None):
    """Collect the tolerated warnings THIS caller caused, inside this block.

    ``task_id`` scopes collection to one card; ``None`` collects every
    write-side warning, which is what a bulk writer wants. Yields the list,
    populated as warnings fire and safe to read after the block exits::

        with collect(task_id) as tolerated:
            save_tasks(...)
        if tolerated:
            result["warnings"] = tolerated

    The token is reset in a ``finally`` so an exception mid-write cannot leave a
    collector armed for the next caller in this context.
    """
    sink: list[str] = []
    token = _COLLECTOR.set((sink, task_id))
    try:
        yield sink
    finally:
        _COLLECTOR.reset(token)


__all__ = ["collect", "record"]

# EOF
