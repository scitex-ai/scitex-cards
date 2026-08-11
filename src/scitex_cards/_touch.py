#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ACTIVITY stamp — one helper, so no verb has to REMEMBER the invariant.

``last_activity`` is not decoration. It is the field every last-writer-wins
reconciler reads to answer the only question it can ask: *of these two copies
of the same card, which one is the later act?* A verb that changes a card
without advancing it produces a card that lies about its own age.

WHAT THAT COSTS, MEASURED — scitex-dev, 2026-08-10, reconciling three hosts
(laptop / scitex-04 / scitex-03) to 3719 cards each. Two cards could not be
ordered at all: both copies carried an IDENTICAL ``last_activity``, because
the act that distinguished them was a COMPLETION, and ``complete_task``
stamped ``_log_meta.completed_at`` without touching ``last_activity``. The
reconciler fell back to "which side has a completion stamp" by hand.

Note the SHAPE of that failure, because it is the dangerous kind. It does not
lose a card — it loses a COMPLETION, and it loses it in the direction that
looks like ordinary reconciliation: the completed copy reads as the STALE one
whenever the other host touched the card later for any trivial reason, so the
reconciler un-completes finished work and reports success. Two cards were
caught by a human doing it once. A periodic reconciler does it forever.

AN AUDIT FOUND FIVE, NOT ONE. ``complete_task`` was the verb that got caught;
``resolve_task``, ``reopen_task``, ``restore_task`` and ``set_edge`` had the
same hole. That ratio is the argument for this module existing: the invariant
was already written down in prose, and prose lost 5-to-1 against the habit of
adding a verb. Hence a NAMED helper the next author trips over, plus the
enumerating test in ``tests/scitex_cards/test__mutating_verbs_touch_activity``
which walks the AST and FAILS on a new verb that forgets — the same posture
``clear_completion_stamp`` takes in ``_store_lifecycle``.

WHY NOT STAMP INSIDE ``_save_doc_unlocked`` INSTEAD, which would need no
discipline at all: because that primitive receives the whole document and
cannot tell a semantic edit from a rewrite. Stamping every card it persists
would advance ``last_activity`` on cards nobody touched, which silently RESETS
the stale-active nudge clock (``_stale_active``) for the whole board on any
bulk write — trading a reconciler bug for a board-wide alerting bug. The
invariant belongs to the VERB, which knows what it changed; the test is what
makes the verb's obligation mechanical rather than remembered.
"""

from __future__ import annotations

__all__ = ["touch_last_activity"]


def touch_last_activity(task: dict, when: str | None = None) -> str:
    """Advance ``task['last_activity']``. Returns the stamp that was written.

    Call this from EVERY verb that mutates a card, at the point the mutation
    is known to be real — after the idempotent-no-op early returns, before the
    persist. Passing ``when`` lets a verb that already computed a timestamp
    (for a comment, say) reuse it, so the card's comment and its activity
    stamp agree to the second instead of straddling a tick.

    Deliberately NOT idempotent-guarded: re-touching is the correct behaviour
    for a second real mutation, and a verb that must not stamp is a verb that
    should not call this at all.
    """
    from ._store import _utc_now_iso

    stamp = when or _utc_now_iso()
    task["last_activity"] = stamp
    return stamp


# EOF
