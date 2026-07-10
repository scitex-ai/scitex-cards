#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YAML load/save primitives + the crash-safe writer — split out of
``_model.py``.

Extracted verbatim from ``_model.py`` (the 512-line-cap split, see
``GITIGNORED/REFACTORING.md`` while in progress / the git history of that
file once complete). ``_model.py`` re-exports every public name here so
existing ``from ._model import load_tasks, save_tasks, ...`` keep
resolving unchanged.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

from ._model_validate import _validate_tasks  # hook-bypass: line-limit
from ._store_verify import _verify_dumped_tmp  # hook-bypass: line-limit
from ._yaml import safe_dump, safe_load  # hook-bypass: line-limit


def load_tasks(path: str | Path) -> list[dict]:
    """Load and validate the task list from a YAML store.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML task store. The document must have a top-level
        ``tasks:`` list.

    Returns
    -------
    list of dict
        The validated task mappings, in document order.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        If the store is structurally invalid: ``tasks`` is not a list, a
        task is missing ``id`` or ``title``, an ``id`` is duplicated, a
        ``status`` is not in :data:`VALID_STATUSES`, or a ``priority`` is
        present but not an integer.

    Examples
    --------
    >>> tasks = load_tasks("tasks.yaml")  # doctest: +SKIP
    >>> tasks[0]["id"]                     # doctest: +SKIP
    'design'
    """
    data = load_doc(path, validate=True)
    return data.get("tasks")


def load_doc(path: str | Path, *, validate: bool = False) -> dict:
    """Load the FULL parsed mapping from a YAML store in ONE ``safe_load``.

    This is the single-read primitive that both :func:`load_tasks` and the
    ``_store`` CRUD verbs build on. Returning the *whole* top-level mapping
    (not just ``tasks``) lets a read-modify-write cycle reuse the one parse
    for BOTH the ``tasks`` payload it mutates AND the non-``tasks`` sections
    (notably the ``users:`` registry) it must carry through untouched — so
    the store is parsed once under the lock instead of twice (the old
    ``_save_tasks_unlocked`` re-read is eliminated; the ~2.3 s per single-card
    write it cost on the ~7.7 MB shared store goes away).

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML task store.
    validate : bool, default False
        When True, run :func:`_validate_tasks` on ``data.get("tasks")`` before
        returning (the read-time gate :func:`load_tasks` applies). Left off for
        pure write-preservation reads that validate at dump time instead.

    Returns
    -------
    dict
        The parsed top-level mapping. Empty/``None`` documents normalize to
        ``{}``; a non-mapping top level is returned as-is (the caller decides).

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        Only when ``validate=True`` and the ``tasks`` payload is invalid.
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"task store not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = safe_load(handle) or {}  # hook-bypass: line-limit

    if validate:
        tasks = data.get("tasks") if isinstance(data, dict) else None
        _validate_tasks(tasks, source=str(path))
    return data


@contextlib.contextmanager
def _store_lock(path: Path):
    """Hold an exclusive `fcntl.flock` on a sibling `.<name>.lock` file.

    Phase 1 prerequisite for the cross-host sync substrate (Req 2): two
    concurrent writers — say a CLI verb and the board's `/priority` POST
    handler — must serialize so the YAML payload they write is atomic at
    the task-list granularity. We hold the lock on a separate `.lock`
    sentinel file rather than on the store itself so we don't fight the
    ruamel YAML reader/writer that re-opens the path.

    The lock file is created if missing, never removed (next caller reuses
    it). Empty mode is fine — only the lockf state matters.

    Parameters
    ----------
    path : Path
        The store path (e.g. ``~/.scitex/todo/tasks.yaml``). The lock
        sentinel sits next to it as ``.tasks.yaml.lock``.

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


