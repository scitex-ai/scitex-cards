#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side Python API for the scitex-todo task store.

THIN ORCHESTRATOR. The verbs themselves now live in focused siblings; this
module owns the SHARED helpers they all pull on (identity resolution, the
timestamp, the locked read-modify-write loader) and RE-EXPORTS the whole
public surface, so every historical import keeps working untouched —
``from ._store import add_task``, ``_store.complete_task(...)``,
``from ._store import _resolved_store`` / ``load_tasks``, the MCP tool table,
the CLI, the tests.

    _store_mutate      add_task / update_task (+ `_stamp_deferred_at`)
    _store_lifecycle   complete / resolve / reopen / reassign / delete / restore
    _store_comment     comment_task (the card's Issue-activity log)
    _store_relations   set_edge / set_collaborator / set_subscriber
    _store_events      the fail-soft card-event + unblock emit seams
    _store_list        the READ half: list_tasks / summarize_tasks / _match
    _store_write       the LOW-LEVEL persistence layer (lock / save)

Every mutation runs through :func:`_model.save_tasks`, which holds an
``fcntl.flock``-based mutex on a sibling lock file so two concurrent writers
cannot interleave (PHASE 1 prereq for the cross-host sync substrate — see
``GITIGNORED/ARCHITECTURE.md`` Req 2).

The filter functions are read-only and do NOT lock — they snapshot the
store via :func:`_model.load_tasks` and apply the filter in memory.

Design constraints
------------------
- **Generic** (Req 8): scope/assignee/status are free-form strings. The
  helpers don't know what an "agent" is.
- **Centralized** (Req 3): the default store is the SQLite database
  resolved by ``$SCITEX_CARDS_DB``; callers can override with an
  explicit ``store=`` path. The user-scope default
  (``~/.scitex/cards/cards.db``) covers Req 7.
- **Shared with scopes** (Req 1): ``$SCITEX_TODO_SCOPE`` provides the
  default value for ``list_tasks(scope=...)`` when the caller doesn't pass
  one explicitly. Pass ``scope=""`` (empty string) to ignore the env
  default and see everything.

C5 (card-event producers) wires the mutating verbs to ALSO emit a
canonical :class:`scitex_cards._events.Event` onto the hook bus, plus a
new atomic :func:`reassign_task` owner-change primitive. The mutation→
event mapping (each emit is ADDITIVE + FAIL-SOFT — the mutation persists
first, THEN we emit, and a raising/slow emit can never break or roll back
the write):

    add_task        → ``created``        {card_id, actor, ts}
    comment_task    → ``commented``      (IN ADDITION to the existing
                                          ``card-message`` dispatch)
    update_task     → ``status_changed`` ONLY when ``status`` actually
                      changes {extra:{from,to}} — a completion via
                      update_task(status="done") emits ``completed``
                      (see the completed-vs-status_changed rule below).
    complete_task   → ``completed``      {card_id, actor, ts}
    resolve_task    → ``status_changed`` {extra:{from,to:done}}
    reassign_task   → ``reassigned``     {extra:{from_owner,to_owner}}

Completed-vs-status_changed rule: a flip to ``done`` is modelled as a
single ``completed`` event (NOT also a ``status_changed``) to avoid
double-firing; every OTHER status flip is a ``status_changed``.

There is intentionally NO consumer wired here — delivery / notify is C4
(a separate card). ``reassign_task`` EMITS the ``reassigned`` event; it
does NOT deliver. NO ``sac`` import: this stays a pure producer that
reuses :func:`scitex_cards._events.emit` + the store's own lock/save
helpers.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from ._model import (  # noqa: F401  (see the re-export note below)
    VALID_STATUSES,
    StoreShrinkRefusedError,
    TaskValidationError,
    _save_doc_unlocked,
    _save_tasks_unlocked,
    _store_lock,
    load_doc,
    load_tasks,
    save_tasks,
)
from ._paths import resolve_tasks_path  # noqa: F401  (re-export)

# ^ `VALID_STATUSES`, `load_tasks` and `resolve_tasks_path` are no longer used INSIDE
# this module (they moved out with the read surface) — but they are part of `_store`'s
# de-facto public surface and other modules import them THROUGH it, e.g.
# `_cli/_stale.py`: `from .._store import load_tasks`. "Unused in this file" is not
# "unused". I pruned them once; the full suite caught it immediately. Do not prune
# them again — remove the re-export only together with its importers.
# The READ / QUERY surface now lives in `_store_list` — `list_tasks` /
# `summarize_tasks` / `_match` and the resolvers they need. RE-EXPORTED HERE so
# every existing caller keeps working untouched: `_store.list_tasks(...)`,
# `from ._store import _resolved_store`, the MCP tool table, the CLI, the tests.
#
# The split is not cosmetic. `_store` is the WRITE surface (add / update / complete
# / delete / comment, the locked read-modify-write cycle, enum gating, event
# emission); those two halves share nothing but a store path, and it is the READ
# half the whole fleet hits on every poll — 830 ms per call on the live board,
# every agent, forever. It has earned its own file.
from ._store_list import (  # noqa: F401  (re-export: preserve the public surface)
    ENV_SCOPE,
    _default_scope,
    _match,
    _resolved_store,
    list_tasks,
    summarize_tasks,
)

#: Env var name carrying the agent's identity. Used as the default
#: `completed_by` when :func:`complete_task` doesn't get an explicit `by=`.
ENV_AGENT = "SCITEX_TODO_AGENT_ID"

#: previous name of :data:`ENV_AGENT`. Renamed 2026-07-02. We fail LOUD (never
#: silently honour it) if it is still set, so a stale export can't quietly
#: mis-attribute a write — the operator must migrate to the new name.
ENV_AGENT_DEPRECATED = "SCITEX_TODO_AGENT"


def _reject_deprecated_agent_env() -> None:
    """Fail loud if the old ``SCITEX_TODO_AGENT`` var is still set.

    No silent fallback: a leftover export of the old name is a configuration
    error the operator must fix, not something we quietly translate.
    """
    if os.environ.get(ENV_AGENT_DEPRECATED) is not None:
        raise RuntimeError(
            f"{ENV_AGENT_DEPRECATED} was renamed to {ENV_AGENT}; "
            f"unset the old var (it is no longer honoured)."
        )


class TaskNotFoundError(KeyError):
    """Raised when an update/complete target id is not in the store."""


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _default_agent(arg: str | None) -> str:
    """Resolve an ACTOR/AUTHOR — FAIL LOUD when it cannot be resolved.

    Precedence: an explicit ``by=``/``actor`` arg → ``$SCITEX_TODO_AGENT_ID``.
    Deliberately does NOT fall back to ``getpass.getuser()`` / ``"unknown"``
    (the former lenient chain): the operator mandate (constitution rule 2
    "fail fast and fail loud, NO silent fallbacks") requires completion /
    comment authorship to record a REAL acting agent, never a blank or
    ``"unknown"`` placeholder that mis-attributes the action on the board.

    Now identical in behaviour to :func:`_resolve_creator_or_raise` — it
    simply delegates to keep a single source of truth (DRY) while preserving
    this public name for the completion/comment callers.

    Raises
    ------
    TaskValidationError
        When the actor resolves to empty or the ``"unknown"`` sentinel, with
        an ACTIONABLE hint naming both fixes.
    """
    return _resolve_creator_or_raise(arg)


def _resolve_creator_or_raise(arg: str | None) -> str:
    """Resolve a card CREATOR — FAIL LOUD when it cannot be resolved.

    Precedence: an explicit ``created_by``/``by=`` arg → ``$SCITEX_TODO_AGENT_ID``.
    Deliberately does NOT fall back to ``getpass.getuser()`` / ``"unknown"``:
    the operator mandate (constitution rule 2 "fail fast and fail loud, NO
    silent fallbacks") requires a card to record a REAL creator, never a blank
    or ``"unknown"`` placeholder. A card whose creator can't be resolved must
    not be born. This is the SSOT resolver — :func:`_default_agent` (actor /
    author for completion & comments) now delegates here so both share the
    identical fail-loud behaviour.

    Raises
    ------
    RuntimeError
        When the deprecated ``$SCITEX_TODO_AGENT`` is still exported (renamed
        away — see :func:`_reject_deprecated_agent_env`).
    TaskValidationError
        When the creator resolves to empty or the ``"unknown"`` sentinel,
        with an ACTIONABLE hint naming both fixes.
    """
    _reject_deprecated_agent_env()
    resolved = (arg or os.environ.get(ENV_AGENT) or "").strip()
    if not resolved or resolved == "unknown":
        raise TaskValidationError(
            "creator unresolved — set SCITEX_TODO_AGENT_ID=<your-agent> or pass "
            "created_by=/by= (creator+assignee are mandatory; no silent "
            "fallback to a blank/'unknown' creator; see constitution)."
        )
    return resolved


# THE ONE FAIL-LOUD READER now lives in its own module — the guard, the
# incident history behind each of its checks, and its tests belong
# together rather than buried among the mutation helpers. Re-exported
# here so every historical `from ._store import _read_canonical_db_or_raise`
# keeps working, and so there is still exactly ONE reader shared by the
# read door, the write door and `_model.load_doc`.
from ._store_canonical_read import (  # noqa: F401  (re-export)
    _read_canonical_db_or_raise,
)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution and the ``Z`` suffix.

    Trims the microseconds (the operator reads these on the board; second
    resolution is plenty) and uses the canonical ``Z`` suffix rather than
    ``+00:00`` so the string round-trips losslessly through YAML.
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --------------------------------------------------------------------------- #
# Read-modify-write helper                                                     #
# --------------------------------------------------------------------------- #
def _read_write_doc(path: str | Path) -> tuple[dict, list]:
    """Load the FULL store doc ONCE for a locked read-modify-write cycle.

    Returns ``(doc, tasks)`` where ``tasks is doc["tasks"]`` (always a list).
    Callers mutate ``tasks`` (or rebind it) then persist via
    ``_save_doc_unlocked(doc, path, tasks=tasks)``, so this one read serves
    BOTH the mutated ``tasks`` payload AND the non-``tasks`` sections
    (``users:`` etc.) that must survive the rewrite.

    THE ``missing_ok`` PARAMETER IS GONE, and its removal is the safety
    property rather than a tidy-up. It used to mean "an absent store yields an
    empty doc instead of raising", which was reasonable when the store was a
    file that a fresh install legitimately lacked. Against a database it is a
    loaded gun: an empty doc flows into a read-modify-write, the caller appends
    its one new card, and ``mirror_doc_incremental`` diffs that one-card
    document against the DB and DELETES every card missing from it.

    Measured on a scratch store during this cutover: five sequential writes
    left exactly ONE row each time. On the live board that is 2065 cards down
    to 1, silently, with nothing raised anywhere in the stack. Found by
    round-tripping real writes, not by reading the diff — the write path looked
    correct in isolation and only end-to-end exercise showed the loss.

    So there is no "absent store" case to be tolerant about: a missing database
    is a configuration error and :func:`_read_canonical_db_or_raise` says so.
    Emptiness must never be inferred; it must be read.
    """
    doc = _read_canonical_db_or_raise()
    return doc, doc["tasks"]


# --------------------------------------------------------------------------- #
# Public API (kept here)                                                       #
# --------------------------------------------------------------------------- #
def resolve_store(store: str | Path | None = None) -> dict:
    """Return the resolved task store path and the precedence chain.

    Mirrors the data the `scitex-todo resolve-store` CLI verb and the
    `resolve_store` MCP tool emit. Keeping a Python API by the same name
    as the MCP tool satisfies audit §6 (Convention A: tool_name == api_name).

    Output shape::

        {
          "resolved":         "/abs/path/to/cards.db",
          "explicit":         <the `store` arg you passed, or None>,
          "db_env":           <value of $SCITEX_CARDS_DB, or None>,
          "user_store":       "/abs/path/to/~/.scitex/cards/cards.db",
          "pkg_short":        "cards",
          "exists":           bool,
        }
    """
    import os

    from ._db import DEFAULT_DB_FILENAME, ENV_DB, resolve_db_path
    from ._paths import PKG_SHORT, _user_root

    # The resolved store is the DATABASE — the sole store identity.
    resolved = resolve_db_path(
        store if isinstance(store, (str, type(None))) else str(store)
    )
    return {
        "resolved": str(resolved),
        "explicit": str(store) if store is not None else None,
        "db_env": os.environ.get(ENV_DB),
        "user_store": str(_user_root() / DEFAULT_DB_FILENAME),
        "pkg_short": PKG_SHORT,
        "exists": Path(resolved).exists(),
    }


def get_task(
    store: str | Path | None = None,
    task_id: str | None = None,
) -> dict:
    """Return a single task by id, or raise ``TaskNotFoundError``.

    Companion to ``add_task`` / ``update_task`` / ``list_tasks`` — the
    natural "read one" verb every CRUD surface expects but the Python
    API was missing (PR #56 audit gap). The MCP wrapper exposes this as
    ``get_task`` per Convention A.

    A TOMBSTONED row (see :func:`scitex_cards._task._is_tombstoned`) is
    treated as NOT FOUND — the 2026-07-21 tombstone change keeps a
    deleted card's row on disk forever, but this read must behave exactly
    as it did when ``delete_task`` physically removed it.
    """
    from . import _model, _task

    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("get_task: 'task_id' is required")
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        for t in tasks:
            if t.get("id") == task_id and not _task._is_tombstoned(t):
                return dict(t)
    raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")


# --------------------------------------------------------------------------- #
# RE-EXPORTS — the moved verbs (PURE MOVE; this stays their import site)       #
# --------------------------------------------------------------------------- #
# Imported at the BOTTOM, after the shared helpers above are defined: the moved
# modules pull `_read_write_doc` / `_utc_now_iso` / `_default_agent` /
# `TaskNotFoundError` back OUT of this module (deferred, inside their function
# bodies), so the helpers must exist by the time any of them RUNS. A split that
# does not re-export is a rename with extra steps — every caller and test uses
# `from ._store import <verb>` / `_store.<verb>`, and those must keep resolving.
from ._store_comment import comment_task  # noqa: E402,F401  (re-export)
from ._store_events import (  # noqa: E402,F401  (re-export)
    _emit_card_event,
    _emit_unblock_for_dependents,
)
from ._store_lifecycle import (  # noqa: E402,F401  (re-export)
    complete_task,
    delete_task,
    reassign_task,
    reopen_task,
    resolve_task,
    restore_task,
)
from ._store_mutate import (  # noqa: E402,F401  (re-export)
    _stamp_deferred_at,
    _wip_statuses,
    add_task,
    update_task,
)
from ._store_reassign import reassign_all  # noqa: E402,F401  (re-export)
from ._store_relations import (  # noqa: E402,F401  (re-export)
    _set_list_member,
    set_collaborator,
    set_edge,
    set_subscriber,
)
from ._store_rescore import rescore_task  # noqa: E402,F401  (re-export)

__all__ = [
    "ENV_AGENT",
    "ENV_SCOPE",
    "StoreShrinkRefusedError",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reassign_all",
    "reassign_task",
    "reopen_task",
    "rescore_task",
    "resolve_store",
    "resolve_task",
    "restore_task",
    "set_collaborator",
    "set_edge",
    "set_subscriber",
    "summarize_tasks",
    "update_task",
]

# EOF
