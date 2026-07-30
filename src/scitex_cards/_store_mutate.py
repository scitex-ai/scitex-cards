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

from ._db import resolve_db_path
from ._model import (
    TaskValidationError,
    _save_doc_unlocked,
    _store_lock,
)
from ._paths import refuse_ambient_store_creation as _refuse_ambient_store_creation
from ._store_enums import resolve_enum_clears as _resolve_enum_clears
from ._store_events import _emit_card_event, _emit_unblock_for_dependents
from ._store_list import _resolved_store


def add_task(
    store: str | Path | None = None,
    *,
    id: str,
    title: str,
    status: str = "deferred",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    repo: str | None = None,
    created_by: str | None = None,  # hook-bypass: line-limit
    entry_points=None,
    **extras,
) -> dict:
    """Append a new task to ``store`` and persist via :func:`save_tasks`.

    Returns the inserted task mapping (a fresh dict, not the underlying
    YAML node) for convenient round-trip use by callers — the CLI prints
    it, the MCP tools serialize it as the JSON result.

    The ``**extras`` keyword catches operator-co-designed Task dataclass
    fields (``task`` / ``project`` / ``host`` / ``agent`` / ``goal`` /
    ``last_activity`` / ``blocker`` / ``pr_url`` / ``issue_url`` / ``kind``
    + compute metadata ``job_id`` / ``command`` / ``started_at`` /
    ``finished_at``) without an explosion of named parameters. ``None``
    values are dropped; non-``None`` values flow into the new task dict
    and the writer's validator gates closed enums (``status`` / ``kind``
    / ``blocker``) — typos raise ``TaskValidationError`` with the bad
    value and the valid set. Unknown keys are accepted at this layer
    (forward-compat); the validator decides whether they're shape-valid.

    Raises
    ------
    TaskValidationError
        On duplicate id or any other structural fault — `save_tasks`
        re-runs the full validation gate before touching disk.
    """
    from ._store import _read_write_doc, _resolve_creator_or_raise, _utc_now_iso

    # Same ONE rule as `update_task` (the sibling write path): a `""` on a
    # closed-enum field is a clear, so the key is simply NOT written on
    # insert — rather than written as `""` for the validator to reject. A
    # `status=""` is refused loudly (a card cannot be born status-less).
    _enum_in = _resolve_enum_clears({"status": status, **extras}, source="add_task")
    status = _enum_in.pop("status")
    extras = _enum_in
    resolved = _resolved_store(store)
    # A write against a store that does not exist must not INVENT one when
    # nothing named the path — that is how a decoy board accumulates and then
    # gets imported over the real one. See the guard's docstring for the
    # measured 2026-07-20 chain. An explicit `store` is the opt-in.
    #
    # The guard asks ONE question — "would this write MANUFACTURE a board?" —
    # and answers it with `path.exists()`. So it must be handed the store's real
    # LOCATION: the canonical SQLite database, which is what `save_tasks` writes
    # and what `init-store` creates. `_resolved_store` returns a DISPLAY LABEL
    # (`<db_dir>/tasks.yaml`) that the SQLite backend maps to that database —
    # good enough to name a store in a message, never a thing on disk. The YAML
    # tier was deleted (#512), so that label can NEVER exist, and passing it here
    # made the guard refuse unconditionally: every `add` failed for any agent
    # without $SCITEX_CARDS_DB while its own reads and updates succeeded, and the
    # error told you to run `init-store` — which did not help, because the file
    # it created was not the file being tested. Reported and reproduced by
    # scitex-ui on 0.17.7. Guard the database, not the label.
    _refuse_ambient_store_creation(resolve_db_path(store), store)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    # FAIL-LOUD on a missing/blank OWNER (operator mandate 2026-06-26,
    # constitution rule 2 "no silent fallbacks"). The OWNER is `assignee`
    # OR `agent` (lock-step below). A card with neither reached a blank
    # creator/assignee on the board + a fallback lane + an owner-less
    # comment relay that silently no-op'd — so an owner is REQUIRED.
    # `agent` arrives via **extras (operator-co-designed field).  # noqa: E501  # hook-bypass: line-limit
    _agent_in = extras.get("agent")
    _owner_in = assignee or _agent_in or ""
    _owner_in = _owner_in.strip() if isinstance(_owner_in, str) else _owner_in
    if not _owner_in:
        raise TaskValidationError(
            "assignee is required — pass assignee=<user> (or agent=<user>); "
            "creator+assignee are mandatory and an owner-less card is "
            "rejected (no silent fallback; see constitution)."
        )
    # RESOLVE the creator STRICTLY — raises a clear, actionable error when
    # it can't be resolved (blank / "unknown"). Done BEFORE any write so a
    # creatorless card never touches disk. (hook-bypass: line-limit)
    _creator = _resolve_creator_or_raise(created_by)
    new: dict = {"id": id, "title": title, "status": status}
    # D11 partial-fix (ADR-0008): auto-stamp ``created_at`` +
    # ``last_activity`` at insert time. ``created_at`` is the immutable
    # insert stamp; ``last_activity`` starts equal and ticks on every
    # subsequent successful update_task. Callers can override by passing
    # the field explicitly (e.g. importers replaying historical state).
    _stamp = _utc_now_iso()
    new["created_at"] = _stamp
    new["last_activity"] = _stamp
    # `created_by` — the creating USER, STRICTLY resolved above (never a
    # blank/"unknown" placeholder). Drives the board detail ROLES section +
    # ADR-0009's creator auto-subscribe. (hook-bypass: line-limit)
    new["created_by"] = _creator
    if scope is not None:
        new["scope"] = scope
    # Keep `agent` + `assignee` in LOCK-STEP: whichever the caller supplied,
    # BOTH are stamped to the resolved owner so the board/relay/notify never
    # see an owner-less or half-owned card (mirrors `reassign_task`). The
    # `agent` half is set from **extras after this block; force it here so
    # an assignee-only OR agent-only call yields a fully-owned card. The
    # explicit `agent` extra (if any) is overwritten with the same owner.
    new["assignee"] = _owner_in
    extras["agent"] = _owner_in
    if priority is not None:
        new["priority"] = priority
    if parent is not None:
        new["parent"] = parent
    if note is not None:
        new["note"] = note
    if depends_on is not None:
        new["depends_on"] = list(depends_on)
    if blocks is not None:
        new["blocks"] = list(blocks)
    if repo is not None:
        new["repo"] = repo
    # Operator-co-designed surface (TG 9667) + compute metadata
    # (ADR-0002). Forwarded through **extras so callers don't have to
    # match a long explicit parameter list and the writer's validator
    # gates the closed enums.
    for key, value in extras.items():
        if value is None:
            continue
        new[key] = value

    # Lock for the FULL read-modify-write — without this, two concurrent
    # writers each load a stale snapshot and the second `save_tasks` call
    # silently clobbers the first writer's insert. See
    # tests/scitex_cards/test__store.py::test_two_concurrent_writers...
    with _store_lock(resolved):
        # `missing_ok=True` is gone deliberately. It meant "an absent store
        # yields an empty doc", which against a database feeds an empty doc
        # into this read-modify-write and lets the subsequent save delete every
        # card absent from it. A missing database is a configuration error, not
        # an empty board — see `_read_write_doc`.
        doc, tasks = _read_write_doc(resolved)
        # WIP-validation gate (operator standing direction via lead a2a
        # `d99b8de6839d46e586e4ee692f43c1d9` + ``5acfbb5d0db44db8a7fa4f70c399d539``,
        # 2026-06-12). WARN to stderr at the limit, HARD REFUSE at 2x — EXCEPT
        # for the emergency band (``priority <= 1``), which is never gated and
        # is stamped with an audit comment when it lands over the cap. The whole
        # policy — thresholds, exemption, refusal text, audit stamp — lives in
        # ``_store_wip`` so it is readable in one screen; this is the same
        # focused-sibling pattern as ``_store_enums`` / ``_store_verify``.
        # See that module's header for the 2026-07-12 P0 the exemption closes.
        # (hook-bypass: line-limit)
        from ._store_wip import enforce_wip_gate

        enforce_wip_gate(new, tasks, now_iso=_stamp)
        tasks.append(new)
        _save_doc_unlocked(doc, resolved, tasks=tasks)
    # C5: emit a canonical `created` card-event AFTER the card is durably
    # persisted + the lock released. Fail-soft (the mutation already
    # succeeded). Actor = the resolved creating user (same chain that
    # `created_by` resolves through). (hook-bypass: line-limit)
    _emit_card_event(
        "card_created",
        id,
        actor=new.get("created_by"),
        store=resolved,
        entry_points=entry_points,
    )
    # Liveness (assignee-liveness feature): the creator just touched the
    # store → stamp its heartbeat; and surface the ASSIGNEE's liveness in
    # the result so the caller learns immediately if it just assigned to a
    # non-running agent. Both fail-soft (never break the durable write).
    from ._liveness import _assignee_liveness, _heartbeat

    _heartbeat(new.get("created_by"), resolved)
    result = dict(new)
    _liveness = _assignee_liveness(new.get("assignee"), resolved)
    if _liveness is not None:
        result["assignee_liveness"] = _liveness
    return result


