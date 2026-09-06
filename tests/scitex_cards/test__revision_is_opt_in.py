#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``tasks.revision`` is a CAPABILITY, not a PROTECTION — pinned mechanically.

WHY A TEST AND NOT A COMMENT. This file exists because a comment saying exactly
this drifted from the code and cost a real edit. ``_db.py`` asserted, in the
present tense and as a property of the schema, that ``revision`` was "asserted
in the write's WHERE clause". Nothing asserted it until #790. Another agent read
that line, concluded the optimistic lock was protecting them, skipped the check,
and lost a write to a concurrent writer — reporting the work done, because
nothing contradicted them.

A comment cannot fail. That is the whole defect, and it happened twice in the
same write path (the sibling was ``_store._read_write_doc`` claiming the mirror
still inferred deletes from absence, fixed in #794). So the claim is pinned
here instead.

THE GUARD IS TWO-SIDED ON PURPOSE, which is the part worth keeping if this file
is ever rewritten. It fails if the WHERE clause DISAPPEARS — the capability
regressing to nothing. And it fails if the OPT-IN DEFAULT changes — because that
is the day ``_db.py``'s prose stops being true, and the person who makes it true
should be told to update it rather than leaving the next reader with the same
overstatement in the opposite direction.

A one-sided test would have caught only the first, and it is the SECOND that
recreates the original defect.

THE SECOND SIDE HAS NOW FIRED ONCE, WHICH IS WHY THIS PARAGRAPH CHANGED. Until
2026-08-16 it fired on "a public verb accepts the argument at all", and
``update_task`` gaining ``expected_revision`` (once #872 made a per-row guard
honest there) tripped it exactly as designed. The failure message said to update
the comment and the test in the same change, and that is what happened. What the
class pins now is the DEFAULT: "opt-in" was never about refusing the argument,
it is about an un-opted write emitting no clause.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import scitex_cards
from scitex_cards import _db_bootstrap, _db_mirror

SRC = pathlib.Path(scitex_cards.__file__).parent

#: The one SQL site that compares the counter. Named rather than pattern-matched
#: so a reader can go and look at it.
_WHERE_CLAUSE = "WHERE tasks.revision = ?"

#: Verbs on the PUBLIC surface. If any of these grows an ``expected_revision``
#: parameter, ``_db.py``'s "no public verb does" is false and must be rewritten.
_PUBLIC_VERBS = ("add_task", "update_task")


def _public_verb_params():
    """Parameter names of each public mutating verb, by import not by text."""
    from scitex_cards import _store_mutate

    return {
        name: set(inspect.signature(getattr(_store_mutate, name)).parameters)
        for name in _PUBLIC_VERBS
        if hasattr(_store_mutate, name)
    }


def _where_clause_sites():
    """Every source line in the package carrying the revision WHERE clause."""
    return [
        f"{path.relative_to(SRC)}:{n}"
        for path in sorted(SRC.rglob("*.py"))
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        )
        if _WHERE_CLAUSE in line
    ]


class TestTheCapabilityExists:
    """The lock must not silently regress to nothing."""

    def test_the_revision_where_clause_is_present_somewhere(self):
        # Arrange: #790 added it; before that the comment claimed a guard that
        # did not exist anywhere in the package.
        expected_minimum = 1

        # Act
        sites = _where_clause_sites()

        # Assert
        assert len(sites) >= expected_minimum, (
            "no writer compares tasks.revision in a WHERE clause; the optimistic "
            "lock has regressed to a bare column and _db.py's description of it "
            "is now an overstatement again"
        )

    def test_the_internal_writer_accepts_the_argument(self):
        # Arrange
        sig = inspect.signature(_db_bootstrap._insert_tasks)

        # Act
        accepts = "expected_revision" in sig.parameters

        # Assert
        assert accepts is True

    def test_the_mirror_writer_forwards_the_argument(self):
        # Arrange
        sig = inspect.signature(_db_mirror._write_card)

        # Act
        accepts = "expected_revision" in sig.parameters

        # Assert
        assert accepts is True


class TestItIsStillOptIn:
    """The other direction — and THIS CLASS ALREADY FIRED ONCE, as designed.

    It used to assert that no public verb accepted ``expected_revision``, and
    its failure message said: "That is GOOD — and it makes _db.py's 'no public
    verb does' FALSE. Update that comment and this test together, in the same
    change." That day arrived when ``update_task`` gained the parameter, the
    test failed, and the comment and this class were rewritten together.

    THE PROPERTY IT NOW PINS IS THE ONE THAT SURVIVED. "Opt-in" was never about
    the verb REFUSING the argument; it is about the DEFAULT emitting no guard,
    because ``_migrate_v6_to_v7`` ruled REJECT-by-default unusable across a
    fleet that cannot be made uniformly current. Refusing the kwarg was the
    stronger claim that happened to be true while nothing had wired it. Pinning
    the default keeps the guarantee that actually matters and still fails if
    someone makes the guard mandatory.
    """

    def test_update_task_can_be_asked_for_the_guard(self):
        # Arrange
        params = _public_verb_params()
        # Act
        accepts = "expected_revision" in params.get("update_task", set())
        # Assert
        assert accepts is True, (
            "update_task() no longer accepts expected_revision. If that is "
            "deliberate, _db.py's 'one public entry' paragraph is now false and "
            "must be rewritten in the same change."
        )

    def test_the_guard_is_off_unless_asked_for(self):
        """The REAL opt-in property: the default emits no clause, so an
        un-opted write stays last-write-wins for a fleet that cannot be made
        uniformly current."""
        # Arrange
        from scitex_cards import _store_mutate

        sig = inspect.signature(_store_mutate.update_task)
        # Act
        default = sig.parameters["expected_revision"].default
        # Assert
        assert default is None, (
            "expected_revision is no longer opt-in by default. _migrate_v6_to_v7 "
            "ruled REJECT-by-default unusable: a writer that knows nothing about "
            "revision would abort, failing fleet writes until every container is "
            "current."
        )

    def test_add_task_still_has_nothing_to_compare_against(self):
        """An INSERT has no prior revision, so the argument would be meaningless
        there rather than merely unimplemented."""
        # Arrange
        params = _public_verb_params()
        # Act
        accepts = "expected_revision" in params.get("add_task", set())
        # Assert
        assert accepts is False, (
            "add_task() now accepts expected_revision. Say what it compares "
            "against on an insert, and update _db.py's paragraph, together."
        )


class TestTheCommentSaysSo:
    """The prose and the code must agree, checked rather than trusted."""

    def test_the_schema_comment_names_the_opt_in(self):
        # Arrange: the exact overstatement that cost an edit was the phrase
        # "asserted in the write's WHERE clause" with no qualifier.
        source = (SRC / "_db.py").read_text(encoding="utf-8")

        # Act
        qualified = "only by a caller that opts in" in source

        # Assert
        assert qualified is True, (
            "_db.py no longer qualifies the revision guard as opt-in; it is "
            "drifting back toward the claim that cost scitex-dev a lost edit"
        )

    def test_the_module_still_parses(self):
        # Arrange: a docstring/comment edit is the easiest way to break a file
        # subtly, and this one is large.
        source = (SRC / "_db.py").read_text(encoding="utf-8")

        # Act
        tree = ast.parse(source)

        # Assert
        assert isinstance(tree, ast.Module)

# EOF
