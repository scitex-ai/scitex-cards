#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A board bound to a public hostname must be able to authenticate its callers.

THE DEFECT THIS FILE EXISTS FOR, stated so it cannot be re-introduced by someone
who reads the branch and concludes it is guarded.

``SCITEX_CARDS_PUBLIC_HOST`` used to add a public hostname to ``ALLOWED_HOSTS``
while asserting only that ``DJANGO_SECRET_KEY`` was set. That check is real, but
it measures a DIFFERENT property: it makes session and CSRF signatures
unforgeable. It says nothing about who may send a request. So a board behind an
enforcing Cloudflare Access policy and a board behind nothing at all produced
byte-identical settings -- two states with one representation, and the unsafe one
rendering as the safe one at exactly the moment it mattered.

A security-shaped check sitting on that branch made it worse rather than better,
because it reads as "this path is guarded" and terminates the question.

THE RULE: the board authenticates its own callers, the way sshd does -- a key or
a password, never neither. A proxy in front is a SECOND layer, never the
boundary.

An earlier version of this gate also accepted "something in front authenticates
for me" as a written, auditable claim. That was a third concept nobody asked
for, and it was the only path to a naked origin. The operator's ruling replaced
it with the simpler rule, which is also the stronger one: a misconfigured Access
policy stops being a breach, and the standalone case -- the same code path with
no proxy at all -- stays honest.

WHY THESE TESTS USE try/except RATHER THAN ``pytest.raises``: the AAA markers
this repo requires cannot be placed around a ``with pytest.raises(...)`` block
without fusing Act and Assert into one line. Catching explicitly keeps the call
under ``# Act`` and the verdict under ``# Assert``, which is what the structure
is for.
"""

from __future__ import annotations

from scitex_cards._django._board_exposure import (
    PublicExposureWithoutAuthError,
    assert_public_exposure_is_authenticated,
)


def _refusal(host: str, password: str):
    """Call the gate and hand back the refusal it raised, or ``None``."""
    try:
        assert_public_exposure_is_authenticated(host, password)
    except PublicExposureWithoutAuthError as error:
        return error
    return None


class TestABoardWithNoLoginCannotGoPublic:
    """The unsafe state is unreachable rather than discouraged."""

    def test_public_host_without_a_password_is_refused(self):
        # Arrange
        host, password = "cards.example.org", ""

        # Act
        refusal = _refusal(host, password)

        # Assert
        assert refusal is not None

    def test_whitespace_password_does_not_count_as_a_password(self):
        # Arrange -- a stray space in a shell export must not open the board.
        host, password = "cards.example.org", "   "

        # Act
        refusal = _refusal(host, password)

        # Assert
        assert refusal is not None

    def test_refusal_names_the_secret_key_check_as_a_different_property(self):
        # Arrange -- the message has to stop the next maintainer concluding
        # that DJANGO_SECRET_KEY already covered this.
        host = "cards.example.org"

        # Act
        refusal = _refusal(host, "")

        # Assert
        assert "DJANGO_SECRET_KEY" in str(refusal)

    def test_refusal_names_the_remedy(self):
        # Arrange -- an error that does not say how to fix it costs a search.
        host = "cards.example.org"

        # Act
        refusal = _refusal(host, "")

        # Assert
        assert "SCITEX_CARDS_PASSWORD" in str(refusal)

    def test_refusal_says_a_proxy_is_a_second_layer_not_the_boundary(self):
        # Arrange -- someone deploying behind Cloudflare Access will otherwise
        # read this refusal as "the tunnel should have been enough".
        host = "cards.example.org"

        # Act
        refusal = _refusal(host, "")

        # Assert
        assert "second layer" in str(refusal).lower()


class TestTheAcceptedConfigurations:
    """Exactly two states load: authenticated, or not public at all."""

    def test_a_board_password_permits_the_public_binding(self):
        # Arrange
        host, password = "cards.example.org", "s3cret"

        # Act
        refusal = _refusal(host, password)

        # Assert
        assert refusal is None

    def test_no_public_host_needs_no_password(self):
        # Arrange -- loopback only; the operating system is the access control.
        host, password = "", ""

        # Act
        refusal = _refusal(host, password)

        # Assert
        assert refusal is None

    def test_whitespace_only_host_is_not_a_public_binding(self):
        # Arrange
        host, password = "   ", ""

        # Act
        refusal = _refusal(host, password)

        # Assert
        assert refusal is None


class TestThereIsNoThirdOption:
    """The gate takes a password or nothing -- no delegation parameter."""

    def test_the_gate_accepts_exactly_two_arguments(self):
        # Arrange -- a third parameter would be somewhere to put "a proxy
        # authenticates for me", which is the escape that was removed.
        import inspect

        signature = inspect.signature(assert_public_exposure_is_authenticated)

        # Act
        parameters = list(signature.parameters)

        # Assert
        assert parameters == ["public_host", "password"]


# EOF
