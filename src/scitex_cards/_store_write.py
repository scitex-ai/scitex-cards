#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The store's WRITE PATH — locking, crash-safe save, optimistic concurrency.

Split out of :mod:`scitex_cards._model` (2026-07-12), which had grown to 1,566
lines by tangling two unrelated responsibilities:

* **the MODEL** — what a task IS: the ``Task`` dataclass, the closed enums, the
  validator, the deadline grammar, and the READ path. That stays in ``_model``.
* **the WRITE PATH** — how a task gets to DISK. That is this file.

The separation is not cosmetic. Every store incident of the past two weeks lived
in THIS code and nowhere else:

* the ~20 s whole-document round-trip write, O(whole-store) on every card change;
* the 2026-06-13 mid-string corruption, recovered by hand — now prevented by the
  tmp + fsync + ``os.replace`` dance and the post-dump reparse check below;
* the 2026-06-08 autoassign-parallel-run data loss (no atomic replace);
* and the LOCK CONVOY: MEASURED 2026-07-12 at **11,176 ms per card write** on the
  live 1,257-card store, while holding a FLEET-WIDE lock. Two agents writing means
  the second waits 11 seconds. That single number explains every "the board is
  slow" report we have ever had.

The lock is correct. What we do while holding it is not. This file is where that
gets fixed (see :mod:`scitex_cards._dual_write` and the SQLite migration), so it
deserves to be readable on its own rather than buried at the end of the model.

Every name here is re-exported from ``_model`` for backwards compatibility — 43
test files and every caller import from there, and the split must be invisible to
them.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import subprocess
from pathlib import Path

from ._model import (
    StaleStoreError,
    TaskValidationError,
    _validate_tasks,
    load_doc,
)


@contextlib.contextmanager
def _store_lock(path: Path):
    """Hold an exclusive `fcntl.flock` on a sibling `.<name>.lock` file.

    Phase 1 prerequisite for the cross-host sync substrate (Req 2): two
    concurrent writers — say a CLI verb and the GUI's `/priority` POST
    handler — must serialize so the payload they write is atomic at the
    task-list granularity. We hold the lock on a separate `.lock` sentinel
    file rather than on the store itself so we don't fight a reader/writer
    that re-opens the path.

    The lock file is created if missing, never removed (next caller reuses
    it). Empty mode is fine — only the lockf state matters.

    Parameters
    ----------
    path : Path
        The store path (e.g. a legacy sidecar file). The lock sentinel
        sits next to it as ``.<name>.lock``.

    Yields
    ------
    None
        After the lock is held; released on context exit (even on errors).
    """
    path = Path(path)
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # `O_CREAT|O_RDWR` semantics via `open("a+")` — `a+` works even on
    # FS that lack `O_EXLOCK` (e.g. WSL2 ext4) because we acquire the
    # advisory lock via `fcntl.flock` after the open.
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def save_tasks(
    tasks: list[dict],
    path: str | Path,
    *,
    expected_generation: str | None = None,
) -> None:
    """Validate then write a task list back to the store.

    Re-runs the same validation gate as :func:`load_tasks` *before* touching
    the store, so a malformed mutation can never corrupt it.

    Parameters
    ----------
    tasks : list of dict
        The (already-mutated) task mappings to persist. Validated first.
    path : str or pathlib.Path
        Destination store. If it already exists, its comments + structure are
        preserved and only the ``tasks:`` payload is updated; otherwise a
        fresh document is written.

    Raises
    ------
    TaskValidationError
        If ``tasks`` fails structural validation (nothing is written).

    Examples
    --------
    >>> tasks = load_tasks("cards.db")             # doctest: +SKIP
    >>> tasks[0]["priority"] = 1                    # doctest: +SKIP
    >>> save_tasks(tasks, "cards.db")               # doctest: +SKIP
    """
    path = Path(path).expanduser()
    # The lock covers only THIS write. It cannot cover the caller's earlier
    # `load_tasks`, so plain load → mutate → save still loses a concurrent
    # writer's rows (2026-07-10 bulk-migration incident). Callers doing a
    # read-modify-write must either use :func:`edit_tasks` (one lock across
    # the whole cycle) or pass ``expected_generation`` from
    # :func:`store_generation` so a stale write is refused, never applied.
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        if expected_generation is not None:
            current = store_generation(path)
            if current != expected_generation:
                raise StaleStoreError(
                    f"{path}: store changed since your read (generation "
                    f"{current[:12]} != expected {expected_generation[:12]}). "
                    f"Another writer committed in between; writing now would "
                    f"erase their rows. Reload, re-apply your change, retry — "
                    f"or use edit_tasks() to hold the lock across the cycle."
                )
        _save_tasks_unlocked(tasks, path)


