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

import contextlib
from pathlib import Path

from django.conf import settings as django_settings
from django.test import override_settings

from scitex_cards._django._request_store import (
    STORE_REQUEST_ATTR,
    read_store,
    write_store,
)

#: Distinguishes "the setting is absent" from any value it could hold.
_MISSING = object()


class _Req:
    """Minimal request double: query params plus optional attributes."""

    def __init__(self, get=None):
        self.GET = get or {}


@contextlib.contextmanager
def _settings_carrying_no_exposure_switch():
    """Settings with no ``PUBLIC_HOST`` at all — a host application's shape.

    NOT a mock (STX-NM). It deletes the attribute from the real settings
    object and restores it, so the code under test meets the genuine
    ``getattr`` miss that an embedding deployment produces, rather than a
    stand-in that merely reports one. ``override_settings`` cannot express
    this: it can set a value, and absence is not a value.
    """
    previous = getattr(django_settings, "PUBLIC_HOST", _MISSING)
    if previous is not _MISSING:
        delattr(django_settings, "PUBLIC_HOST")
    try:
        yield
    finally:
        if previous is not _MISSING:
            setattr(django_settings, "PUBLIC_HOST", previous)


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


# ---------------------------------------------------------------------------
# The query channel is bounded by EXPOSURE.
#
# The seam above is safe on a loopback board with one caller and unsafe the
# moment a hostile caller can reach the door. ``settings.PUBLIC_HOST`` already
# is "the ONE switch that says 'this board is reachable from the internet'",
# so the channel keys off that switch rather than off a second notion of
# exposure that could drift from it.
#
# The DM rail is why this is not hygiene: it is the one surface where the
# resolved store actually selects a database (``_dm_ids`` -> ``_db.open_db``).
# The card rail discards the value at the door, so it was safe by a DIFFERENT
# defect — which is exactly why the unguarded surface went unnoticed.
# ---------------------------------------------------------------------------
@override_settings(PUBLIC_HOST="")
def test_the_query_is_admitted_on_a_board_that_is_not_publicly_reachable():
    """The standalone board and this suite keep the seam they depend on."""
    # Arrange
    request = _Req(get={"store": "/srv/loopback/cards.db"})

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved == "/srv/loopback/cards.db"


@override_settings(PUBLIC_HOST="cards.example.com")
def test_the_query_is_refused_on_a_publicly_reachable_board():
    """A caller that can reach the door may not choose what it opens."""
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved is None


@override_settings(PUBLIC_HOST="cards.example.com")
def test_the_trusted_attribute_still_wins_on_a_publicly_reachable_board():
    """Exposure bounds the QUERY channel only — a middleware still decides.

    Refusing the query must not also refuse the trusted attribute, or an
    exposed multi-tenant deployment would lose the one channel that carries
    its tenancy.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})
    setattr(request, STORE_REQUEST_ATTR, "/srv/tenant/cards.db")

    # Act
    resolved = read_store(request)

    # Assert
    assert resolved == "/srv/tenant/cards.db"


def test_the_query_is_refused_when_settings_carry_no_exposure_switch():
    """Absent is not "not exposed" — it is "cannot tell", so it fails closed.

    A host application embedding this board brings its own settings module,
    which has no ``PUBLIC_HOST``. Reading that miss as "not exposed" would
    admit a caller-named store on every embedding deployment while looking
    like a conservative default.
    """
    # Arrange
    request = _Req(get={"store": "/tmp/attacker-chosen.db"})

    # Act
    with _settings_carrying_no_exposure_switch():
        resolved = read_store(request)

    # Assert
    assert resolved is None


# EOF
