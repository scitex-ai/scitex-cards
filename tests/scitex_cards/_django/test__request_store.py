#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A request names its store through two channels of UNEQUAL trust.

``request.scitex_store`` is a request ATTRIBUTE, so only code already running
in this process can set it. ``?store=`` is a query parameter, so the caller
sets it. Reads accept either and PREFER the attribute; writes accept only the
attribute.

The preference is the security property, not a tidiness one. While reads
consulted the query alone, scitex-hub's tenancy middleware had to overwrite
``request.GET["store"]`` with its server-resolved value, which — as their own
comment says — made their injected store and a hostile one "byte-identical,
indistinguishable by construction" downstream. Preferring the attribute makes
a caller-supplied ``?store=`` inert wherever a middleware runs, so hub can
stop overwriting and simply set the attribute.
"""

from __future__ import annotations

from pathlib import Path

from scitex_cards._django._request_store import (
    STORE_REQUEST_ATTR,
    read_store,
    write_store,
)


class _Req:
    """Minimal request double: query params plus optional attributes."""

    def __init__(self, get=None):
        self.GET = get or {}


def test_a_read_prefers_the_trusted_attribute_over_the_query():
    """The whole point: where a middleware runs, ``?store=`` stops mattering.

    Nobody has to remember to discard the caller's value, because the caller's
    value is never consulted while a trusted one exists.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})
    setattr(request, STORE_REQUEST_ATTR, "/srv/tenant/cards.db")

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_a_read_falls_back_to_the_query_when_no_middleware_ran():
    """The standalone loopback board and this test suite still use the seam.

    Deliberately still true. Removing the fallback before scitex-hub deletes
    its query injection would drop tenancy for a release window and fall the
    board back to its ambient canonical store — one store for every tenant.
    """
    # Arrange
    request = _Req(get={"store": "/srv/tenant/cards.db"})

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_a_read_names_no_store_when_the_request_carries_neither():
    """``None`` means "resolve your own", never "use whatever is ambient here"."""
    # Arrange
    request = _Req()

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved is None


def test_a_write_ignores_the_query_even_with_no_attribute_present():
    """A request parameter must never choose the file that gets written.

    Found by scitex-hub in design review 2026-07-28: a URL-path allowlist never
    sees a query string, so every gate reasoning about paths was reasoning
    about the wrong thing.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})

    # Act
    resolved = write_store(request)

    # Assert
    assert resolved is None


def test_a_write_honours_the_trusted_attribute():
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})
    setattr(request, STORE_REQUEST_ATTR, "/srv/tenant/cards.db")

    # Act
    resolved = write_store(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_an_empty_attribute_does_not_scope_a_write():
    """Blank is absence, not a selection — otherwise "" reaches the resolver.

    Three-valued, per the constitution: present, absent, and not-a-value
    collapsing into absent rather than into a store path.
    """
    # Arrange
    request = _Req()
    setattr(request, STORE_REQUEST_ATTR, "")

    # Act
    resolved = write_store(request)

    # Assert
    assert resolved is None


def test_an_empty_attribute_lets_a_read_fall_through_to_the_query():
    """Absent means absent, so the channel behind it still applies."""
    # Arrange
    request = _Req(get={"store": "/srv/tenant/cards.db"})
    setattr(request, STORE_REQUEST_ATTR, "")

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_a_path_attribute_resolves_to_the_value_the_query_carried():
    """The migration must not change the VALUE, only the channel.

    scitex-hub sets the attribute to a ``Path`` while injecting the query as
    ``str(store)``. Honouring the attribute without normalising would hand the
    resolver a different type than it has been resolving all along — a silent
    behaviour change riding along with a security fix.
    """
    # Arrange
    as_query = _Req(get={"store": "/srv/tenant/cards.db"})
    as_attribute = _Req()
    setattr(as_attribute, STORE_REQUEST_ATTR, Path("/srv/tenant/cards.db"))

    # Act
    from_query = read_store(as_query)
    from_attribute = read_store(as_attribute)

    # Assert
    assert from_attribute == from_query


def test_a_path_attribute_is_normalised_to_a_string():
    """The resolver has only ever been handed ``str`` — keep it that way."""
    # Arrange
    request = _Req()
    setattr(request, STORE_REQUEST_ATTR, Path("/srv/tenant/cards.db"))

    # Act
    resolved = read_store(request)

    # Assert
    assert isinstance(resolved, str)


# EOF
