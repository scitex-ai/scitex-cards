#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Help-wait / help-clear card semantics — the "agent is stuck waiting on the
operator" card as a first-class verb pair.

Background (SoC lift)
---------------------
A dotfiles Notification hook used to hand-roll these cards by shelling out to
the generic ``scitex-cards add / update / list-tasks`` verbs. Whenever the card
schema drifted, the hook broke *silently*. Lifting the semantics into the
package makes scitex-cards the single source of truth; the hook becomes a thin
trigger that calls one verb (``help-wait`` / ``help-clear``).

Card contract (byte-for-byte what the old hook produced, so this is a drop-in
replacement):

  - id:            ``help-<agent>-waiting``
  - title:         ``[help] <agent> waiting on operator decision``
  - status:        ``blocked``
  - blocker:       ``operator-decision``  (a valid ``VALID_BLOCKERS`` member
                   on a ``status=blocked`` row)
  - assignee:      ``<agent>``
  - scope:         ``agent:<agent>``
  - host:          the ``host`` arg if given, else best-effort hostname
  - last_activity: current UTC ISO-8601
  - note:          the question text (or a placeholder when empty)

Existing help cards use the canonical revision-bound delivery guard. Creation
retains the legacy model writer and no-notification contract.
"""

from __future__ import annotations

from pathlib import Path

from ._model import _save_doc_unlocked, _store_lock
from ._store import _read_write_doc, _resolved_store, _utc_now_iso
from ._store_delivery import mutate_card, prepare_new_card
from ._store_errors import RevisionConflictError
from ._store_lifecycle import clear_completion_stamp
from ._store_single_card import read_card_or_raise
from ._store_target import resolve_store_target

#: Placeholder note when the caller passes no question text. Matches the
#: dotfiles hook's "no question captured" wording so the card reads the same
#: whether it was minted by the old hook or the new verb.
HELP_WAIT_PLACEHOLDER = "(agent is waiting on the operator; no question text captured)"


def _best_effort_hostname() -> str:
    """Resolve a hostname the way the dotfiles hook did (best-effort).

    ``socket.gethostname()`` is the portable POSIX probe; a bare exception
    (extremely rare — e.g. a sandbox with no name configured) degrades to
    ``"unknown"`` rather than blowing up the card.
    """
    import socket

    try:
        name = socket.gethostname()
    except Exception:  # pragma: no cover — extremely rare environments
        return "unknown"
    return name or "unknown"


def help_card_id(agent: str) -> str:
    """Canonical card id for an agent's help-wait card: ``help-<agent>-waiting``."""
    return f"help-{agent}-waiting"


def help_wait(
    store: str | Path | None = None,
    agent: str | None = None,
    *,
    question: str | None = None,
    host: str | None = None,
) -> dict:
    """UPSERT the canonical "agent is waiting on the operator" card.

    Idempotent: exactly ONE ``help-<agent>-waiting`` card per agent. A re-run
    refreshes ``note`` + ``last_activity`` (and ``host`` / status / blocker)
    in place rather than inserting a duplicate. Existing-card writes use a
    fresh canonical read and SQL revision compare-and-set without stale retries.

    ``agent`` is taken as already-sanitized by the caller (the hook owns
    sanitization); this only trims surrounding whitespace. Returns the
    upserted card mapping (a fresh dict).
    """
    agent = (agent or "").strip()
    if not agent:
        raise ValueError("help_wait: 'agent' is required and must be non-empty")
    note = question if (question and str(question).strip()) else HELP_WAIT_PLACEHOLDER
    host_eff = host if host else _best_effort_hostname()
    title = f"[help] {agent} waiting on operator decision"
    card_id = help_card_id(agent)
    resolved = _resolved_store(store)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    def refresh(existing: dict) -> None:
        if existing.get("status") == "done":
            clear_completion_stamp(existing)
        existing["title"] = title
        existing["status"] = "blocked"
        existing["blocker"] = "operator-decision"
        existing["assignee"] = agent
        existing["scope"] = f"agent:{agent}"
        existing["host"] = host_eff
        existing["note"] = note
        existing["last_activity"] = _utc_now_iso()

    existing, _revision = read_card_or_raise(resolve_store_target(None), card_id)
    if existing is not None:
        return mutate_card(store, card_id, refresh, operation="update", actor=agent)

    # Preserve the legacy creation path and its no-notification behavior.
    # Atomic expected-absence insertion remains an adapter activation prerequisite.
    with _store_lock(resolved):
        doc, tasks = _read_write_doc(resolved)
        if any(t.get("id") == card_id for t in tasks):
            raise RevisionConflictError(card_id, -1, None)
        stamp = _utc_now_iso()
        new = {
            "id": card_id,
            "title": title,
            "status": "blocked",
            "blocker": "operator-decision",
            "assignee": agent,
            "scope": f"agent:{agent}",
            "host": host_eff,
            "note": note,
            "created_at": stamp,
            "last_activity": stamp,
        }
        new = prepare_new_card(new, operation="add")
        tasks.append(new)
        _save_doc_unlocked(doc, resolved, tasks=tasks, touched_ids=[card_id])
        return dict(new)


def help_clear(
    store: str | Path | None = None,
    agent: str | None = None,
) -> dict:
    """Resolve the agent's ``help-<agent>-waiting`` card (status=done, clear blocker).

    No-op when the card does not exist — returns ``{"task_id": <id>,
    "cleared": False}`` rather than raising, so the thin hook trigger can call
    this unconditionally (the operator may have already resolved the card on
    the board). When the card IS present it flips ``status`` to ``done`` and
    drops the ``blocker`` field (same in-place mutation contract as the board
    Resolve button).
    """
    agent = (agent or "").strip()
    if not agent:
        raise ValueError("help_clear: 'agent' is required and must be non-empty")
    card_id = help_card_id(agent)
    target, _revision = read_card_or_raise(resolve_store_target(None), card_id)
    if target is None:
        return {"task_id": card_id, "cleared": False}

    def clear(task: dict) -> None:
        task["status"] = "done"
        task.pop("blocker", None)
        task["last_activity"] = _utc_now_iso()

    target = mutate_card(store, card_id, clear, operation="resolve", actor=agent)
    return {"task_id": card_id, "cleared": True, "task": target}


__all__ = [
    "HELP_WAIT_PLACEHOLDER",
    "help_card_id",
    "help_clear",
    "help_wait",
]

# EOF
