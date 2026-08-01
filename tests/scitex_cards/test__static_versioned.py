#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for static-URL version stamping.

The motivating failure is recorded in the module docstring and on card
cards-dm-right-click-reported-selects-not-menu-20260730: the operator's browser
held a stale chat_menu.js, right-click stopped opening the context menu, and it
was unreproducible anywhere else. A hard reload fixed it. These tests pin the
behaviour that makes that unnecessary.
"""

import pytest

from scitex_cards._django.static_versioned import append_version


class TestAppendVersion:
    def test_a_plain_url_gets_the_version(self):
        # Arrange
        url = "/static/scitex_cards/chat/chat_menu.js"
        # Act
        result = append_version(url, "0.24.0")
        # Assert
        assert result == "/static/scitex_cards/chat/chat_menu.js?v=0.24.0"

    def test_the_path_itself_is_not_altered(self):
        # Arrange
        url = "/static/scitex_cards/chat/chat_menu.js"
        # Act
        result = append_version(url, "0.24.0")
        # Assert
        assert result.split("?")[0] == url

    def test_two_versions_produce_different_urls(self):
        # Arrange: this IS the fix -- an upgrade must change the URL, or the
        # browser has no reason to re-fetch.
        url = "/static/scitex_cards/chat/chat_menu.js"
        # Act
        before = append_version(url, "0.23.0")
        after = append_version(url, "0.24.0")
        # Assert
        assert before != after


class TestMissingVersionMustNotBreakThePage:
    """A page rendering un-stamped URLs is merely today's behaviour. A page
    that raises because a version string was missing is worse than the bug.
    """

    def test_none_returns_the_url_unchanged(self):
        # Arrange
        url = "/static/x.js"
        # Act
        result = append_version(url, None)
        # Assert
        assert result == url

    def test_empty_string_returns_the_url_unchanged(self):
        # Arrange
        url = "/static/x.js"
        # Act
        result = append_version(url, "")
        # Assert
        assert result == url

    def test_whitespace_only_returns_the_url_unchanged(self):
        # Arrange
        url = "/static/x.js"
        # Act
        result = append_version(url, "   ")
        # Assert
        assert result == url

    def test_a_padded_version_is_stripped_not_embedded(self):
        # Arrange
        url = "/static/x.js"
        # Act
        result = append_version(url, "  0.24.0  ")
        # Assert
        assert result == "/static/x.js?v=0.24.0"


class TestAnExistingQueryStringIsPreserved:
    """{% static %} does not normally produce one, but a storage subclass must
    not corrupt a URL shape it did not anticipate.
    """

    def test_an_existing_query_is_not_destroyed(self):
        # Arrange
        url = "/static/x.js?theme=dark"
        # Act
        result = append_version(url, "0.24.0")
        # Assert
        assert "theme=dark" in result

    def test_a_second_parameter_uses_an_ampersand(self):
        # Arrange
        url = "/static/x.js?theme=dark"
        # Act
        result = append_version(url, "0.24.0")
        # Assert
        assert result == "/static/x.js?theme=dark&v=0.24.0"

    def test_only_one_question_mark_is_ever_emitted(self):
        # Arrange
        url = "/static/x.js?theme=dark"
        # Act
        result = append_version(url, "0.24.0")
        # Assert
        assert result.count("?") == 1


@pytest.mark.parametrize(
    "url",
    [
        "/static/scitex_cards/chat/chat.js",
        "/static/scitex_cards/chat/chat_menu.js",
        "/static/scitex_ui/css/shell/theme.css",
        "/board/static/scitex_cards/chat/chat.js",
    ],
    ids=["chat", "menu", "ui-css", "sub-path-mount"],
)
def test_every_real_asset_shape_gets_stamped(url):
    # Arrange
    version = "0.24.0"
    # Act
    result = append_version(url, version)
    # Assert
    assert result.endswith("?v=0.24.0")


# EOF
