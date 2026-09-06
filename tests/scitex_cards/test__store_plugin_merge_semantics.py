#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every card column must state a merge rule, or state why it has none.

THE RISK THIS GUARDS. scitex-dev's primitive has NO DEFAULT merge rule, and the
operator's own framing is that a wrong one "loses data without raising". The
dangerous case is therefore not a bad rule — the validator catches several of
those — it is a column that NOBODY DECIDED ABOUT. A field added to `tasks` next
month with no policy is the silent failure.

So the load-bearing test here is `test_every_task_column_is_decided`: it reads
the REAL DDL and asserts every column is either declared with a rule, or
registered as a promotion candidate with the rule it would take and why, or
listed as deliberately undeclared WITH A REASON — and that no column is in two
of those buckets, because two answers is not a decision. Adding a column
without touching `_store_plugin` turns that test red.

ADR-0018 D1 CHANGED WHICH BUCKET MOST COLUMNS ARE IN, not what has to be true
about them. The card DOCUMENT is declared and the ~30 typed columns beside it
are derived from it, so their rules moved from the schema into
`PROMOTION_CANDIDATES`. Every fact those rules were pinned for is still pinned
here, against the register instead of against the schema — a rule nobody checks
is a rule nobody can rely on, whether or not it is live today.

The rest pin the handful of rules where being wrong is expensive and silent.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import replace
from pathlib import Path

import pytest

from scitex_dev.store import HLC, FieldKind, FieldRole, MergeRule, merge_field