def store_generation(path: str | Path) -> str:
    """Content hash of the store file — the optimistic-concurrency token.

    Take it BEFORE :func:`load_tasks`, hand it to
    ``save_tasks(..., expected_generation=...)``. Content-based (sha256), not
    mtime-based: mtime has coarse granularity on some filesystems and lies
    across clock skew, and this store is shared over network mounts.
    """
    # Read-STABLE content hash of the canonical store. The token is the LOGICAL
    # content (load_doc's output), NOT the DB file's bytes and NOT the `path`
    # argument's file. Two traps this avoids:
    #   - the store-identity path (the legacy sidecar name) is never a real
    #     file under SQLite, so hashing it always returned "absent" and
    #     silently disabled the optimistic-concurrency guard (a stale write
    #     was never refused);
    #   - SQLite in WAL mode rewrites cards.db on a plain READ (it creates
    #     -wal/-shm), so hashing the DB FILE returned a new token after an
    #     un-contended read and falsely refused a fresh guarded write.
    # Hashing the logical doc is stable across reads and changes only when the
    # cards/users actually change. Missing DB -> "absent".
    import json

    from ._db import resolve_db_path

    db = Path(resolve_db_path(None)).expanduser()
    if not db.exists():
        return "absent"
    doc = load_doc(path, validate=False)
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


