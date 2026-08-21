#!/usr/bin/env python3
"""The identity pin must check BOTH halves, and each half must go red alone.

THE DEFECT THIS PINS, found by dotfiles 2026-08-17 by MUTATION-TESTING the gate
instead of reading it — they set a deliberately wrong value and checked whether
the gate noticed::

    SCITEX_CARDS_STORE_UUID=deadbeef-1111-4222-8333-444455556666 \\
    SCITEX_CARDS_STORE_INSTANCE=7672112238472680366 \\
      scitex-cards resolve-store --json

    "store_uuid":       "1d55dd6e-3d2a-4c24-a429-a78835ab988f"
    "expected_uuid":    "deadbeef-1111-4222-8333-444455556666"
    "identity_verdict": "matches"        <-- observed != expected, adjacent lines
    "may_proceed":      true

The uuid was read from the environment and rendered into the report, and never
reached a comparison: both call sites passed the INSTANCE into a single
``expected`` field. Their isolation table showed setting the uuid changed
nothing in any combination — the instance alone decided every row.

WHY A ROW-BY-ROW TABLE RATHER THAN A HAPPY-PATH TEST. A test that pins only
"both correct -> matches" passes just as well against a gate that ignores one
half entirely, which is exactly the gate that shipped. Each half must be
mutated INDEPENDENTLY so each can be seen to go red on its own; that is the
only arrangement that can catch this class, and it is the method dotfiles used
to find it.

THE TABLE IS THE CONTRACT, agreed with dotfiles before either of us wrote a
test, with row 3 changed on my call: a half-pin is CANNOT_TELL, never MATCHES.
Satisfying only the instance answers which SERVER and never which DATABASE, so
a database restored onto that same server would sail through — the 2026-08-09
frozen-store incident this pin exists to catch.

BOTH POLES ARE EXERCISED. Rows asserting MATCHES prove the gate can still go
GREEN; a guard that refuses everything is as useless as one that passes
everything, and only a table containing both proves it is neither.
"""

import inspect

import pytest

from scitex_cards import _store_instance, _store_pin
from scitex_cards._store_identity_decision import IdentityVerdict, decide_identity
from scitex_cards._store_instance import Certainty, StoreInstance

#: The real values this fleet reports, so the fixtures are not invented shapes.
REAL_UUID = "1d55dd6e-3d2a-4c24-a429-a78835ab988f"
REAL_INSTANCE = "7672112238472680366"

#: A second REAL instance id, measured 2026-08-17 on nas-03 port 55432 while
#: 55433 answered REAL_INSTANCE — same host, same store_uuid, different server.
#: The live fork this pin has to catch, not a synthetic value.
OTHER_INSTANCE = "7674038125294264831"

WRONG_UUID = "deadbeef-1111-4222-8333-444455556666"


def _observed(instance_id=REAL_INSTANCE):
    """A store that can report its instance."""
    return StoreInstance(
        backend="postgres",
        certainty=Certainty.KNOWN,
        instance_id=instance_id,
    )


def _unreadable():
    """A store that cannot report an instance at all."""
    return StoreInstance(
        backend="sqlite",
        certainty=Certainty.UNKNOWN,
        instance_id=None,
        reason="this backend has no cluster identifier",
    )


#: (label, expected_uuid, expected_instance, verdict, may_proceed)
TABLE = [
    ("uuid-correct-instance-unset", REAL_UUID, None, IdentityVerdict.CANNOT_TELL, False),
    ("uuid-garbage-instance-unset", WRONG_UUID, None, IdentityVerdict.DIFFERS, False),
    ("uuid-unset-instance-correct", None, REAL_INSTANCE, IdentityVerdict.CANNOT_TELL, False),
    ("uuid-unset-instance-garbage", None, OTHER_INSTANCE, IdentityVerdict.DIFFERS, False),
    ("both-correct", REAL_UUID, REAL_INSTANCE, IdentityVerdict.MATCHES, True),
    ("uuid-garbage-instance-correct", WRONG_UUID, REAL_INSTANCE, IdentityVerdict.DIFFERS, False),
    ("uuid-correct-instance-garbage", REAL_UUID, OTHER_INSTANCE, IdentityVerdict.DIFFERS, False),
    ("both-unset", None, None, IdentityVerdict.CANNOT_TELL, False),
]


