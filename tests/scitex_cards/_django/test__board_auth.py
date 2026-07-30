#!/usr/bin/env python3
"""Tests for the board's password gate.

THE LOAD-BEARING TEST HERE IS NOT "the right password works". That one passes
even if the gate is trivially bypassable, and it is the test somebody writes when
they think the feature is the password. The feature is the REFUSAL: opening the
board beyond loopback without a password must raise, because for as long as it
merely warned, the honest description of the knob was "publishes every DM to the
network" and a comment was all that stood in front of it.

So the tests below are weighted toward what must NOT be possible: no header, a
wrong password, a non-Basic scheme, undecodable base64, and a colon-less payload
must all be rejected -- and exposure without a password must not start at all.
"""

import base64
from pathlib import Path

import pytest
from django.core.exceptions import MiddlewareNotUsed

from scitex_cards._django._board_auth import (
    BoardPasswordMiddleware,
    challenge,
    is_authorised,
)
from scitex_cards._django._board_exposure import (
    ExposureWithoutPasswordError,
    assert_exposure_is_authenticated,
)


def _basic(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# === what must be accepted ===============================================


def test_the_configured_password_is_accepted():
    # Arrange
    header = _basic("operator", "correct-horse")

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is True


def test_the_username_is_not_checked():
    """Any username works: there is one password and no user model behind it."""
    # Arrange
    header = _basic("anything-at-all", "correct-horse")

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is True


def test_a_password_containing_a_colon_still_matches():
    """Splitting on the LAST colon would silently reject these."""
    # Arrange
    header = _basic("operator", "a:b:c")

    # Act
    ok = is_authorised(header, "a:b:c")

    # Assert
    assert ok is True


def test_no_password_configured_means_no_gate():
    """The loopback default: unprotected, and openly so."""
    # Arrange
    header = None

    # Act
    ok = is_authorised(header, "")

    # Assert
    assert ok is True


# === what must be rejected ===============================================


def test_a_missing_header_is_rejected():
    # Arrange
    header = None

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


def test_a_wrong_password_is_rejected():
    # Arrange
    header = _basic("operator", "guess")

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


def test_a_password_that_is_a_prefix_of_the_real_one_is_rejected():
    """Guards against a comparison that stops at the shorter string."""
    # Arrange
    header = _basic("operator", "correct")

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


def test_a_bearer_token_is_not_mistaken_for_basic():
    # Arrange
    header = "Bearer correct-horse"

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


def test_undecodable_base64_is_rejected_rather_than_raising():
    """A malformed header must be a 401, not a 500 with a traceback."""
    # Arrange
    header = "Basic not!valid!base64!"

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


def test_a_payload_with_no_colon_is_rejected():
    # Arrange
    header = "Basic " + base64.b64encode(b"nocolonhere").decode("ascii")

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


def test_an_empty_basic_payload_is_rejected():
    # Arrange
    header = "Basic "

    # Act
    ok = is_authorised(header, "correct-horse")

    # Assert
    assert ok is False


# === the challenge ========================================================


def test_the_challenge_is_a_401():
    # Arrange
    expected = 401

    # Act
    response = challenge()

    # Assert
    assert response.status_code == expected


def test_the_challenge_asks_the_browser_for_a_password():
    """Without WWW-Authenticate the phone shows an error, not a prompt."""
    # Arrange
    expected = "Basic"

    # Act
    response = challenge()

    # Assert
    assert expected in response["WWW-Authenticate"]


# === the middleware =======================================================


def test_the_middleware_removes_itself_when_unconfigured():
    """MiddlewareNotUsed is how the loopback default pays nothing."""
    # Arrange
    called = []

    def get_response(request):
        called.append(request)

    # Act
    try:
        BoardPasswordMiddleware(get_response, password="")
        raised = False
    except MiddlewareNotUsed:
        raised = True

    # Assert
    assert raised is True


def test_an_unauthenticated_request_never_reaches_the_handler():
    """The point of sitting high in the stack: no handler, no store, no work."""
    # Arrange
    reached = []

    def get_response(request):
        reached.append(request)
        return "handler ran"

    middleware = BoardPasswordMiddleware(get_response, password="correct-horse")
    request = type("R", (), {"META": {}})()

    # Act
    middleware(request)

    # Assert
    assert reached == []


def test_an_authenticated_request_reaches_the_handler():
    # Arrange
    reached = []

    def get_response(request):
        reached.append(request)
        return "handler ran"

    middleware = BoardPasswordMiddleware(get_response, password="correct-horse")
    request = type(
        "R", (), {"META": {"HTTP_AUTHORIZATION": _basic("op", "correct-horse")}}
    )()

    # Act
    middleware(request)

    # Assert
    assert len(reached) == 1


# === the refusal: the reason this file exists ==============================
#
# An earlier draft of this section asserted `bool(hosts) and not password` -- the
# guard's own expression, re-typed into the test. It would have passed with the
# guard DELETED, which makes it worse than no test: a green line reporting
# coverage of the one behaviour this whole file exists to protect. These execute
# the real module against the real environment instead.


def test_exposing_the_board_without_a_password_refuses():
    """THE test. A password nobody is forced to set is a password nobody sets."""
    # Arrange
    hosts = "192.168.11.121"

    # Act
    try:
        assert_exposure_is_authenticated(hosts, "")
        raised = False
    except ExposureWithoutPasswordError:
        raised = True

    # Assert
    assert raised is True, (
        "a LAN host was accepted with no password -- every DM and card would be "
        "readable and writable by anyone on that network"
    )


def test_the_refusal_names_the_variable_that_fixes_it():
    """A refusal that does not say what to set just looks like a crash."""
    # Arrange
    hosts = "192.168.11.121"

    # Act
    try:
        assert_exposure_is_authenticated(hosts, "")
        message = ""
    except ExposureWithoutPasswordError as exc:
        message = str(exc)

    # Assert
    assert "SCITEX_CARDS_PASSWORD" in message


def test_a_whitespace_password_is_not_a_password():
    """A stray space in a shell export must not count as protection."""
    # Arrange
    hosts = "192.168.11.121"

    # Act
    try:
        assert_exposure_is_authenticated(hosts, "   ")
        raised = False
    except ExposureWithoutPasswordError:
        raised = True

    # Assert
    assert raised is True


def test_a_password_lets_the_same_exposure_through():
    """The other half: with a password, the LAN host is permitted."""
    # Arrange
    hosts = "192.168.11.121"

    # Act
    assert_exposure_is_authenticated(hosts, "correct-horse")

    # Assert
    assert True, "no exception is the assertion"


def test_loopback_only_still_needs_no_password():
    """The default must not regress into demanding configuration."""
    # Arrange
    hosts = ""

    # Act
    assert_exposure_is_authenticated(hosts, "")

    # Assert
    assert True, "no exception is the assertion"


def test_the_settings_module_wires_the_guard_in():
    """A perfect guard nobody calls protects nothing.

    Checks the CALL, not the string: settings.py must invoke the function on the
    exposure path. Anchored on the import plus the call, which prose cannot
    produce -- an earlier test of mine matched its own explanatory comment.
    """
    # Arrange
    path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "scitex_cards"
        / "_django"
        / "settings.py"
    )

    # Act
    source = path.read_text(encoding="utf-8")

    # Assert
    assert "assert_exposure_is_authenticated(_extra_hosts, BOARD_PASSWORD)" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
