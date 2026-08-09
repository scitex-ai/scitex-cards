#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A verb that REPORTS a store must name the store it actually READ.

REPORTED BY scitex-storage, 2026-08-09, measured on scitex-compute-04:

    summarize_tasks(assignee="scitex-storage")
      -> {"store": "/home/agent/.scitex/cards/tasks.yaml", "total": 52, ...}
    resolve_store()
      -> {"resolved": "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards",
          "backend": "postgresql", ...}
    on disk
      -> NO tasks.yaml, NO cards.db. Only a lockfile for a store file that
         does not exist.

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

The operator's standing instruction the same day: "WE NEVER USE YAML CARDS BUT
DBs ONLY", "DO REMOVE ALL REFERENCES THAT SAYS YAML FOR CARDS", "NO SILENT
FALLBACKS". A verb naming a YAML file it did not read is all three at once.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._store_list import summarize_tasks
from scitex_cards._store_target import resolve_store_target

_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
_TARGET_VARS = ("SCITEX_CARDS_DB", "SCITEX_TODO_DB")


@pytest.fixture()
def env_target():
    """Set a real store target in the real environment, restore on teardown.

    No ``monkeypatch``: the defect was about which RESOLVER answers, so the test
    moves the same environment a deployment moves. Patching a resolver would
    assert a belief about the resolver rather than exercise the resolution.
    """
    saved = {name: os.environ.get(name) for name in _TARGET_VARS}
    os.environ["SCITEX_CARDS_DB"] = _DSN
    yield _DSN
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_the_reported_store_matches_the_resolver(env_target):
    """THE CONTRACT: the label is the resolver's answer, not a composed path.

    Asserted against ``resolve_store_target`` rather than against the literal
    DSN, because the property that matters is AGREEMENT between what the verb
    says and what the package resolves — not any particular value.
    """
    # Arrange
    expected = resolve_store_target(None)

    # Act
    reported = summarize_tasks()["store"]

    # Assert
    assert reported == expected


def test_the_reported_store_is_never_a_yaml_path(env_target):
    """The reported case, pinned by its own shape.

    Even if the resolver's answer changes, a cards store is a database. A
    ``.yaml`` in this field means the label was composed from the legacy path
    resolver again — the exact regression this fixes.
    """
    # Arrange
    # Act
    reported = summarize_tasks()["store"]

    # Assert
    assert not str(reported).endswith(".yaml")


def test_an_explicit_store_argument_is_reported_as_given(env_target, tmp_path):
    """POSITIVE CONTROL: the fix must not hardcode "always the env target".

    An explicit argument outranks the environment, and the label has to follow
    it. Without this, a fix that simply always printed the DSN would pass both
    tests above while lying to every caller that passed a store explicitly —
    the same defect, relabelled.
    """
    # Arrange
    explicit = tmp_path / "explicit" / "cards.db"

    # Act
    reported = summarize_tasks(str(explicit))["store"]

    # Assert
    assert str(explicit) in str(reported)

# EOF
