#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The store's READ / QUERY surface: ``list_tasks``, ``summarize_tasks``, ``_match``.

Extracted from :mod:`scitex_cards._store`, which had grown to 1,645 lines around two
unrelated jobs — the WRITE surface (add / update / complete / delete / comment, the
locked read-modify-write cycle, enum gating, event emission) and this, the read
surface. They share nothing but a store path, and the read surface is the half that
the fleet hits on every poll. ``_store`` re-exports every name defined here, so no
caller had to move.

SQLite IS the store (see :mod:`scitex_cards._store_backend`) — there is no other
backend and no way to select one. :func:`list_tasks` always reads through
:func:`scitex_cards._model.load_tasks`, which reads the canonical database via
:func:`scitex_cards._store._read_canonical_db_or_raise`. An unresolvable or
unreadable store RAISES with an actionable message; there is no YAML chain and no
bundled example left to fall back to (both were deleted 2026-07-19/21).

This module used to also dispatch to a second, SQLite-indexed read path
(``_store_read_sqlite`` — S2) when a set of runtime checks passed, and fell back to
this Python-predicate path otherwise. That accelerator is DELETED (2026-07-21
incident): now that SQLite is canonical rather than a mirror, its freshness guard
compared the DB's provenance stamp against a YAML file that no longer exists, so it
refused to serve and fell back — and that fallback resolved to an empty bundled
example, silently serving a blank board while claiming reads were merely slow. A
mirror that can never again pass its own freshness check is not a slow path, it is
dead code that fails dangerous. One read path, always exercised, always through the
same ownership-guarded reader.
"""

from __future__ import annotations

import os
from pathlib import Path

from ._model import VALID_STATUSES, load_tasks
from ._paths import resolve_tasks_path
from ._task import _is_tombstoned

#: Env var an agent sets to scope its default `list_tasks` / `summary` view. The
#: CLI's `--scope` flag overrides this; pass `scope=""` in the Python API to see
#: the unfiltered store.
ENV_SCOPE = "SCITEX_TODO_SCOPE"


def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path argument through the precedence chain.

    ``None`` ⇒ apply the full resolution chain (`_paths.resolve_tasks_path`).
    Explicit path ⇒ used as-is (must exist for reads; will be created for
    fresh writes by :func:`_model.save_tasks`).
    """
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


def _default_scope(arg: str | None) -> str | None:
    """Resolve a scope argument, honoring ``$SCITEX_TODO_SCOPE`` as the default.

    ``None`` (caller didn't pass anything) → env var if set, else ``None``
    (no filter).
    Empty string ``""`` → caller explicitly opted out of filtering.
    Non-empty string → used as-is.
    """
    if arg is None:
        env = os.environ.get(ENV_SCOPE)
        return env if env else None
    if arg == "":
        return None
    return arg


