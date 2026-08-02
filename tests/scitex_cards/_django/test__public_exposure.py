#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Binding a public hostname refuses unless something names the auth boundary.

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

THE RULE IS NOT "a public board must have a password". Access-only is a
legitimate deployment, and this process genuinely cannot observe whether Access
is enforcing. The rule is that **silence is not the permissive case**: either the
board authenticates its own callers, or someone names the system that does, in a
value that is auditable and that they can be held to. Omission reaches neither,
so omission refuses.

The enforcer set is closed for the same reason. If any non-empty string counted,
a typo or a leftover export would disarm the refusal while looking deliberate.

WHY THESE TESTS USE try/except RATHER THAN ``pytest.raises``: the AAA markers
this repo requires cannot be placed around a ``with pytest.raises(...)`` block
without fusing Act and Assert into one line. Catching explicitly keeps the call
under ``# Act`` and the verdict under ``# Assert``, which is what the structure
is for.
"""

from __future__ import annotations

from scitex_cards._django._board_exposure import (
    EXTERNAL_ENFORCERS,
    PublicExposureWithoutAuthError,
    assert_public_exposure_is_authenticated,
)


def _refusal(host: str, password: str, external: str):
    """Call the gate and hand back the refusal it raised, or ``None``."""
    try:
        assert_public_exposure_is_authenticated(host, password, external)
    except PublicExposureWithoutAuthError as error:
        return error
    return None


class TestSilenceRefuses:
    """Omission must not be the permissive case."""

    def test_public_host_with_neither_password_nor_enforcer_is_refused(self):
        # Arrange
        host, password, external = "cards.example.org", "", ""

        # Act
        refusal = _refusal(host, password, external)

        # Assert
        assert refusal is not None

    def test_whitespace_password_does_not_count_as_a_password(self):
        # Arrange -- a stray space in a shell export must not open the board.
        host, password, external = "cards.example.org", "   ", ""

        # Act
        refusal = _refusal(host, password, external)

        # Assert
        assert refusal is not None

    def test_whitespace_enforcer_does_not_count_as_a_claim(self):
        # Arrange
        host, password, external = "cards.example.org", "", "  "

        # Act
        refusal = _refusal(host, password, external)

        # Assert
        assert refusal is not None

    def test_refusal_names_the_secret_key_check_as_a_different_property(self):
        # Arrange -- the message has to stop the next maintainer concluding
        # that DJANGO_SECRET_KEY already covered this.
        host = "cards.example.org"

        # Act
        refusal = _refusal(host, "", "")

        # Assert
        assert "DJANGO_SECRET_KEY" in str(refusal)


class TestAnUnrecognisedEnforcerIsNotAClaim:
    """A closed set, so a typo cannot disarm the refusal by looking deliberate."""

    def test_misspelled_enforcer_name_is_refused(self):
        # Arrange
        host, external = "cards.example.org", "clouflare-access"

        # Act
        refusal = _refusal(host, "", external)

        # Assert
        assert refusal is not None

    def test_arbitrary_truthy_value_is_refused(self):
        # Arrange -- "yes" is what someone reaches for when guessing.
        host, external = "cards.example.org", "yes"

        # Act
        refusal = _refusal(host, "", external)

        # Assert
        assert refusal is not None

    def test_unknown_enforcer_refusal_lists_the_known_ones(self):
        # Arrange
        host = "cards.example.org"

        # Act
        refusal = _refusal(host, "", "nope")

        # Assert
        assert "cloudflare-access" in str(refusal)


class TestTheTwoLegitimatePaths:
    """Both ways of answering the question are accepted, and only those two."""

    def test_a_board_password_is_sufficient(self):
        # Arrange
        host, password = "cards.example.org", "s3cret"

        # Act
        refusal = _refusal(host, password, "")

        # Assert
        assert refusal is None

    def test_a_named_external_enforcer_is_sufficient(self):
        # Arrange
        host, external = "cards.example.org", "cloudflare-access"

        # Act
        refusal = _refusal(host, "", external)

        # Assert
        assert refusal is None

    def test_no_public_host_needs_nothing(self):
        # Arrange -- loopback only; the operating system is the access control.
        host, password, external = "", "", ""

        # Act
        refusal = _refusal(host, password, external)

        # Assert
        assert refusal is None

    def test_whitespace_only_host_is_not_a_public_binding(self):
        # Arrange
        host, password, external = "   ", "", ""

        # Act
        refusal = _refusal(host, password, external)

        # Assert
        assert refusal is None


class TestTheEnforcerSetIsClosed:
    """The set itself is the security-relevant constant, so pin its shape."""

    def test_cloudflare_access_is_a_known_enforcer(self):
        # Arrange
        enforcers = EXTERNAL_ENFORCERS

        # Act
        known = "cloudflare-access" in enforcers

        # Assert
        assert known is True

    def test_the_empty_string_is_not_a_known_enforcer(self):
        # Arrange -- were "" ever a member, omission would become permissive
        # again through the back door.
        enforcers = EXTERNAL_ENFORCERS

        # Act
        known = "" in enforcers

        # Assert
        assert known is False


# EOF
