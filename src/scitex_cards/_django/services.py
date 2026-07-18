#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board service -- resolve + load the task store with a small mtime cache.

Mirrors figrecipe's ``services.get_or_create_editor`` shape but read-only: the
board never mutates the store in this MVP, so the "board" is just the validated
task list plus its resolved path and mtime. The cache avoids re-reading the
YAML on every poll while still picking up external edits (cache is keyed by
path and invalidated when the file's mtime changes).

Per-project lane UNION (operator-validated requirement, lead a2a
`1ceec0ef` + `40c0a42d`, 2026-06-13)
-------------------------------------
Skill 30 describes a two-tier model: a global user-scope store
PLUS hand-curated per-project lanes (``~/proj/<repo>/.scitex/todo/
tasks.yaml``). Pre-this-change the loader resolved to ONE store at a
time, so the operator's nv-lessons (and 31 other neurovista cards
that live in ``~/proj/neurovista/.scitex/todo/tasks.yaml``) were
INVISIBLE on the board.

The board's task set is now the UNION of:
  - the global store (resolved by :func:`resolve_tasks_path`), AND
  - every per-project lane discovered by globbing
    :data:`SCITEX_TODO_LANE_GLOBS` (default
    ``~/proj/*/.scitex/todo/tasks.yaml``).

Collision policy: **project-lane wins** on duplicate ids. Rationale:
human hand-curation beats agent auto-write. Each collision is logged
(``logger.warning``) so silent overrides are visible.

Resilience: a malformed per-project lane is SKIPPED + LOGGED — the
board still renders the rest. Per-lane crash-loud, never whole-view.
"""

import glob
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# In-process cache: store_path_str -> (BoardState, last_access_time)
_board_cache: Dict[str, Tuple["BoardState", float]] = {}
_CACHE_TTL_SECONDS = 3_600  # 1 hour

#: Env override for the per-project lane discovery glob. Comma-separated.
#: Default ``~/proj/*/.scitex/todo/tasks.yaml`` covers the operator's
#: layout; other hosts can override (e.g. ``~/work/*/.scitex/todo/
#: tasks.yaml``) without code change.
ENV_LANE_GLOBS = "SCITEX_TODO_LANE_GLOBS"
DEFAULT_LANE_GLOBS = "~/proj/*/.scitex/todo/tasks.yaml"


@dataclass
class BoardState:
    """A resolved, validated task store snapshot.

    The ``tasks`` list is the UNION of the global store + every
    discovered per-project lane. ``store_path`` is the global store
    path (the resolution anchor); ``mtime`` is the MAX mtime across
    every source so the cache invalidates whenever ANY lane writes.
    """

    tasks: list
    store_path: Path
    mtime: float
    # P10 (lead a2a 2026-06-12) — user-defined project clusters loaded
    # from the same YAML store. Empty list when the store has no
    # ``groups:`` key (back-compat). See :mod:`scitex_cards._groups`.
    groups: list = None  # type: ignore[assignment]
    #: Paths of every per-project lane successfully unioned. Empty when
    #: no lanes were discovered or all were skipped. Useful for the FE
    #: footer ("loaded N lanes") + the test harness.
    lane_paths: List[Path] = field(default_factory=list)


def _discover_lanes() -> List[Path]:
    """Return the list of per-project lane paths to union into the board.

    Honors :data:`ENV_LANE_GLOBS` (comma-separated) when set; otherwise
    uses :data:`DEFAULT_LANE_GLOBS`. Each entry is expanded via
    ``Path.expanduser`` and then ``glob.glob`` so ``~`` works as
    expected and `*` matches per-repo dirs. Non-existent / non-file
    entries are filtered out silently (a never-created lane is not a
    failure — it just isn't a source).
    """
    raw = os.environ.get(ENV_LANE_GLOBS)
    if raw is None:
        raw = DEFAULT_LANE_GLOBS
    # An explicitly-empty env opts out of lane discovery entirely
    # (useful for tests + for hosts that don't want the rollup).
    patterns = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Path] = []
    for pattern in patterns:
        expanded = str(Path(pattern).expanduser())
        for match in glob.glob(expanded):
            mpath = Path(match)
            if mpath.is_file():
                out.append(mpath)
    return sorted(set(out))


def _load_lane_safe(path: Path):
    """Load one per-project lane; return ``(tasks, mtime)`` on success,
    ``(None, None)`` on failure (logged at WARNING — never raise).

    Per-lane crash-loud, never whole-view: a single malformed YAML in
    one repo must not blank the operator's board.
    """
    from scitex_cards._model import load_tasks
    try:
        tasks = load_tasks(path)
        mtime = path.stat().st_mtime
        return tasks, mtime
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        logger.warning(
            "[scitex-todo] skipping malformed per-project lane %s: %s",
            path, exc,
        )
        return None, None


