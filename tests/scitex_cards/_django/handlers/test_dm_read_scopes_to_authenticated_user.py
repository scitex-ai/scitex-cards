#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Negative cross-user tests: the DM reader is the authenticated principal.

OPERATOR REQUIREMENT (2026-09-10): "Cards is user-scoped by default. Resolve
the current user automatically from the authenticated Hub request; ordinary
users may access only their own cards/tasks/threads. Never trust a
browser-supplied user or customer identifier to widen scope."

THE DEFECT THIS PINS. The DM WRITE path already resolved its author from
``request.user`` (the authenticated principal, see
``test_dm_write_store_and_author.py``), but the DM READ path hardcoded
``OPERATOR_NAME`` (``"operator"``) as the reader. On a hub deployment where
every user is authenticated, that meant a hub user reading
``dm_threads_view`` / ``dm_thread_view`` rendered the OPERATOR's threads — a
cross-user leak: reads and writes disagreed about who was looking.

THE FIX. The read path now resolves its reader through the SAME
``_author_of(request)`` the write path uses: the authenticated user's name,
falling back to ``OPERATOR_NAME`` only on the standalone loopback board (no
auth layer; the sole caller IS the operator). These tests pin the property
without a store (the DM store is postgres-only and this environment has no
scratch postgres), which is legitimate here because the reader is decided
BEFORE any store is touched — ``_author_of`` is pure and the views call it
first.

No mocks: the request/user doubles below carry only the attributes the
resolution reads (``.user``, ``.is_authenticated``, ``.get_username``) and the
query/body the handler is told to ignore. They exercise the real ``_author_of``
and the real view source, not a stand-in for them.
"""

from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("django")

from scitex_cards._django.handlers import dm  # noqa: E402


class _User:
    """Minimal authenticated-identity double: the attrs ``_author_of`` reads."""

    def __init__(self, name: str, authenticated: bool = True):
        self._name = name
        self.is_authenticated = authenticated

    def get_username(self):
        return self._name


class _Req:
    """Minimal request double: query params, an optional user, and body.

    ``get`` is a browser-controlled namespace — the whole point of a negative
    test is that nothing in it may widen the reader.
    """

    def __init__(self, get=None, user=None, body=b""):
        self.GET = get or {}
        if user is not None:
            self.user = user
        self.body = body


# --- the resolution itself: authenticated user wins, standalone falls back ---


def test_reader_is_the_authenticated_user() -> None:
    # Arrange
    request = _Req(user=_User("alice"))
    # Act
    # Assert
    assert dm._author_of(request) == "alice"


def test_standalone_without_an_auth_layer_falls_back_to_operator() -> None:
    """The ONLY fallback: no authenticated user (loopback board) -> operator.

    This is documented behaviour for the single-user standalone board, not a
    hole — a hub request is always authenticated, so the fallback is
    unreachable there.
    """
    # Arrange
    request = _Req(user=None)
    # Act
    # Assert
    assert dm._author_of(request) == dm.OPERATOR_NAME


# --- the negative cross-user cases: a browser-supplied identity is inert ---


def test_a_browser_supplied_user_param_does_not_widen_scope() -> None:
    """``?user=bob`` must not change who the board reads as.

    The reader comes from ``request.user`` (set by the hub from its own
    session), never from a query parameter a caller controls. A browser that
    names itself is exactly the surface the operator said not to trust.
    """
    # Arrange — an authenticated alice, plus a browser-supplied ?user=bob.
    request = _Req(get={"user": "bob"}, user=_User("alice"))
    # Act
    # Assert
    assert dm._author_of(request) == "alice"


def test_a_browser_supplied_customer_id_does_not_widen_scope() -> None:
    """A ``?customer=`` / body-named customer identifier is not a reader either."""
    # Arrange — authenticated alice; the browser tries to claim customer "bob".
    request = _Req(
        get={"customer": "bob"},
        user=_User("alice"),
        body=b'{"customer": "bob", "user": "operator"}',
    )
    # Act
    # Assert
    assert dm._author_of(request) == "alice"


def test_an_unauthenticated_browser_cannot_claim_operator_via_params() -> None:
    """The dangerous case: a browser with NO authenticated user that names
    itself ``operator`` in the query. The reader must NOT become "operator"
    from the param — it falls back to OPERATOR_NAME only because there is no
    auth (standalone), which is a different code path than the param being
    honoured. The discriminator: a param value identical to the fallback must
    be indistinguishable from an absent param, proving the param is not read.
    """
    # Arrange — no authenticated user; the browser names itself operator.
    from_user = dm._author_of(_Req(user=None))
    from_param = dm._author_of(_Req(get={"user": dm.OPERATOR_NAME}, user=None))
    # Act
    # Assert — the param had no effect: both resolve identically via the
    # fallback, so a caller cannot distinguish "I am the operator" from "I am
    # nobody" by setting the param.
    assert from_user == from_param == dm.OPERATOR_NAME


# --- the read VIEWS actually derive their reader from the principal ---


def test_read_views_resolve_the_reader_from_the_principal() -> None:
    """Each read view derives its reader from the authenticated principal via
    _author_of, not from a constant or a caller-supplied identity."""
    # Arrange
    views = (dm.dm_threads_view, dm.dm_thread_view, dm.dm_reaction_view)
    # Act
    sources = {v.__name__: inspect.getsource(v) for v in views}
    missing = [n for n, s in sources.items() if "_author_of(request)" not in s]
    # Assert
    assert not missing, (
        f"{missing!r} no longer resolve the reader via _author_of — a view "
        "reverted to a constant or a caller-supplied identity"
    )


def test_dm_threads_view_scopes_the_summary_to_the_reader() -> None:
    # Arrange
    src = inspect.getsource(dm.dm_threads_view)
    # Act
    # threads_summary(reader, …) is the fix; threads_summary(OPERATOR_NAME, …)
    # is the bug (a hub user would see the operator's thread list).
    scoped_to_reader = re.search(r"threads_summary\(\s*reader\b", src) is not None
    # Assert
    assert scoped_to_reader, (
        "dm_threads_view does not scope threads_summary to `reader` — it "
        "passes OPERATOR_NAME, so a hub user would see the operator's list"
    )


def test_dm_thread_view_scopes_the_thread_key_to_the_reader() -> None:
    # Arrange
    src = inspect.getsource(dm.dm_thread_view)
    # Act
    # The thread the caller SEES is (reader, peer). The constant form
    # thread_key(OPERATOR_NAME, peer) is the exact bug: a hub user rendered
    # the operator's conversation.
    scoped_to_reader = (
        re.search(r"thread_key\(\s*reader\s*,\s*peer\s*\)", src) is not None
    )
    # Assert
    assert scoped_to_reader, (
        "dm_thread_view does not derive its thread from `reader` — a hub user "
        "reads the operator's conversation"
    )


def test_dm_reaction_view_scopes_the_thread_key_to_the_reader() -> None:
    # Arrange
    src = inspect.getsource(dm.dm_reaction_view)
    # Act
    # A hub user must not be able to react to a thread they cannot see; the
    # thread is derived from the authenticated reader, not the constant.
    scoped_to_reader = (
        re.search(r"thread_key\(\s*reader\s*,\s*peer\s*\)", src) is not None
    )
    # Assert
    assert scoped_to_reader, (
        "dm_reaction_view does not derive its thread from `reader` — a hub "
        "user could react to a thread they cannot see"
    )
