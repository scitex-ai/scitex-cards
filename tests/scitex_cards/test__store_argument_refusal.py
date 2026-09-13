#!/usr/bin/env python3
"""The `store=` refusal decides on DISAGREEMENT, not on presence.

Imports the decision module directly and passes it every input. No store is
opened, no environment is read, so these run identically with or without a
PostgreSQL server — which is the point of keeping the rule in a pure module.

The case that matters most is `test__a_redundant_dsn_is_not_refused`: the suite
pins $SCITEX_STORE_DSN to a throwaway DSN and hands tests that same DSN, so a
refusal keyed on the mere PRESENCE of an argument would fail thousands of tests
while reporting no real defect. That test is the guard on the guard.
"""

import contextlib
import os
import warnings
from pathlib import Path

import pytest

from scitex_cards._store_arg import (
    deliver_refusal,
    normalise_store_target,
    store_argument_refusal,
    strict_store_arg,
)

STRICT_ENV = "SCITEX_CARDS_STRICT_STORE_ARG"

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

    The suite passes the SAME DSN it pinned $SCITEX_STORE_DSN to. That argument
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


# ------------------------------------------------- warn by default, raise strict
#
# THE DOCSTRING ABOVE PREDICTED THIS AND THE GUARD-ON-THE-GUARD MISSED IT. It
# says a refusal keyed on mere PRESENCE "would fail thousands of tests while
# reporting no real defect", and `test__a_redundant_dsn_is_not_refused` guards
# exactly that -- for DSNs. The suite also hands tests PATH labels, and on the
# real-PostgreSQL job every one of those armed the rule: 357 failed + 219 errors
# (job 101605547958, 2026-09-07). So the delivery warns by default and raises
# only when a caller opts in.
#
# NO MOCKS (PA-306 SS3). `deliver_refusal` reads the real environment and nothing
# else, so these set the real variable and put it back. The first cut of these
# tests used pytest fixture-patching to fake `resolve_store()`; the audit refused
# it, correctly -- a faked resolver would have tested my stub, not the switch.


@contextlib.contextmanager
def _strict(value):
    """Set the REAL env var (None removes it) and restore whatever was there."""
    was = os.environ.get(STRICT_ENV)
    if value is None:
        os.environ.pop(STRICT_ENV, None)
    else:
        os.environ[STRICT_ENV] = value
    try:
        yield
    finally:
        if was is None:
            os.environ.pop(STRICT_ENV, None)
        else:
            os.environ[STRICT_ENV] = was


def test__strict_mode_is_off_unless_asked_for():
    # Arrange
    with _strict(None):
        # Act
        got = strict_store_arg()
    # Assert
    assert got is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test__strict_mode_accepts_the_usual_spellings(raw):
    # Arrange
    with _strict(raw):
        # Act
        got = strict_store_arg()
    # Assert
    assert got is True


def test__an_ineffective_store_warns_rather_than_raising_by_default():
    """576 broken tests is not a guard, it is an outage."""
    # Arrange
    message = "add_task(store=...): selects a LOCAL FILE PATH"

    # Act
    def act():
        with _strict(None):
            deliver_refusal(message)

    # Assert
    with pytest.warns(DeprecationWarning):
        act()


def test__the_default_delivery_does_not_raise():
    # Arrange
    with _strict(None), warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        # Act
        result = deliver_refusal("add_task(store=...): a message")
    # Assert
    assert result is None


def test__an_ineffective_store_raises_under_strict_mode():
    """The hard answer stays available to whoever wants it today."""

    # Arrange
    message = "add_task(store=...): selects a LOCAL FILE PATH"

    # Act
    def act():
        with _strict("1"):
            deliver_refusal(message)

    # Assert
    with pytest.raises(ValueError, match="LOCAL FILE PATH"):
        act()


def test__the_delivered_message_is_carried_through_verbatim():
    """The message names the passed label and the real target; delivery must
    not paraphrase it, or the caller cannot act on it."""
    # Arrange
    text = "add_task(store='/tmp/x/tasks.yaml'): data would go to " + PRIMARY
    # Act
    with _strict(None), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deliver_refusal(text)
    # Assert
    assert str(caught[0].message) == text


# EOF
