#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-user and cross-project AUTHORIZATION for the Cards user-scope boundary.

Own-ledger #230 (consume the common User / Project / Permission infrastructure)
and #231 (separate Cards domain logic from the infrastructure). ``resolve_scope``
is the single point that answers, for one request, "who acts as, and on which
store may they act" — the value the Cards-specific domain logic (graph,
timeline, DM content) consumes. These tests pin the authorization it provides:
that a request is scoped to ITS user and the store the hub trusted for that
user, and that neither a browser-supplied user NOR a project selector can widen
or redirect it.

The distinction from ``test_user_scope_boundary.py`` (which pins the individual
seams — identity, store attr, no-project-selector) is the COMPOSITION: a
scope is the pair (principal, store) that a domain call is authorized to use,
and cross-user / cross-project isolation is a property of that pair, not of
either half alone.

Hermetic (no store, no mocks, no second user store): the request doubles carry
only the attributes the boundary reads. A "user store" is the trusted
``request.scitex_store`` attribute the hub sets per user — this tests that the
boundary returns the scope it was given, never a caller-named one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from scitex_cards._django import _user_scope  # noqa: E402


class _User:
    def __init__(self, name: str, authenticated: bool = True):
        self._name = name
        self.is_authenticated = authenticated

    def get_username(self):
        return self._name


class _Req:
    """A request the hub has prepared: an authenticated user plus (optionally)
    the trusted store ATTRIBUTE. ``get`` is the browser-controlled namespace, so
    a ``store`` key there is a caller param (ignored), distinct from the trusted
    attribute the middleware sets."""

    def __init__(self, get=None, user=None, trusted_store=None):
        self.GET = get or {}
        if user is not None:
            self.user = user
        if trusted_store is not None:
            setattr(self, "scitex_store", trusted_store)


def _hub_request(username, trusted_store, **browser_params):
    """A hub-authenticated request: user + the hub's trusted store attribute,
    plus any browser-supplied query params (the attack surface)."""
    return _Req(
        get=dict(browser_params), user=_User(username), trusted_store=trusted_store
    )


# --- cross-user authorization: a request is scoped to ITS user ------------


def test_two_users_resolve_to_distinct_scopes():
    # Arrange — two hub users, each with the hub's trusted store.
    alice = _user_scope.resolve_scope(_hub_request("alice", "/srv/users/alice/cards"))
    bob = _user_scope.resolve_scope(_hub_request("bob", "/srv/users/bob/cards"))
    # Act
    # Assert
    assert (alice.principal, alice.store) != (bob.principal, bob.store)


def test_a_user_cannot_resolve_as_another_user():
    # Arrange — bob's request, but the browser tries to claim to be alice.
    scope = _user_scope.resolve_scope(
        _hub_request("bob", "/srv/users/bob/cards", user="alice")
    )
    # Act
    # Assert — the principal is the AUTHENTICATED bob, not the forged alice.
    assert scope.principal == "bob"


def test_a_user_is_authorized_on_their_own_store_only():
    # Arrange — alice's request carries the hub's trusted alice store.
    scope = _user_scope.resolve_scope(
        _hub_request("alice", "/srv/users/alice/cards", store="/srv/users/bob/cards")
    )
    # Act
    # Assert — the store scope is the TRUSTED attribute (alice's), not the
    # caller-supplied ?store= (bob's). A user is authorized on their own
    # tenant's store and only that.
    assert scope.store == "/srv/users/alice/cards"


# --- cross-project authorization: a project selector cannot redirect -------


def test_a_project_selector_cannot_change_the_scope():
    # Arrange — alice, hub-trusted store, browser tries ?project=bob's-project.
    scope = _user_scope.resolve_scope(
        _hub_request("alice", "/srv/users/alice/cards", project="bob's-project")
    )
    # Act
    # Assert — principal is alice AND store is alice's; the project param is
    # inert, because a user-scoped app is not re-scoped by a project switch.
    assert (scope.principal, scope.store) == ("alice", "/srv/users/alice/cards")


def test_a_current_project_attribute_cannot_redirect_the_store():
    # Arrange — even if something set request.current_project, the boundary
    # must not consult it (that would couple user tenancy to project tenancy).
    req = _hub_request("alice", "/srv/users/alice/cards")
    setattr(req, "current_project", "other-project")
    # Act
    scope = _user_scope.resolve_scope(req)
    # Assert
    assert scope.store == "/srv/users/alice/cards"


def test_no_trusted_store_is_none_not_a_caller_named_one():
    # Arrange — a hub user the middleware has NOT yet given a store to (e.g. no
    # active project yet). The boundary must say "no scope", not invent one.
    scope = _user_scope.resolve_scope(_Req(user=_User("carol")))
    # Act
    # Assert
    assert scope.store is None


def test_scope_reads_only_common_infra_inputs():
    # Arrange — the scope must be derived from request.user + the trusted attr,
    # and from NOTHING the browser controls. Give the browser every param it
    # might use and confirm none of them leak into the scope.
    scope = _user_scope.resolve_scope(
        _hub_request(
            "dave",
            "/srv/users/dave/cards",
            user="operator",
            store="/srv/other",
            project="other",
            current_project="other",
        )
    )
    # Act
    # Assert — exactly the hub-prepared scope; the browser's user/store/project
    # claims are all ignored.
    assert (scope.principal, scope.store) == ("dave", "/srv/users/dave/cards")


# --- the boundary is a single, reusable point (#231 separation) ------------


def test_resolve_scope_is_a_single_reusable_entry_point():
    # Arrange — the domain logic gets one object, not two separately-derived values.
    scope = _user_scope.resolve_scope(_hub_request("erin", "/srv/users/erin/cards"))
    # Act
    # Assert
    assert isinstance(scope, _user_scope.UserScope)
