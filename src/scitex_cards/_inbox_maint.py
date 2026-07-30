#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-off maintenance verbs for the per-recipient inbox (:mod:`_inbox`).

Kept separate from :mod:`scitex_cards._inbox` (the hot enqueue/poll path) so
that module stays lean; these are operator-run, run-rarely sweeps over the
whole ``inboxes.json`` sidecar.

``collapse_digests`` is the ONE-TIME backlog fix for the digest replay-storm:
a digest is a full point-in-time snapshot, but notifyd historically enqueued a
fresh one every tick without superseding prior unseen digests, so a recipient
whose channel was down piled up dozens of stale digests that all replayed on
reconnect. The durable fix is ``enqueue(..., supersede=True)`` at the digest
call site; this verb clears the ALREADY-accumulated backlog in one safe locked
pass (keep the newest unseen digest deliverable, mark the rest seen).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ._inbox import (
    _inboxes_path,
    _load_inboxes_section,
    _save_inboxes_unlocked,
)
from ._model import _store_lock

logger = logging.getLogger(__name__)


def _is_digest(record: dict, *, event_type: str, card_id: str) -> bool:
    """True when ``record`` is a cumulative digest (its type + synthetic id)."""
    return record.get("event_type") == event_type and record.get("card_id") == card_id


def collapse_digests(store: str | Path | None = None) -> dict:
    """Collapse each recipient's UNSEEN digest backlog to the newest one.

    One safe, locked maintenance pass over the whole inboxes sidecar: for
    every recipient, keep the single NEWEST unseen digest record deliverable
    and mark ALL OLDER unseen digests ``seen=True`` (they are stale snapshots
    superseded by the newest one). Nothing is DELETED (history preserved); a
    non-digest record and any already-seen digest are left untouched.

    "Newest" is the unseen digest with the greatest ``ts`` (ISO strings sort
    chronologically), tie-broken by append order (later wins).

    Idempotent: a second run over an already-collapsed store is a no-op (every
    recipient then has at most one unseen digest).

    Parameters
    ----------
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).

    Returns
    -------
    dict
        ``{"recipients_collapsed": int, "digests_marked_seen": int}`` — how
        many recipients had a backlog collapsed and how many stale digest
        records were flipped to seen in total.
    """
    # The digest discriminators live with the reminder engine that emits them;
    # import lazily to keep this maintenance module import-light and cycle-free.
    from ._reminders import DIGEST_CARD_ID, EVENT_DIGEST

    path = _inboxes_path(store)
    recipients_collapsed = 0
    digests_marked_seen = 0
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        changed = False
        for records in inboxes.values():
            unseen_digests = [
                (i, r)
                for i, r in enumerate(records)
                if not r.get("seen")
                and _is_digest(r, event_type=EVENT_DIGEST, card_id=DIGEST_CARD_ID)
            ]
            if len(unseen_digests) <= 1:
                continue
            newest_index = max(
                unseen_digests, key=lambda t: (t[1].get("ts") or "", t[0])
            )[0]
            collapsed_here = False
            for index, record in unseen_digests:
                if index == newest_index:
                    continue
                record["seen"] = True
                digests_marked_seen += 1
                collapsed_here = True
            if collapsed_here:
                recipients_collapsed += 1
                changed = True
        if changed:
            _save_inboxes_unlocked(inboxes, path)
    return {
        "recipients_collapsed": recipients_collapsed,
        "digests_marked_seen": digests_marked_seen,
    }


__all__ = ["collapse_digests"]

# EOF
