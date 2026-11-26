#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card LIFECYCLE verbs — the state transitions of an existing card.

Split out of ``_store`` (PURE MOVE — no behaviour change), which re-exports
every name below so ``from ._store import complete_task`` keeps working:

    complete_task   done + ``_log_meta.completed_{at,by}`` (idempotent).
    resolve_task    blocked → done, blocker cleared, audit comment.
    reopen_task     done → blocked/``operator-decision`` (the Resolve→Undo).
    reassign_task   atomic owner change (agent+assignee+scope in lock-step).
    delete_task     tombstone + scrub inbound refs (lives in ``_store_tombstone``,
                    re-exported here).
    restore_task    the Delete→Undo partner (same).

The shared helpers (``_read_write_doc`` / ``_utc_now_iso`` / ``_default_agent``
/ ``TaskNotFoundError``) stay in ``_store`` and are imported HERE inside the
function bodies — a deferred import, because ``_store`` imports this module at
module level to re-export its verbs and a top-level import back would cycle.
Same pattern the code already used for ``from . import _model``.
"""

from __future__ import annotations

from pathlib import Path

from ._comment_ids import stamp_comment_id
from ._model import _save_doc_unlocked, _store_lock
from ._store_arg import refuse_ineffective_store
from ._store_events import _emit_card_event, _emit_unblock_for_dependents
from ._store_list import _resolved_store
from ._touch import touch_last_activity

#: The ONLY status that means "this work was delivered". ``failed`` and
#: ``cancelled`` are terminal too, but they are NOT completions — a card can
#: stop without having shipped, and the throughput surfaces must not conflate
#: the two.
COMPLETED_STATUS = "done"

#: The ``_log_meta`` keys :func:`complete_task` stamps. They are the SOLE
#: input to the throughput/timeline aggregates (``_django/handlers/fleet/
#: timing.py``, ``_django/handlers/timeline.py``), which never consult
#: ``status`` — so leaving them on a non-``done`` card reports work that was
#: not delivered.
COMPLETION_STAMP_KEYS = ("completed_at", "completed_by")


def clear_completion_stamp(task: dict) -> bool:
    """Drop ``_log_meta.completed_{at,by}`` from ``task``. True if anything went.

    Call this from ANY transition that takes a card OUT of ``done``. The stamp
    is what the throughput surfaces believe; the status is what the sweeps
    believe. Move one without the other and the card becomes two different
    facts to two different readers — completed to the timeline, open to the
    digest — which is how 5 cards on the live board came to be counted as
    delivered work while still nagging their owners (2026-07-14).

    Keeping this as a named helper rather than two inline ``pop`` calls is the
    point: the next person to add an un-complete transition should find an
    obvious thing to call, not have to REMEMBER an invariant.
    """
    meta = task.get("_log_meta")
    if not isinstance(meta, dict):
        return False
    cleared = False
    for key in COMPLETION_STAMP_KEYS:
        if meta.pop(key, None) is not None:
            cleared = True
    if not meta:
        task.pop("_log_meta", None)
    return cleared


def complete_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    by: str | None = None,
    entry_points=None,  # hook-bypass: line-limit
) -> dict:
    """Mark ``task_id`` as ``done`` and stamp ``_log_meta.completed_{at,by}``.

    Idempotent per ``GITIGNORED/QUESTIONS.md`` #3: re-completing a
    ``done`` task is a no-op (timestamps stay frozen from the first
    completion). Pass ``by=`` to override the
    ``$SCITEX_CARDS_AGENT_ID`` → ``$USER`` → ``"unknown"`` precedence chain.

    Returns the (post-mutation) task mapping.

    Raises
    ------
    TaskNotFoundError
        If no task matches ``task_id``.
    """
    from . import _task  # hook-bypass: line-limit — verb-module split still queued
    from ._store import _default_agent, _read_write_doc, _task_not_found, _utc_now_iso

    if not task_id:
        raise TypeError("complete_task() requires a non-empty task_id")
    refuse_ineffective_store(store, verb="complete_task")
    resolved = _resolved_store(store)
    result: dict | None = None
    transitioned = False
    with _store_lock(resolved):
        doc, tasks = _read_write_doc(resolved)
        # `not _task._is_tombstoned(task)`: a tombstoned row is retained on
        # disk forever but must behave as ABSENT — completing a deleted
        # card would silently resurrect it.
        for task in tasks:
            if task.get("id") == task_id and not _task._is_tombstoned(task):
                if task.get("status") == "done":
                    # Idempotent: don't refresh the stamp, just return.
                    # No unblock emit — re-completing changed nothing.
                    return dict(task)
                task["status"] = "done"
                # CLEAR THE GATE WITH THE STATUS, or the document we are about
                # to save is INVALID and _validate_tasks refuses the whole save.
                #
                # A done card still naming an unresolved blocker is incoherent:
                # either the gate was cleared, or the card is not done. The
                # validator says exactly that, and `resolve_task` has always
                # cleared the blocker for this reason. `complete_task` never
                # learned it, so the two closing verbs disagreed and this one
                # produced a document that could not be written back.
                #
                # Measured on the live */15 reconcile cron, 2026-08-01:
                #   TaskValidationError: task 'ci-runner-gitconfig-lock-collision'
                #   has blocker 'operator-decision' but status is 'done'
                # That card was legitimately blocked on an operator decision and
                # its pull request merged anyway — real data, not corruption.
                # Because validation covers the WHOLE document, that one card
                # stopped the sweep from closing ANY card.
                task.pop("blocker", None)
                log_meta = task.get("_log_meta")
                if not isinstance(log_meta, dict):
                    log_meta = {}
                    task["_log_meta"] = log_meta
                now = _utc_now_iso()
                log_meta["completed_at"] = now
                log_meta["completed_by"] = _default_agent(by)
                # A COMPLETION IS AN ACT, and every reconciler orders acts by
                # `last_activity`. Stamping only `completed_at` made the newest
                # possible change to a card invisible to the field that decides
                # which copy wins — see `_touch` for the 3-host measurement.
                # Same `now`, so the completion stamp and the activity stamp
                # cannot straddle a tick and disagree about when this happened.
                touch_last_activity(task, now)
                _save_doc_unlocked(doc, resolved, tasks=tasks, touched_ids=[task_id])
                result = dict(task)
                transitioned = True
                break
    if result is None:
        raise _task_not_found(task_id)
    # Active-unblock DRIVE (ADR-0009) — OUTSIDE the file lock (the emit
    # re-loads the store + may comment on dependents, which take the
    # same lock). Only on a real non-done→done transition
    # (``pending`` was abolished 2026-07-10).
    if transitioned:
        _emit_unblock_for_dependents(resolved, task_id, by=by)
        # C5: a completion emits a canonical `completed` card-event (the
        # chosen mapping — complete_task → `completed`, NOT also a
        # `status_changed`, to avoid double-firing). Fail-soft, post-
        # persist, only on a real transition (idempotent re-complete
        # returned early above and emits nothing). Actor = resolved
        # completer. (hook-bypass: line-limit)
        _emit_card_event(
            "completed",
            task_id,
            actor=_default_agent(by),
            store=resolved,
            entry_points=entry_points,
        )
    return result


def resolve_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    actor: str | None = None,
    *,
    entry_points=None,  # hook-bypass: line-limit
) -> dict:
    """Flip a task from ``status=blocked`` (typically ``blocker=operator-
    decision``) to ``done`` and clear the blocker. Appends an audit
    comment naming the actor.

    Idempotent on already-resolved tasks (re-resolves are no-ops, just
    log a "noop" comment).
    """
    from . import _model, _task  # hook-bypass: line-limit
    from ._store import TaskNotFoundError, _default_agent, _read_write_doc, _utc_now_iso

    if not task_id:
        raise ValueError("resolve_task: 'task_id' is required")
    who = _default_agent(actor)
    refuse_ineffective_store(store, verb="resolve_task")
    tasks_path = _resolved_store(store)
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        target = _task._find_live_task(tasks, task_id)
        if target is None:
            raise TaskNotFoundError(f"resolve_task: unknown id {task_id!r}")
        was_done = target.get("status") == "done"
        prior_status = target.get("status")  # C5: capture for the event
        target["status"] = "done"
        target.pop("blocker", None)
        now = _utc_now_iso()
        # Stamp even on the already-done noop path: this verb ALWAYS appends a
        # comment, so it always changes the card, and a change that skips the
        # activity stamp is exactly the lie this invariant exists to prevent.
        touch_last_activity(target, now)
        comments = target.setdefault("comments", [])
        comments.append(
            stamp_comment_id(
                {
                    "author": who,
                    "ts": now,
                    "text": (
                        "[resolve (noop — already done)]"
                        if was_done
                        else "[RESOLVED via mcp.resolve_task] flipped status='blocked'->done, blocker cleared."  # noqa: E501  # hook-bypass: line-limit
                    ),
                }
            )
        )
        _model._save_doc_unlocked(doc, tasks_path, tasks=tasks, touched_ids=[task_id])
    # Active-unblock DRIVE (ADR-0009) — resolving a blocker card to done
    # can free its dependents too. Outside the lock; skip the noop
    # (already-done) path. Handler token-dedupe keeps it idempotent.
    if not was_done:
        _emit_unblock_for_dependents(tasks_path, task_id, by=who)
        # C5: a resolve is a status flip TO done. Per the project mapping
        # the resolve path emits `status_changed` {from,to:done} (the
        # `completed` event is reserved for complete_task / a done flip via
        # update_task). Fail-soft, post-persist, skip the noop path.
        # (hook-bypass: line-limit)
        _emit_card_event(
            "status_changed",
            task_id,
            actor=who,
            extra={"from": prior_status, "to": "done"},
            store=tasks_path,
            entry_points=entry_points,
        )
    return {"task_id": task_id, "actor": who, "task": dict(target)}


def reopen_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    by: str | None = None,
) -> dict:
    """Un-resolve a task — flip ``status=done`` back to ``blocked`` with
    ``blocker=operator-decision`` (the original LOUD halo state). Used
    by the board v3 Resolve→Undo loop.

    ALSO CLEARS ``_log_meta.completed_{at,by}``. Un-completing a card that
    keeps its completion stamp is not a reopen — it is a card that is open
    and completed at the same time, and the stamp is the half that gets
    believed: ``_django/handlers/fleet/timing.py`` and ``timeline.py``
    aggregate throughput *solely* on ``completed_at``, never on ``status``.
    So a stamped-but-open card is counted as delivered work forever, while
    simultaneously nagging its owner as backlog.

    (2026-07-14: found 5 such cards on the live board — one of them
    ``sac-keystone``, whose status had just been corrected from a mistaken
    ``done`` to ``cancelled``. The STATUS was fixed; the STAMP was not, so
    the false completion survived the correction. A lie outlives its
    retraction if it is written in two places and you only fix one.)
    """
    from . import _model, _task  # hook-bypass: line-limit
    from ._store import TaskNotFoundError, _default_agent, _read_write_doc, _utc_now_iso

    if not task_id:
        raise ValueError("reopen_task: 'task_id' is required")
    who = _default_agent(by)
    refuse_ineffective_store(store, verb="reopen_task")
    tasks_path = _resolved_store(store)
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        target = _task._find_live_task(tasks, task_id)
        if target is None:
            raise TaskNotFoundError(f"reopen_task: unknown id {task_id!r}")
        target["status"] = "blocked"
        target["blocker"] = "operator-decision"
        cleared = clear_completion_stamp(target)
        now = _utc_now_iso()
        # An UN-completion is as much an act as a completion. Without this the
        # reopen is invisible to timestamp comparison and a reconciler happily
        # re-applies the done copy from the other host, undoing the reopen.
        touch_last_activity(target, now)
        comments = target.setdefault("comments", [])
        text = (
            "[REOPENED via mcp.reopen_task] flipped status='done'->blocked, "
            "blocker=operator-decision restored."
        )
        if cleared:
            text += " Cleared _log_meta.completed_{at,by} — the card is no longer completed."  # noqa: E501  # hook-bypass: line-limit
        comments.append(stamp_comment_id({"author": who, "ts": now, "text": text}))
        _model._save_doc_unlocked(doc, tasks_path, tasks=tasks, touched_ids=[task_id])
    return {"task_id": task_id, "by": who, "task": dict(target)}


# reassign_task now lives in _store_reassign, beside the bulk reassign_all:
# ownership is one responsibility and was split across two modules, with the
# module named for it holding only half. Re-exported here so every existing
# import path (notably _store) keeps resolving unchanged.
from ._store_reassign import reassign_task  # noqa: E402,F401

# delete_task / restore_task live in _store_tombstone (the Delete→Undo pair);
# re-exported so every existing import path keeps resolving unchanged.
from ._store_tombstone import delete_task, restore_task  # noqa: E402,F401

__all__ = [
    "complete_task",
    "delete_task",
    "reassign_task",
    "reopen_task",
    "resolve_task",
    "restore_task",
]

# EOF
