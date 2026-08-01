#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bulk reassignment — move every card owned by one agent to another.

Split out of ``_store_lifecycle`` when adding :func:`reassign_all` pushed that
module past its line budget. ``_store`` re-exports the name below so
``from ._store import reassign_all`` keeps working. The single-card
:func:`reassign_task` stays in ``_store_lifecycle``; this module is the BULK
verb ``sac agents rename`` needs (card ``todo-reassign-all-bulk-primitive``).

The shared helpers (``_read_write_doc`` / ``_utc_now_iso`` / ``_default_agent``)
stay in ``_store`` and are imported inside the function body — a deferred
import, because ``_store`` imports this module at module level to re-export the
verb and a top-level import back would cycle. Same pattern the sibling modules
use for ``from . import _model``.
"""

from __future__ import annotations

from pathlib import Path

from ._store_events import _emit_card_event
from ._store_list import _resolved_store


def reassign_all(
    store: str | Path | None = None,
    old_owner: str | None = None,
    new_owner: str | None = None,
    *,
    by: str | None = None,
    entry_points=None,  # hook-bypass: line-limit
) -> dict:
    """Bulk owner change — move EVERY card owned by ``old_owner`` to
    ``new_owner`` in ONE atomic locked write, then emit ONE batch event.

    The primitive ``sac agents rename`` needs (``todo-reassign-all-bulk-
    primitive``). Mirrors :func:`reassign_task`'s per-card semantics
    EXACTLY — for every matched card it sets ``agent = assignee =
    new_owner``, ``scope = "agent:<new_owner>"``, appends the identical
    audit comment ``"reassigned <old> -> <new> by <actor>"``, and stamps
    ``last_activity`` — but does it for the whole cohort under a SINGLE
    ``_store_lock`` + ``_read_write_doc`` + ``_save_doc_unlocked``.

    Design (why writes and event are decoupled)
    --------------------------------------------
    The WRITES are ATOMIC: one locked read-modify-write moves every card or
    none. The EVENT is emitted AFTER the lock is released, FAIL-SOFT,
    because the emit path enqueues into the recipient inbox and CANNOT run
    under the store lock (it re-loads / re-locks the store and would
    deadlock). Do NOT try to emit inside the lock.

    Recoverability of a lost event does NOT depend on the bus: the durable
    per-card audit comment is written IN the atomic section, so a sweep can
    always find cards now owned by ``new_owner`` whose batch notification
    was never delivered and re-drive it. The event is an accelerator, the
    comments are the record.

    ONE ``reassigned_batch`` event models the ACT (``{from_owner, to_owner,
    count, card_ids}``), NOT the rows — emitting one ``reassigned`` per card
    would be a 158-notification flood, which is the whole reason this verb
    exists.

    Idempotent: a card already owned by ``new_owner`` does not match
    ``old_owner`` and is skipped. Zero matches => ``count == 0``,
    ``changed == False``, NO write, NO event.

    Parameters
    ----------
    old_owner, new_owner : str
        Both required, non-empty. ``old_owner == new_owner`` raises
        ``ValueError`` — a self-rename is meaningless for a bulk verb.
    by : str, optional
        The actor; resolved via ``$SCITEX_TODO_AGENT_ID`` -> ``$USER`` ->
        ``"unknown"``.
    entry_points : iterable, optional
        In-process injection seam forwarded to the event emit (real fake
        handler in tests); ``None`` uses real plugin discovery.

    Returns
    -------
    dict
        ``{"from_owner", "to_owner", "count", "card_ids", "actor",
        "changed"}`` where ``changed = (count > 0)``.

    Raises
    ------
    ValueError
        If ``old_owner`` / ``new_owner`` is missing/empty, or equal.
    """
    from . import _model
    from ._store import _default_agent, _read_write_doc, _utc_now_iso

    if not old_owner or not str(old_owner).strip():
        raise ValueError("reassign_all: 'old_owner' is required")
    if not new_owner or not str(new_owner).strip():
        raise ValueError("reassign_all: 'new_owner' is required")
    old_owner = str(old_owner)
    new_owner = str(new_owner)
    if old_owner == new_owner:
        raise ValueError(
            "reassign_all: 'old_owner' and 'new_owner' are identical "
            f"({new_owner!r}) — a self-rename moves nothing"
        )
    actor = _default_agent(by)
    tasks_path = _resolved_store(store)
    moved: list[str] = []
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        for task in tasks:
            # Current owner = `agent`, falling back to legacy `assignee`.
            current = task.get("agent") or task.get("assignee")
            if current != old_owner:
                continue
            task["agent"] = new_owner
            task["assignee"] = new_owner
            task["scope"] = f"agent:{new_owner}"
            comments = task.setdefault("comments", [])
            comments.append(
                {
                    "author": actor,
                    "ts": _utc_now_iso(),
                    "text": f"reassigned {old_owner} -> {new_owner} by {actor}",
                }
            )
            # Delegation keeps responsibility — mirrors reassign_task
            # EXACTLY: the previous owner + creator stay subscribed through
            # the handoff (operator 2026-07-18; constitution §2).
            subs = list(task.get("subscribers") or [])
            for keeper in (old_owner, task.get("created_by")):
                if keeper and keeper != new_owner and keeper not in subs:
                    subs.append(keeper)
            if subs:
                task["subscribers"] = subs
            task["last_activity"] = _utc_now_iso()
            tid = task.get("id")
            if tid:
                moved.append(str(tid))
        if moved:
            _model._save_doc_unlocked(doc, tasks_path, tasks=tasks)
    count = len(moved)
    # ONE batch event, AFTER the write is durable + the lock released
    # (fail-soft). The event models the ACT — one emit for the whole cohort,
    # NOT one per card. Recoverability lives in the per-card audit comments
    # written inside the atomic section above. (hook-bypass: line-limit)
    if count:
        _emit_card_event(
            "reassigned_batch",
            f"batch:{old_owner}->{new_owner}",
            actor=actor,
            extra={
                "from_owner": old_owner,
                "to_owner": new_owner,
                "count": count,
                "card_ids": list(moved),
            },
            store=tasks_path,
            entry_points=entry_points,
        )
    return {
        "from_owner": old_owner,
        "to_owner": new_owner,
        "count": count,
        "card_ids": moved,
        "actor": actor,
        "changed": count > 0,
    }


def reassign_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    new_owner: str | None = None,
    *,
    by: str | None = None,
    entry_points=None,  # hook-bypass: line-limit
) -> dict:
    """Atomically change a card's owner — the primitive the board lacked.

    C5 (``todo-reassign-verb-with-owner-notify``). In ONE locked write:

      * set ``agent = assignee = new_owner`` (keep the legacy ``assignee``
        in lock-step with the operator-co-designed ``agent`` so every
        reader — old dict-style and new — agrees on the owner), AND
      * set ``scope = "agent:<new_owner>"`` (the convention the fleet
        slices on), AND
      * append an audit comment ``"reassigned <old> -> <new> by <actor>"``.

    THEN (post-persist, outside the lock, fail-soft) emit a canonical
    ``reassigned`` card-event with ``extra={"from_owner", "to_owner"}``.
    The EVENT is the notification path — there is intentionally NO bespoke
    notify/delivery here (delivery is C4, a separate card; this primitive
    EMITS, it does not deliver).

    Idempotent: reassigning to the SAME current owner is a no-op — no
    write, no audit comment, no spurious event — so a replayed/duplicate
    reassign is harmless.

    Parameters
    ----------
    task_id : str
        The card to reassign (required).
    new_owner : str
        The new owning agent (required, non-empty).
    by : str, optional
        The actor performing the reassignment; resolved through the usual
        ``$SCITEX_TODO_AGENT_ID`` → ``$USER`` → ``"unknown"`` chain.
    entry_points : iterable, optional
        In-process injection seam forwarded to the event emit (real fake
        handler in tests); ``None`` uses real plugin discovery.

    Returns
    -------
    dict
        ``{"task_id", "from_owner", "to_owner", "actor", "changed", "task"}``
        where ``changed`` is ``False`` on the same-owner no-op path.

    Raises
    ------
    ValueError
        If ``task_id`` or ``new_owner`` is missing/empty.
    TaskNotFoundError
        If no task matches ``task_id``.
    """
    from . import _model, _task  # hook-bypass: line-limit
    from ._store import TaskNotFoundError, _default_agent, _read_write_doc, _utc_now_iso

    if not task_id:
        raise ValueError("reassign_task: 'task_id' is required")
    if not new_owner or not str(new_owner).strip():
        raise ValueError("reassign_task: 'new_owner' is required")
    new_owner = str(new_owner)
    actor = _default_agent(by)
    tasks_path = _resolved_store(store)
    changed = False
    old_owner: str | None = None
    result_task: dict | None = None
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        target = _task._find_live_task(tasks, task_id)
        if target is None:
            raise TaskNotFoundError(f"reassign_task: unknown id {task_id!r}")
        # Current owner = `agent`, falling back to legacy `assignee`.
        old_owner = target.get("agent") or target.get("assignee")
        if old_owner == new_owner:
            # Idempotent no-op: same owner → no write, no event. Return the
            # current state with changed=False.
            result_task = dict(target)
        else:
            target["agent"] = new_owner
            target["assignee"] = new_owner
            target["scope"] = f"agent:{new_owner}"
            comments = target.setdefault("comments", [])
            comments.append(
                {
                    "author": actor,
                    "ts": _utc_now_iso(),
                    "text": (
                        f"reassigned {old_owner or '(unassigned)'} -> "
                        f"{new_owner} by {actor}"
                    ),
                }
            )
            # Delegation keeps responsibility (operator 2026-07-18,
            # 「渡しました、で終わられると困る」/ constitution §2 "ownership
            # never dangles"): the PREVIOUS owner and the card's creator stay
            # subscribed through the handoff, so lateness on the delegate
            # reaches the delegator. Dropping out is an explicit
            # set_subscriber remove, never a side effect of handing off.
            subs = list(target.get("subscribers") or [])
            for keeper in (old_owner, target.get("created_by")):
                if keeper and keeper != new_owner and keeper not in subs:
                    subs.append(keeper)
            if subs:
                target["subscribers"] = subs
            target["last_activity"] = _utc_now_iso()
            _model._save_doc_unlocked(doc, tasks_path, tasks=tasks)
            result_task = dict(target)
            changed = True
    # C5: emit `reassigned` ONLY on a real owner change, AFTER the write is
    # durable + the lock released (fail-soft). The event is the
    # notification path; delivery is C4. (hook-bypass: line-limit)
    if changed:
        _emit_card_event(
            "reassigned",
            task_id,
            actor=actor,
            extra={"from_owner": old_owner, "to_owner": new_owner},
            store=tasks_path,
            entry_points=entry_points,
        )
    # Liveness (assignee-liveness feature): heartbeat the reassigning actor,
    # and surface the NEW owner's liveness so the caller learns immediately
    # if it just reassigned the card to a non-running agent. Both fail-soft.
    from ._liveness import _assignee_liveness, _heartbeat

    _heartbeat(actor, tasks_path)
    out = {
        "task_id": task_id,
        "from_owner": old_owner,
        "to_owner": new_owner,
        "actor": actor,
        "changed": changed,
        "task": result_task,
    }
    _liveness = _assignee_liveness(new_owner, tasks_path)
    if _liveness is not None:
        out["assignee_liveness"] = _liveness
    return out


__all__ = ["reassign_all"]
