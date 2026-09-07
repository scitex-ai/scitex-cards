#!/usr/bin/env python3
"""The `store=` refusal decides on DISAGREEMENT, not on presence.

Imports the decision module directly and passes it every input. No store is
opened, no environment is read, so these run identically with or without a
PostgreSQL server — which is the point of keeping the rule in a pure module.

The case that matters most is `test__a_redundant_dsn_is_not_refused`: the suite
pins $SCITEX_CARDS_DB to a throwaway DSN and hands tests that same DSN, so a
refusal keyed on the mere PRESENCE of an argument would fail thousands of tests
while reporting no real defect. That test is the guard on the guard.
"""

from pathlib import Path

import pytest

from scitex_cards._store_arg import (
    normalise_store_target,
    store_argument_refusal,
)

PRIMARY = "postgresql://scitex-primary:55432/scitex"


def _refusal(explicit, resolved=PRIMARY, backend="postgresql", verb="add_task"):
    return store_argument_refusal(
        explicit, resolved_target=resolved, backend=backend, verb=verb
    )


# ---------------------------------------------------------------- refuses


def test__a_directory_is_refused_on_a_server_backend():
    """The reported defect: a temp dir passed to isolate a write."""
    # Arrange
    passed = "/tmp/probe-isolation"
    # Act
    message = _refusal(passed)
    # Assert
    assert message is not None


def test__the_message_shows_what_the_caller_passed():
    """A reader must be able to CHECK the claim, not just trust it."""
    # Arrange
    passed = "/tmp/probe-isolation"
    # Act
    message = _refusal(passed)
    # Assert
    assert passed in message


def test__the_message_shows_where_the_data_would_go():
    """The second half of the pair the consumer asked for."""
    # Arrange
    passed = "/tmp/probe-isolation"
    # Act
    message = _refusal(passed)
    # Assert
    assert PRIMARY in message


def test__the_message_names_the_resolver_that_decided():
    """So the reader can re-run the comparison themselves."""
    # Arrange
    passed = "/tmp/probe-isolation"
    # Act
    message = _refusal(passed)
    # Assert
    assert "resolve_store()" in message


def test__the_message_names_the_verb_that_refused():
    # Arrange
    passed = "/tmp/x"
    # Act
    message = _refusal(passed, verb="comment_task")
    # Assert
    assert "comment_task" in message


def test__a_dsn_is_never_refused_even_for_a_different_database():
    """A DELIBERATE GAP, and the reason matters more than the gap.

    Two spellings can name one store: `postgresql://scitex-primary:55432/scitex`
    and `postgresql://100.64.0.5:55432/scitex` are the same database and unequal
    as strings. Comparing DSN text would refuse callers who did nothing wrong —
    a LOUD wrong answer, the mirror of the silent one this module exists to
    stop. So no DSN argument is refused until identity can be compared properly,
    and that includes one naming a genuinely different database.
    """
    # Arrange
    other = "postgresql://scitex-primary:55432/scitex_cards"
    # Act
    message = _refusal(other)
    # Assert
    assert message is None


def test__an_equivalent_dsn_spelling_is_not_refused():
    """The case that would fail under string equality.

    Same store, different spelling — a host alias resolved to its address. A
    rule keyed on text would refuse this correct-and-redundant argument.
    """
    # Arrange
    by_address = "postgresql://100.64.0.5:55432/scitex"
    # Act
    message = _refusal(by_address)
    # Assert
    assert message is None


def test__a_credentialled_spelling_of_the_resolved_store_is_not_refused():
    """Adding a user does not make it a different store."""
    # Arrange
    with_user = "postgresql://scitex_cards@scitex-primary:55432/scitex"
    # Act
    message = _refusal(with_user)
    # Assert
    assert message is None


def test__a_server_target_is_detected_from_the_scheme_without_a_backend():
    """A caller holding only the target must still get the right answer."""
    # Arrange
    passed = "/tmp/x"
    # Act
    message = _refusal(passed, backend=None)
    # Assert
    assert message is not None


def test__a_path_object_is_accepted_not_just_a_string():
    """Callers pass Path as readily as str; the rule must not care."""
    # Arrange
    passed = Path("/tmp/x")
    # Act
    message = _refusal(passed)
    # Assert
    assert message is not None


# ---------------------------------------------------------------- allows


def test__no_argument_is_never_refused():
    """`store=None` means "the ambient store", which is always honoured."""
    # Arrange
    passed = None
    # Act
    message = _refusal(passed)
    # Assert
    assert message is None


def test__a_redundant_dsn_is_not_refused():
    """THE GUARD ON THE GUARD.

    The suite passes the SAME DSN it pinned $SCITEX_CARDS_DB to. That argument
    is redundant, not wrong, and refusing it would break the suite while
    reporting nothing real. The discriminator is disagreement.
    """
    # Arrange
    passed = PRIMARY
    # Act
    message = _refusal(passed)
    # Assert
    assert message is None


def test__a_trailing_slash_is_not_a_different_store():
    # Arrange
    passed = PRIMARY + "/"
    # Act
    message = _refusal(passed)
    # Assert
    assert message is None


def test__a_file_backend_honours_a_path_so_nothing_is_refused():
    """The rule is backend-specific: where `store=` WORKS it must stay silent."""
    # Arrange
    passed = "/tmp/x"
    # Act
    message = _refusal(passed, resolved="/var/lib/cards/tasks.yaml", backend="yaml")
    # Assert
    assert message is None


# ---------------------------------------------------------------- normalise


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  /tmp/x  ", "/tmp/x"),
        ("/tmp/x/", "/tmp/x"),
        (PRIMARY + "/", PRIMARY),
        ("/", "/"),
    ],
)
def test__normalise_strips_only_whitespace_and_one_trailing_slash(raw, expected):
    # Arrange
    value = raw
    # Act
    got = normalise_store_target(value)
    # Assert
    assert got == expected


def test__normalise_is_not_a_dsn_comparator():
    """Two spellings of one database are NOT equal after normalising.

    Kept as a warning, not a feature: it is exactly why
    `store_argument_refusal` never compares one DSN against another. Anyone
    tempted to reintroduce a text comparison should read this assertion as the
    reason it cannot work.
    """
    # Arrange
    creds = "postgresql://user@scitex-primary:55432/scitex"
    # Act
    got = normalise_store_target(creds)
    # Assert
    assert got != normalise_store_target(PRIMARY)


# EOF