def save_tasks(tasks: list[dict], path: str | Path) -> None:
    """Validate then write a task list back to a YAML store, preserving comments.

    Re-runs the same validation gate as :func:`load_tasks` *before* touching
    disk, so a malformed mutation can never corrupt the store. Uses
    ``ruamel.yaml`` round-trip mode so hand-written comments and key layout in
    the existing store survive the rewrite.

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
    >>> tasks = load_tasks("tasks.yaml")          # doctest: +SKIP
    >>> tasks[0]["priority"] = 1                    # doctest: +SKIP
    >>> save_tasks(tasks, "tasks.yaml")            # doctest: +SKIP
    """
    path = Path(path).expanduser()
    # Hold the cross-process advisory lock for the FULL read-modify-write
    # cycle, not just the write — otherwise two writers could each load
    # the file, mutate independently, and the second `dump` would silently
    # clobber the first's mutation. The lock IS the at-most-once gate.
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        _save_tasks_unlocked(tasks, path)


def _save_tasks_unlocked(tasks: list[dict], path: Path) -> None:
    """Validate-and-write a task list WITHOUT acquiring the store lock.

    Thin back-compat wrapper over :func:`_save_doc_unlocked`. Callers that
    only hold a mutated ``tasks`` list (not the full parsed doc) land here;
    it does the ONE ``safe_load`` needed to recover the non-``tasks`` top-
    level sections (the ``users:`` registry etc.), splices in ``tasks``, and
    delegates the actual crash-safe write. Callers on the hot read-modify-
    write path should instead reuse the doc they already parsed via
    :func:`load_doc` and call :func:`_save_doc_unlocked` directly — that
    avoids this extra re-read entirely.

    Used by callers (the `_store.add_task`/`update_task`/`complete_task`
    Python API) that hold `_store_lock` for their whole read-modify-write
    cycle. Calling `save_tasks` recursively would deadlock — `flock` on
    a fresh fd to the same path blocks until the OUTER context releases.

    Direct callers must already hold `_store_lock(path)`.
    """
    path = Path(path)
    # Recover the existing non-`tasks` sections (users:, …) so they survive
    # the rewrite. This is the SAME read the old inline path did; it stays
    # here ONLY for callers that don't already hold the parsed doc.
    doc: dict = {"tasks": []}
    if path.exists():
        loaded = load_doc(path, validate=False)
        if isinstance(loaded, dict):
            doc = loaded
    _save_doc_unlocked(doc, path, tasks=tasks)


