#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE BOARD RESOLVES A VIEWER, and resolves NOBODY to nobody.

The phone view (card ``cards-gui-phone-view-own-cards-20260814``) shows "your
own cards", which needs a "your". :mod:`scitex_cards._django._board_identity`
is the one place that answers it, and the property worth pinning is not that
the happy path works -- it is WHAT HAPPENS WHEN IT DOES NOT.

The dangerous outcome for this feature is not an error. It is a full board:
if an unidentified request quietly widened to "show everything", a visitor to
a public scitex.ai would be handed somebody else's cards, and the page would
look exactly like the feature working correctly. So the tests below spend most
of their attention on the negative space -- anonymous, unlinked, tampered --
because that is where a regression would be invisible.

Real signed cookies (``django.core.signing`` with the module's own salt), real
``RequestFactory`` requests, and a real ``os.environ`` mutation restored on
teardown. The only stand-in is an authenticated-user object: the standalone
board settings install no ``django.contrib.auth`` tables, so there is no real
``User`` row to make, and the code reads exactly two attributes off it
(``is_authenticated``, ``email``) -- a hand-rolled fake at a documented
boundary, which is what the no-mocks rule asks for.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("django")

from django.core import signing  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django._board_identity import (  # noqa: E402
    ENV_IDENTITY,
    Viewer,
    resolve_viewer,
)
from scitex_cards._django._board_login import (  # noqa: E402
    COOKIE_NAME,
    SIGNING_SALT,
)


class _SignedInUser:
    """An authenticated user, reduced to the two attributes the code reads."""

    is_authenticated = True

    def __init__(self, email: str) -> None:
        self.email = email


class _AnonymousVisitor:
    """A visitor who never signed in -- Django's ``AnonymousUser`` contract.

    Mirrors the two attributes that class exposes to this code path. The real
    one is unavailable in these settings (see the test that uses this).
    """

    is_authenticated = False
    email = ""


def _request(*, user=None, cookie: str | None = None):
    """A real request, optionally carrying a session user and/or a cookie."""
    request = RequestFactory().get("/mine")
    if user is not None:
        request.user = user
    if cookie is not None:
        request.COOKIES[COOKIE_NAME] = cookie
    return request


def _signed(payload: dict) -> str:
    """A cookie signed with the board's REAL salt, exactly as login issues it."""
    return signing.dumps(payload, salt=SIGNING_SALT)


@pytest.fixture(autouse=True)
def configured_identity():
    """Own ``SCITEX_CARDS_IDENTITY`` for the duration of one test.

    Autouse so the variable starts UNSET in every test: a developer machine
    that exports it would otherwise make each "anonymous" assertion below pass
    for the wrong reason -- rung 3 answering instead of the rung-4 refusal
    actually under test. Tests that want the variable set request this fixture
    by name and call the yielded setter, which writes the real environment;
    teardown restores whatever was there before.
    """
    original = os.environ.get(ENV_IDENTITY)
    os.environ.pop(ENV_IDENTITY, None)

    def _set(value: str) -> None:
        os.environ[ENV_IDENTITY] = value

    yield _set

    if original is None:
        os.environ.pop(ENV_IDENTITY, None)
    else:
        os.environ[ENV_IDENTITY] = original


# --- rung 4: nobody -------------------------------------------------------


def test_a_bare_request_resolves_to_nobody():
    """THE PROPERTY THE FEATURE'S SAFETY RESTS ON."""
    # Arrange
    request = _request()
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


def test_an_unidentified_viewer_names_no_identity():
    """``name`` is None, not "" / "*" / "everyone" -- nothing a filter widens on."""
    # Arrange
    request = _request()
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.name is None


def test_an_unidentified_viewer_reports_itself_as_anonymous():
    """The source is machine-readable so a page can explain, not just fail."""
    # Arrange
    request = _request()
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.source == "anonymous"


# --- rung 3: the configured identity --------------------------------------


def test_a_configured_identity_is_used_when_nothing_stronger_exists(
    configured_identity,
):
    """Today's real deployment: one human, one shared password, one name."""
    # Arrange
    configured_identity("ywatanabe")
    request = _request()
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.name == "ywatanabe"


def test_a_configured_identity_reports_its_source(configured_identity):
    """So an operator can tell WHICH rung answered without reading the code."""
    # Arrange
    configured_identity("ywatanabe")
    request = _request()
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.source == "configured"


def test_a_blank_configured_identity_is_not_an_identity(configured_identity):
    """Whitespace is how an env var is accidentally "set" -- it must not count."""
    # Arrange
    configured_identity("   ")
    request = _request()
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


# --- rung 2: the signed cookie --------------------------------------------


def test_a_signed_cookie_subject_identifies_the_viewer():
    """The escape hatch for a deployment handing over a subject, not a session."""
    # Arrange
    request = _request(cookie=_signed({"v": 2, "sub": "ywatanabe"}))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.name == "ywatanabe"


def test_a_legacy_v1_cookie_carries_no_identity():
    """BACK-COMPAT. Existing ``{"v": 1}`` sessions predate identity entirely.

    They must keep working as sessions (that is ``_board_auth``'s business)
    while claiming to be nobody here -- not be read as some default person.
    """
    # Arrange
    request = _request(cookie=_signed({"v": 1}))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


def test_a_tampered_cookie_grants_no_identity():
    """An identity anyone can type into devtools is not an identity.

    The signature is what makes rung 2 safe to trust at all, so this is the
    test that would catch someone "simplifying" the verification away.
    """
    # Arrange
    request = _request(cookie=_signed({"v": 2, "sub": "ywatanabe"}) + "x")
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


def test_an_unsigned_cookie_grants_no_identity():
    """The obvious forgery: a plain JSON-ish value in the cookie slot."""
    # Arrange
    request = _request(cookie='{"v": 2, "sub": "ywatanabe"}')
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


# --- rung 1: an authenticated session, and the unlinked case ---------------


def test_an_unlinked_session_email_does_not_resolve_to_an_identity():
    """A logged-in stranger is NOT the operator.

    This is the leak the precedence order exists to prevent: rung 1 must not
    fall through to the configured identity, or the first person to sign in to
    a public scitex.ai would be served the operator's own board.
    """
    # Arrange
    request = _request(user=_SignedInUser("stranger@example.com"))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


def test_an_unlinked_session_email_does_not_reach_the_configured_identity(
    configured_identity,
):
    """THE REGRESSION TEST FOR THE ABOVE, with rung 3 armed and waiting."""
    # Arrange
    configured_identity("ywatanabe")
    request = _request(user=_SignedInUser("stranger@example.com"))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.name != "ywatanabe"


def test_an_unlinked_session_is_reported_as_unlinked_rather_than_anonymous():
    """A state the page can explain: "not linked yet" beats "unknown error"."""
    # Arrange
    request = _request(user=_SignedInUser("stranger@example.com"))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.source == "unlinked-email"


def test_an_unlinked_session_reports_the_address_that_needs_linking():
    """Carrying the email is the entire point of the unlinked state."""
    # Arrange
    request = _request(user=_SignedInUser("stranger@example.com"))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.email == "stranger@example.com"


def test_a_visitor_who_never_signed_in_is_not_treated_as_signed_in():
    """A present-but-anonymous user is a NO, and the check must be the flag.

    Django's own ``AnonymousUser`` cannot be constructed here: the standalone
    board settings do not install ``django.contrib.auth``, so importing it
    raises "Model class ... isn't in an application in INSTALLED_APPS". That
    absence is exactly why rung 1 reads the attributes defensively rather than
    isinstance-checking a class this deployment does not have -- so the stand-
    in below carries the same contract Django's class does:
    ``is_authenticated is False``.
    """
    # Arrange
    request = _request(user=_AnonymousVisitor())
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


def test_a_session_user_without_an_email_is_not_identified():
    """An OAuth provider that returns no verified email identifies nobody."""
    # Arrange
    request = _request(user=_SignedInUser(""))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.is_known is False


# --- precedence between rungs ---------------------------------------------


def test_a_cookie_subject_outranks_the_configured_identity(configured_identity):
    """A request that names its own subject beats the server-wide default."""
    # Arrange
    configured_identity("fallback-person")
    request = _request(cookie=_signed({"v": 2, "sub": "cookie-person"}))
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.name == "cookie-person"


def test_a_session_user_outranks_a_cookie_subject():
    """A verified login is a stronger claim than a subject we were handed."""
    # Arrange
    request = _request(
        user=_SignedInUser("stranger@example.com"),
        cookie=_signed({"v": 2, "sub": "cookie-person"}),
    )
    # Act
    viewer = resolve_viewer(request)
    # Assert
    assert viewer.source == "unlinked-email"


# --- the type itself -------------------------------------------------------


def test_is_known_is_false_for_the_default_viewer():
    """The default construction is the SAFE one -- nobody, not everybody."""
    # Arrange
    expected = False
    # Act
    viewer = Viewer()
    # Assert
    assert viewer.is_known is expected


def test_a_named_viewer_is_known():
    """The positive control for ``is_known``, so the tests above can fail."""
    # Arrange
    expected = True
    # Act
    viewer = Viewer(name="ywatanabe", source="configured")
    # Assert
    assert viewer.is_known is expected


# EOF
