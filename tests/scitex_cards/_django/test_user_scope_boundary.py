#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""User-scoped boundary for Cards: common identity, NO project selector.

Own-ledger #230 (use the SciTeX common User/Project/Permission infra), #231
(separate Cards-specific logic from the infra), and the Cards portions of #48
(project-scope vs user-scope is specified by the leaf package) and #239.

``scitex_cards._django._user_scope`` is the single named boundary that decides
"who/what is this request" from the COMMON inputs: ``request.user`` (the hub's
authenticated Django user) and ``request.scitex_store`` (the store the hub's
tenancy middleware resolved for THIS user). These negative tests pin the
invariant that follows from Cards being USER-scoped:

  * the principal is the authenticated user — a browser-supplied identity or a
    project selector cannot widen or redirect it (cross-user isolation), and
  * the store scope is the trusted request ATTRIBUTE — a browser ``?store=`` /
    ``?project=`` cannot select it.

Hermetic (no store, no mocks): the request/user doubles carry only the
attributes the boundary reads, exactly the shape the existing
``test_dm_write_store_and_author.py`` harness uses.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

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
    """Request double: a browser-controlled GET dict, an optional user, and
    (server-set) attributes. The negative tests prove GET contents are inert."""

    def __init__(self, get=None, user=None):
        self.GET = get or {}
        if user is not None:
            self.user = user


# --- #230: the principal comes from the common authenticated user ---------


def test_principal_is_the_authenticated_user():
    # Arrange
    request = _Req(user=_User("alice"))
    # Act
    principal = _user_scope.current_user(request)
    # Assert
    assert principal == "alice"


def test_a_second_user_is_a_different_principal():
    # Arrange — cross-user: two authenticated principals.
    request = _Req(user=_User("bob"))
    # Act
    principal = _user_scope.current_user(request)
    # Assert — bob's request acts as bob, not alice's and not the operator.
    assert principal == "bob"


def test_standalone_with_no_auth_falls_back_to_operator():
    # Arrange
    request = _Req(user=None)
    # Act
    principal = _user_scope.current_user(request)
    # Assert
    assert principal == "operator"


# --- cross-user isolation: a browser-supplied identity cannot widen it ----


def test_browser_supplied_user_param_does_not_set_principal():
    # Arrange — authenticated alice, plus a hostile ?user=operator.
    request = _Req(get={"user": "operator"}, user=_User("alice"))
    # Act
    principal = _user_scope.current_user(request)
    # Assert
    assert principal == "alice"


def test_browser_supplied_project_param_does_not_set_principal():
    # Arrange — the dangerous cross-tenant case: ?project=<other user's store>.
    request = _Req(get={"project": "other-user"}, user=_User("alice"))
    # Act
    principal = _user_scope.current_user(request)
    # Assert
    assert principal == "alice"


# --- the boundary reads NO project selector (#48 user-scope) ---------------


def test_boundary_declares_user_scope():
    # Arrange
    scope = _user_scope.SCOPE
    # Act
    # Assert — Cards is a user-scoped app, so tenancy is not project-driven.
    assert scope == "user"


def test_manifest_declares_user_scope_for_the_leaf_package():
    # Arrange
    manifest = (
        Path(_user_scope.__file__).resolve().parent / "manifest.json"
    )
    # Act
    declared = json.loads(manifest.read_text(encoding="utf-8")).get("scope")
    # Assert
    assert declared == "user"


def test_current_user_source_never_consults_a_project_selector():
    # Arrange — walk the executable AST (a docstring is a Constant, never an
    # attribute access, so mentioning a selector in prose cannot trip this).
    import ast

    tree = ast.parse(inspect.getsource(_user_scope.current_user))

    consults_project = False
    for node in ast.walk(tree):
        # request.project / request.current_project
        if isinstance(node, ast.Attribute) and node.attr in ("project", "current_project"):
            if isinstance(node.value, ast.Name) and node.value.id == "request":
                consults_project = True
        # ?project= via request.GET.get("project") / get('project')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "project":
                consults_project = True
    # Act
    # Assert
    assert not consults_project, (
        "current_user consults a project selector — a user-scoped app must not "
        "be re-scoped by a project switch (own-ledger #48)"
    )


# --- the store scope is the trusted attribute, not a caller param ----------


def test_store_scope_ignores_a_browser_supplied_store():
    # Arrange — no trusted attribute; a hostile ?store= in the query.
    request = _Req(get={"store": "/tmp/attacker", "project": "other"})
    # Act
    scope = _user_scope.user_scoped_store(request)
    # Assert
    assert scope is None


def test_store_scope_reads_only_the_trusted_attribute():
    # Arrange — the hub's tenancy middleware sets the attribute (server-side).
    request = _Req(get={"store": "/tmp/attacker"})
    setattr(request, "scitex_store", "/srv/tenant-alice/cards.db")
    # Act
    scope = _user_scope.user_scoped_store(request)
    # Assert
    assert scope == "/srv/tenant-alice/cards.db"


# --- #231: the DM author delegates to the single boundary -----------------


def test_dm_author_of_delegates_to_the_user_scope_boundary():
    # Arrange
    from scitex_cards._django.handlers.dm import _author_of

    src = inspect.getsource(_author_of)
    # Act
    delegates = "_user_scope" in src or "current_user" in src
    # Assert
    assert delegates, (
        "dm._author_of no longer delegates to _user_scope — the 'who is this "
        "request' logic should live in ONE boundary, not be re-implemented in "
        "the DM view (own-ledger #231)"
    )
