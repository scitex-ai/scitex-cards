#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The DM sidecar's lock and its crash-safe whole-document writer.

Split out of :mod:`scitex_cards._threads` when the DM-into-the-store dual write
pushed that module past its size budget. The seam is real: this module is the
sidecar's FILE MECHANICS, while ``_threads`` is the DM vocabulary (threads,
peers, read state) built on top. Both names are re-exported from ``_threads``,
so ``_threads._threads_lock`` / ``_threads._save_threads_unlocked`` keep
resolving for callers and for monkeypatching tests.

WHAT THIS CODE IS, AS OF SCHEMA v5: the ROLLBACK STATE, not the store of
record. DMs are written to ``cards.db`` first and mirrored here afterwards
(see ``docs/design/dm-into-cards-db-migration.md`` M3). The sidecar is kept
complete and correct precisely so that undoing the migration is redeploying
the previous version rather than restoring anything.

It is also the defect the migration exists to retire. :func:`_save_threads_unlocked`
rewrites the WHOLE document to append one message — on the live sidecar a ~3 MB
read-modify-write per DM. Whole-document rewrite is the amplifier behind every
wipe this package has survived: a writer that restates rows it did not author
can lose rows it never saw. The database write path only ever inserts its own
row, so a lost update is not expressible there.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path


@contextlib.contextmanager
def _threads_lock(path: Path):
    """Exclusive flock on the sidecar's OWN ``.threads.json.lock`` sentinel.

    Deliberately SEPARATE from ``_model._store_lock`` (the task store): chat
    traffic must never convoy with card writes. Same mechanics otherwise.
    """
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def _save_threads_unlocked(
    threads: dict[str, list[dict]], path: Path, *, threads_key: str = "threads"
) -> None:
    """Crash-safe write of the whole sidecar document.

    Mirrors ``_model._save_doc_unlocked``: dump to a sibling ``.tmp``, fsync,
    REPARSE the tmp bytes and verify the thread count + total message count
    match the in-memory doc, then ``os.replace`` (POSIX-atomic) into place.
    Never promotes suspect bytes; the canonical file stays intact on any
    failure. Callers must already hold :func:`_threads_lock`.
    """
    import json

    doc = {threads_key: threads}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass  # best-effort (overlay/fuse); os.replace is the swap
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = json.load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did not "
                f"reparse cleanly after dump ({type(verify_exc).__name__}: "
                f"{verify_exc}). Canonical file left untouched."
            ) from verify_exc
        verify_threads = (
            verify_doc.get(threads_key) if isinstance(verify_doc, dict) else None
        )
        want_msgs = sum(len(v) for v in threads.values())
        have_msgs = (
            sum(len(v) for v in verify_threads.values() if isinstance(v, list))
            if isinstance(verify_threads, dict)
            else -1
        )
        if (
            not isinstance(verify_threads, dict)
            or len(verify_threads) != len(threads)
            or have_msgs != want_msgs
        ):
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected threads payload ({have_msgs} msgs vs in-memory "
                f"{want_msgs}). Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = ["_save_threads_unlocked", "_threads_lock"]

# EOF