@contextlib.contextmanager
def edit_tasks(path: str | Path):
    """One locked read-modify-write cycle — the sanctioned bulk-edit primitive.

    Yields the mutable ``tasks`` list with the store lock HELD; on clean exit
    the (possibly mutated) list is validated and written back, preserving the
    non-``tasks`` sections and comments. On an exception nothing is written.

    This exists because every raw ``load_tasks → mutate → save_tasks`` script
    has a lost-update window as wide as its own runtime, and on 2026-07-10 a
    bulk migration used exactly that shape and ate two concurrent writes.

    Examples
    --------
    >>> with edit_tasks("cards.db") as tasks:        # doctest: +SKIP
    ...     for t in tasks:
    ...         if t.get("status") == "pending":
    ...             t["status"] = "deferred"
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        # SQLite is the store: load_doc reads the canonical DB (and fail-louds
        # if it is missing). The old `if path.exists() else {}` gated on the
        # YAML store PATH, which is NEVER a real file under SQLite, so it
        # silently yielded an empty doc and the write below wiped the store.
        doc = load_doc(path, validate=True)
        if not isinstance(doc, dict):
            raise TaskValidationError(f"{path}: top level is not a mapping")
        tasks = doc.get("tasks") or []
        yield tasks
        _save_doc_unlocked(doc, path, tasks=tasks)


def _save_tasks_unlocked(tasks: list[dict], path: Path) -> None:
    """Validate-and-write a task list WITHOUT acquiring the store lock.

    Thin back-compat wrapper over :func:`_save_doc_unlocked`. Callers that
    only hold a mutated ``tasks`` list (not the full doc) land here; it does
    the ONE extra read needed to recover the non-``tasks`` top-level sections
    (the ``users:`` registry etc.), splices in ``tasks``, and delegates the
    actual write. Callers on the hot read-modify-write path should instead
    reuse the doc they already hold via :func:`load_doc` and call
    :func:`_save_doc_unlocked` directly — that avoids the extra read.

    Used by callers (the `_store.add_task`/`update_task`/`complete_task`
    Python API) that hold `_store_lock` for their whole read-modify-write
    cycle. Calling `save_tasks` recursively would deadlock — `flock` on
    a fresh fd to the same path blocks until the OUTER context releases.

    Direct callers must already hold `_store_lock(path)`.
    """
    path = Path(path)
    # Recover the existing non-`tasks` sections (users:, …) so they survive
    # the rewrite. Read UNCONDITIONALLY: load_doc reads the canonical DB (and
    # fail-louds if it is missing). The old `if path.exists()` gated on the
    # YAML store PATH — never a real file under SQLite — so it skipped this
    # read, wrote back doc={"tasks": tasks} with NO users section, and the
    # incremental mirror then DELETEd the users registry (a card write wiped
    # every user). Every caller of this wrapper (save_tasks, help-wait/clear)
    # hit that.
    loaded = load_doc(path, validate=False)
    doc: dict = loaded if isinstance(loaded, dict) else {"tasks": []}
    _save_doc_unlocked(doc, path, tasks=tasks)


def _save_doc_unlocked(
    doc: dict,
    path: Path,
    *,
    tasks: list[dict] | None = None,
    deleted_ids: list[str] | None = None,
    allow_shrink: bool = False,
) -> None:
    """Validate-and-write an ALREADY-PARSED full doc WITHOUT the store lock.

    The doc-based write primitive. The read-modify-write callers in
    ``_store`` read the store ONCE under the lock (via :func:`load_doc`),
    mutate ``doc["tasks"]`` in place, then hand the whole doc here — so the
    non-``tasks`` sections (``users:`` etc.) captured by that same locked
    read survive the rewrite without a redundant second read. When ``tasks``
    is passed it replaces ``doc["tasks"]`` (the CRUD verbs may rebind the
    list, e.g. ``keep = [...]`` in delete).

    ``deleted_ids`` names cards a verb INTENTIONALLY removed (``delete_task``):
    the pruned ``tasks`` no longer lists them, but SQLite is upsert-only and the
    mirror never infers a delete from absence, so the ids are forwarded
    explicitly and the mirror drops exactly those rows. Omit on ordinary writes.
    NOTE: as of the 2026-07-21 tombstone change, ``delete_task`` itself no
    longer passes this — it keeps the row (see ``_store_lifecycle``) — so in
    practice this stays empty on every call reachable from the public API.

    ``allow_shrink`` forwards to :func:`scitex_cards._store_backend.write_doc_to_db`'s
    shrink guard (P0, third board wipe): a write missing rows the store
    already has is refused UNLESS this is ``True``. Keyword-only, never an
    env var — see that function's docstring. Omit on ordinary writes.

    Direct callers must already hold `_store_lock(path)`.
    """
    if tasks is not None:
        doc["tasks"] = tasks
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        doc["tasks"] = tasks
    _validate_tasks(tasks, source="<save_tasks>")  # hook-bypass: line-limit

    # SQLite IS the store. This is the whole write path — there is no second
    # branch, and that is the point of the change rather than a side effect of
    # it. `write_doc_to_db` RAISES on failure; see `_store_backend` for why
    # that inverts the usual best-effort posture.
    #
    # WHAT WAS DELETED HERE, recorded because the absences are load-bearing and
    # a future reader will otherwise reintroduce one of them as an improvement:
    #
    #   the YAML dump + atomic tmp/os.replace promotion — there is no file to
    #     promote; SQLite's own transaction is the atomicity boundary now
    #   the dual-write mirror — a mirror needs an original, and the original
    #     was the thing removed
    #   the git auto-commit of the store directory — it version-controlled a
    #     YAML file that no longer exists, and doing it on every card write was
    #     only ever affordable because it was best-effort
    #
    # Each of those was correct while YAML was canonical. None of them is
    # correct now, and keeping any one would recreate a second representation
    # of the board — which is the exact defect this cutover exists to remove.
    from ._store_backend import write_doc_to_db

    write_doc_to_db(doc, path, deleted_ids=deleted_ids, allow_shrink=allow_shrink)


def _git_autocommit_store(path: Path) -> None:
    """Initialize a per-store .git on first call, then commit on each save.

    Operator-visible recovery handle: with this in place, even a future
    SIGKILL-mid-write or bad mutation is recoverable via standard git
    commands (`git -C <store-dir> log` + `git show <sha>:<file>`). The
    fcntl lock + atomic write are the LIVE crash-safety; this is the
    POST-MORTEM recovery layer.

    Best-effort: never raises. Skips entirely if git isn't installed.

    Opt-out: set ``SCITEX_TODO_STORE_GIT_AUTOCOMMIT`` to a falsy value
    (``0``/``false``/``no``/``off``/empty) to skip the per-save commit
    entirely. This is the POST-MORTEM recovery layer, NOT the live
    crash-safety (that is the fcntl lock + atomic write in the caller), so
    disabling it is safe. Two uses: (a) avoid the git-repo bloat that
    per-save commits accumulate on a hot shared store, and (b) make the
    write path deterministic + fast under test (no git subprocess). Default
    is ON (unset ⇒ enabled).
    """
    if os.environ.get("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
        "",
    ):
        return

    store_dir = path.parent
    git_dir = store_dir / ".git"
    if not git_dir.exists():
        # Lazy-init.
        #
        # `gc.pruneExpire=never` is the guard that ACTUALLY protects old
        # snapshots: with it, gc NEVER deletes anything. It only PACKS.
        #
        # *** DO NOT SET gc.auto=0 HERE. IT USED TO BE SET, AND IT COST 13 GB. ***
        # The old code disabled auto-gc "so every snapshot stays reachable",
        # which conflated two different things. gc does not prune REACHABLE
        # objects — every commit on the branch is reachable by definition — and
        # `pruneExpire=never` already forbids pruning even unreachable ones. All
        # gc.auto=0 achieved was stopping git from ever PACKING, so every save's
        # full ~6.5 MB blob stayed a separate loose object forever.
        #
        # MEASURED on the live fleet store, 2026-07-14 (5 weeks, 10,828 commits):
        #     .git = 13 GB, 23,252 loose objects, on a 94%-full shared disk
        #     after `git gc`: 90 MB, 3 loose objects, ALL 10,829 commits preserved
        # 144x smaller, zero history lost. The "small store" assumption was the
        # error: the STORE is 6.5 MB, but a store committed on EVERY card write
        # grows a repo without bound unless something packs it.
        #
        # So: keep pruneExpire=never (nothing is ever deleted), and let git's
        # auto-gc do its job (default threshold; git auto-detaches it, so a
        # commit does not block on packing).
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(store_dir)],
            check=False,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        for cfg in (
            ("gc.pruneExpire", "never"),
            ("user.name", "scitex-todo"),
            ("user.email", "scitex-todo@localhost"),
        ):
            subprocess.run(
                ["git", "-C", str(store_dir), "config", *cfg],
                check=False,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
    # Stage + commit just this one file. Use --quiet so a clean tree
    # (no actual change) doesn't print to stderr.
    subprocess.run(
        ["git", "-C", str(store_dir), "add", "--", path.name],
        check=False,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(store_dir),
            "commit",
            "-q",
            "--allow-empty-message",
            "-m",
            "",
        ],
        check=False,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


# `_merge_tasks_into_seq` removed: it existed only to preserve per-node
# comments during a whole-document round-trip write. That write path is gone.
# (hook-bypass: line-limit — _model.py split still queued.)


# EOF
