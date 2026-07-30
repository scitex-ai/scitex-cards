#!/usr/bin/env python3
"""scitex-hub's identity validator must never accept what ours refuses.

WHY THIS FILE EXISTS, and it is not the same test as test__workspace.py.

Two components now decide independently whether a workspace identity is valid:
hub validates at the injection point, we validate in the resolver. On 2026-07-30 we
agreed the safe property is `hub ACCEPTS ⊆ cards ACCEPTS` - if hub is stricter,
anything it passes we pass, and we cannot disagree in the direction that matters. The
dangerous direction is hub being LOOSER: hub injects a value it considers valid and
our resolver 400s a real workspace in production.

THEN THE AGREEMENT BROKE TWICE, IN WRITING, BEFORE ANYONE RAN IT.

  attempt 1   hub proposed  ^[a-z0-9-]{1,63}$
              accepts a LEADING HYPHEN, which we refuse. 61 disagreements.
              hub caught this themselves by writing both patterns side by side.

  attempt 2   hub proposed  ^[a-z0-9][a-z0-9-]{0,62}$
              looks like a subset. IS NOT. In Python, `$` matches at the end of the
              string OR JUST BEFORE A TRAILING NEWLINE; `\\Z` matches only the
              absolute end. So `acme\\n` is accepted by hub and refused by us.
              24 disagreements. BOTH of us reviewed this pattern in writing,
              specifically on the subset question, and BOTH missed it.

What found it was not a third reading. It was enumerating a hostile alphabet and
diffing the two accept sets - 3,615 strings, under a second. hub then reproduced the
same 61, the same 24, and the same first witnesses independently, which is what makes
it a fact rather than two agents agreeing on a story.

A trailing newline is not hypothetical here: `SLUG=$(cmd)` keeps it, and any value
read from a file has one. That is the realistic path by which a real workspace would
have 400'd.

TWO DESIGN NOTES, both from hub and both right:

1. ASSERT THE DIRECTION, NOT EQUALITY. We deliberately differ on underscore - we
   allow it, hub does not. An equality test would go red the moment either side
   intentionally widens, and a test that fails on correct behaviour gets relaxed
   rather than read. So this asserts only `hub ⊆ cards`, and lets the reverse
   difference stand as intended.

2. THE PATTERN BELOW IS A TRANSCRIPTION, and that is the remaining weakness. hub will
   send the exact string from their merged code; until then this is what they told us
   in a message, and transcription is precisely how the `$`/`\\Z` bug survived two
   reviews. Marked so nobody mistakes it for verified.
"""

import itertools
import re

import pytest

from scitex_cards._workspace import is_valid_identity

#: hub's validator, TRANSCRIBED from their message of 2026-07-30 (not yet read from
#: their repo - see note 2 above). `\A...\Z`, deliberately not `$`.
HUB_IDENTITY_PATTERN = r"\A[a-z0-9][a-z0-9-]{0,62}\Z"

#: Deliberately hostile. Every character here has broken something in this codebase:
#: `.` and `/` form traversal, `\` is the other separator, NUL defeated Read, grep
#: and length(), TAB/NEWLINE smuggle a second value past an unanchored pattern, `%`
#: begins an encoded traversal, `A` is the case that gives one workspace two
#: spellings, and `_` is the one character on which we and hub intentionally differ.
_ALPHABET = "az09-_.A/\\ \t\n\x00%"


def _hub_accepts(value: str) -> bool:
    return re.match(HUB_IDENTITY_PATTERN, value) is not None


# === the property that matters ===========================================


def test_nothing_hub_accepts_is_refused_by_cards():
    """THE TEST. Direction only: hub ⊆ cards. Not equality.

    Exhaustive over short strings, which is where anchor bugs live - the `$`
    disagreement's first witness was two characters long.
    """
    # Arrange
    disagreements = []

    # Act
    for length in (1, 2, 3):
        for combo in itertools.product(_ALPHABET, repeat=length):
            candidate = "".join(combo)
            if _hub_accepts(candidate) and not is_valid_identity(candidate):
                disagreements.append(candidate)

    # Assert
    assert disagreements == [], (
        f"hub accepts {len(disagreements)} value(s) that cards refuses, so hub can "
        f"inject an identity that 400s in production. First: {disagreements[:5]!r}"
    )


def test_the_reverse_difference_is_intended_and_still_present():
    """A control. If this ever fails, the two patterns have converged - which is
    fine, but it means the underscore decision changed and somebody should know."""
    # Arrange
    underscored = "acme_corp"

    # Act
    ours, theirs = is_valid_identity(underscored), _hub_accepts(underscored)

    # Assert
    assert (ours, theirs) == (True, False)


# === regression pins for the two patterns that were WRONG =================


@pytest.mark.parametrize(
    "rejected_pattern, witness",
    [
        (r"^[a-z0-9-]{1,63}$", "-"),  # attempt 1: leading hyphen
        (r"^[a-z0-9][a-z0-9-]{0,62}$", "a\n"),  # attempt 2: `$` before newline
    ],
)
def test_the_previously_proposed_patterns_really_did_disagree(
    rejected_pattern, witness
):
    """Proves these tests would have CAUGHT the two bugs, rather than asserting they
    are absent now. A guard nobody has seen fail is not known to work - and four of
    my checks proved vacuous on 2026-07-30 for exactly that reason."""
    # Arrange
    pattern = re.compile(rejected_pattern)

    # Act
    hub_would_accept = pattern.match(witness) is not None
    cards_refuses = not is_valid_identity(witness)

    # Assert
    assert hub_would_accept and cards_refuses


def test_dollar_anchor_is_not_end_of_string_in_python():
    """The language fact the whole incident rests on, pinned so it is not folklore.

    If a future Python changed this, the reasoning above would need revisiting - and
    a comment saying "$ also matches before a trailing newline" is not the same as a
    test that fails when it does not.
    """
    # Arrange
    value = "acme\n"

    # Act
    dollar = re.match(r"^[a-z]+$", value) is not None
    upper_z = re.match(r"\A[a-z]+\Z", value) is not None

    # Assert
    assert (dollar, upper_z) == (True, False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
