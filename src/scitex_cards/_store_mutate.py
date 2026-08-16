#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The insert / update half of the store's write surface.

Split out of ``_store`` (PURE MOVE — no behaviour change), which re-exports
every name below so ``from ._store import add_task`` keeps working:

    add_task            Append a new task (owner + creator FAIL-LOUD, WIP gate).
    update_task         Mutate fields of an existing task by id.
    _stamp_deferred_at  Stamp the backlog age clock on ENTRY into `deferred`.
    _stamp_blocked_at   Stamp the blocked-check clock when the (status, blocker)
                        PAIR moves — never on a passing comment.
    _wip_statuses       Back-compat re-export of ``_throughput.WIP_STATUSES``.

Named ``_store_mutate`` rather than ``_store_write`` because ``_store_write``
is ALREADY the low-level persistence layer (``_store_lock`` / ``save_tasks`` /
``_save_doc_unlocked``) that this module writes THROUGH.

The shared helpers (``_read_write_doc`` / ``_utc_now_iso`` /
``_resolve_creator_or_raise`` / ``ENV_AGENT`` / ``TaskNotFoundError``) stay in
``_store`` and are imported HERE inside the function bodies — a deferred
import, because ``_store`` imports this module at module level to re-export
its verbs and a top-level import back would cycle. Same pattern the code
already used for ``from . import _model``.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._model import (
    TaskValidationError,
    _save_doc_unlocked,
    _store_lock,
)
from ._store_add import add_task  # noqa: F401 -- re-export, see module docstring
from ._store_clocks import (
    _clear_completion_stamp_on_leaving_done,
    _stamp_blocked_at,
    _stamp_deferred_at,
)
from ._store_enums import resolve_enum_clears as _resolve_enum_clears
from ._store_events import _emit_card_event, _emit_unblock_for_dependents
from ._store_list import _resolved_store


def _wip_statuses() -> frozenset[str]:
    """Re-export from ``_throughput`` so the gate's predicate stays a single
    source of truth. WIP is work in flight — ``in_progress`` — not backlog.

    The add path no longer calls this (``_store_wip.enforce_wip_gate`` reads
    ``WIP_STATUSES`` straight from ``_throughput``); kept for out-of-tree
    importers. (hook-bypass: line-limit)
    """
    from ._throughput import WIP_STATUSES

    return WIP_STATUSES


#: Kwargs that are CONTROL PARAMETERS somewhere in this stack but are not
#: parameters of :func:`update_task` -- so ``**fields`` would swallow them
#: and write them onto the card as DATA, silently, returning success.
#:
#: ``expected_revision`` USED TO BE LISTED HERE and is now a real parameter of
#: :func:`update_task`. PR #790 refused it because this function was a
#: whole-document read-modify-write, so a per-row guard "would assert the lock on
#: the caller's card while overwriting every other card from the same read".
#: THAT PREMISE EXPIRED with #872: update_task declares ``touched_ids=[task_id]``
#: and ``_db_mirror`` intersects the write set with it, so the write already
#: reaches exactly one row. The refusal outlived its reason by six days because
#: it stated a CONCLUSION rather than the CONDITION it depended on -- had it read
#: "refused while update_task is whole-document RMW" it would have expired
#: visibly. The read is still whole-document; that is a scale property now, not a
#: correctness one.
#:
#: ``tasks_path`` is the same concept as this function's ``store`` parameter
#: under the name the backend/MCP layers use for it. It is NOT hypothetical:
#: card ``probe-with-assignee`` has carried ``tasks_path='/tmp/seedprobe.yaml'``
#: as a data field since 2026-07-10, measured across all 4,488 live cards.
_CONTROL_KWARGS: dict[str, str] = {
    "tasks_path": (
        "did you mean the `store` parameter? `tasks_path` is the backend/MCP "
        "name for the same thing and is not a card field -- passing it here "
        "would write it onto the card as data"
    ),
}


