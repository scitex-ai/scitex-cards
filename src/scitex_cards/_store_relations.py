#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card RELATIONS — the edges and the people, not the card's own state.

Split out of ``_store`` (PURE MOVE — no behaviour change), which re-exports
every name below so ``from ._store import set_edge`` keeps working:

    set_edge          add / remove a ``depends_on`` / ``blocks`` edge.
    set_collaborator  add / remove a collaborator (ADR-0009 roles).
    set_subscriber    add / remove a subscriber — the notify list.
    _set_list_member  the shared idempotent add/remove on a str-list field.

The shared helpers (``_read_write_doc`` / ``_utc_now_iso`` /
``TaskNotFoundError``) stay in ``_store`` and are imported inside the function
bodies — deferred, because ``_store`` imports this module at module level to
re-export its verbs and a top-level import back would cycle. Same pattern the
code already used for ``from . import _model``.
"""

from __future__ import annotations

from pathlib import Path

from ._model import _save_doc_unlocked, _store_lock
from ._store_list import _resolved_store


def set_edge(
    store: str | Path | None = None,
    action: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    target: str | None = None,
) -> dict:
    """Add or remove a depends_on / blocks edge — and SUBSCRIBE THE WAITER.

    ``action`` in {"add", "remove"}. ``kind`` in {"depends_on", "blocks"}.
    Mutates ``tasks[source][kind]`` (adding/removing ``target``).

    *** ADDING AN EDGE SUBSCRIBES THE WAITING CARD'S OWNER TO THE CARD THEY ARE
    WAITING ON. Until 2026-07-13 it did not, and that was a SILENT NO-OP. ***

    Measured by scitex-writer, with a controlled experiment:

        depends_on edge + set_subscriber  ->  notification FIRES
        depends_on edge ALONE             ->  NOTHING. Total silence.

    The entire reason to record "A depends_on B" is so that FINISHING B TELLS A.
    An agent who wants to hear when their blocker clears reaches for
    ``depends_on`` — it is the semantically obvious call and it is literally
    named for the relationship — and got silence. And SILENCE IS
    INDISTINGUISHABLE FROM "the gate has not cleared yet", so nobody ever finds
    out. A silent no-op wearing the costume of a working mechanism is strictly
    WORSE than no mechanism at all: with no mechanism, you go and check.

    Not hypothetical. FOUR cards on the live board sat blocked on gates that had
    ALREADY CLEARED — including a mutual deadlock between two agents, each
    recorded as waiting on the other, built out of two stale sentences, neither
    ever told.

    THE RULE, stated once and applied to both kinds — THE OWNER OF THE WAITING
    CARD IS SUBSCRIBED TO THE CARD THEY WAIT ON:

        A depends_on B   — A waits on B  =>  subscribe A's owner to B
        A blocks B       — B waits on A  =>  subscribe B's owner to A

    ``blocks`` is the same relationship pointing the other way; leaving it silent
    would just move the landmine one call to the left.

    REMOVING an edge does NOT unsubscribe. The owner may have subscribed for
    their own reasons, and silently dropping that subscription would re-create
    this very bug from the other side. An extra notification is a nuisance; a
    missing one strands a card for weeks. Unsubscribe explicitly with
    :func:`set_subscriber` when you mean it.

    Returns ``subscribed``: WHO will now be told when the awaited card completes,
    or ``None`` when the edge was removed or the waiting card has no owner. The
    caller can SEE that delivery is wired instead of assuming it — which is the
    whole complaint this fixes.

    CAVEAT WORTH KNOWING WHEN YOU TEST THIS: a self-completion does not notify,
    because ``actor == subscriber`` is suppressed. Anyone who exercises the
    mechanism on their OWN card sees nothing and concludes it is broken. That
    suppression is correct — it just needs saying.
    """
    from . import _model, _task
    from ._store import TaskNotFoundError, _read_write_doc

    if action not in ("add", "remove"):
        raise ValueError("set_edge: action must be 'add' or 'remove'")
    if kind not in ("depends_on", "blocks"):
        raise ValueError("set_edge: kind must be 'depends_on' or 'blocks'")
    if not source or not target:
        raise ValueError("set_edge: 'source' and 'target' are required")
    if source == target:
        raise ValueError("set_edge: self-edge is forbidden")
    tasks_path = _resolved_store(store)
    subscribed: str | None = None
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        # A tombstoned row (`_task._is_tombstoned`) is retained forever but
        # must behave as ABSENT — edging onto a deleted card would silently
        # resurrect it (2026-07-21 tombstone change).
        src_task = _task._find_live_task(tasks, source)
        tgt_task = _task._find_live_task(tasks, target)
        if src_task is None:
            raise TaskNotFoundError(f"set_edge: unknown source id {source!r}")
        if tgt_task is None:
            raise TaskNotFoundError(f"set_edge: unknown target id {target!r}")
        edges = src_task.get(kind) or []
        if action == "add" and target not in edges:
            edges = list(edges) + [target]
        elif action == "remove":
            edges = [e for e in edges if e != target]
        if edges:
            src_task[kind] = edges
        else:
            src_task.pop(kind, None)

        if action == "add":
            # WHO waits, and WHO is waited on? `depends_on` points from the waiter
            # to the gate; `blocks` points the other way. Resolve the direction
            # here so the delivery rule stays one sentence rather than two.
            waiter, awaited = (
                (src_task, tgt_task) if kind == "depends_on" else (tgt_task, src_task)
            )
            owner = (waiter.get("agent") or waiter.get("assignee") or "").strip()
            if owner:
                subs = list(awaited.get("subscribers") or [])
                if owner not in subs:
                    subs.append(owner)
                    awaited["subscribers"] = subs
                    subscribed = owner
            # An OWNERLESS waiter cannot be subscribed to anything — there is nobody
            # to tell. We do NOT invent a recipient; `subscribed: None` says so
            # plainly rather than letting the caller assume delivery is wired.

        _model._save_doc_unlocked(doc, tasks_path, tasks=tasks)
    return {
        "action": action,
        "kind": kind,
        "source": source,
        "target": target,
        "subscribed": subscribed,
    }


def _set_list_member(
    tasks_path: Path,
    task_id: str,
    field: str,
    who: str,
    action: str,
) -> dict:
    """Idempotent add / remove of ``who`` in ``task[field]`` (a str list).

    Adds only if absent; removes every occurrence. Drops the key when the
    list becomes empty (same convention as :func:`set_edge` on edges, so
    the YAML stays sparse). Stamps ``last_activity``. Returns the task.
    """
    from . import _task
    from ._store import TaskNotFoundError, _read_write_doc, _utc_now_iso

    with _store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        for task in tasks:
            # See `_task._is_tombstoned`: a deleted card's row is retained
            # forever but must behave as ABSENT here.
            if task.get("id") == task_id and not _task._is_tombstoned(task):
                members = [m for m in (task.get(field) or []) if m != who]
                if action == "add":
                    members.append(who)
                if members:
                    task[field] = members
                else:
                    task.pop(field, None)
                task["last_activity"] = _utc_now_iso()
                _save_doc_unlocked(doc, tasks_path, tasks=tasks)
                return dict(task)
    raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")


def set_collaborator(
    store: str | Path | None = None,
    *,
    task_id: str | None = None,
    who: str | None = None,
    action: str = "add",
) -> dict:
    """Add or remove ``who`` on a card's ``collaborators`` (ADR-0009).

    ``action`` in {"add", "remove"}. Adding a collaborator ALSO subscribes
    them (the ADR default — subscribers ⊇ collaborators), so they get
    feedback by default. Removing a collaborator leaves their subscription
    intact; call :func:`set_subscriber` with ``action="remove"`` to also
    stop their notices. Returns the (post-mutation) task mapping.
    """
    if not task_id or not who:
        raise ValueError("set_collaborator: 'task_id' and 'who' are required")
    if action not in ("add", "remove"):
        raise ValueError("set_collaborator: action must be 'add' or 'remove'")
    tasks_path = _resolved_store(store)
    task = _set_list_member(tasks_path, task_id, "collaborators", who, action)
    if action == "add":
        task = _set_list_member(tasks_path, task_id, "subscribers", who, "add")
    return task


def set_subscriber(
    store: str | Path | None = None,
    *,
    task_id: str | None = None,
    who: str | None = None,
    action: str = "add",
) -> dict:
    """Add or remove ``who`` on a card's ``subscribers`` — the notify list
    (ADR-0009).

    ``action`` in {"add", "remove"}. Anyone may unsubscribe — even a
    collaborator (the ADR's "always unsubscribable" rule): a ``remove``
    here drops them from the notify list without touching collaborators.
    Returns the (post-mutation) task mapping.
    """
    if not task_id or not who:
        raise ValueError("set_subscriber: 'task_id' and 'who' are required")
    if action not in ("add", "remove"):
        raise ValueError("set_subscriber: action must be 'add' or 'remove'")
    tasks_path = _resolved_store(store)
    return _set_list_member(tasks_path, task_id, "subscribers", who, action)


__all__ = [
    "_set_list_member",
    "set_collaborator",
    "set_edge",
    "set_subscriber",
]

# EOF
