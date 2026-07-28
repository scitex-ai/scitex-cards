#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE IDENTITY IS A UUID, NOT A PATH -- the decision table, as executable spec.

Design: ``docs/design/store-identity-is-a-uuid.md``.
Card: ``scitex-cards-resolver-never-default-yaml-20260727`` (P0).
Companion: ``test__store_uuid_guard_integration.py`` (the same contract against a
real database; split only because this file hit the 512-line cap).

WHAT THESE TESTS ARE. Every test marked ``@NOT_YET`` is ``xfail(strict=True)``
against an API that DOES NOT EXIST. They fail today, on the import of
``scitex_cards._store_uuid``. When the implementation lands they XPASS, and
``strict=True`` turns an XPASS into a FAILURE -- so the implementation PR
deletes the markers rather than writing fresh assertions against whatever it
happened to build. The spec is written first and cannot be quietly edited to
match the code.

THE DEFECT, measured on the HOST on 2026-07-28 (the only place it reproduces)::

    stamped store_path : /home/agent/.scitex/cards/cards.db
    exists on HOST     : False
    host resolves      : /home/ywatanabe/.scitex/cards/cards.db
                         -> /home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db

ONE bind-mounted file, two names. ``_same_file`` compares by inode when both
paths exist and falls back to a realpath STRING compare when one does not. From
the host ``/home/agent/...`` cannot be stat'd, the strings never match, and the
board is refused its own database -- ``GET /tasks`` returned HTTP 500 all day.
scitex-storage's formulation is the whole thing: a path is not identity when
more than one view or code path can produce it.

THE 500 IS THE GUARD WORKING. Three repairs were tried on 2026-07-28 and all
three were refused by defences this package already carries: loosening the
shared predicate (broke the foreign-clobber test, correctly), confining the
relaxation to the read door (the exact asymmetry ``_read_canonical_db_or_raise``
exists to prevent), and re-stamping the live store to the host path (the 500
cleared and the board returned an EMPTY document, which is the 2,138-card-wipe
shape). None of them is repeated here.

