#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`comment_scalars` replaces a card's comments[] on list surfaces.

The four scalars exist to get 4.4 MB of comment prose out of the /graph
payload (measured 19.8 MB total, 1.0-2.6 s per request at 2,854 cards).

Two properties carry most of the weight here:

`test_absent_comments_still_reports_every_key` — the keys are always
present. A consumer that reads an absent key gets `undefined`, renders
nothing, and looks like it worked; that is the failure mode this whole
family of bugs shares.

`test_malformed_comments_do_not_raise` — /graph renders the entire board,
so one bad row must not blank the operator's page.
"""

from __future__ import annotations

from scitex_cards._django.handlers._comment_digest import (
    PREVIEW_CHARS,
    comment_scalars,
)


def _c(author="alice", text="hello", ts="2026-07-30T00:00:00Z"):
    return {"author": author, "text": text, "ts": ts}


# --------------------------------------------------------------------------
# Always-present keys
# --------------------------------------------------------------------------


def test_absent_comments_still_reports_every_key():
    """A card with no comments reports the keys, not silence."""
    # Arrange
    task = {"id": "x"}

    # Act
    got = comment_scalars(task)

    # Assert
    assert set(got) == {
        "comment_count",
        "last_comment",
        "first_comment_ts",
        "first_comment_author",
    }


def test_absent_comments_counts_zero():
    """Zero, not None — the count is always a number."""
    # Arrange
    task = {"id": "x"}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["comment_count"] == 0


def test_absent_comments_has_no_last_comment():
    """None is the honest answer for "there is no last comment"."""
    # Arrange
    task = {"id": "x"}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["last_comment"] is None


# --------------------------------------------------------------------------
# The scalars themselves
# --------------------------------------------------------------------------


def test_counts_every_comment():
    """The badge count must match the thread length."""
    # Arrange
    task = {"comments": [_c(), _c(), _c()]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["comment_count"] == 3


def test_last_comment_is_the_last_not_the_first():
    """Order matters; the Time view renders the most recent."""
    # Arrange
    task = {"comments": [_c(author="first"), _c(author="last")]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["last_comment"]["author"] == "last"


def test_first_comment_author_is_the_first():
    """Used as the creator fallback for legacy cards."""
    # Arrange
    task = {"comments": [_c(author="first"), _c(author="last")]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["first_comment_author"] == "first"


def test_first_comment_ts_is_the_first():
    """recentSort falls back to this for cards with no created_at."""
    # Arrange
    task = {"comments": [_c(ts="2026-01-01T00:00:00Z"), _c(ts="2026-12-31T00:00:00Z")]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["first_comment_ts"] == "2026-01-01T00:00:00Z"


# --------------------------------------------------------------------------
# The preview is a truncated copy, and says so
# --------------------------------------------------------------------------


def test_preview_is_truncated_to_the_budget():
    """Long comments must not smuggle the full body back into the payload."""
    # Arrange
    task = {"comments": [_c(text="x" * 500)]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert len(got["last_comment"]["text_preview"]) == PREVIEW_CHARS


def test_preview_field_is_named_preview():
    """The name is the guard against posting it back as the comment body."""
    # Arrange
    task = {"comments": [_c()]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert "text_preview" in got["last_comment"]


def test_preview_does_not_expose_a_plain_text_key():
    """A `text` key would read as the full body and get posted back."""
    # Arrange
    task = {"comments": [_c()]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert "text" not in got["last_comment"]


def test_short_comment_is_not_padded_or_ellipsised():
    """Under the budget the preview is the text verbatim."""
    # Arrange
    task = {"comments": [_c(text="short")]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["last_comment"]["text_preview"] == "short"


# --------------------------------------------------------------------------
# One bad row must not blank the board
# --------------------------------------------------------------------------


def test_malformed_comments_do_not_raise():
    """A non-list `comments` is treated as absent, not fatal."""
    # Arrange
    task = {"comments": "not a list"}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["comment_count"] == 0


def test_non_dict_entries_are_skipped():
    """Junk entries are dropped rather than counted or dereferenced."""
    # Arrange
    task = {"comments": [_c(), "junk", None, 42]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["comment_count"] == 1


def test_missing_text_yields_empty_preview_not_none():
    """A comment with no text still gives the client a string to render."""
    # Arrange
    task = {"comments": [{"author": "a", "ts": "t"}]}

    # Act
    got = comment_scalars(task)

    # Assert
    assert got["last_comment"]["text_preview"] == ""


# EOF
