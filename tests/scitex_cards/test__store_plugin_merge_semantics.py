#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every card column must state a merge rule, or state why it has none.

THE RISK THIS GUARDS. scitex-dev's primitive has NO DEFAULT merge rule, and the
operator's own framing is that a wrong one "loses data without raising". The
dangerous case is therefore not a bad rule — the validator catches several of
those — it is a column that NOBODY DECIDED ABOUT. A field added to `tasks` next
month with no policy is the silent failure.

So the load-bearing test here is `test_every_task_column_is_decided`: it reads
the REAL DDL and asserts every column is either declared with a rule or listed
as deliberately undeclared WITH A REASON. Adding a column without touching
`_store_plugin` turns that test red.

The rest pin the handful of rules where being wrong is expensive and silent.
"""

from __future__ import annotations

import inspect
import re

import pytest

from scitex_dev.store import FieldRole, MergeRule

from scitex_cards import _db_schema_sql
from scitex_cards._store_plugin import (
    STORE_NAME,
    TASK_FIELDS,
    provide,
    task_schema,
    undeclared_fields_and_why,
)


def _task_columns() -> "list[str]":
    """Column names read from the REAL CREATE TABLE, not from a copy."""
    src = inspect.getsource(_db_schema_sql)
    body = re.search(
        r"CREATE TABLE IF NOT EXISTS tasks\s*\((.*?)\n\s*\)\s*;", src, re.S
    ).group(1)
    return [
        line.strip().split()[0]
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]


def test_every_task_column_is_decided():
    # Arrange
    decided = set(TASK_FIELDS) | set(undeclared_fields_and_why())
    # Act
    undecided = [c for c in _task_columns() if c not in decided]
    # Assert
    assert undecided == [], (
        f"columns with no merge rule and no stated reason: {undecided}. "
        "Declare each in TASK_FIELDS or add it to UNDECLARED with why."
    )


def test_no_rule_is_declared_for_a_column_that_does_not_exist():
    # Arrange
    columns = set(_task_columns())
    # Act
    phantom = [f for f in TASK_FIELDS if f not in columns]
    # Assert
    assert phantom == []


def test_the_schema_builds_against_the_real_primitive():
    # Arrange
    expected = STORE_NAME
    # Act
    built = task_schema()
    # Assert
    assert built.name == expected


def test_the_card_id_is_immutable_identity():
    # Arrange — the primitive REFUSES identity+last_writer_wins, and my first
    # draft wrote exactly that. Pinned so it cannot come back.
    policy = TASK_FIELDS["id"]
    # Act
    pair = (policy.role, policy.merge)
    # Assert
    assert pair == (FieldRole.IDENTITY, MergeRule.IMMUTABLE)


def test_creation_facts_are_immutable():
    # Arrange
    fields = ("created_at", "created_by")
    # Act
    rules = {f: TASK_FIELDS[f].merge for f in fields}
    # Assert
    assert rules == {f: MergeRule.IMMUTABLE for f in fields}


def test_last_activity_only_moves_forward():
    # Arrange
    expected = MergeRule.MAX
    # Act
    rule = TASK_FIELDS["last_activity"].merge
    # Assert
    assert rule == expected


def test_row_order_is_not_declared_because_it_is_derived():
    # Arrange — a projection over the whole table has no per-field rule; any
    # choice yields duplicate and missing positions in a total order.
    undeclared = undeclared_fields_and_why()
    # Act
    present = "row_order" in undeclared
    # Assert
    assert present


def test_every_undeclared_field_states_a_reason():
    # Arrange
    undeclared = undeclared_fields_and_why()
    # Act
    silent = [k for k, why in undeclared.items() if not (why or "").strip()]
    # Assert
    assert silent == []


def test_provide_never_raises_in_either_world():
    # Arrange — this runs in BOTH worlds and must hold in both: where
    # StorePlugin is absent it returns [], where it is present it returns one
    # plugin. A raising provider is swallowed by discover_store_plugins and
    # would be indistinguishable from a leaf that declares nothing.
    #
    # CI CAUGHT A REAL BUG HERE that no local run could: my machine's
    # scitex_dev lacks StorePlugin, so the construction branch never executed
    # locally and shipped `WriterPolicy()` — calling an Enum with no member.
    # CI has a scitex_dev that HAS StorePlugin, ran that branch, and failed on
    # all three Python versions.
    expected = list
    # Act
    got = provide()
    # Assert
    assert isinstance(got, expected)


@pytest.fixture
def constructed_plugin():
    """The plugin, or a skip where the federation is not installed.

    The construction branch cannot run on a machine whose scitex_dev lacks
    StorePlugin — which is exactly how `WriterPolicy()` (calling an Enum with
    no member) reached CI. Guarding here rather than in the test body keeps
    one assertion per test.
    """
    plugins = provide()
    if not plugins:
        pytest.skip("scitex_dev.store.StorePlugin absent — nothing constructed")
    return plugins[0]


def test_a_constructed_plugin_declares_multi_writer(constructed_plugin):
    # Arrange — SINGLE_WRITER promises one owner per record, which the board
    # breaks hourly: any agent may comment on, reassign or complete any card
    # from any host.
    from scitex_dev.store import WriterPolicy

    # Act
    policy = constructed_plugin.writer_policy
    # Assert
    assert policy == WriterPolicy.MULTI_WRITER


def test_a_constructed_plugin_is_named_for_its_schema(constructed_plugin):
    # Arrange — `name == schema.name` is the federation's dedup key.
    expected = STORE_NAME
    # Act
    name = constructed_plugin.name
    # Assert
    assert name == expected


def test_a_status_rule_exists_even_though_it_is_imperfect():
    # Arrange — status is a lifecycle, not a free scalar; LWW can resurrect a
    # cancelled card. It is the least-bad AVAILABLE rule and must be stated
    # rather than omitted, so the compromise is visible.
    expected = MergeRule.LAST_WRITER_WINS
    # Act
    rule = TASK_FIELDS["status"].merge
    # Assert
    assert rule == expected


# EOF