EVERYTHING IN THIS FILE IS PURE. ``identity_verdict`` takes two optional strings
and nothing else -- no path, no connection, no environment. That is not a
convenience: it means no view, mount namespace or working directory can change
the answer, and these tests touch no filesystem at all.
"""

from __future__ import annotations

import re

import pytest

#: Applied to every test whose subject is not implemented yet. ``strict`` is the
#: point: an XPASS fails the suite, so landing the implementation FORCES this
#: marker to be removed test by test, and a marker left behind is a red build
#: rather than a silently-skipped promise.
NOT_YET = pytest.mark.xfail(
    strict=True,
    reason=(
        "scitex_cards._store_uuid does not exist yet -- this is the design PR's "
        "executable spec; the implementation PR removes this marker"
    ),
)

#: A minted identity is a uuid4 in the bare lowercase 8-4-4-4-12 spelling.
_MINTED_UUID4 = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)

#: Two fixed identities, written out rather than minted, so the comparison tests
#: assert on values a reader can see are different.
IDENTITY_A = "3f2b8c1e-9d4a-4f77-b0c5-1a2e3d4f5a6b"
IDENTITY_B = "7c9e0d21-5b3f-4a08-9e6d-2f4a6b8c0d1e"


# --------------------------------------------------------------------------- #
# The decision table                                                          #
# --------------------------------------------------------------------------- #


@NOT_YET
def test_one_store_reached_under_two_names_is_one_store():
    """The outage, at the level where it is decided.

    Host and container reach ONE bind-mounted database under two names. Under a
    path identity that is two stores; under this one it is one, because the
    function has no parameter through which a name could enter.
    """
    # Arrange
    from scitex_cards._store_uuid import ACCEPT, identity_verdict

    # Act
    verdict = identity_verdict(IDENTITY_A, IDENTITY_A)

    # Assert
    assert verdict == ACCEPT


@NOT_YET
def test_a_genuinely_different_store_is_still_refused():
    """The PAIR of the test above, and the reason it is not just ``return True``.

    Widening the comparison must not open the door: without this, an "always
    accept" implementation satisfies every other test in this file and deletes
    the guard. This is the ONLY row of the table that refuses.
    """
    # Arrange
    from scitex_cards._store_uuid import REFUSE, identity_verdict

    # Act
    verdict = identity_verdict(IDENTITY_A, IDENTITY_B)

    # Assert
    assert verdict == REFUSE


@NOT_YET
def test_a_legacy_unstamped_database_stays_adoptable():
    """EVERY database that exists today carries no identity.

    Including the live ``cards.db``. If an unstamped database were refused,
    landing this change would brick the fleet board on deploy -- read-only, no
    YAML behind it, the outage this work exists to end. Unstamped means "not yet
    claimed", never "wrong".
    """
    # Arrange
    from scitex_cards._store_uuid import ADOPT, identity_verdict

    # Act
    verdict = identity_verdict(None, None)

    # Assert
    assert verdict == ADOPT


@NOT_YET
def test_a_legacy_database_is_adoptable_even_when_an_expectation_is_configured():
    """Row 2 of the table -- the contract's reading, and an OPEN QUESTION.

    This encodes the agreed contract ("a legacy UNSTAMPED database must stay
    ADOPTABLE"), not the author's preference. See section 10 of the design doc:
    the argument for REFUSE here is that an expectation is a STATEMENT that this
    store must be X, and a database that cannot show it is X does not satisfy
    it -- "no identity" is not "the identity you named". The argument against is
    that REFUSE strands an operator mid-migration with a board that refuses
    every write.

    A reviewer who prefers REFUSE changes this one line. It is flagged rather
    than decided silently.
    """
    # Arrange
    from scitex_cards._store_uuid import ADOPT, identity_verdict

    # Act
    verdict = identity_verdict(None, IDENTITY_A)

    # Assert
    assert verdict == ADOPT


@NOT_YET
def test_no_expectation_is_not_evidence_of_a_foreign_store():
    """Contract rule 4b, and the rule most likely to be "improved" into a bug.

    A caller with no expectation is a caller that has not said which store it
    wants -- not a caller that has said it wants a different one. Accepting here
    is correct for IDENTITY and must never merge with RESOLUTION, or it becomes
    "use whatever you were pointed at". That separation is pinned by
    ``test_a_matching_identity_does_not_bypass_the_ambient_store_creation_guard``
    in the companion file.
    """
    # Arrange
    from scitex_cards._store_uuid import ACCEPT, identity_verdict

    # Act
    verdict = identity_verdict(IDENTITY_A, None)

    # Assert
    assert verdict == ACCEPT


@NOT_YET
def test_the_comparison_is_exact_string_equality_not_a_uuid_parse():
    """Uppercase is a DIFFERENT string, therefore a different identity.

    A comparison that case-folds is a comparison with two spellings, which is
    the entire class of bug being removed. The identity is OPAQUE: it is never
    parsed, normalised, sorted or interpreted -- only compared.
    """
    # Arrange
    from scitex_cards._store_uuid import REFUSE, identity_verdict

    # Act
    verdict = identity_verdict(IDENTITY_A, IDENTITY_A.upper())

    # Assert
    assert verdict == REFUSE


@NOT_YET
def test_the_comparison_does_not_normalise_a_braced_spelling():
    """The other normalisation temptation: ``{...}`` and ``urn:uuid:``.

    Same rule as case folding, different door. Anything that teaches the
    comparison to recognise a second spelling re-creates the view-dependence
    that a path identity had.
    """
    # Arrange
    from scitex_cards._store_uuid import REFUSE, identity_verdict

    # Act
    verdict = identity_verdict(IDENTITY_A, "{" + IDENTITY_A + "}")

    # Assert
    assert verdict == REFUSE


# --------------------------------------------------------------------------- #
# Minting                                                                     #
# --------------------------------------------------------------------------- #


@NOT_YET
def test_minting_is_never_derived_so_two_mints_in_one_process_differ():
    """The "never derived from path, hostname, or timestamp" clause, enforced.

    scitex-dev required that clause to live as a comment in the code, because
    someone will later "improve" minting into a deterministic hash of the store
    path -- which is precisely the view-dependence this change removes. A
    comment is not a check; this is. Two mints from ONE process, ONE host and
    ONE working directory must differ, which no path/host/second-resolution
    derivation can satisfy.
    """
    # Arrange
    from scitex_cards._store_uuid import mint_store_uuid

    # Act
    minted = [mint_store_uuid(), mint_store_uuid()]

    # Assert
    assert minted[0] != minted[1]


@NOT_YET
def test_a_minted_identity_has_the_agreed_bare_lowercase_form():
    """The ecosystem-wide form: bare lowercase uuid4, 8-4-4-4-12.

    Adopted verbatim by scitex-dev, so any scitex package with a
    shared-authoritative store writes the SAME key in the SAME shape. A braced
    or uppercase mint would compare unequal against a peer's correct spelling
    and refuse a store that is in fact its own.
    """
    # Arrange
    from scitex_cards._store_uuid import mint_store_uuid

    # Act
    minted = mint_store_uuid()

    # Assert
    assert _MINTED_UUID4.match(minted)


# EOF
