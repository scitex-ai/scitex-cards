#!/usr/bin/env python3
"""The IDENTITY door must refuse an unexpanded shell variable.

WHY THIS FILE EXISTS. `reject_unexpanded_variable` guarded four STORE-TARGET
doors in this package -- `_paths` (x2), `_backend_connect`, `_db`, `_index` --
and identity had NONE. Until 2026-08-21 this held::

    _default_agent("${SCITEX_CARDS_AGENT_ID}")  -> returned it VERBATIM
    _default_agent("$SCITEX_CARDS_AGENT_ID")    -> returned it VERBATIM
    _default_agent("unknown")                   -> refused
    _default_agent("")                          -> resolved from the env, fine

So the door was blind to exactly one thing: UNEXPANDED-VARIABLE SYNTAX. A card
could be attributed to a placeholder that LOOKS like a real name on the board,
which is worse than a blank creator -- a blank is obviously missing.

NOT HYPOTHETICAL. On 2026-07-18/19 fifteen `tasks` rows were written with a
literal `$` in `created_by`. The card that closed that incident asserted "0 rows
carry the literal env var (was 7)" -- true when written, false later, because a
restore brought the rows back and nobody re-measured. That card asked for this
guard in as many words and it was never built.

THE ORDERING THIS PROTECTS. sac injects BOTH the current and the legacy env
spellings on every agent boot, and that is the only reason a bad value does not
appear today. They are holding that compatibility path open until this guard
lands. Sequence: THIS guard -> dotfiles migrates 110 specs -> sac drops
`LEGACY_BOARD_ID_ENV`. Reversing the last two writes the literal again, silently.

THE BARE FORM IS THE HALF THAT NEARLY ESCAPED. `is_unexpanded_variable` matches
`${FOO}` and deliberately NOT bare `$FOO` -- reasonable for a store path, wrong
for an identity, where an unquoted assignment yields exactly the bare form. The
first version of this guard used that helper alone and let
`$SCITEX_CARDS_AGENT_ID` straight through. Hence the `startswith("$")` arm, and
hence the accept-side rows below, which are what stop that arm from growing into
a ban on every dollar in every name.
"""

import pytest

from scitex_cards._model import TaskValidationError
from scitex_cards._store import _default_agent, _resolve_creator_or_raise

PLACEHOLDERS = [
    "${SCITEX_CARDS_AGENT_ID}",
    "$SCITEX_CARDS_AGENT_ID",
    "${FOO}",
    "$FOO",
]

# Names that CONTAIN a dollar but do not BEGIN with one. The guard is
# deliberately narrow -- an agent name never starts with `$`, and a dollar
# elsewhere is nobody's business but the namer's. Without these rows a guard
# that simply rejected any `$` would pass every refusal test above and quietly
# outlaw legitimate identities.
REAL_NAMES_WITH_A_DOLLAR = ["agent-with-$-inside", "a$b"]


@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
def test_default_agent_refuses_an_unexpanded_placeholder(placeholder):
    # Arrange
    door = _default_agent
    # Act
    raised = pytest.raises(TaskValidationError)
    # Assert
    with raised:
        door(placeholder)


@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
def test_the_creator_resolver_refuses_it_too(placeholder):
    # Arrange — `_default_agent` delegates here, so pinning only the delegate
    # would leave the SSOT resolver unguarded for any future direct caller.
    door = _resolve_creator_or_raise
    # Act
    raised = pytest.raises(TaskValidationError)
    # Assert
    with raised:
        door(placeholder)


@pytest.mark.parametrize("name", REAL_NAMES_WITH_A_DOLLAR)
def test_a_dollar_inside_a_name_is_still_a_valid_identity(name):
    # Arrange
    supplied = name
    # Act
    resolved = _default_agent(supplied)
    # Assert
    assert resolved == supplied


def test_an_ordinary_agent_name_still_resolves():
    # Arrange — the control. If this fails the guard is rejecting everything
    # and the refusal tests above would pass for the wrong reason.
    supplied = "scitex-cards"
    # Act
    resolved = _default_agent(supplied)
    # Assert
    assert resolved == supplied


def test_the_refusal_names_the_offending_value():
    # Arrange — an actionable hint must say WHICH value was wrong; a bare
    # "invalid creator" sends the reader to the wrong env var.
    # The empty default matters: if the call unexpectedly SUCCEEDS, `message`
    # stays "" and the assertion fails, so this cannot pass by not raising.
    supplied = "${SCITEX_CARDS_AGENT_ID}"
    message = ""
    # Act
    try:
        _default_agent(supplied)
    except TaskValidationError as exc:
        message = str(exc)
    # Assert
    assert supplied in message


def test_unknown_is_still_refused_by_the_original_check():
    # Arrange — the pre-existing guard must survive this change; the new
    # placeholder branch sits AFTER it and must not shadow it.
    door = _default_agent
    # Act
    raised = pytest.raises(TaskValidationError)
    # Assert
    with raised:
        door("unknown")
