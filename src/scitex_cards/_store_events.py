#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Event-emission seams for the store's mutating verbs.

Split out of ``_store`` (pure move, no behaviour change) so the write /
lifecycle / relations modules can share ONE producer seam:

    _emit_card_event              fail-soft emit of a canonical C1 card-event
    _emit_unblock_for_dependents  the ADR-0009 active-unblock DRIVE

Both are called AFTER the mutation is durably persisted and OUTSIDE the store
file lock, and both are FAIL-SOFT: a raising or slow emit can never break or
roll back the write it reports on.

This module is deliberately a LEAF — it imports ``_events`` / ``_hooks`` /
``_model`` / ``_runnable`` / ``_store`` lazily inside the function bodies. That
is not stylistic: ``_events`` imports ``_utc_now_iso`` from ``_store``, and
``_store`` imports THIS module at module level to re-export these names, so a
top-level import either way would cycle.
"""

from __future__ import annotations

from pathlib import Path


def _emit_unblock_for_dependents(
    tasks_path: Path,
    completed_id: str,
    *,
    by: str | None = None,
    entry_points=None,
) -> list[str]:
    """Active-unblock DRIVE (ADR-0009).

    A card just flipped to ``done``. Find its DIRECT dependents whose
    last blocking dependency this completion just cleared — i.e. the
    dependents that are NOW runnable — and emit ONE ``unblock`` event
    naming them. A consumer (SAC) notifies each unblocked card's
    assignee + subscribers (*"your task is now unblocked"*).

    "Direct dependent" = a card D with ``completed_id`` in its
    ``depends_on``, OR a card D that ``completed_id`` lists in its
    ``blocks``. "Now runnable" reuses :func:`_runnable.runnable_tasks`
    verbatim (DRY — same dep-satisfied predicate the dispatcher uses):
    a direct dependent that is runnable now could not have been runnable
    before (``completed_id`` was an unresolved upstream), so its presence
    in the runnable set means *this* completion unblocked it.

    Returns the unblocked card ids (possibly empty). Best-effort: this is
    called AFTER the completion is durably saved, so any compute/bus
    error is caught + logged, never raised — feedback must not break the
    done transition it reports on.
    """
    try:
        from . import _hooks, _model, _runnable
        from ._store import _default_agent, _utc_now_iso

        tasks = _model.load_tasks(tasks_path)
        unlocker = next(
            (t for t in tasks if isinstance(t, dict) and t.get("id") == completed_id),
            None,
        )
        downstream_via_blocks = set(unlocker.get("blocks") or ()) if unlocker else set()
        dependents = {
            t.get("id")
            for t in tasks
            if isinstance(t, dict)
            and t.get("id")
            and (
                completed_id in (t.get("depends_on") or ())
                or t.get("id") in downstream_via_blocks
            )
        }
        if not dependents:
            return []
        runnable_now = {t.get("id") for t in _runnable.runnable_tasks(tasks).tasks}
        unblocked = sorted(str(i) for i in (dependents & runnable_now) if i)
        if not unblocked:
            return []
        _hooks.dispatch_event(
            {
                "kind": "unblock",
                "unlocker_id": completed_id,
                "card_ids": unblocked,
                "author": _default_agent(by),
                "unblocked_at": _utc_now_iso(),
            },
            # Pass the SAME store so the built-in `_handle_unblock` writes
            # the `[unblocked]` comment to this store, not the default one.
            store=tasks_path,
            entry_points=entry_points,
        )
        return unblocked
    except Exception:  # noqa: BLE001 — unblock drive must not break `done`
        import scitex_logging as slogging

        slogging.getLogger(__name__).warning(
            "unblock drive failed for completed card %r", completed_id, exc_info=True
        )
        return []


def _emit_card_event(
    factory: str,
    card_id: str,
    *,
    actor: str | None,
    store=None,  # mutation's store -> threaded to dispatcher/inbox (hook-bypass: line-limit)
    entry_points=None,
    **kw,
) -> None:
    """Fail-soft emit of a canonical C1 card-event from a store mutation.

    C5 producer seam (hook-bypass: line-limit). ``factory`` names a
    classmethod on :class:`scitex_cards._events.Event` (``card_created`` /
    ``commented`` / ``status_changed`` / ``completed`` / ``reassigned``);
    ``kw`` carries the event-specific extras (e.g.
    ``extra={"from": ..., "to": ...}``).

    DEFENSIVE / ADDITIVE: this is ALWAYS called AFTER the mutation is
    durably persisted (and outside the file lock), so a raising or slow
    emit can never break or roll back the write. :func:`scitex_cards._events.emit`
    already never raises, but we wrap the lazy import + envelope
    construction too — mirroring ``_hooks._handlers._emit_git_event``. The
    ``_events`` import is lazy because ``_events`` imports ``_utc_now_iso``
    from ``_store`` (avoid a top-level circular import).

    ``entry_points`` is the in-process injection seam (forwarded to
    ``emit`` → ``dispatch_event``) so no-mock tests capture the emitted
    event via a real fake handler; ``None`` uses real plugin discovery.
    """
    try:
        from ._events import Event, emit

        ev = getattr(Event, factory)(card_id, actor=actor, **kw)
        emit(ev, store=store, entry_points=entry_points)  # hook-bypass: line-limit
    except Exception:  # noqa: BLE001 — emit must never break a mutation
        import scitex_logging as slogging

        slogging.getLogger(__name__).warning(
            "scitex_cards._store: card-event emit failed for factory=%r card_id=%r",
            factory,
            card_id,
            exc_info=True,
        )


__all__ = ["_emit_card_event", "_emit_unblock_for_dependents"]

# EOF
