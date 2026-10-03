#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``upsert_task`` — make a card EXIST in a stated state, whatever state it is in.

WHY THIS VERB EXISTS. An alerter that owns a fixed card id per host had only two
verbs, and both were wrong:

    add_task      refuses a duplicate id
    comment_task  succeeds, and MUTES

So every caller wanting idempotent alerting hand-rolled ``add || comment``, and
that fallback is the mute: once the card is closed, ``add`` fails and the outage
notice lands as a COMMENT ON A DONE CARD, which appears in no queue. Measured
2026-09-07 on scitex-compute-03 — a replica sat down ~8 h and the alert fired
correctly into a place nothing reads. The alerter's own source carries the
warning it could not act on::

    "could not reopen ... this alert is only a comment on a done card and
     NOBODY IS PAGED"

The semantics needed are "make this card exist AND be in a state a human will
see". They already existed in this package — :func:`scitex_cards.help_wait`
reads, and if the card is present IN ANY STATE including ``done`` it force-sets
status and blocker, under one store lock. They were simply not reachable as a
verb an external caller could call.

THE TARGET STATUS IS A PARAMETER, AND THAT IS THE WHOLE DESIGN POINT.
``help_wait`` hardcodes ``blocked`` / ``operator-decision``, which is right for
a help card and WRONG for an alert: ``scitex-cards runnable`` surfaces only
``in_progress`` and ``deferred``, so reopening a store-down alert to ``blocked``
would move it from one queue that does not show it to another queue that does
not show it — the mute would survive the fix and look repaired. Copying
``help_wait`` verbatim is therefore the partial-copy failure this fleet has hit
before, where a working pattern's shape is reproduced without the one constant
that gave it meaning.

Existing rows use the authoritative single-row delivery guard and SQL revision
compare-and-set. Creation retains the strict owner gate and refuses delivery
namespace injection.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ._delivery_policy import DeliveryPolicyError, assert_namespace_preserved
from ._model import _save_doc_unlocked, _store_lock
from ._store_arg import refuse_ineffective_store
from ._store_delivery import DeliveryGuardError, mutate_card, prepare_new_card
from ._store_events import _emit_card_event
from ._store_lifecycle import clear_completion_stamp
from ._store_list import _resolved_store
from ._store_single_card import read_card_or_raise
from ._store_target import resolve_store_target
from ._touch import touch_last_activity

__all__ = ["upsert_task"]

#: Fields an upsert refreshes on an EXISTING card. Deliberately short: an
#: upsert is not a general editor, and a caller wanting to change something
#: else should use ``update_task`` and say so. ``id`` is the key and is never
#: refreshed; ``created_at`` is never touched, so a reopened card keeps its
#: original birth stamp and its age stays honest.
REFRESHABLE = ("title", "note", "host", "assignee", "agent", "scope", "priority")


