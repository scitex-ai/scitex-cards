#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A VIEWER IS RESOLVED THROUGH THE USER REGISTRY, and two aliases are one person.

Companion to ``test__board_identity.py``, which covers the precedence chain
against an EMPTY registry -- every identity there falls through to the verbatim
string. That left ``_registered_name`` itself, the branch that actually consults
:func:`scitex_cards._users.resolve_user`, with no coverage at all: the tests
passed because nothing was registered, which is the harness agreeing with
itself rather than the code being exercised.

WHAT THIS PINS, and why each matters to "whose cards am I looking at?":

* AN ALIAS RESOLVES TO THE CANONICAL NAME. ``names`` is an alias list -- a
  rename keeps the old name so historical card references still resolve -- so
  one person can arrive under several strings. The viewer must collapse to ONE
  of them or the same human gets two different-looking boards depending on
  which name they were configured with.
* A ``host@name`` JOIN KEY RESOLVES. This is the card's "multi-instance safety
  via host_at_name" constraint reaching the identity layer.
* AN UNREGISTERED NAME IS STILL A VIEWER. Card owners are free-form strings
  that predate the registry, so refusing them would lock out precisely the
  boards that never registered anybody.

ON MULTI-INSTANCE COLLISION, since the card raised it: names are UNIQUE across
a registry, enforced at the WRITE path (``register_user`` rejects a name that
already maps to another user, via ``_names_index``). So within one cards store
a name cannot mean two people, and two instances either share that store -- in
which case its uniqueness rule governs both -- or use separate stores, which
are separate namespaces by construction. The bare-name collision the constraint
guards against is therefore unreachable here rather than merely unlikely, and
the last test in this file pins the property that argument rests on.

Real users written to a real scratch store. No mocks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.core import signing  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django._board_identity import resolve_viewer  # noqa: E402
from scitex_cards._django._board_login import (  # noqa: E402
    COOKIE_NAME,
    SIGNING_SALT,
)
from scitex_cards._users import register_user  # noqa: E402
from scitex_cards._users._model import UserValidationError  # noqa: E402


def _as(name: str):
    """A request identified as ``name`` through a REAL signed board cookie."""
    request = RequestFactory().get("/me/cards")
    request.COOKIES[COOKIE_NAME] = signing.dumps(
        {"v": 2, "sub": name}, salt=SIGNING_SALT
    )
    return request


@pytest.fixture
def registry() -> Path:
    """The per-test USER REGISTRY path, resolved the way the registry does.

    NOT ``SCITEX_CARDS_DB``. That is the CARDS store -- a SQLite/Postgres
    database -- whereas ``users:`` is a YAML section whose path comes from
    ``resolve_tasks_path``. Handing the database over is exactly what surfaced
    the production defect fixed alongside this file: the YAML loader tried to
    decode a SQLite header and raised ``UnicodeDecodeError: … byte 0x89``.
    Registering through the registry's OWN resolution is both correct and what
    the code under test now does.
    """
    from scitex_cards._paths import resolve_tasks_path

    return resolve_tasks_path(None)


@pytest.fixture(autouse=True)
def no_ambient_identity():
    """Keep a developer machine's ``SCITEX_CARDS_IDENTITY`` out of these tests."""
    from scitex_cards._django._board_identity import ENV_IDENTITY

    original = os.environ.get(ENV_IDENTITY)
    os.environ.pop(ENV_IDENTITY, None)
    yield
    if original is not None:
        os.environ[ENV_IDENTITY] = original


@pytest.fixture
def renamed_person(registry):  # noqa: ARG001 -- ordering, not a value
    """One registered human carrying a current name AND a historical alias."""
    return register_user(
        kind="human",
        names=["ywatanabe", "yusuke-watanabe"],
    )


# --- aliases collapse to one viewer ---------------------------------------


def test_the_canonical_name_resolves_to_itself(renamed_person):
    """The base case, so the alias tests below cannot pass vacuously."""
    # Arrange
    expected = "ywatanabe"
    # Act
    viewer = resolve_viewer(_as("ywatanabe"))
    # Assert
    assert viewer.name == expected


def test_a_historical_alias_resolves_to_the_canonical_name(renamed_person):
    """A rename must not orphan the person from their own cards."""
    # Arrange
    expected = "ywatanabe"
    # Act
    viewer = resolve_viewer(_as("yusuke-watanabe"))
    # Assert
    assert viewer.name == expected


def test_both_aliases_yield_the_same_viewer(renamed_person):
    """THE REASON ``_registered_name`` RETURNS ``names[0]``.

    Returning the string as supplied would give one human two different-
    looking boards depending on which of their own names they signed in with.
    """
    # Arrange
    current = resolve_viewer(_as("ywatanabe"))
    # Act
    historical = resolve_viewer(_as("yusuke-watanabe"))
    # Assert
    assert historical.name == current.name


# --- the host@name join key -----------------------------------------------


def test_a_host_at_name_join_key_resolves_to_the_user(registry):  # noqa: ARG001
    """The card's multi-instance constraint, reaching the identity layer."""
    # Arrange
    register_user(
        kind="agent",
        names=["scitex-cards-gui"],
        host_at_name="scitex-compute-04@scitex-cards-gui",
    )
    # Act
    viewer = resolve_viewer(_as("scitex-compute-04@scitex-cards-gui"))
    # Assert
    assert viewer.name == "scitex-cards-gui"


