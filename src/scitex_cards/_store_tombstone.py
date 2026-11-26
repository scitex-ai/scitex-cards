#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Card TOMBSTONE verbs — delete a card, and undo that delete.

Split out of ``_store_lifecycle`` (PURE MOVE of both verbs, plus the one
behaviour fix named below), which re-exports both names so
``from scitex_cards._store_lifecycle import delete_task`` and
``from ._store import delete_task`` keep working:

    delete_task    tombstone in place + scrub inbound refs (returns the Undo payload).
    restore_task   the Delete→Undo partner.

The pair lives together because each is the other's inverse: the payload
``delete_task`` returns is exactly what ``restore_task`` takes.

BEHAVIOUR FIX carried with the move (2026-11-26): tombstoning a ``done`` card
now drops its ``_log_meta.completed_{at,by}``. A tombstone is ``cancelled``,
which is not a completion, and the throughput surfaces aggregate on
``completed_at`` alone — so every deleted ``done`` card kept counting as
delivered work (the ``terminal_state_honest`` doctor check found three such
tombstones on the live board). The Undo payload's ``_log_meta`` is now
unshared from the live row first, so it keeps the stamp and does not pick up
the tombstone's ``deleted_at``.

Shared helpers stay where they were and are imported inside the function
bodies, the same deferred-import pattern ``_store_lifecycle`` uses to avoid an
import cycle with ``_store``.
"""

from __future__ import annotations

from pathlib import Path

from ._comment_ids import stamp_comment_id
from ._store_arg import refuse_ineffective_store
from ._store_list import _resolved_store
from ._touch import touch_last_activity


def delete_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    expected_revision: int | None = None,
) -> dict:
    """TOMBSTONE a task + scrub references to it. Returns the lossless
    payload the client can pass to ``restore_task`` for Undo.

    2026-07-21 P0 (third board wipe) — operator ruling 一度書いたものは
    消えない, "a written card never disappears": this NO LONGER physically
    removes the row. It marks it in place — ``status`` flips to
    ``cancelled``, ``_log_meta.deleted_at`` (+ ``deleted_by``) records when
    and who — and the row is retained forever (see
    :func:`_task._is_tombstoned`). Physical removal is IMPOSSIBLE through
    this, the normal API; a genuine purge is a deliberate admin verb, not
    this one.

    The board v3 Delete-with-Undo flow uses this via ``handlers/crud.py``;
    exposing the same operation here lets MCP agents do the same delete +
    later undo without round-tripping HTTP. Reads (``list_tasks`` /
    ``get_task`` / ``set_edge`` / every other lookup) treat a tombstoned
    row as ABSENT by default, so board behaviour is unchanged.

    Returns ``{"removed": <full pre-tombstone task dict>, "refs": [<refs
    scrubbed>]}`` where each ref is the id of another task whose
    depends_on / blocks / parent pointed at the deleted task (the client
    passes ``removed`` back to ``restore_task`` to lossless-revert).

    ``expected_revision`` makes the tombstone a COMPARE-AND-SET, OPT-IN
    exactly like ``update_task``'s: ``None`` (the default) is byte-identical
    to before. Armed, the tombstone lands ONLY if the live row is still at
    that revision — a racing update between the caller's read and this write
    is refused with :class:`RevisionConflictError` instead of deleting a card
    the caller never saw. The ref-scrubs travel in the same atomic batch and
    a refused fence rolls the whole batch back, so a lost race leaves the
    target live AND the dependents pointing at it: nothing is half-deleted.
    """
    from . import _model, _task
    from ._store import _default_agent, _read_write_doc, _task_not_found, _utc_now_iso
    from ._store_lifecycle import clear_completion_stamp

    refuse_ineffective_store(store, verb="delete_task")
    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("delete_task: 'task_id' is required")
    if expected_revision is not None and (
        isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
    ):
        raise TypeError(
            "delete_task: expected_revision must be an int (the revision the "
            f"tombstone asserts) or None (fence off); got {expected_revision!r}."
        )
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        target = _task._find_live_task(tasks, task_id)
        if target is None:
            raise _task_not_found(task_id)
        original = dict(target)  # pre-tombstone snapshot: the Undo payload
        # Unshare `_log_meta`: `original` is a SHALLOW copy, so its nested meta
        # dict IS the live row's, and the tombstone below pops the completion
        # stamp and writes `deleted_at` into it. Without this the Undo payload
        # silently lost its stamp and gained the tombstone marker — same
        # aliasing restore_task already guards against on its side.
        if isinstance(target.get("_log_meta"), dict):
            target["_log_meta"] = dict(target["_log_meta"])
        refs: list[str] = []
        for t in tasks:
            if t is target:
                continue
            mutated = False
            if isinstance(t.get("depends_on"), list) and task_id in t["depends_on"]:
                t["depends_on"] = [d for d in t["depends_on"] if d != task_id]
                if not t["depends_on"]:
                    t.pop("depends_on", None)
                mutated = True
            if isinstance(t.get("blocks"), list) and task_id in t["blocks"]:
                t["blocks"] = [b for b in t["blocks"] if b != task_id]
                if not t["blocks"]:
                    t.pop("blocks", None)
                mutated = True
            if t.get("parent") == task_id:
                t.pop("parent", None)
                mutated = True
            if mutated:
                refs.append(t.get("id"))
        # TOMBSTONE in place — never a physical removal. `tasks` still
        # contains `target`, so this is an ordinary upsert-by-id write, not
        # the `deleted_ids` path (that path stays reserved for a future,
        # deliberate admin purge; see `_db_mirror`).
        actor = _default_agent(None)
        now = _utc_now_iso()
        target["status"] = "cancelled"
        # CLEAR THE GATE WITH THE STATUS — the same rule `complete_task` learned
        # on 2026-08-01, which this verb had not. A card that is `cancelled`
        # while still naming an unresolved blocker is incoherent, and
        # `_validate_tasks` refuses it:
        #
        #   TaskValidationError: task 'a' has blocker 'dependency' but status is
        #   'cancelled'; set status: blocked or remove the blocker field
        #
        # So DELETING ANY BLOCKED CARD THAT NAMES ITS GATE FAILED OUTRIGHT — and
        # because validation covers the WHOLE document, that one card stopped
        # every other write in the same save. Found by the restore/undo test
        # below, which deletes a realistically-blocked card rather than a bare
        # one; the previous fixtures only ever tombstoned cards with no blocker,
        # so the hole sat behind a test that could not reach it.
        target.pop("blocker", None)
        # A tombstone is `cancelled`, which is NOT a completion: tombstoning a
        # `done` card must drop its completion stamp, or the throughput surfaces
        # keep counting deleted work as delivered. The Undo payload (`original`)
        # was snapshotted above, so restore_task still receives the stamp and
        # applies its own rule to it.
        clear_completion_stamp(target)
        log_meta = target.get("_log_meta")
        if not isinstance(log_meta, dict):
            log_meta = {}
            target["_log_meta"] = log_meta
        log_meta["deleted_at"] = now
        log_meta["deleted_by"] = actor
        comments = target.setdefault("comments", [])
        comments.append(
            stamp_comment_id(
                {
                    "author": actor,
                    "ts": now,
                    "text": (
                        "[TOMBSTONED via delete_task] status -> cancelled, "
                        "_log_meta.deleted_at stamped. Row retained (never "
                        "physically removed); restore_task is the Undo."
                    ),
                }
            )
        )
        target["last_activity"] = now
        # NAMES EVERY CARD IT TOUCHED, WHICH IS MORE THAN ONE. This verb
        # tombstones `task_id` AND scrubs inbound references to it from every
        # card in `refs` — so its intent genuinely spans several rows, unlike
        # the other lifecycle verbs.
        #
        # `touched_ids=[task_id]` would have been the obvious conversion and it
        # would have SILENTLY DISCARDED THE REF SCRUBS, leaving dangling
        # references to a tombstoned card — a new defect introduced by the fix
        # for an old one. The ids are already collected because the Undo payload
        # returns them; naming intent here costs nothing extra.
        #
        # `expected_revisions` (when the caller armed `expected_revision`)
        # fences ONLY the tombstone row: the ref-scrubs travel unguarded in
        # the same atomic batch, and a lost tombstone race rolls the whole
        # batch back — refused siblings included — so a failed fence leaves
        # the target live AND the dependents pointing at it, exactly as
        # before the call. See `mirror_doc_incremental`.
        _armed_rev = expected_revision
        _mirror = _model._save_doc_unlocked(
            doc,
            tasks_path,
            tasks=tasks,
            touched_ids=[task_id, *refs],
            expected_revisions=(
                {task_id: _armed_rev} if _armed_rev is not None else None
            ),
        )
        if (
            _armed_rev is not None
            and _mirror
            and task_id in (_mirror.get("revision_skipped") or [])
        ):
            from ._store_errors import RevisionConflictError

            raise RevisionConflictError(
                task_id,
                _armed_rev,
                (_mirror.get("revision_found") or {}).get(task_id),
            )
    return {"removed": original, "refs": refs}


def restore_task(
    store: str | Path | None = None,
    task: dict | None = None,
    refs: list[str] | None = None,
) -> dict:
    """Undo a ``delete_task``: UN-TOMBSTONE the row back to its pre-delete
    state (or, for a row with no tombstone at all — legacy/never-deleted
    — re-insert it, the original pre-tombstone-era behaviour).

    Idempotent on a duplicate id that is NOT a tombstone — raises
    ``ValueError`` (use ``update_task`` to mutate; this verb is the
    Delete-Undo partner only). A tombstoned row is exactly what this verb
    expects to find and reverses in place.

    On a ``task.status`` outside ``VALID_STATUSES`` (including abolished
    ``"pending"``) — before any store resolution, lock, or write, so a
    rejected restore never touches the store. A mapping with NO status at
    all defaults to ``"deferred"`` (the ``handle_restore`` twin default),
    never a status-less row. A restored status other than ``"blocked"``
    drops the ``blocker`` gate (the schema validator rejects a real blocker
    on a non-``blocked`` row — without this a status-less+blocked mapping
    defaulted to ``deferred`` fails on its own gate). A snapshot stamp
    does not survive the restore unless the card re-enters AS ``done`` —
    ``completed_at`` on a live row is the phantom-delivery row the
    throughput aggregates count forever.

    Raises:
        ValueError: ``task`` is not a dict with an ``id``.
        TaskValidationError: ``task.status`` is present but outside
            ``VALID_STATUSES`` (abolished values carry the abolition hint).
    """
    from . import _model, _task
    from ._store import _read_write_doc, _utc_now_iso
    from ._store_lifecycle import COMPLETED_STATUS, clear_completion_stamp

    if not isinstance(task, dict) or not task.get("id"):
        raise ValueError("restore_task: 'task' must be a dict with 'id'")
    # The store verb is a SOURCE, so it stays strict like the board restore
    # handler (``undo.handle_restore``) and the sibling verbs: a restored
    # status outside VALID_STATUSES (including abolished "pending") raises
    # BEFORE the store is resolved or locked — the save path only warns on
    # bad values per the 2026-07-10 ruling, so without this a crafted `task`
    # mapping (MCP ``restore_task`` replays the ``delete_task`` payload
    # verbatim) could mint a forbidden row through Delete→Undo. A mapping
    # with NO status at all is defaulted to "deferred" on the COPY below
    # (the handle_restore twin default) — the caller's dict is never
    # mutated: the Undo payload stays replayable.
    status = task.get("status")
    if status and status not in _task.VALID_STATUSES:
        hint = _task.ABOLISHED_STATUSES.get(status)
        if hint is not None:
            raise _task.TaskValidationError(f"restore_task: {hint}")
        raise _task.TaskValidationError(
            f"restore_task: invalid status {status!r}; "
            f"must be one of {_task.VALID_STATUSES}"
        )
    refuse_ineffective_store(store, verb="restore_task")
    tasks_path = _resolved_store(store)
    tid = task["id"]
    with _model._store_lock(tasks_path):
        doc, tasks = _read_write_doc(tasks_path)
        # The row we write is a COPY, stamped as a fresh act. `delete_task`
        # advances `last_activity` when it tombstones (see its body), so a
        # restore that reused the caller's pre-delete snapshot verbatim would
        # be strictly OLDER than the tombstone it reverses. On a second host
        # the reconciler would then read the tombstone as the later act and
        # re-delete the card — an Undo that undoes itself. We do not mutate
        # the caller's dict: the Undo payload stays replayable.
        restored = dict(task)
        if not restored.get("status"):
            # Twin of the board default (``handle_restore``): a mapping with
            # NO status lands in the backlog, never a status-less row. The
            # gate above already refused every PRESENT-but-bad value, so
            # only falsy (missing/None/"") reaches this line — and the
            # caller's dict keeps whatever it had (replayable Undo payload).
            restored["status"] = "deferred"
        if restored.get("status") != "blocked":
            # Leaving `blocked` MUST clear the gate: the validator REJECTS a
            # real blocker on a non-`blocked` row (`_validate.py` rule (b)),
            # so without this a status-less+gated mapping defaulted to
            # `deferred` above fails on its own gate — the same 400-on-own-
            # gate `handle_archive` + `close --reason` were fixed for, and
            # the same pop `handle_reopen` keeps on non-`blocked` restores.
            # A verbatim blocked snapshot (status `blocked`) keeps its gate.
            restored.pop("blocker", None)
        if restored.get("status") != COMPLETED_STATUS:
            # The restored card re-enters as LIVE work: a done+stamped
            # snapshot restored to a live status would keep `completed_at`
            # on a non-`done` row — the phantom-delivery row throughput
            # (fleet/timing.py + timeline.py) counts forever, because those
            # aggregates never consult `status`. Restored AS `done` keeps
            # its genuine stamp (it really was completed). Twin of the
            # `handle_restore` board clear; same honesty class as the
            # `reopen_task` + `update_task` + upsert clears.
            #
            # Unshare `_log_meta` first: `restored` is a SHALLOW copy, so
            # its nested meta dict IS the caller's — and the clear pops
            # keys in place. Without this the caller's Undo payload would
            # lose its stamp (the contract above promises the caller's
            # dict is never mutated and stays replayable).
            _meta = restored.get("_log_meta")
            if isinstance(_meta, dict):
                restored["_log_meta"] = dict(_meta)
            clear_completion_stamp(restored)
        touch_last_activity(restored, _utc_now_iso())
        existing = next((t for t in tasks if t.get("id") == tid), None)
        if existing is not None:
            if not _task._is_tombstoned(existing):
                raise ValueError(f"restore_task: id {tid!r} already present")
            # UN-TOMBSTONE in place: replace with the caller's pre-delete
            # snapshot, at the SAME list position (an ordinary upsert).
            tasks[tasks.index(existing)] = restored
        else:
            # No row at all — a legacy pre-tombstone-era delete, or an
            # admin purge. Fall back to the original append behaviour.
            tasks.append(restored)
        # NAMES THE ONE CARD IT TOUCHED. Both branches above write exactly `tid`
        # -- un-tombstoning replaces that row in place, the fallback appends it
        # -- so nothing else in the document is this verb's intent.
        _model._save_doc_unlocked(doc, tasks_path, tasks=tasks, touched_ids=[tid])
    # refs are descriptive (the client passes them through so callers can
    # see which tasks had been mutated; we don't reverse-apply them since
    # the depends_on / blocks values were just stripped, not stored).
    return {"task": task, "refs": list(refs or [])}


__all__ = ["delete_task", "restore_task"]

# EOF