def _match(
    task: dict,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    repo: str | None = None,  # hook-bypass: line-limit
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> bool:
    """String-equality + predicate filter. ``None`` / empty = no constraint.

    Filter semantics (per PR #66 / ADR-0008 D2 + D10):
      - ``status`` (single) and ``statuses`` (multi) are OR-combined: a
        row matches if its status is in ``set([status]) ∪ set(statuses)``
        (after dropping ``None``).
      - ``blocker="__none"`` matches rows with no blocker field (the
        explicit "no blocker named" filter the board's `/graph` uses).
      - ``kind=None`` is NO filter; ``kind="task"`` matches both
        explicit ``"task"`` AND ``absent`` rows (since absent ≡ "task"
        per ADR-0002).
      - ``id_prefix`` is a substring match on the front of ``id`` —
        the cheap "find my project's rows" without remembering exact
        ids.
      - ``blocking_me`` is the BLOCKING-YOU predicate (board-v3 panel):
        ``status == "blocked" AND blocker == "operator-decision"``.
        Composes with the other filters via AND.
      - a TOMBSTONED row (see :func:`scitex_cards._task._is_tombstoned`)
        never matches, regardless of any other filter — see the 2026-07-21
        tombstone change: ``delete_task`` keeps the row on disk forever, so
        board reads must keep excluding it exactly as when it was
        physically removed.

    """
    if _is_tombstoned(task):
        return False
    if scope is not None and task.get("scope") != scope:
        return False
    if assignee is not None and task.get("assignee") != assignee:
        return False
    if agent is not None and task.get("agent") != agent:
        return False
    if project is not None and task.get("project") != project:
        return False
    if host is not None and task.get("host") != host:
        return False
    if repo is not None and task.get("repo") != repo:  # hook-bypass: line-limit
        return False
    if blocker is not None:
        if blocker == "__none":
            if task.get("blocker"):
                return False
        elif task.get("blocker") != blocker:
            return False
    if kind is not None:
        eff = task.get("kind") or "task"
        if eff != kind:
            return False
    if id_prefix and not str(task.get("id", "")).startswith(id_prefix):
        return False
    # Union the single + multi status constraints. None and empty list
    # collapse to "no constraint." When BOTH are provided, the union is
    # checked (so callers can extend an existing single-status default).
    allowed: set[str] = set()
    if status is not None:
        allowed.add(status)
    if statuses:
        allowed.update(statuses)
    if allowed and task.get("status") not in allowed:
        return False
    if blocking_me and not (
        task.get("status") == "blocked" and task.get("blocker") == "operator-decision"
    ):
        return False
    if overdue:
        from ._model import is_overdue as _is_overdue

        if not _is_overdue(task):
            return False
    return True


def list_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    # PR #66 additions per ADR-0008 D2 / D10:
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    repo: str | None = None,  # hook-bypass: line-limit
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> list[dict]:
    """Snapshot the store, then filter by any combination of fields.

    Filter semantics:

    - ``scope=None`` (default): use ``$SCITEX_TODO_SCOPE`` if set, else
      no filter. ``scope=""`` opts out of the env default explicitly.
    - ``assignee`` / ``agent`` / ``project`` / ``host`` / ``repo`` /
      ``status``: ``None`` = no filter; any string = exact match.
      (Generic Req 8 — no fuzzy / glob; callers compose.) ``repo`` matches
      the card's ``repo`` field (``owner/repo``) — the reusable seam a
      producer uses to resolve repo->card at emit time (find-card verb).
      (hook-bypass: line-limit)
    - ``statuses`` (list) AND ``status`` (single) are OR-combined.
    - ``blocker="__none"`` matches rows with no blocker field; any other
      value is an exact match (closed-enum gating at the CLI layer).
    - ``kind="task"`` matches both explicit ``"task"`` AND absent rows
      (since absent ≡ ``"task"`` per ADR-0002).
    - ``id_prefix`` matches the front of ``id`` (cheap project-rollup
      lookup without exact id).
    - ``blocking_me=True`` is the board's BLOCKING-YOU predicate
      (``status == "blocked" AND blocker == "operator-decision"``);
      composes with the other filters via AND.

    The returned list contains fresh dicts, safe to mutate without
    affecting the on-disk store (no save here).

    Reads always go through :func:`scitex_cards._model.load_tasks`, which reads the
    ONE canonical SQLite database and raises rather than returning an empty or
    stale document when the store cannot be resolved (see the module docstring —
    the S2 SQLite-indexed accelerator that used to dispatch here is deleted).
    """
    resolved = _resolved_store(store)
    scope_eff = _default_scope(scope)
    tasks = load_tasks(resolved)
    return [
        dict(t)
        for t in tasks
        if _match(
            t,
            scope=scope_eff,
            assignee=assignee,
            status=status,
            statuses=statuses,
            agent=agent,
            project=project,
            host=host,
            repo=repo,  # hook-bypass: line-limit
            blocker=blocker,
            kind=kind,
            id_prefix=id_prefix,
            blocking_me=blocking_me,
            overdue=overdue,
        )
    ]


def summarize_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
) -> dict:
    """Return numeric progress counts grouped by status, scope, assignee.

    Output shape (always present keys):

    ::

        {
          "store": "/abs/path/to/cards.db",
          "total": int,
          "by_status": {<status>: int, ...},  # one key per VALID_STATUSES
          "by_scope": {<scope|"">: int, ...},
          "by_assignee": {<assignee|"">: int, ...},
        }

    Tasks with no scope / assignee bucket under the empty string ``""``.
    The ``by_status`` map is densified to all :data:`VALID_STATUSES` so
    consumers (web UI, progress widgets) don't have to special-case
    zero-count keys.

    Still YAML-only, ON PURPOSE. It could be a handful of ``GROUP BY`` queries, and
    that is precisely the temptation to resist in this PR: a second aggregation
    written in SQL is a second implementation to keep in step with this one, and it
    is not covered by the equality proof that makes :func:`list_tasks` safe to
    switch. One path at a time, each proven identical before the next.
    """
    resolved = _resolved_store(store)
    tasks = load_tasks(resolved)
    scope_eff = _default_scope(scope)
    by_status: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    by_scope: dict[str, int] = {}
    by_assignee: dict[str, int] = {}
    total = 0
    for task in tasks:
        if not _match(task, scope=scope_eff, assignee=assignee, status=None):
            continue
        total += 1
        st = task.get("status")
        if st in by_status:
            by_status[st] += 1
        sc = task.get("scope") or ""
        by_scope[sc] = by_scope.get(sc, 0) + 1
        asg = task.get("assignee") or ""
        by_assignee[asg] = by_assignee.get(asg, 0) + 1
    return {
        "store": str(resolved),
        "total": total,
        "by_status": by_status,
        "by_scope": by_scope,
        "by_assignee": by_assignee,
    }


__all__ = [
    "ENV_SCOPE",
    "_default_scope",
    "_match",
    "_resolved_store",
    "list_tasks",
    "summarize_tasks",
]

# EOF