# --- the unregistered case, which must keep working -----------------------


def test_an_unregistered_name_is_still_a_viewer(renamed_person):
    """Card owners predate the registry and are still free-form strings.

    Refusing them would lock out exactly the boards that never registered
    anybody -- which is most of them today.
    """
    # Arrange
    expected = "never-registered"
    # Act
    viewer = resolve_viewer(_as("never-registered"))
    # Assert
    assert viewer.name == expected


def test_an_unregistered_name_does_not_borrow_a_registered_identity(
    renamed_person,
):
    """The verbatim fallback must not drift into a nearest-match guess.

    A resolution that "helpfully" matched an unknown string to a similar
    registered one would serve that person's cards to whoever typed it.
    """
    # Arrange
    registered = "ywatanabe"
    # Act
    viewer = resolve_viewer(_as("ywatanabe-typo"))
    # Assert
    assert viewer.name != registered


# --- rung 1: the hub seam, on a registry that can actually answer ---------


class _SignedInUser:
    """An authenticated user, reduced to the two attributes the code reads."""

    is_authenticated = True

    def __init__(self, email: str) -> None:
        self.email = email


def _signed_in_as(email: str):
    """A request carrying an authenticated session for ``email``."""
    request = RequestFactory().get("/me/cards")
    request.user = _SignedInUser(email)
    return request


@pytest.fixture
def linked_account(registry):  # noqa: ARG001 -- ordering, not a value
    """A person whose VERIFIED EMAIL is registered as one of their aliases.

    THIS IS THE CLAIM I MADE TO scitex-hub, so it gets a test rather than a
    promise: they can link an account today with NO schema change, because
    ``names`` is explicitly an alias list and an email is a legitimate alias.
    If that stops being true, this fails and the advice on their card is
    withdrawn by the test suite rather than by somebody noticing.
    """
    return register_user(
        kind="human",
        names=["ywatanabe", "ywata1989@gmail.com"],
    )


def test_a_linked_session_email_resolves_to_the_board_identity(linked_account):
    """THE HUB SEAM WORKING -- the success path rung 1 exists for.

    Every other rung-1 test in this suite runs against an EMPTY registry, so
    they all take the unlinked branch and none of them proves this branch is
    reachable at all. Without this test, ``source="session-user"`` is a string
    that appears in the source and in no assertion anywhere.
    """
    # Arrange
    expected = "ywatanabe"
    # Act
    viewer = resolve_viewer(_signed_in_as("ywata1989@gmail.com"))
    # Assert
    assert viewer.name == expected


def test_a_linked_session_reports_the_session_as_its_source(linked_account):
    """The rung is named in the payload so a deployment is diagnosable."""
    # Arrange
    expected = "session-user"
    # Act
    viewer = resolve_viewer(_signed_in_as("ywata1989@gmail.com"))
    # Assert
    assert viewer.source == expected


def test_a_linked_session_still_reports_the_email_it_came_from(linked_account):
    """Carried on success too, not only in the unlinked case."""
    # Arrange
    expected = "ywata1989@gmail.com"
    # Act
    viewer = resolve_viewer(_signed_in_as("ywata1989@gmail.com"))
    # Assert
    assert viewer.email == expected


def test_an_unknown_email_is_unlinked_even_when_others_are_registered(
    linked_account,
):
    """THE NON-VACUOUS VERSION of the unlinked test.

    The sibling suite asserts this against an empty registry, where NOTHING
    resolves and the test cannot distinguish "correctly refused" from "the
    lookup is not wired up". Here a registry that demonstrably CAN resolve an
    email refuses a different one.
    """
    # Arrange
    expected = "unlinked-email"
    # Act
    viewer = resolve_viewer(_signed_in_as("stranger@example.com"))
    # Assert
    assert viewer.source == expected


def test_an_unknown_email_yields_no_identity_beside_a_registered_one(
    linked_account,
):
    """The leak check, with a real neighbour to leak INTO.

    A stranger must not end up as ``ywatanabe`` because that user happens to
    exist in the registry the lookup just consulted.
    """
    # Arrange
    neighbour = "ywatanabe"
    # Act
    viewer = resolve_viewer(_signed_in_as("stranger@example.com"))
    # Assert
    assert viewer.name != neighbour


# --- the property the collision argument rests on -------------------------


def test_a_name_cannot_be_claimed_by_two_users(renamed_person):
    """NAMES ARE UNIQUE PER REGISTRY, enforced at the write path.

    This is load-bearing for the whole identity layer, not a registry
    curiosity: it is WHY resolving a viewer by name cannot land on two
    different people, and therefore why the multi-instance bare-name
    collision the card asked about is unreachable rather than merely
    unlikely. Asserted here because ``_board_identity`` depends on it and
    would resolve ambiguously if it ever stopped holding.
    """
    # Arrange
    taken = "ywatanabe"

    def claim_it():
        register_user(kind="human", names=[taken])

    # Act
    outcome = pytest.raises(UserValidationError)
    # Assert
    with outcome:
        claim_it()


# EOF