def _stamp_deferred_at(task: dict, prior_status: str | None) -> None:
    """Set ``deferred_at`` when a card ENTERS the backlog, and only then.

    Fires on the TRANSITION only. A card that was already ``deferred`` is left
    untouched — including the legacy cards that carry no stamp at all, whose
    age ``deferred_since`` reads from ``created_at``. Stamping those on any
    passing mutation (a comment, a reassign) would silently reset the rot clock
    on the entire existing backlog, which is the one thing this field exists to
    prevent. A card that leaves and later returns is re-stamped, because that
    genuinely is a new spell in the backlog.
    """
    from ._backlog_triage import BACKLOG_STATUS, FIELD_DEFERRED_AT
    from ._store import _utc_now_iso

    if task.get("status") != BACKLOG_STATUS or prior_status == BACKLOG_STATUS:
        return
    task[FIELD_DEFERRED_AT] = _utc_now_iso()


def _stamp_blocked_at(
    task: dict, prior_status: str | None, prior_blocker: str | None
) -> None:
    """Set ``blocked_at`` when the ``(status, blocker)`` PAIR moves, and only then.

    The blocked-check's clock, exactly parallel to :func:`_stamp_deferred_at` but
    keyed on the pair rather than the status alone — because re-blocking the same
    card on a DIFFERENT blocker genuinely starts a new wait, while commenting on
    it does not. A comment changes ``last_activity`` and neither element of the
    pair, so it must leave this stamp alone: keying the sweep on a field every
    mutation touches is what made the alarm silenceable by typing.

    Cards already blocked before this shipped carry no stamp; they are left
    untouched here rather than back-filled on a passing mutation, and
    ``_blocked_age_hours`` reads their age from ``created_at`` instead. That
    makes them read as maximally stale, so the alarm errs toward firing.
    """
    from ._stale_active_clocks import FIELD_BLOCKED_AT
    from ._store import _utc_now_iso

    if task.get("status") != "blocked":
        return
    if prior_status == "blocked" and task.get("blocker") == prior_blocker:
        return  # Pair unchanged — not a new wait.
    task[FIELD_BLOCKED_AT] = _utc_now_iso()


