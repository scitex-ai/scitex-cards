#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``stx_path``: the join that cannot produce a protocol-relative URL.

The defect it exists for was measured in a browser, not found by a test: the
board's links and form actions were spelled ``{{ api_base }}/projects``, which is
correct at a sub-path mount (``/apps/cards``) and WRONG at the root mount, where
api_base is empty and the result is ``//projects`` — a string every browser reads
as a HOST named ``projects``. Clicking a card left Chromium on
``chrome-error://chromewebdata/`` while the page itself returned 200 and passed
every server-side assertion it had.
"""

from __future__ import annotations

from scitex_cards._django.templatetags.scitex_paths import stx_path


def test_a_root_mount_joins_with_one_leading_slash():
    """The exact case the naive concatenation got wrong."""
    # Arrange
    base, path = "", "/projects"
    # Act
    joined = stx_path(base, path)
    # Assert
    assert joined == "/projects"


def test_a_sub_path_mount_keeps_its_prefix():
    """Behind the hub the app lives at /apps/cards and the links must go there."""
    # Arrange
    base, path = "/apps/cards", "/projects"
    # Act
    joined = stx_path(base, path)
    # Assert
    assert joined == "/apps/cards/projects"


def test_a_trailing_slash_on_the_base_does_not_double_up():
    """A doubled slash inside a path is legal but reads as a mistake, and some
    proxies rewrite it."""
    # Arrange
    base, path = "/apps/cards/", "/projects"
    # Act
    joined = stx_path(base, path)
    # Assert
    assert joined == "/apps/cards/projects"


def test_a_path_without_a_leading_slash_is_still_joined_once():
    """Callers pass both spellings; the join normalises rather than guessing."""
    # Arrange
    base, path = "/apps/cards", "projects"
    # Act
    joined = stx_path(base, path)
    # Assert
    assert joined == "/apps/cards/projects"


def test_an_empty_path_returns_the_base_alone():
    """A link back to the mount root is a legitimate thing to build."""
    # Arrange
    base, path = "/apps/cards", ""
    # Act
    joined = stx_path(base, path)
    # Assert
    assert joined == "/apps/cards"


def test_a_missing_base_is_treated_as_the_root_mount():
    """None is what a template hands over when the context key is absent, and the
    root mount is the only safe reading of it."""
    # Arrange
    base, path = None, "/projects"
    # Act
    joined = stx_path(base, path)
    # Assert
    assert joined == "/projects"


def test_it_never_returns_a_protocol_relative_url():
    """The property the whole filter exists for, asserted directly: no output may
    begin with two slashes, because a browser reads that as a host name."""
    # Arrange
    cases = [("", "/projects"), ("/apps/cards", "/projects"), (None, "projects")]
    # Act
    offenders = [stx_path(b, p) for b, p in cases if stx_path(b, p).startswith("//")]
    # Assert
    assert offenders == []