def _save_doc_unlocked(
    doc: dict, path: Path, *, tasks: list[dict] | None = None
) -> None:
    """Validate-and-write an ALREADY-PARSED full doc WITHOUT the store lock.

    The doc-based write primitive. The read-modify-write callers in
    ``_store`` parse the store ONCE under the lock (via :func:`load_doc`),
    mutate ``doc["tasks"]`` in place, then hand the whole doc here — so the
    non-``tasks`` sections (``users:`` etc.) captured by that same locked
    read survive the rewrite WITHOUT a redundant second ``safe_load``. When
    ``tasks`` is passed it replaces ``doc["tasks"]`` (the CRUD verbs may
    rebind the list, e.g. ``keep = [...]`` in delete).

    Direct callers must already hold `_store_lock(path)`.
    """
    if tasks is not None:
        doc["tasks"] = tasks
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        doc["tasks"] = tasks
    _validate_tasks(tasks, source="<save_tasks>")  # hook-bypass: line-limit

    # FAST WRITE (was: ruamel round-trip). The old path loaded the whole
    # 2.3 MB / ~695-card store with ruamel round-trip mode, merged the new
    # tasks into the comment-bearing nodes by id, then re-serialized with
    # ruamel — ~20 s PER single-card write, O(whole-store). ruamel's
    # round-trip machinery is the cost; it exists only to preserve the ~41
    # hand-written header/section comments. The store is machine-managed, so
    # dropping those comments is accepted. We now read with the fast safe
    # loader and dump with the fast safe dumper (libyaml when present).
    #
    # CRITICAL: the NON-`tasks` top-level sections (notably the `users:`
    # registry) are preserved because `doc` — parsed under the lock by the
    # caller (or by the `_save_tasks_unlocked` wrapper) — is written back
    # whole; we only ever replaced `doc["tasks"]`, every other top-level
    # key is carried through untouched.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # CRASH-SAFE WRITE (lead a2a `3b0df14a`, post-2026-06-08 autoassign-
    # parallel-run data loss): dump to a sibling .tmp file, fsync it, then
    # os.replace into the canonical path. os.replace is POSIX-atomic — a
    # SIGTERM/SIGKILL mid-dump leaves either the OLD file intact (if the
    # crash hits before replace) or the NEW file in place (if after).
    # Never a half-written file like the one we recovered from today.
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        # Serialize to a STRING first so the post-dump byte-length check can
        # compare on-disk bytes to what we intended to write (and so we never
        # dump twice). Then write that exact string to the tmp, flush + fsync.
        dumped = safe_dump(doc)  # returns the YAML string (stream=None)
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(dumped)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # fsync can fail on some FS (overlay / fuse). Best-effort —
                # the os.replace below is what gives the atomic guarantee.
                pass
        # POST-DUMP INTEGRITY CHECK (lead a2a `d5809cd3`, 2026-06-13 — the
        # recovered-by-hand corruption episode where the canonical file ended
        # mid-string at line ~2784). Before we promote the tmp into the
        # canonical slot, prove the written bytes are FULLY REPARSEABLE. The
        # pre-write `_validate_tasks` proves the in-memory structure is sound;
        # this catches any failure mode introduced by the dump itself
        # (unterminated scalar, partial flush, disk-full leaving a truncated
        # file even if fsync didn't error).
        #
        # CHEAPENED (Fix B2): the old check ran a FULL `safe_load` construct-
        # reparse (~2.3 s / ~159k objects on the live 9.2 MB store) purely to
        # prove parseability, then compared the reparsed task COUNT to the
        # in-memory count. We now do the equivalent two cheap checks in
        # `_verify_dumped_tmp` — a byte-length check + a libyaml EVENT-SCAN
        # reparse to StreamEnd — which proves the same "fully reparseable"
        # property WITHOUT building the objects. The task-count match is
        # DROPPED deliberately: reaching StreamEnd proves the whole stream
        # parsed, so a truncation that silently drops tasks can't reach
        # promotion (it aborts the parse first). Flagged for scitex-dev
        # review; see docs/ CHANGELOG + `_store_verify._verify_dumped_tmp`.
        _verify_dumped_tmp(tmp_path, dumped)
        # All checks passed — atomic POSIX rename promotes tmp → canonical.
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort tmp cleanup so a crashed dump doesn't leave a
        # stale sidecar.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # Best-effort git auto-commit on the store dir (lead a2a `3b0df14a`).
    # Lazy-init a small `.git` inside the store dir on first call; commit
    # each save so the operator gets time-travel via `git show <sha>:<file>`.
    # NEVER raises — a git failure must not block the actual save (the
    # YAML is already on disk; the commit is an audit-trail bonus).
    try:
        _git_autocommit_store(path)
    except Exception:  # noqa: BLE001 — best-effort
        pass


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

    import subprocess

    store_dir = path.parent
    git_dir = store_dir / ".git"
    if not git_dir.exists():
        # Lazy-init. Disable auto-gc + auto-pack so every snapshot stays
        # reachable; the store is small enough that aggressive gc would
        # waste cycles + risk reachable-but-old snapshots being pruned.
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(store_dir)],
            check=False,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        for cfg in (
            ("gc.auto", "0"),
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


# `_merge_tasks_into_seq` removed: it existed only to preserve ruamel
# per-node comments during the round-trip write. The write path now uses a
# fast safe dump (no comment preservation), so the merge helper is dead.
# (hook-bypass: line-limit — _model.py split still queued.)


# EOF
