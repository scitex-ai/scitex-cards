#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card write is durable, and it stands up NO per-write git repo.

*** HISTORY: gc.auto=0 ON THE PER-WRITE AUTOCOMMIT REPO ONCE COST 13 GB. ***

Card writes used to auto-commit the store *directory* to a lazily-init'd
``.git`` "recovery repo" (the "must PACK, never PRUNE" layer whose ``gc.auto=0``
misconfiguration grew 23,252 loose objects / 13 GB on the live fleet store).

The SQLite cutover REMOVED that per-write autocommit entirely.
``_store_write._save_doc_unlocked`` now writes through ``write_doc_to_db`` and
stands up no git repo — the removal is deliberate and load-bearing (see the
comment there explicitly warning a future reader against reintroducing it).
Post-mortem recovery moved to the EXPLICIT ``scitex-cards db snapshot`` command,
which commits an export into a SEPARATE git repo under the DB's ``snapshots/``
dir; that repo's gc/prune contract belongs with that command, not the hot write
path.

So the "must PACK, never PRUNE" tests for the per-write repo have no subject
anymore and are deleted. What SURVIVES is the deeper rule they stood for:

  * a card write must PERSIST — pinned now against the canonical DB
    (round-trip), not against a git commit; and
  * the 13 GB disaster is prevented BY CONSTRUCTION — the write path creates
    no per-store ``.git`` at all.
"""

from __future__ import annotations

from pathlib import Path

from scitex_cards import _store
from scitex_cards._paths import resolve_tasks_path


def _store_dir() -> Path:
    """The directory the (now-removed) autocommit repo would have lived in.

    The old autocommit used ``resolve_tasks_path(None).parent`` as its repo
    root; the pinned STORE identity and the canonical DB share that scratch dir
    under the test harness, so this is exactly where a ``.git`` would appear if
    the write path still created one.
    """
    return resolve_tasks_path(None).parent


def test_a_card_write_is_durable():
    """A write PERSISTS — the surviving rule the old 'a write is committed'
    test pinned, now against the canonical SQLite store rather than a git
    commit."""
    # Arrange / Act — one card write via the store API.
    _store.add_task(None, id="c1", title="t", status="deferred", agent="a")
    # Assert — the write round-trips through the canonical DB.
    got = _store.get_task(None, task_id="c1")
    assert got is not None and got["id"] == "c1"


def test_a_write_creates_no_per_store_git_repo():
    """THE 13 GB era is over by construction.

    The per-write autocommit repo was removed with the SQLite cutover, so a
    card write must NOT lazily init a ``.git`` in the store directory. Pinning
    its absence guards the deliberate removal against a well-meaning
    reintroduction.
    """
    # Arrange / Act — one card write.
    _store.add_task(None, id="c1", title="t", status="deferred", agent="a")
    # Assert — no per-store recovery repo is created on the write path.
    assert not (_store_dir() / ".git").exists()
