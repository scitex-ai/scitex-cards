#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-instance ``flock`` guard for side-effecting one-shot CLI runs.

A small, reusable mirror of the wake-watcher's process-level lock
(:func:`scitex_cards._wake_watcher.acquire_single_instance_lock`, PR #344).
The wake-watcher guards a long-lived polling LOOP; this guards a one-shot
CLI invocation that is re-launched on a schedule.

Motivation (incident-todo-wake-watcher-interval2-spiral-20260708, third
store-size daemon):

  The managed notify cron runs ``scitex-todo print-stats --by agent
  --notify --nudge-quiet`` every 10 minutes. ``print-stats --by agent``
  re-derives per-agent rollups from all ~930 cards in the ~9 MB
  store. When a single run exceeds the 10-min period it OVERLAPS
  the next cron tick, so runs STACK (observed: 2 concurrent at ~63% CPU
  each) — the cron/one-shot analogue of the wake-watcher death-spiral (#344)
  and the MCP inbox-drain spin (#345). Same store-size root; the durable
  fix is archival, but overlapping runs need a stacking guard NOW.

Contract (identical flock idiom to #344):

  * NON-BLOCKING ``flock(LOCK_EX | LOCK_NB)`` — a run that finds the lock
    already held (a prior run still going) does NOT wait; the caller skips
    the tick cleanly. A skipped nudge tick is fine; the next tick runs.
  * Released on context exit AND automatically on process death, so a
    crashed run never wedges the lock.
  * Guards ONLY the side-effecting path. A plain, read-only ``print-stats``
    (no ``--notify``) never takes or is blocked by the lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
from pathlib import Path
from typing import Iterator, Optional, TextIO

from ._paths import runtime_dir

logger = logging.getLogger(__name__)

#: Lockfile basename for the ``print-stats --notify`` single-instance guard.
NOTIFY_LOCK_NAME = "print-stats-notify.lock"


def _acquire(lock_path: Path) -> Optional[TextIO]:
    """Take an exclusive, NON-BLOCKING ``flock`` on ``lock_path``.

    Returns the held file object on success (the caller MUST keep it alive
    for the guarded region — closing it releases the lock) or ``None`` when
    another process already holds it.
    """
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("w", encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("single-instance: cannot open lockfile %s: %s", lock_path, exc)
        return None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Held by another process — do NOT block.
        handle.close()
        return None
    try:
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:  # pragma: no cover - defensive
        pass
    return handle


def notify_lock_path(tasks_path: str | Path | None = None) -> Path:
    """Lockfile path for the ``print-stats --notify`` single-instance guard.

    Resolved under the store's runtime dir (``<store>/runtime``) via
    :func:`scitex_cards._paths.runtime_dir`, the same resolver the delivery
    ledger / pidfiles use — so the lock tracks whichever store scope the run
    resolved to (and a test that passes ``--tasks`` gets a deterministic,
    isolated lockfile).
    """
    return runtime_dir(tasks_path) / NOTIFY_LOCK_NAME


@contextlib.contextmanager
def single_instance(lock_path: str | Path) -> Iterator[bool]:
    """Context manager for a single-instance ``flock``.

    Yields ``True`` when the exclusive lock was acquired (the caller may do
    its side-effecting work) or ``False`` when a prior holder still has it
    (the caller should skip). Releases the lock on exit; a process death
    releases it automatically.

    Example
    -------
    >>> with single_instance(notify_lock_path()) as acquired:  # doctest: +SKIP
    ...     if not acquired:
    ...         return  # a prior run is still going — skip this tick
    ...     do_side_effecting_work()
    """
    handle = _acquire(Path(lock_path).expanduser())
    try:
        yield handle is not None
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:  # pragma: no cover - defensive
                pass


__all__ = [
    "NOTIFY_LOCK_NAME",
    "notify_lock_path",
    "single_instance",
]

# EOF
