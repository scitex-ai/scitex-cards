#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A verb that REPORTS a store must name the store it actually READ.

REPORTED BY scitex-storage, 2026-08-09, measured on scitex-compute-04:

    summarize_tasks(assignee="scitex-storage")
      -> {"store": "/home/agent/.scitex/cards/tasks.yaml", "total": 52, ...}
    resolve_store()
      -> {"resolved": "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards", ...}
    on disk
      -> NO tasks.yaml. Only a lockfile for a store file that does not exist.

THE COUNTS WERE CORRECT -- 10 blocked / 24 done / 18 deferred matched PostgreSQL
exactly. So it read the right store and MISLABELLED it. The label came from
``_resolved_store`` (the legacy path resolver) while the data came from
``load_tasks`` (the canonical read). Two resolvers, one output, nothing forcing
them to agree.

WHY THIS IS NOT MERELY COSMETIC. This package's own doctor calls the resolved
target the SOLE store identity, and ADR-0016's failure mode BEGINS with someone
believing the wrong path is authoritative. A summary that confidently prints a
nonexistent ``tasks.yaml`` sends the next person debugging a board discrepancy
to chase a file that cannot exist -- and on 2026-08-09 the operator spent an
evening on exactly that class, because his board silently served a store nobody
had chosen.

NO ENVIRONMENT OVERRIDE IN THIS FILE, and the first version's failure is the
reason it is worth stating. That version pointed ``SCITEX_CARDS_DB`` at a live
PostgreSQL DSN so it could assert against the real fleet store. It passed here
and failed all three CI legs, because CI has no such server -- a test that
depends on one machine's running database is not a test, it is a local probe
wearing a test's name.

``tests/conftest.py`` ALREADY hands every test its own scratch SQLite store via
an autouse fixture, precisely so the suite can never touch the live board. The
first version FOUGHT that fixture. This one uses it: the assertion is that the
reported label AGREES WITH THE RESOLVER, whatever the resolver happens to
resolve, which is the property that actually matters and is true on every
backend.
"""

from __future__ import annotations

from scitex_cards._store_list import summarize_tasks
from scitex_cards._store_target import resolve_store_target


def test_the_reported_store_matches_the_resolver():
    """THE CONTRACT: the label is the resolver's answer, not a composed path.

    Asserted against ``resolve_store_target`` rather than a literal value,
    because the property that matters is AGREEMENT between what the verb says
    and what the package resolves -- not any particular store.
    """
    # Arrange
    expected = resolve_store_target(None)

    # Act
    reported = summarize_tasks()["store"]

    # Assert
    assert reported == expected


def test_the_reported_store_is_never_a_yaml_path():
    """The reported case, pinned by its own shape.

    Even when the resolver's answer changes, a cards store is a database. A
    ``.yaml`` in this field means the label was composed from the legacy path
    resolver again -- the exact regression this fixes.
    """
    # Arrange
    # Act
    reported = summarize_tasks()["store"]

    # Assert
    assert not str(reported).endswith(".yaml")


def test_an_explicit_store_argument_is_reported_as_given(tmp_path):
    """POSITIVE CONTROL: the fix must not hardcode "always the resolved target".

    An explicit argument outranks the ambient resolution, and the label has to
    follow it. Without this, a fix that simply always printed the env target
    would pass both tests above while lying to every caller that passed a store
    explicitly -- the same defect, relabelled.

    Uses the store the autouse fixture already created, copied to a second path,
    so the argument names a REAL readable store rather than a path that would
    fail for an unrelated reason.
    """
    # Arrange
    import shutil

    resolved = resolve_store_target(None)
    explicit = tmp_path / "explicit-cards.db"
    shutil.copyfile(resolved, explicit)

    # Act
    reported = summarize_tasks(str(explicit))["store"]

    # Assert
    assert str(explicit) in str(reported)

# EOF