from scitex_cards import _db_schema_sql
from scitex_cards._db_bootstrap import TASK_INSERT_COLS
from scitex_cards._paths import PKG_SHORT
from scitex_cards._store_plugin import (
    DOCUMENT_COL,
    PACKAGE,
    PROMOTION_CANDIDATES,
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


def _columns_derived_from_the_document() -> "list[str]":
    """Columns whose value is a COPY of something inside the card document.

    Read from `TASK_INSERT_COLS` — the list the writer actually uses on every
    card write — rather than from a copy kept in step by hand, so a column
    added to the write path arrives here without anyone remembering to add it.

    Three exclusions, each for a different reason:

    * `id` is the record key. `Schema.build` refuses a schema without an
      IDENTITY field, and `id` is IMMUTABLE on both sides, so the column and
      `card_json["id"]` have no way to diverge. Every other entry does.
    * `card_json` IS the document, not a copy of it.
    * `row_order` is a projection over the whole table rather than a fact
      copied out of one card — it is in `UNDECLARED`, not derived from here.
    """
    return [
        col
        for col in TASK_INSERT_COLS
        if col not in ("id", "row_order", DOCUMENT_COL)
    ]


def test_every_task_column_is_decided():
    # Arrange — three buckets, and a column belongs to exactly one of them.
    buckets = (
        set(TASK_FIELDS),
        set(PROMOTION_CANDIDATES),
        set(undeclared_fields_and_why()),
    )
    decided = set().union(*buckets)
    # Act
    undecided = [c for c in _task_columns() if c not in decided]
    twice = sorted(c for c in decided if sum(c in b for b in buckets) > 1)
    # Assert
    assert (undecided, twice) == ([], []), (
        f"columns with no merge rule and no stated reason: {undecided}; "
        f"columns decided twice: {twice}. Declare each in TASK_FIELDS, or "
        "register it in PROMOTION_CANDIDATES with the rule it would take and "
        "why, or add it to UNDECLARED with why — exactly one of the three."
    )


def test_exactly_two_fields_are_declared_and_no_more():
    # Arrange — the MEMBERSHIP tests above cannot catch a THIRD declaration.
    # Measured by counterexample: adding `deleted_at` to TASK_FIELDS yields
    # three declared fields and the whole file still passes, because the other
    # tests only ask that every column lands in exactly one bucket — moving a
    # column between buckets satisfies them. Nothing anywhere pinned the count
    # (`rg "len(TASK_FIELDS)"` -> 0; control: TASK_FIELDS resolves 8x here).
    #
    # This is not hypothetical: `deleted_at` is named in this module's own
    # UNDECLARED text as "the obvious first promotion once HIDE_FLAG lands".
    # Promoting a column to a typed field is an ADR-0018 D1 decision, so it
    # must break a test and be argued, not merely pass.
    expected = {"id", DOCUMENT_COL}
    # Act
    declared = set(TASK_FIELDS)
    # Assert
    assert declared == expected


def test_no_rule_is_declared_for_a_column_that_does_not_exist():
    # Arrange — the register is held to the same standard as the schema: a
    # rationale for a column that does not exist is reasoning about nothing.
    columns = set(_task_columns())
    named = (
        set(TASK_FIELDS) | set(PROMOTION_CANDIDATES) | set(undeclared_fields_and_why())
    )
    # Act
    phantom = sorted(f for f in named if f not in columns)
    # Assert
    assert phantom == []


def test_the_document_is_declared_and_the_columns_it_duplicates_are_not():
    # Arrange — ADR-0018 D1, pinned so a future edit cannot reintroduce the
    # inconsistency the ADR exists to prevent. A typed column merged per-field
    # BESIDE the document that duplicates it lets the two representations
    # disagree: host A's `status` column beside host B's `card_json.status`
    # produces a row that is internally inconsistent while every field merged
    # "correctly". So the document is declared, and nothing derived from it is.
    #
    # `scanned` is in the assertion on purpose: if `TASK_INSERT_COLS` is ever
    # renamed or emptied, `duplicated` goes empty too and this test PASSES
    # while inspecting nothing. An empty scan is the silent way a guard dies.
    document = TASK_FIELDS.get(DOCUMENT_COL)
    declared_rule = (document.kind, document.merge) if document else None
    derived = _columns_derived_from_the_document()
    # Act
    duplicated = [c for c in derived if c in TASK_FIELDS]
    scanned = len(derived) > 20
    # Assert
    assert (declared_rule, duplicated, scanned) == (
        (FieldKind.JSON, MergeRule.LAST_WRITER_WINS),
        [],
        True,
    ), (
        f"{DOCUMENT_COL} must be the declared JSON document under "
        f"LAST_WRITER_WINS, and these derived columns must NOT be declared "
        f"beside it: {duplicated}. Their rules belong in PROMOTION_CANDIDATES "
        "until ADR-0018's escape hatch promotes one, with a stated reason. "
        f"(columns scanned: {len(derived)} — a low number means the scan "
        "broke, not that the schema is clean.)"
    )


def test_the_schema_builds_against_the_real_primitive():
    # Arrange
    expected = STORE_NAME
    # Act
    built = task_schema()
    # Assert
    assert built.name == expected


def test_the_card_id_is_immutable_identity():
    # Arrange — the primitive REFUSES identity+last_writer_wins, and my first
    # draft wrote exactly that. Pinned so it cannot come back. `id` is the one
    # typed column D1 leaves declared, and structurally so: `Schema.build`
    # raises "has no IDENTITY field" without it. It is the record KEY, not
    # replicated payload — which is why it is not a promotion candidate.
    policy = TASK_FIELDS["id"]
    # Act
    got = (policy.role, policy.merge, "id" in PROMOTION_CANDIDATES)
    # Assert
    assert got == (FieldRole.IDENTITY, MergeRule.IMMUTABLE, False)


def test_creation_facts_would_be_promoted_immutable():
    # Arrange — the rule survives D1 in the register: a creation stamp is
    # written once, two hosts cannot disagree about it unless one is wrong, and
    # picking the later value would let a re-import rewrite history.
    fields = ("created_at", "created_by")
    # Act
    rules = {f: PROMOTION_CANDIDATES[f].policy.merge for f in fields}
    # Assert
    assert rules == {f: MergeRule.IMMUTABLE for f in fields}


def test_last_activity_is_not_max_because_it_has_a_public_setter():
    # Arrange — MAX was the first draft, on the claim that this stamp "only
    # ever moves forward". `_store_mutate.py:403` auto-stamps only when the
    # caller did NOT pass the field, and it is public on add_task/update_task
    # and the CLI, so the claim is false and MAX is unrepairable (below).
    expected = MergeRule.LAST_WRITER_WINS
    # Act
    rule = PROMOTION_CANDIDATES["last_activity"].policy.merge
    # Assert
    assert rule == expected


def test_finished_at_is_not_max_either():
    # Arrange — same shape, stronger: `_validate.py:344` classes `finished_at`
    # compute-only, so a caller-supplied string is its ONLY source.
    expected = MergeRule.LAST_WRITER_WINS
    # Act
    rule = PROMOTION_CANDIDATES["finished_at"].policy.merge
    # Assert
    assert rule == expected


@pytest.mark.parametrize("field", ["last_activity", "finished_at"])
def test_max_would_make_a_bad_timestamp_permanently_unrepairable(field):
    # Arrange — a writer stamps a far-future value through the public setter;
    # an operator then writes the CORRECT time with a STRICTLY LATER HLC. Under
    # MAX `merge_field` compares VALUES, not stamps (`_merge.py:121-138`), so
    # the repair is a lower value and loses — on every host, forever. Run
    # against the registered policy, which is what a promotion would install.
    bogus, repair = "9999-01-01T00:00:00Z", "2026-08-14T09:00:00Z"
    early = HLC(wall_us=1_000, logical=0, node="host-a")
    later = HLC(wall_us=2_000, logical=0, node="host-b")
    as_max = replace(PROMOTION_CANDIDATES[field].policy, merge=MergeRule.MAX)
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
def test_the_registered_rule_lets_that_same_repair_land(field):
    # Arrange — the identical scenario under the rule the register records. The
    # repair is simply the newest write, and the HLC makes it win everywhere.
    bogus, repair = "9999-01-01T00:00:00Z", "2026-08-14T09:00:00Z"
    early = HLC(wall_us=1_000, logical=0, node="host-a")
    later = HLC(wall_us=2_000, logical=0, node="host-b")
    # Act
    outcome = merge_field(
        field,
        PROMOTION_CANDIDATES[field].policy,
        current=bogus,
        current_stamp=early,
        incoming=repair,
        incoming_stamp=later,
    )
    # Assert
    assert (outcome.value, outcome.changed) == (repair, True)


def test_row_order_is_not_declared_because_it_is_derived():
    # Arrange — a projection over the whole table has no per-field rule; any
    # choice yields duplicate and missing positions in a total order. It is not
    # a promotion candidate either: a column would not help.
    undeclared = undeclared_fields_and_why()
    # Act
    present = ("row_order" in undeclared, "row_order" in PROMOTION_CANDIDATES)
    # Assert
    assert present == (True, False)


def test_every_deferred_decision_states_a_reason():
    # Arrange — the same standard for both dicts. An undeclared column with no
    # reason is an oversight wearing a decision's clothes, and a promotion
    # candidate with no reason cannot be argued when someone proposes it.
    undeclared = undeclared_fields_and_why()
    # Act
    silent = sorted(
        [k for k, why in undeclared.items() if not (why or "").strip()]
        + [k for k, c in PROMOTION_CANDIDATES.items() if not (c.why or "").strip()]
    )
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
    """DELETED BODY, DELIBERATELY — this guard could never have fired.

    It used to read::

        pytest.importorskip("scitex_dev.store").StorePlugin  # noqa: B018

    and its docstring claimed it was "Keyed on THE SYMBOL". It was not, twice
    over, and both are worth stating so nobody restores it:

    1. UNREACHABLE. Line 37 of this module does
       ``from scitex_dev.store import HLC, ...`` at MODULE SCOPE, unguarded.
       If ``scitex_dev.store`` were absent, collection dies there and this
       function never runs. A guard downstream of an unguarded import of the
       same module cannot fire.

    2. WRONG KEY ANYWAY. ``importorskip`` keys on the MODULE and then reaches
       for the attribute, so on scitex-dev 0.44.0–0.48.0 the import SUCCEEDS
       and ``.StorePlugin`` raises AttributeError — the tests behind it would
       have ERRORED rather than skipped, which is the opposite of a skip
       guard's job.

    3. NOW ALSO UNNECESSARY. ``pyproject.toml`` pins ``scitex-dev>=0.49.0``
       for exactly this module's needs (0.49.0 is the first release exporting
       ``StorePlugin``), so a conforming install cannot be too old.

    Kept as a no-op with this text rather than removed outright because the
    call sites and this explanation are the documentation for why the guard
    must not come back. Applying the usual root/full-path ``importorskip``
    repair here would produce a correctly-shaped guard that still cannot fire
    — cosmetic, and indistinguishable from a real fix to a reviewer or a grep.
    """


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
        "governs live data. ADR-0018 D1 settled card_json; row_order still has "
        "no DERIVED role upstream, `status` still has no lifecycle latch, and "
        "D3's tombstone has not left the document — settle those first, then "
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


def test_status_keeps_a_stated_rule_but_is_not_promoted():
    # Arrange — status is a lifecycle, not a free scalar. LWW resurrects a
    # terminal card, which sac measured against ONE peer store: 439 status
    # forks, 303 of them locally done/cancelled while the peer believes the
    # card active. The rule is still STATED (the compromise stays visible) and
    # the promotion is still BLOCKED, on an upstream lifecycle-latch rule.
    #
    # MAX IS NOT THE SUBSTITUTE, and this is the executable half of that claim:
    # `merge_field` compares VALUES (`_merge.py:121-138`), the values are
    # strings, so the comparison is lexicographic and 'in_progress' >
    # 'cancelled'. Below, the local CANCELLED write is the STRICTLY NEWER one
    # and MAX still takes the peer's stale `in_progress`.
    candidate = PROMOTION_CANDIDATES["status"]
    as_max = replace(candidate.policy, merge=MergeRule.MAX)
    stale = HLC(wall_us=1_000, logical=0, node="peer")
    newer = HLC(wall_us=2_000, logical=0, node="here")
    # Act
    under_max = merge_field(
        "status",
        as_max,
        current="cancelled",
        current_stamp=newer,
        incoming="in_progress",
        incoming_stamp=stale,
    )
    # Assert
    assert (
        candidate.policy.merge,
        "status" in TASK_FIELDS,
        bool((candidate.blocked_on or "").strip()),
        under_max.value,
    ) == (MergeRule.LAST_WRITER_WINS, False, True, "in_progress")


# EOF
