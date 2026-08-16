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
from dataclasses import replace
from pathlib import Path

import pytest

from scitex_dev.store import HLC, FieldRole, MergeRule, merge_field

from scitex_cards import _db_schema_sql
from scitex_cards._paths import PKG_SHORT
from scitex_cards._store_plugin import (
    PACKAGE,
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


def test_last_activity_is_not_max_because_it_has_a_public_setter():
    # Arrange — MAX was the first draft, on the claim that this stamp "only
    # ever moves forward". `_store_mutate.py:379` auto-stamps only when the
    # caller did NOT pass the field, and it is public on add_task/update_task
    # and the CLI, so the claim is false and MAX is unrepairable (below).
    expected = MergeRule.LAST_WRITER_WINS
    # Act
    rule = TASK_FIELDS["last_activity"].merge
    # Assert
    assert rule == expected


def test_finished_at_is_not_max_either():
    # Arrange — same shape, stronger: `_validate.py:344` classes `finished_at`
    # compute-only, so a caller-supplied string is its ONLY source.
    expected = MergeRule.LAST_WRITER_WINS
    # Act
    rule = TASK_FIELDS["finished_at"].merge
    # Assert
    assert rule == expected


@pytest.mark.parametrize("field", ["last_activity", "finished_at"])
def test_max_would_make_a_bad_timestamp_permanently_unrepairable(field):
    # Arrange — a writer stamps a far-future value through the public setter;
    # an operator then writes the CORRECT time with a STRICTLY LATER HLC. Under
    # MAX `merge_field` compares VALUES, not stamps (`_merge.py:121-138`), so
    # the repair is a lower value and loses — on every host, forever.
    bogus, repair = "9999-01-01T00:00:00Z", "2026-08-14T09:00:00Z"
    early = HLC(wall_us=1_000, logical=0, node="host-a")
    later = HLC(wall_us=2_000, logical=0, node="host-b")
    as_max = replace(TASK_FIELDS[field], merge=MergeRule.MAX)
    # Act
    under_max = merge_field(
        field,
        as_max,
        current=bogus,
        current_stamp=early,
        incoming=repair,
        incoming_stamp=later,
    )
    # Assert — the newer, correct value is REJECTED. There is no in-band fix.
    assert (under_max.value, under_max.changed) == (bogus, False)


@pytest.mark.parametrize("field", ["last_activity", "finished_at"])
def test_the_declared_rule_lets_that_same_repair_land(field):
    # Arrange — the identical scenario under the rule actually declared. The
    # repair is simply the newest write, and the HLC makes it win everywhere.
    bogus, repair = "9999-01-01T00:00:00Z", "2026-08-14T09:00:00Z"
    early = HLC(wall_us=1_000, logical=0, node="host-a")
    later = HLC(wall_us=2_000, logical=0, node="host-b")
    # Act
    outcome = merge_field(
        field,
        TASK_FIELDS[field],
        current=bogus,
        current_stamp=early,
        incoming=repair,
        incoming_stamp=later,
    )
    # Assert
    assert (outcome.value, outcome.changed) == (repair, True)


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


def test_provide_never_returns_an_empty_list():
    # Arrange — the silent branch is [], NOT a raise: `discover_store_plugins`
    # logs a warning WITH a traceback for a provider that raises
    # (`federation/_discover.py:113-122`), whereas [] is indistinguishable
    # from a leaf that declares nothing. So on a scitex-dev that HAS
    # StorePlugin, provide() must yield the declaration; on one that lacks it,
    # the ImportError propagates and is reported upstream.
    #
    # CI CAUGHT A REAL BUG in the construction branch that no local run could:
    # a machine whose scitex_dev lacks StorePlugin never executed it, and it
    # shipped `WriterPolicy()` — calling an Enum with no member.
    _require_federation()
    # Act
    got = provide()
    # Assert
    assert len(got) == 1


def _require_federation():
    """Skip where scitex-dev is too old to construct a StorePlugin.

    Keyed on THE SYMBOL, not on `provide()` returning empty — provide() no
    longer has an empty branch to key on, and testing for the symbol you need
    in the environment that will run it is the lesson this module records.
    """
    pytest.importorskip("scitex_dev.store").StorePlugin  # noqa: B018


@pytest.fixture
def constructed_plugin():
    """The plugin, or a skip where the federation is not installed."""
    _require_federation()
    return provide()[0]


def test_the_plugin_resolves_to_the_stores_short_name(constructed_plugin):
    # Arrange — `pkg` DECIDES WHERE THE STORE RESOLVES: "two plugins naming
    # different `pkg` values resolve to different stores"
    # (`federation/_spec.py:46-52`). An earlier draft passed the DISTRIBUTION
    # name here as a "provenance label", which resolves a different, empty
    # store from the live board and cannot be caught by any validator — both
    # strings are non-empty and plausible. This fails if the two ever drift.
    expected = PKG_SHORT
    # Act
    pkg = constructed_plugin.pkg
    # Assert
    assert pkg == expected


def test_the_distribution_name_is_provenance_not_resolution(constructed_plugin):
    # Arrange — the other half of the same confusion: `provider` is "The
    # declaring pip package" (:60-63), carried so a listing can say who is
    # responsible. It must NOT be what `pkg` carries.
    expected = (PACKAGE, True)
    # Act
    got = (constructed_plugin.provider, constructed_plugin.pkg != PACKAGE)
    # Assert
    assert got == expected


@pytest.fixture
def pyproject_text() -> str:
    """The repo's real pyproject, or a skip when it is not on disk."""
    path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not path.is_file():
        pytest.skip("running against an installed package, not the repo")
    return path.read_text(encoding="utf-8")


def test_the_entry_point_is_not_wired_yet(pyproject_text):
    # Arrange — `provide()`'s docstring claims nothing calls this in
    # production. That claim is only true while pyproject omits the group, and
    # an earlier revision of THIS PR registered it while the docstring still
    # said otherwise. Checked, not remembered.
    # The COMMENTED example of the block-to-be lives in pyproject too, so
    # match live TOML only — a commented group registers nothing.
    group = 'scitex_dev.store.plugins"]'
    live = [ln for ln in pyproject_text.splitlines() if not ln.lstrip().startswith("#")]
    # Act
    wired = any(group in ln for ln in live)
    # Assert
    assert not wired, (
        "pyproject now registers scitex_dev.store.plugins, so this declaration "
        "governs live data. Settle card_json / row_order / status first, then "
        "update provide()'s docstring and delete this test."
    )


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