def _union_tasks(global_tasks: list, lane_tasks_by_path: Dict[Path, list]) -> list:
    """Union global + per-project lanes; project-lane wins on id collision.

    Returns a fresh list, leftmost-first by id so the FE's deterministic
    ordering doesn't shuffle on every load. Logs every collision so
    silent overrides are visible.
    """
    seen: Dict[str, dict] = {}
    seen_origin: Dict[str, str] = {}
    # Seed with global first.
    for t in global_tasks:
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid:
            continue
        seen[tid] = t
        seen_origin[tid] = "global"
    # Lane order is sorted (see _discover_lanes), so the iteration is
    # deterministic. Project lane wins on collision; LOG the override.
    for lane_path, lane_tasks in lane_tasks_by_path.items():
        for t in lane_tasks:
            tid = t.get("id") if isinstance(t, dict) else None
            if not tid:
                continue
            if tid in seen:
                logger.warning(
                    "[scitex-todo] id %r collision — %s overrides %s",
                    tid, lane_path, seen_origin[tid],
                )
            seen[tid] = t
            seen_origin[tid] = str(lane_path)
    return list(seen.values())


def get_board(tasks_path: Optional[str] = None) -> BoardState:
    """Resolve the task store, load + validate it, and cache by mtime.

    Returns a :class:`BoardState` whose ``tasks`` field is the UNION of
    the global store and every per-project lane discovered by
    :func:`_discover_lanes`. Collision policy: project-lane wins (logged).
    ``mtime`` is MAX across every source so the cache invalidates
    whenever ANY source's YAML rolls forward.

    Parameters
    ----------
    tasks_path : str or None
        Optional explicit store path. When ``None``, the standard
        project -> user -> bundled resolution chain is used. The explicit
        path becomes the GLOBAL source; lanes are still discovered + unioned.

    Returns
    -------
    BoardState
        The validated, unioned task list + the global-store anchor +
        the per-source MAX mtime + the lane paths actually consumed.
    """
    from scitex_cards._groups import load_groups
    from scitex_cards._model import load_tasks
    from scitex_cards._paths import resolve_tasks_path

    _cleanup_expired()

    resolved = resolve_tasks_path(tasks_path)
    global_mtime = resolved.stat().st_mtime if resolved.exists() else 0.0

    # Discover + load every per-project lane. Build a stable lane-mtime
    # dict so the cache key reflects the WHOLE source set.
    discovered = _discover_lanes()
    lane_tasks_by_path: Dict[Path, list] = {}
    lane_mtimes: List[float] = []
    successful_lanes: List[Path] = []
    for lane_path in discovered:
        tasks, mt = _load_lane_safe(lane_path)
        if tasks is None:
            continue
        lane_tasks_by_path[lane_path] = tasks
        lane_mtimes.append(mt)
        successful_lanes.append(lane_path)

    # Effective mtime = MAX(global, *lanes). Any source's write rolls
    # the cache forward.
    effective_mtime = max([global_mtime] + lane_mtimes) if lane_mtimes else global_mtime

    # Cache key is the GLOBAL path; the lane set is implicit (any lane
    # change bumps effective_mtime, which the cache check sees).
    key = str(resolved)
    cached = _board_cache.get(key)
    if cached is not None:
        board, _ = cached
        if board.mtime == effective_mtime:
            _board_cache[key] = (board, time.time())
            return board

    global_tasks = load_tasks(resolved) if resolved.exists() else []
    unioned = _union_tasks(global_tasks, lane_tasks_by_path)

    # P10: load + validate groups against the GLOBAL store only.
    # Per-project lanes don't currently carry groups; future PR can
    # union ``groups:`` similarly if the operator requests it.
    #
    # HONEST EMPTY STATE (hub card hub-cards-board-data-404): a resolved
    # store path that does not exist yet is a LEGITIMATE fresh workspace
    # (0 tasks), not an error — every other read above already treats it
    # that way (`if resolved.exists() else ...`). Calling load_groups on
    # the absent file was the one leftover raise (FileNotFoundError →
    # api_dispatch 400 "No task store found.") that turned a brand-new
    # tenant's board into an error banner. Reads now render "no cards
    # yet"; writes keep their existing contracts (create makes the store
    # via the locked verbs' ``missing_ok=True``; update/comment/delete on
    # a missing id still 404 loudly).
    task_ids = {t["id"] for t in unioned if isinstance(t, dict) and t.get("id")}
    groups = load_groups(resolved, task_ids=task_ids) if resolved.exists() else []

    board = BoardState(
        tasks=unioned,
        store_path=resolved,
        mtime=effective_mtime,
        groups=groups,
        lane_paths=successful_lanes,
    )
    _board_cache[key] = (board, time.time())
    logger.info(
        "[scitex-todo] Loaded board from %s + %d lane(s) "
        "(%d total tasks, %d groups)",
        resolved, len(successful_lanes), len(unioned), len(groups),
    )
    return board


def _cleanup_expired() -> None:
    """Drop board snapshots untouched for longer than the TTL."""
    now = time.time()
    expired = [
        k for k, (_, ts) in _board_cache.items() if now - ts > _CACHE_TTL_SECONDS
    ]
    for k in expired:
        _board_cache.pop(k, None)


def _reset_cache() -> None:
    """Clear the cache (test hook)."""
    _board_cache.clear()


# EOF
