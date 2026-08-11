#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A credential prompt must say where its answer lives.

THE INCIDENT, 2026-08-02. The board was given a password so it could be exposed
through a tunnel. The password was generated with ``secrets.token_urlsafe``,
written into a systemd drop-in, and never surfaced anywhere. The operator then
opened their OWN board on loopback and met a dialog they had not configured:

    "何のパスワードかってまず心当たりがなくて、ユーザネームもわからない"
    (no idea what password this is, and I do not know the username either)

The realm said ``SciTeX Cards``. The body said ``This board is password
protected.`` -- restating the fact the user could already see, while withholding
the only thing they needed. There was no path from the dialog to the secret.

WHY THIS IS A SECURITY BUG AND NOT A POLISH BUG. An anonymous credential prompt
is indistinguishable from a phishing one. A user who cannot tell them apart is
being trained to type secrets into whichever dialog appears -- so the fix is not
"be friendlier", it is "make the prompt REFUSABLE". Naming the source is what
lets someone check whether the source is theirs and decline when it is not.

These tests pin the property, not the wording: the challenge must name the
credential's source, must say the username is unused, and must tell an
unexpecting reader not to answer. Assertions are on those FACTS so the text can
be rewritten freely.
"""

from __future__ import annotations

from scitex_cards._django._board_auth import (
    CHALLENGE_BODY,
    REALM,
    challenge,
)

#: The env var that actually holds the password. If this is ever renamed, every
#: assertion below follows it rather than pinning a stale string.
SOURCE_NAME = "SCITEX_CARDS_PASSWORD"


class TestTheRealmIsTransportSafe:
    """A realm is an HTTP quoted-string; neither quote nor backslash escapes."""

    def test_realm_contains_no_double_quote(self):
        # Arrange
        realm = REALM

        # Act
        offending = '"' in realm

        # Assert
        assert offending is False

    def test_realm_contains_no_backslash(self):
        # Arrange
        realm = REALM

        # Act
        offending = "\\" in realm

        # Assert
        assert offending is False

    def test_realm_stays_short_enough_for_a_browser_dialog(self):
        # Arrange -- browsers truncate long realms, which would silently drop
        # the hint this whole change exists to deliver.
        realm = REALM

        # Act
        length = len(realm)

        # Assert
        assert length <= 80


class TestTheRealmNamesTheSource:
    """The realm is what the browser prints INSIDE its own dialog."""

    def test_realm_names_where_the_password_comes_from(self):
        # Arrange
        realm = REALM

        # Act
        names_source = SOURCE_NAME in realm

        # Assert
        assert names_source is True


class TestTheBodyAnswersWhatTheDialogCannot:
    """The body is what the browser renders when the dialog is cancelled."""

    def test_body_names_where_the_password_comes_from(self):
        # Arrange
        body = CHALLENGE_BODY

        # Act
        names_source = SOURCE_NAME in body

        # Assert
        assert names_source is True

    def test_body_states_that_the_username_is_unused(self):
        # Arrange -- the operator asked "ユーザネームもわからない", and the
        # answer is that there is nothing to know: it is discarded.
        body = CHALLENGE_BODY.lower()

        # Act
        states_it = "ignored" in body and "username" in body

        # Assert
        assert states_it is True

    def test_body_gives_a_runnable_way_to_read_the_value(self):
        # Arrange -- an error that only names the problem is half-written.
        body = CHALLENGE_BODY

        # Act
        has_command = "systemctl" in body or "grep" in body

        # Assert
        assert has_command is True

    def test_body_tells_an_unexpecting_reader_not_to_answer(self):
        # Arrange -- this is the anti-phishing half, and the reason the change
        # is security-relevant rather than cosmetic.
        body = CHALLENGE_BODY.lower()

        # Act
        warns = "did not set this password" in body

        # Assert
        assert warns is True


class TestTheResponseCarriesBoth:
    """Whichever route the user takes, they reach the same answer."""

    def test_challenge_status_is_401(self):
        # Arrange
        response = challenge()

        # Act
        status = response.status_code

        # Assert
        assert status == 401

    def test_challenge_header_carries_the_realm(self):
        # Arrange
        response = challenge()

        # Act
        header = response["WWW-Authenticate"]

        # Assert
        assert REALM in header

    def test_challenge_body_carries_the_hint(self):
        # Arrange
        response = challenge()

        # Act
        body = response.content.decode("utf-8")

        # Assert
        assert SOURCE_NAME in body

    def test_challenge_body_is_not_the_old_content_free_sentence(self):
        # Arrange -- the regression this file exists to prevent is reverting to
        # a message that restates the situation and answers nothing.
        response = challenge()

        # Act
        body = response.content.decode("utf-8").strip()

        # Assert
        assert body != "This board is password protected."


# EOF
