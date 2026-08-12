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
regressing to nothing. And it fails if a PUBLIC VERB starts accepting
``expected_revision`` — because that is the day the comment's "no public verb
does" stops being true, and the person who makes it true should be told to
update the prose rather than leaving the next reader with the same overstatement
in the opposite direction.

A one-sided test would have caught only the first, and it is the SECOND that
recreates the original defect.
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
    """The other direction: the day this fails, the COMMENT must change."""

    @pytest.mark.parametrize("verb", _PUBLIC_VERBS)
    def test_the_public_verb_does_not_accept_expected_revision(self, verb):
        # Arrange: _db.py states that every write through the public surface is
        # last-write-wins. If that stops being true, this test is how the
        # person who changed it finds out the prose needs rewriting.
        params = _public_verb_params()

        # Act
        accepts = "expected_revision" in params.get(verb, set())

        # Assert
        assert accepts is False, (
            f"{verb}() now accepts expected_revision. That is GOOD — and it "
            "makes _db.py's 'no public verb does' FALSE. Update that comment "
            "and this test together, in the same change."
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
