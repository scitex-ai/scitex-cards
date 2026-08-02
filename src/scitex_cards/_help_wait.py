#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Help-wait / help-clear card semantics — the "agent is stuck waiting on the
operator" card as a first-class verb pair.

Background (SoC lift)
---------------------
A dotfiles Notification hook used to hand-roll these cards by shelling out to
the generic ``scitex-todo add / update / list-tasks`` verbs. Whenever the card
schema drifted, the hook broke *silently*. Lifting the semantics into the
package makes scitex-todo the single source of truth; the hook becomes a thin
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

The functions REUSE the package's model store I/O (``load_tasks`` /
``_save_tasks_unlocked`` / ``_store_lock``) so comment / key-order
round-tripping and validation are preserved — no bespoke YAML I/O here.
"""

from __future__ import annotations

from pathlib import Path

from ._model import _save_tasks_unlocked, _store_lock, load_tasks
from ._store import _resolved_store, _utc_now_iso

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
    in place rather than inserting a duplicate. The whole read-decide-write
    runs under the store lock so two concurrent callers can't both miss the
    existing card and double-insert.

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

    # Lock the full read-modify-write so a concurrent caller can't also
    # observe "no card" and double-insert. Doing the existence decision under
    # one lock makes the upsert atomic.
    with _store_lock(resolved):
        # Read the canonical DB unconditionally. The old `if resolved.exists()
        # else []` gated on the YAML store PATH (never a real file under
        # SQLite), so this always read [] — re-inserting (regenerating
        # created_at) instead of upserting, AND writing back only the new card,
        # which diff-deleted every OTHER agent's cards. load_tasks reads the DB.
        tasks = load_tasks(resolved)
        existing = next((t for t in tasks if t.get("id") == card_id), None)
        if existing is not None:
            existing["title"] = title
            existing["status"] = "blocked"
            existing["blocker"] = "operator-decision"
            existing["assignee"] = agent
            existing["scope"] = f"agent:{agent}"
            existing["host"] = host_eff
            existing["note"] = note
            existing["last_activity"] = _utc_now_iso()
            _save_tasks_unlocked(tasks, resolved)
            return dict(existing)
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
        tasks.append(new)
        _save_tasks_unlocked(tasks, resolved)
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
    resolved = _resolved_store(store)
    # No YAML-file existence gate: the store is the canonical DB. load_tasks
    # reads it; an absent card still yields cleared=False below (target None).
    # The old `if not resolved.exists()` made help_clear a permanent no-op
    # under SQLite (the store path is never a real file).
    with _store_lock(resolved):
        tasks = load_tasks(resolved)
        target = next((t for t in tasks if t.get("id") == card_id), None)
        if target is None:
            return {"task_id": card_id, "cleared": False}
        target["status"] = "done"
        target.pop("blocker", None)
        target["last_activity"] = _utc_now_iso()
        _save_tasks_unlocked(tasks, resolved)
        return {"task_id": card_id, "cleared": True, "task": dict(target)}


__all__ = [
    "HELP_WAIT_PLACEHOLDER",
    "help_card_id",
    "help_clear",
    "help_wait",
]

# EOF
