#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A DM WRITE must not let the caller choose the file, nor forge the author.

Found by scitex-hub in design review 2026-07-28, confirmed in source:

  ``_store_of`` returned ``request.GET.get("store")`` and ``_threads`` derived
  the file it writes from that value, so a request PARAMETER selected the write
  target. A URL-path allowlist never sees a query string, so any gate reasoning
  about paths was reasoning about the wrong thing.

  ``append_message(OPERATOR_NAME, ...)`` hardcoded the author, so any caller
  admitted by any gate posted AS the operator — a human who did not write it.

These pin both halves: writes ignore the query entirely and honour only a
trusted request ATTRIBUTE (unforgeable over HTTP), and the author comes from
the authenticated principal when one exists.
"""

from __future__ import annotations

from scitex_cards._django.handlers.dm import (
    STORE_REQUEST_ATTR,
    _author_of,
    _store_of,
    _write_store_of,
)


class _Req:
    """Minimal request double: query params, an optional user, attributes."""

    def __init__(self, get=None, user=None):
        self.GET = get or {}
        if user is not None:
            self.user = user


class _User:
    def __init__(self, name, authenticated=True):
        self._name = name
        self.is_authenticated = authenticated

    def get_username(self):
        return self._name


def test_a_trusted_attribute_overrides_the_store_query_parameter():
    """The migration seam: an attribute cannot be forged over HTTP, a query can.

    Note what this does NOT assert. The query seam still WINS when no
    attribute is set (next test), because the hub injects tenancy through it
    and the existing view tests scope themselves the same way — removing it
    outright was tried and broke both. So the caller can still choose the
    write target until the hub switches to the attribute; that is the open
    half of the defect, tracked on the card, not something this test hides.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.yaml"})
    setattr(request, STORE_REQUEST_ATTR, "/srv/tenant/cards.db")

    # Act
    resolved = _write_store_of(request)

    # Assert — the trusted value wins over the forgeable one.
    assert resolved == "/srv/tenant/cards.db"


def test_the_query_seam_still_applies_until_the_hub_migrates():
    # Arrange — no trusted attribute set, which is today's normal case.
    request = _Req(get={"store": "/srv/tenant/cards.db"})

    # Act
    resolved = _write_store_of(request)

    # Assert — documents the CURRENT contract, deliberately, so the day it
    # changes this test fails loudly instead of the behaviour drifting.
    assert resolved == "/srv/tenant/cards.db"


def test_a_read_still_honours_the_store_query_parameter():
    """Reads keep the query seam — the hub injects tenancy through it today.

    Changing this in the same step would trade a security bug for an outage;
    the coordinated fix moves hub to the attribute first.
    """
    # Arrange
    request = _Req(get={"store": "/srv/tenant/cards.db"})

    # Act
    resolved = _store_of(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_an_authenticated_caller_writes_under_their_own_name():
    # Arrange
    request = _Req(user=_User("alice"))

    # Act
    author = _author_of(request)

    # Assert — not the operator, whoever the gate let in.
    assert author == "alice"


def test_an_unauthenticated_caller_does_not_borrow_an_authenticated_name():
    # Arrange — a user object that is present but NOT authenticated.
    request = _Req(user=_User("alice", authenticated=False))

    # Act
    author = _author_of(request)

    # Assert — falls back to the standalone-board default, not alice.
    assert author != "alice"


def test_the_standalone_board_still_writes_as_the_operator():
    """No auth layer at all: loopback board, sole caller IS the operator."""
    # Arrange
    request = _Req()

    # Act
    author = _author_of(request)

    # Assert
    assert author == "operator"
