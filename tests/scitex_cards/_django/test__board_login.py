#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Test file for: scitex_cards/_django/_board_login.py

"""Tests for the login PAGE that replaced an invisible realm string.

THE BUG THESE EXIST FOR was not in any of this code. The 401 challenge named
its own source correctly — realm and body both — and curl printed both. Chrome
does not print the realm at all, so the operator saw a bare username/password
box on his own board and could not get in.

So the property under test is not "does the header say the right thing" (it
did) but "does the thing the USER LOOKS AT say it". That is why the page is
asserted on its rendered text, and why this file exists next to a header that
was already correct.
"""

from __future__ import annotations

import pytest

from scitex_cards._django._board_login import (
    COOKIE_NAME,
    cookie_is_valid,
    issue_cookie,
    login_page,
    password_matches,
    wants_html,
)


class _Request:
    """The three fields the module actually reads. Not a Django test client.

    Deliberately minimal: a full RequestFactory would drag Django settings
    configuration into a module that needs none of it, and these functions take
    a request only to read two dicts off it.
    """

    def __init__(self, accept: str = "", cookies: dict | None = None):
        self.META = {"HTTP_ACCEPT": accept}
        self.COOKIES = cookies or {}


def test_a_browser_navigation_wants_html():
    # Arrange
    request = _Request(accept="text/html,application/xhtml+xml,*/*;q=0.8")
    # Act
    result = wants_html(request)
    # Assert
    assert result is True


def test_a_bare_curl_does_not_want_html():
    # Arrange
    # curl sends `Accept: */*`. It gets the 401 + realm, which it can print.
    request = _Request(accept="*/*")
    # Act
    result = wants_html(request)
    # Assert
    assert result is False


def test_a_request_with_no_accept_header_does_not_want_html():
    # Arrange
    request = _Request(accept="")
    # Act
    result = wants_html(request)
    # Assert
    assert result is False


def test_the_page_names_the_environment_variable():
    # Arrange
    # THE WHOLE POINT. Chrome swallowed this when it lived in the realm.
    # Act
    body = login_page(env_var="SCITEX_CARDS_PASSWORD").content.decode()
    # Assert
    assert "SCITEX_CARDS_PASSWORD" in body


def test_the_page_gives_a_command_that_reads_the_value():
    # Arrange
    # Naming the variable is half an answer; the reader still has to get at it.
    # Act
    body = login_page(env_var="SCITEX_CARDS_PASSWORD").content.decode()
    # Assert
    assert "systemctl --user show" in body


def test_the_page_says_there_is_no_username():
    # Arrange
    # The operator's actual question was "user は ywatanabe?". The page must
    # answer it rather than leave an unexplained empty field.
    # Act
    body = login_page(env_var="SCITEX_CARDS_PASSWORD").content.decode()
    # Assert
    assert "no username" in body


def test_the_page_warns_against_typing_a_password_you_did_not_set():
    # Arrange
    # A credential prompt that cannot say where its answer lives has the shape
    # of a phishing prompt. This page CAN say, and it still warns.
    # Act
    body = login_page(env_var="SCITEX_CARDS_PASSWORD").content.decode()
    # Assert
    assert "phishing" in body


def test_the_page_is_200_not_401():
    # Arrange
    # A 401 carrying HTML makes the browser open its native Basic dialog ON TOP
    # of this page — the dialog with no hint in it — hiding the explanation
    # behind the very prompt this page replaces.
    # Act
    response = login_page(env_var="SCITEX_CARDS_PASSWORD")
    # Assert
    assert response.status_code == 200


def test_a_failed_attempt_shows_the_error():
    # Arrange
    # Act
    body = login_page(
        env_var="SCITEX_CARDS_PASSWORD", error="That password did not match."
    ).content.decode()
    # Assert
    assert "That password did not match." in body


def test_the_correct_password_matches():
    # Arrange
    # Act
    result = password_matches("hunter2", "hunter2")
    # Assert
    assert result is True


def test_a_wrong_password_does_not_match():
    # Arrange
    # Act
    result = password_matches("wrong", "hunter2")
    # Assert
    assert result is False


def test_no_password_supplied_does_not_match():
    # Arrange
    # An empty submission must never satisfy an empty-ish comparison.
    # Act
    result = password_matches(None, "hunter2")
    # Assert
    assert result is False


def test_a_request_with_no_cookie_is_not_valid():
    # Arrange
    request = _Request()
    # Act
    result = cookie_is_valid(request)
    # Assert
    assert result is False


def test_a_forged_cookie_is_not_valid(settings_configured):
    # Arrange
    # An unsigned "logged_in=1" would be a password anyone can type into
    # devtools. This asserts the signature is actually checked.
    request = _Request(cookies={COOKIE_NAME: "not-a-signed-value"})
    # Act
    result = cookie_is_valid(request)
    # Assert
    assert result is False


def test_a_cookie_we_issued_is_valid(settings_configured):
    # Arrange
    from django.http import HttpResponse

    response = issue_cookie(HttpResponse(), secure=False)
    token = response.cookies[COOKIE_NAME].value
    request = _Request(cookies={COOKIE_NAME: token})
    # Act
    result = cookie_is_valid(request)
    # Assert
    assert result is True


def test_the_cookie_is_httponly(settings_configured):
    # Arrange
    # Not readable from JavaScript: an XSS on the board must not be able to
    # lift the session.
    from django.http import HttpResponse

    # Act
    response = issue_cookie(HttpResponse(), secure=False)
    # Assert
    assert response.cookies[COOKIE_NAME]["httponly"] is True


def test_the_cookie_is_samesite_lax(settings_configured):
    # Arrange
    from django.http import HttpResponse

    # Act
    response = issue_cookie(HttpResponse(), secure=False)
    # Assert
    assert response.cookies[COOKIE_NAME]["samesite"] == "Lax"


@pytest.fixture
def settings_configured():
    """Minimal Django settings, because signing needs SECRET_KEY."""
    from django.conf import settings

    if not settings.configured:
        settings.configure(SECRET_KEY="test-key-for-signing-only", DEBUG=False)
    return settings


def test_a_request_object_with_no_cookie_jar_at_all_is_not_valid():
    # Arrange
    # The minimal stub the existing _board_auth suite builds:
    #     type("R", (), {"META": {}})()
    # It has no COOKIES attribute, and it was RIGHT — the read was too narrow.
    # A request carrying no cookie jar has no cookie, so False is the correct
    # answer rather than an AttributeError. This is the regression that broke
    # 3 matrix legs.
    request = type("R", (), {"META": {}})()
    # Act
    result = cookie_is_valid(request)
    # Assert
    assert result is False


# EOF