def upsert_task(
    store: str | Path | None = None,
    *,
    id: str,
    title: str,
    status: str,
    **fields,
) -> dict:
    """Create the card, or reopen and refresh it if it already exists.

    Idempotent by id: exactly one card per ``id``, ever. A re-run on a card in
    ANY state — including ``done`` and ``cancelled`` — sets ``status`` to what
    the caller asked for and refreshes ``last_activity``, so a recurring
    condition re-enters the queue instead of accumulating comments on a closed
    card.

    Existing rows are read and written with a revision-bound compare-and-set.
    A concurrent mutation is reported as a conflict without a stale retry.

    Parameters
    ----------
    store
        The store, as every write verb takes it.
    id
        The fixed card id the caller owns. This is the whole point: an alerter
        picks one id per host so an outage refreshes ONE card rather than
        minting one per tick.
    title, status
        Required. ``status`` is what the card must be in AFTERWARDS — pass a
        status your queue actually surfaces (``in_progress`` or ``deferred``
        for something a human must pick up; ``blocked`` only when it genuinely
        waits on someone).
    **fields
        Any other card field. On create they are written; on update only the
        keys in :data:`REFRESHABLE` are refreshed, so an upsert cannot quietly
        rewrite history it was not asked about.

    Returns
    -------
    dict
        The card as it now stands, plus ``_upsert_action`` — ``"created"`` or
        ``"reopened"``. The caller needs to know which happened: an alerter
        that reopened a closed card is reporting a RECURRENCE, which is
        different news from a first occurrence.
    """
    if not (isinstance(id, str) and id.strip()):
        raise ValueError("upsert_task: 'id' is required and must be non-empty")
    if not (isinstance(title, str) and title.strip()):
        raise ValueError("upsert_task: 'title' is required and must be non-empty")
    if not (isinstance(status, str) and status.strip()):
        raise ValueError(
            "upsert_task: 'status' is required — an upsert must say what state "
            "the card should be in afterwards. There is no safe default: the "
            "queue only surfaces some statuses, so a guessed one can mute the "
            "very card this verb exists to make visible."
        )

    from . import _task
    from ._store import _read_write_doc, _utc_now_iso

    card_id = id.strip()
    refuse_ineffective_store(store, verb="upsert_task")
    resolved = _resolved_store(store)
    action = "created"
    existing, _revision = read_card_or_raise(resolve_store_target(None), card_id)

    def refuse_deleted(task: dict) -> None:
        if _task._is_tombstoned(task):
            raise ValueError(
                f"upsert_task: card {card_id!r} was DELETED (tombstoned). "
                "Refusing to resurrect it. Use restore_task if the "
                "deletion was wrong, or choose a different id."
            )

    def refresh(task: dict) -> None:
        nonlocal action
        refuse_deleted(task)
        # Upsert's short refresh list must not silently accept an attempt to
        # inject or erase evidence through an otherwise ignored metadata field.
        if "_log_meta" in fields:
            requested = deepcopy(task)
            if fields["_log_meta"] is None:
                requested.pop("_log_meta", None)
            else:
                requested["_log_meta"] = deepcopy(fields["_log_meta"])
            try:
                assert_namespace_preserved(task, requested)
            except DeliveryPolicyError as exc:
                raise DeliveryGuardError(str(exc)) from exc
        prior_status = task.get("status")
        action = "reopened" if prior_status != status else "refreshed"
        task["status"] = status
        for key in REFRESHABLE:
            if key == "title":
                task["title"] = title
            elif key in fields and fields[key] is not None:
                task[key] = fields[key]
        if status != "blocked":
            task.pop("blocker", None)
        elif fields.get("blocker"):
            task["blocker"] = fields["blocker"]
        if prior_status == "done" and status != "done":
            clear_completion_stamp(task)
        touch_last_activity(task, _utc_now_iso())

    if existing is not None:
        refuse_deleted(existing)
        result = mutate_card(store, card_id, refresh, operation="upsert")
    else:
        owner = fields.get("assignee") or fields.get("agent")
        if not (isinstance(owner, str) and owner.strip()):
            raise _task.TaskValidationError(
                "upsert_task: assignee is required when the card does not "
                "exist yet — pass assignee=<user> (or agent=<user>). "
                "creator+assignee are mandatory and an owner-less card is "
                "rejected; this verb creates, so it enforces the same gate "
                "as add_task rather than routing around it."
            )
        now = _utc_now_iso()
        new = {"id": card_id, "title": title, "status": status}
        for key, value in fields.items():
            if value is not None:
                new[key] = value
        new = prepare_new_card(new, operation="upsert")
        new.setdefault("_log_meta", {})["created_at"] = now
        touch_last_activity(new, now)
        with _store_lock(resolved):
            doc, tasks = _read_write_doc(resolved)
            if any(t.get("id") == card_id for t in tasks):
                # No stale retry: a concurrent create is a conflict, not
                # permission to refresh a row we did not read authoritatively.
                from ._store_errors import RevisionConflictError

                raise RevisionConflictError(card_id, -1, None)
            tasks.append(new)
            _save_doc_unlocked(doc, resolved, tasks=tasks, touched_ids=[card_id])
        result = dict(new)

    # Outside the lock: the emit re-loads the store and may comment on other
    # cards, which take the same lock.
    try:
        _emit_card_event(
            resolved, card_id, "status_changed" if action != "created" else "created"
        )
    except Exception:  # noqa: BLE001 — an event must not fail the write
        pass

    result["_upsert_action"] = action
    return result


# EOF