def _wip_statuses() -> frozenset[str]:
    """Re-export from ``_throughput`` so the gate's predicate stays a single
    source of truth. WIP is work in flight — ``in_progress`` — not backlog.

    The add path no longer calls this (``_store_wip.enforce_wip_gate`` reads
    ``WIP_STATUSES`` straight from ``_throughput``); kept for out-of-tree
    importers. (hook-bypass: line-limit)
    """
    from ._throughput import WIP_STATUSES

    return WIP_STATUSES


def update_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    entry_points=None,  # hook-bypass: line-limit
    **fields,
) -> dict:
    """Update fields of the task with id ``task_id``; return the merged dict.

    Any keyword argument becomes a field on the task. Passing ``None`` for
    a field DELETES it (matches the operator's mental model: "clear the
    scope" = `update_task(..., scope=None)`). To leave a field untouched,
    just omit it.

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
    with _store_lock(resolved):
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
                _stamp_deferred_at(task, prior_status)
                # Same lesson, the blocked-check's clock: stamp when the
                # (status, blocker) PAIR moves, never on a passing comment.
                _stamp_blocked_at(task, prior_status, prior_blocker)
                _save_doc_unlocked(doc, resolved, tasks=tasks)
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
    # (best-effort from $SCITEX_TODO_AGENT_ID — update_task has no `by`, and we
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
    return result


__all__ = [
    "_stamp_deferred_at",
    "_stamp_blocked_at",
    "_wip_statuses",
    "add_task",
    "update_task",
]

# EOF