@pytest.mark.parametrize(
    "expected_uuid,expected_instance,verdict",
    [(row[1], row[2], row[3]) for row in TABLE],
    ids=[row[0] for row in TABLE],
)
def test_the_verdict_matches_the_agreed_table(expected_uuid, expected_instance, verdict):
    """Every row of the contract decides the verdict the contract states."""
    # Arrange
    observed = _observed()
    # Act
    check = decide_identity(
        observed,
        expected_instance,
        observed_uuid=REAL_UUID,
        expected_uuid=expected_uuid,
    )
    # Assert
    assert check.verdict is verdict


@pytest.mark.parametrize(
    "expected_uuid,expected_instance,may_proceed",
    [(row[1], row[2], row[4]) for row in TABLE],
    ids=[row[0] for row in TABLE],
)
def test_permission_matches_the_agreed_table(expected_uuid, expected_instance, may_proceed):
    """``may_proceed`` is the field callers branch on, so it is pinned too."""
    # Arrange
    observed = _observed()
    # Act
    check = decide_identity(
        observed,
        expected_instance,
        observed_uuid=REAL_UUID,
        expected_uuid=expected_uuid,
    )
    # Assert
    assert check.may_proceed is may_proceed


def test_a_wrong_uuid_is_not_masked_by_a_matching_instance():
    """THE 2026-08-17 DEFECT, stated as its own test rather than a table row.

    This exact combination answered ``matches`` / ``may_proceed=True`` before
    the fix. It is the row the whole card is about, so it gets a name a reader
    will recognise in a failure report.
    """
    # Arrange
    observed = _observed(REAL_INSTANCE)
    # Act
    check = decide_identity(
        observed,
        REAL_INSTANCE,
        observed_uuid=REAL_UUID,
        expected_uuid=WRONG_UUID,
    )
    # Assert
    assert check.verdict is IdentityVerdict.DIFFERS


def test_a_matching_uuid_is_not_evidence_when_the_instance_differs():
    """The live fork: same uuid, two servers, 2026-08-17 on nas-03.

    The ninth row, added because it stopped being hypothetical — two ports on
    one host carried the SAME ``store_uuid`` and different ``system_identifier``
    while one was seven days stale.
    """
    # Arrange
    observed = _observed(OTHER_INSTANCE)
    # Act
    check = decide_identity(
        observed,
        REAL_INSTANCE,
        observed_uuid=REAL_UUID,
        expected_uuid=REAL_UUID,
    )
    # Assert
    assert check.verdict is IdentityVerdict.DIFFERS


def test_a_pinned_uuid_the_store_cannot_report_is_cannot_tell():
    """Unreadable is not "different" — reporting it as one misdirects the fix."""
    # Arrange
    observed = _observed()
    # Act
    check = decide_identity(
        observed,
        REAL_INSTANCE,
        observed_uuid=None,
        expected_uuid=REAL_UUID,
    )
    # Assert
    assert check.verdict is IdentityVerdict.CANNOT_TELL


def test_a_pinned_instance_the_store_cannot_report_is_cannot_tell():
    """Same rule for the other half, so neither can silently pass."""
    # Arrange
    observed = _unreadable()
    # Act
    check = decide_identity(
        observed,
        REAL_INSTANCE,
        observed_uuid=REAL_UUID,
        expected_uuid=REAL_UUID,
    )
    # Assert
    assert check.verdict is IdentityVerdict.CANNOT_TELL


def test_every_refusal_carries_a_reason():
    """A refusal a caller cannot print is a refusal nobody can act on."""
    # Arrange
    refusing_rows = [row for row in TABLE if row[3] is not IdentityVerdict.MATCHES]
    # Act
    refusals = [
        decide_identity(
            _observed(),
            row[2],
            observed_uuid=REAL_UUID,
            expected_uuid=row[1],
        )
        for row in refusing_rows
    ]
    # Assert
    assert all(check.reason for check in refusals)


def test_the_two_guards_share_one_decision_body():
    """Both call sites delegate, so they cannot drift apart again.

    The structural half. The behavioural rows above would all pass again if
    someone re-expressed the comparison inside one of the two guards and got it
    subtly wrong — which is precisely how this defect arose, from two bodies
    "kept IDENTICAL" by discipline.
    """
    # Arrange
    guards = [_store_instance.check_store_identity, _store_pin._check_against]
    # Act
    bodies = [inspect.getsource(guard) for guard in guards]
    # Assert
    assert all("decide_identity(" in body for body in bodies)
