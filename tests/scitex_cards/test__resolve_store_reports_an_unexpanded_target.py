#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`resolve_store` must SHOW an unexpanded target, not just refuse it elsewhere.

WHY THIS FILE EXISTS. `reject_unexpanded_variable` already guards every door
that OPENS a store (`_paths`, `_backend_connect`, `_db`), so a real read with
`SCITEX_CARDS_DB='${SCITEX_CARDS_DB}'` correctly fails. But `resolve_store` --
the verb an agent runs precisely when it is confused about its configuration --
reported `backend: "sqlite"`, `target_is_malformed_dsn: False` and exit 0, and
said nothing about the placeholder.

That is the gap `reject_unexpanded_variable`'s own docstring anticipates:

    "Resolution stays total and silent so a caller that merely REPORTS a
     target can show the ambiguity instead of raising on it."

This dict is that caller, and it was not consulting the detector built for it.

Measured 2026-08-21 by claude-code-telegrammer on the live fleet: with the
literal set, `resolve_store` returned success while an actual read exited 1.
The system was safe; the diagnostic was silent.

NO MOCKS. `resolve_store` takes the target as an argument, so every case here
is a plain call -- no environment mutation, no monkeypatch, nothing to leak
into a sibling test.
"""

from __future__ import annotations

from scitex_cards._store import resolve_store

BRACED = "${SCITEX_CARDS_DB}"
COMMAND_SUBSTITUTION = "$(cat /tmp/whatever)"
PLAIN_PATH = "/tmp/a-real-looking-store.db"


def test_resolve_store_flags_a_braced_placeholder_target():
    # Arrange -- the exact literal measured in the field, brace and all
    target = BRACED
    # Act
    report = resolve_store(target)
    # Assert
    assert report["target_is_unexpanded_variable"] is True


def test_resolve_store_flags_a_command_substitution_target():
    # Arrange -- `$(...)` cannot survive any shell that ran, same as `${...}`
    target = COMMAND_SUBSTITUTION
    # Act
    report = resolve_store(target)
    # Assert
    assert report["target_is_unexpanded_variable"] is True


def test_resolve_store_does_not_flag_a_plain_path():
    """The control. Without it an always-True field would pass every case above."""
    # Arrange
    target = PLAIN_PATH
    # Act
    report = resolve_store(target)
    # Assert
    assert report["target_is_unexpanded_variable"] is False


def test_the_malformed_dsn_field_is_blind_to_a_braced_placeholder():
    """Pins WHY a second field was needed rather than widening the first.

    `target_is_malformed_dsn` asks "does this look like a broken SERVER
    address?" A placeholder is not DSN-shaped -- no `://`, no libpq keyword,
    no bare host:port -- so it answers False, correctly and uselessly.
    """
    # Arrange
    target = BRACED
    # Act
    report = resolve_store(target)
    # Assert
    assert report["target_is_malformed_dsn"] is False


def test_the_new_field_sees_the_braced_placeholder_the_dsn_field_missed():
    # Arrange
    target = BRACED
    # Act
    report = resolve_store(target)
    # Assert
    assert report["target_is_unexpanded_variable"] is True


def test_a_bare_dollar_variable_is_deliberately_not_flagged():
    """`$FOO` is NOT matched, and that gap is chosen rather than overlooked.

    `$` is a legal character in a POSIX filename, so a bare-`$` rule could
    refuse a store that works today. `${` and `$(` cannot survive any shell
    that ran, which is what makes them decidable. Pinned so a later "fix"
    that widens the pattern has to argue with this test first.
    """
    # Arrange
    target = "$SCITEX_CARDS_DB"
    # Act
    report = resolve_store(target)
    # Assert
    assert report["target_is_unexpanded_variable"] is False


def test_a_placeholder_is_distinguishable_from_a_healthy_store():
    """The reader-facing property: some reported field must DIFFER.

    The field-level asserts above can all hold while a caller still cannot tell
    a placeholder from a real store, if the differing field is one nobody reads.
    This asserts the discrimination itself, against a plain path as the control.
    """
    # Arrange
    healthy = resolve_store(PLAIN_PATH)
    placeholder = resolve_store(BRACED)
    # Act
    differing = {
        key
        for key in ("target_is_unexpanded_variable", "target_is_malformed_dsn")
        if healthy.get(key) != placeholder.get(key)
    }
    # Assert
    assert "target_is_unexpanded_variable" in differing

# EOF