def update_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    entry_points=None,  # hook-bypass: line-limit
    expected_revision: int | None = None,
    **fields,
) -> dict:
    """Update fields of the task with id ``task_id``; return the merged dict.

    Any keyword argument becomes a field on the task. Passing ``None`` for
    a field DELETES it (matches the operator's mental model: "clear the
    scope" = `update_task(..., scope=None)`). To leave a field untouched,
    just omit it.

    ``expected_revision`` makes the write a COMPARE-AND-SET: pass the ``revision``
    you read and it lands only if nobody has written since. On a mismatch NOTHING
    is written and :class:`RevisionConflictError` is raised, so a caller re-reads
    and re-applies rather than clobbers.

    IT IS OPT-IN, and that is load-bearing. ``_migrate_v6_to_v7`` records that
    REJECT-by-default was RULED UNUSABLE -- "an UPDATE from a writer that knows
    nothing about ``revision`` would ABORT, so fleet writes would fail until every
    container is current", which this fleet cannot establish. With ``None`` no
    guard is emitted and the write is byte-identical to before.

    It RAISES here while the bulk path REPORTS, and the predicate is the opt-in
    rather than the layer: passing a revision IS an assertion, and a violated
    explicit assertion that returns quietly is an invisible lost update.

    The ONE exception is :data:`_CONTROL_KWARGS` -- names that are control
    parameters elsewhere in this stack. Those are REFUSED with a message
    naming the real path, because silently storing a requested guard as a
    data field is worse than not offering it: the caller is then wrong about
    whether they are protected.

    ONE clear rule, closed enums included: an empty string ``""`` on a
    CLOSED-ENUM field (``blocker`` / ``kind``) also DELETES the key — it is
    a delete instruction, consumed here, never written as a value. This is
    what the MCP/CLI surfaces have always promised ("pass '' to CLEAR");
    previously ``""`` was written literally and the validator rejected the
    save, so the documented way to clear a blocker was the one way that
    could not work. The validator is NOT weakened: a genuinely invalid
    value (``blocker="banana"``) still raises.

    ``status`` is the exception and CANNOT be cleared — every card must
    carry a decision. ``status=""`` raises with the reason and the valid
    set rather than silently dropping the request. See `_store_enums`.

    Raises
    ------
    TaskNotFoundError
        If no task matches ``task_id``.
    TaskValidationError
        If the resulting mutation is structurally invalid, or if ``status``
        was passed the ``""`` clear-sentinel (status cannot be cleared).
    """
    from . import _task
    from ._store import ENV_AGENT, TaskNotFoundError, _read_write_doc, _utc_now_iso

    if not task_id:
        raise TypeError("update_task() requires a non-empty task_id")
    # Refuse control parameters BEFORE anything is read or locked, so a
    # doomed call never touches the store. See `_CONTROL_KWARGS` for why
    # each name is listed; the short version is that `**fields` would
    # otherwise write a requested GUARD onto the card as DATA and report
    # success, leaving the caller wrong about whether they are protected.
    for _name, _why in _CONTROL_KWARGS.items():
        if _name in fields:
            raise TypeError(f"update_task() does not accept {_name!r}: {_why}")
    # `""` on a CLOSED-ENUM field is a DELETE INSTRUCTION, consumed HERE —
    # it must never reach the validator as a value (see _store_enums: the
    # documented "pass '' to clear" contract used to be the one way that
    # could NOT clear a blocker, and it failed at SAVE time, aborting whole
    # bulk batches). `status` is refused loudly instead: it cannot be
    # cleared. Done BEFORE the lock so a doomed mutation never takes it.
    fields = _resolve_enum_clears(fields, source="update_task")
    resolved = _resolved_store(store)
    result: dict | None = None
    transitioned_to_done = False
    # C5: capture the (from, to) status pair when `status` actually flips
    # so we can emit the matching card-event AFTER the lock. None = no flip.
    # (hook-bypass: line-limit)
    status_change: tuple[str | None, str | None] | None = None
    # COLLECT the tolerated-value warnings this write raises, so they reach the
    # caller rather than only the server's stderr. See `_tolerated`: three
    # `pending` cards were created after that status was abolished, by the
    # maintainer of the package that abolished it, each firing this warning into
    # a place they never looked.
    from ._tolerated import collect as _collect_tolerated

    with _collect_tolerated(task_id) as _tolerated, _store_lock(resolved):
        doc, tasks = _read_write_doc(resolved)
        for task in tasks:
            # See `_task._is_tombstoned`: a deleted card's row is retained
            # forever but must behave as ABSENT — mutating it here would
            # silently resurrect it (2026-07-21 tombstone change).
            if task.get("id") == task_id and not _task._is_tombstoned(task):
                prior_status = task.get("status")
                prior_blocker = task.get("blocker")
                for key, value in fields.items():
                    if value is None:
                        task.pop(key, None)
                    else:
                        task[key] = value
                # D11 partial-fix (ADR-0008): auto-stamp ``last_activity``
                # on every successful mutation (drives the recency-color
                # signal on the board). Skip if the caller passed an
                # explicit ``last_activity`` field this call — their
                # value wins over the auto-stamp.
                if "last_activity" not in fields:
                    task["last_activity"] = _utc_now_iso()
                # Stamp the backlog age clock ONCE, on entry into `deferred`.
                # Never on a re-defer: `last_activity` above already moved, and
                # if the age clock moved with it, a card re-deferred every week
                # would read as permanently young and could never expire. The
                # rot would be real and invisible at the same time.
                # LEAVING `done` must drop the completion stamp. Placed with the
                # other transition clocks, at the one point a status change is
                # applied, so a future exit from `done` inherits it without its
                # author knowing the invariant exists. Before this, the only
                # unstamping path was `reopen_task` — which forces
                # status=blocked, and is therefore wrong for a card being
                # deferred or cancelled. So every honest exit kept the stamp.
                _clear_completion_stamp_on_leaving_done(task, prior_status)
                _stamp_deferred_at(task, prior_status)
                # Same lesson, the blocked-check's clock: stamp when the
                # (status, blocker) PAIR moves, never on a passing comment.
                _stamp_blocked_at(task, prior_status, prior_blocker)
                # DECLARE THE ROW. Without `touched_ids` the mirror treats
                # "differs from the database" as "the caller meant to write
                # it" — so this whole-document write re-asserts every card in
                # the caller's snapshot, silently reverting anything another
                # agent committed since the read. Both writers are told they
                # succeeded. Measured by figrecipe 2026-08-10: a
                # `complete_task` that RETURNED `status=done` was later found
                # back at `status=blocked`.
                #
                # `[task_id]` is sufficient here and that is verified, not
                # assumed: this function mutates exactly one dict — the card
                # matched by id — through `fields`, the `last_activity`
                # auto-stamp, and the three lifecycle clocks, every one of
                # which takes `task` and writes only `task[...]`. Contrast
                # `_store_rescore`, which shifts NEIGHBOURING rows and
                # therefore must declare them too; under-declaring is the way
                # this parameter goes wrong (see `_store_relations:181`).
                _mirror = _save_doc_unlocked(
                    doc,
                    resolved,
                    tasks=tasks,
                    touched_ids=[task_id],
                    expected_revision=expected_revision,
                )
                # RAISE rather than return counts. The mirror reports a refusal as
                # `revision_skipped`; a caller who does not inspect it is told
                # nothing and believes the write landed -- the invisible lost
                # update, reintroduced one layer up from where it was fixed.
                if _mirror and _mirror.get("revision_skipped"):
                    from ._store_errors import RevisionConflictError

                    raise RevisionConflictError(
                        task_id,
                        expected_revision,
                        (_mirror.get("revision_found") or {}).get(task_id),
                    )
                result = dict(task)
                transitioned_to_done = (
                    fields.get("status") == "done" and prior_status != "done"
                )
                # Record a genuine status flip (post-state differs from
                # prior): `status` present in fields AND changed value.
                new_status = task.get("status")
                if "status" in fields and new_status != prior_status:
                    status_change = (prior_status, new_status)
                break
    if result is None:
        raise TaskNotFoundError(f"task id {task_id!r} not found in {resolved}")
    # Active-unblock DRIVE (ADR-0009) — a direct status→done via
    # update_task() drives the same unblock as complete_task(). Outside
    # the lock; the handler's per-card token dedupe makes a double-path
    # (e.g. update_task then complete_task) idempotent.
    if transitioned_to_done:
        _emit_unblock_for_dependents(resolved, task_id, by=None)
    # C5: emit a canonical card-event for a genuine status flip, AFTER the
    # write is durable + lock released (fail-soft). A flip TO `done` is a
    # `completed` event (NOT also a `status_changed` — avoids double-fire);
    # every other flip is a `status_changed` with {from,to}.
    if status_change is not None:
        _from, _to = status_change
        if _to == "done":
            _emit_card_event(
                "completed",
                task_id,
                actor=None,
                store=resolved,
                entry_points=entry_points,
            )
        else:
            _emit_card_event(
                "status_changed",
                task_id,
                actor=None,
                extra={"from": _from, "to": _to},
                store=resolved,
                entry_points=entry_points,
            )
    # Liveness (assignee-liveness feature). Heartbeat the acting agent
    # (best-effort from $SCITEX_CARDS_AGENT_ID — update_task has no `by`, and we
    # deliberately reuse the SAME env identity seam rather than inventing a
    # second one; fail-soft so a missing env never breaks the update). When
    # this update SET an assignee/agent, surface that owner's liveness in the
    # result so a reassign-via-update also tells the caller "you assigned to
    # a non-running agent."
    from ._liveness import _assignee_liveness, _heartbeat

    _heartbeat(os.environ.get(ENV_AGENT), resolved)
    if "assignee" in fields or "agent" in fields:
        _owner = result.get("assignee") or result.get("agent")
        _liveness = _assignee_liveness(_owner, resolved)
        if _liveness is not None:
            result["assignee_liveness"] = _liveness
    # Same shape as `assignee_liveness` above and for the same reason: a fact the
    # caller needs, attached to the result rather than logged past them. Only
    # when non-empty, so an ordinary write is byte-identical to before.
    if _tolerated:
        result["warnings"] = list(_tolerated)
    return result


__all__ = [
    "_stamp_deferred_at",
    "_stamp_blocked_at",
    "_wip_statuses",
    "add_task",
    "update_task",
]

# EOF
