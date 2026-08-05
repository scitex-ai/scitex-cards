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


def test_a_trusted_attribute_scopes_the_write():
    """An attribute cannot be forged over HTTP, a query can — so only it counts.

    This is the sole remaining way to scope a write. The hub's tenancy
    middleware sets it per request; if it stopped being honoured, every hub
    tenant would silently share one store.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.yaml"})
    setattr(request, STORE_REQUEST_ATTR, "/srv/tenant/cards.db")

    # Act
    resolved = _write_store_of(request)

    # Assert — the trusted value wins over the forgeable one.
    assert resolved == "/srv/tenant/cards.db"


def test_the_query_seam_no_longer_applies_to_a_write():
    """The hub has migrated, so the fallback is gone — the defect is closed.

    REPLACES ``test_the_query_seam_still_applies_until_the_hub_migrates``,
    which pinned the opposite and said why: "documents the CURRENT contract,
    deliberately, so the day it changes this test fails loudly instead of the
    behaviour drifting." It did exactly that. Today is that day, and the
    honest response to a deliberate contract-pin failing is to invert it with
    the reason, not to delete it.

    What changed, measured 2026-07-29: scitex-hub's TodoBoardTenancyMiddleware
    sets ``request.scitex_store`` (its own comment calls that the PRIMARY
    CHANNEL and marks the query injection legacy). Both halves of the
    migration were already in place; each side was waiting on the other.
    """
    # Arrange — a query naming a store, and NO trusted attribute
    request = _Req(get={"store": "/srv/tenant/cards.db"})

    # Act
    resolved = _write_store_of(request)

    # Assert — None means "no trusted scope", and the caller must then fall
    # back to its own server-side resolution rather than to anything the
    # request carried.
    assert resolved is None


def test_a_read_still_honours_the_store_query_parameter():
    """Reads keep the query seam, but no longer PREFER it (2026-08-06).

    The seam stays for the standalone loopback board and for this suite.
    Removing it before scitex-hub deletes its query injection would drop
    tenancy for a release window — that ordering is why the fallback exists.
    """
    # Arrange
    request = _Req(get={"store": "/srv/tenant/cards.db"})

    # Act
    resolved = _store_of(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_a_read_prefers_the_trusted_attribute_over_the_query():
    """The read half caught up with the write half, nine days later.

    ``_write_store_of`` stopped trusting the query on 2026-07-28 while
    ``_store_of``, in the adjacent function, kept trusting it alone. Because
    reads consulted the query ONLY, scitex-hub could not simply set the
    attribute — it had to keep OVERWRITING ``request.GET["store"]``, which its
    own comment calls out as putting a security-critical value in the exact
    namespace an attacker controls. Preferring the attribute here is what lets
    them delete that block.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})
    setattr(request, STORE_REQUEST_ATTR, "/srv/tenant/cards.db")

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
