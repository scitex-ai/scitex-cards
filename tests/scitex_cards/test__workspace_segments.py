#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A workspace identity is SEGMENTS, and provisioning is a separate verb.

WHY SEGMENTS. scitex-hub's tenancy is two-dimensional — a tenant is
``(owner, project)`` — and flattening it into one separator-joined slug
COLLIDES, which they measured before building against it:

    owner "alice-my" + project "project"    ->  alice-my-project
    owner "alice"    + project "my-project" ->  alice-my-project

Two tenants, one identity, one store. Any separator has this property as long as
it is legal inside either component, so the encoding is unsatisfiable rather than
merely bad. Under ADR-0017 (a tenant is a STORE, not a row) an identity collision
IS a cross-tenant read — arrived at THROUGH the sanctioned primitive, which is
worse than around it because it looks compliant.

Joining as PATH COMPONENTS removes the separator entirely, so the collision
cannot be expressed. MUTATION-TESTED: replacing the join with
``"-".join(segments)`` fails exactly two of these thirteen tests —
``test_the_two_shapes_that_used_to_collide_now_differ`` (the collision itself)
and ``test_each_segment_is_its_own_directory`` (the structure that prevents
it). The other eleven still pass, so the mutation is narrowly caught rather
than caught by everything, which is what makes those two load-bearing.

An earlier version of this docstring said "the ONLY test that fails". That was
measured with ``-x``, which stops at the first failure — one observation
generalised into a claim about the whole file. Corrected here rather than left
standing, because a test docstring asserting a false coverage property is the
same defect class this suite exists to guard.

WHY TWO VERBS. ``resolve`` refuses to create — a resolver that creates on miss
turns a typo into a new empty tenant and the caller cannot tell that from a
workspace that genuinely existed. But refusing to create with no creation path
anywhere leaves every new tenant at a fail-closed raise, which is the pressure
that eventually softens the resolver. ``provision`` is that path, and it must
leave the store in a state ``resolve`` ACCEPTS — an earlier draft created only
the parent directory and the very next resolve raised, which its own first test
run caught.

Real tmp directories, real environment, a real SQLite store. No mocks and no
``monkeypatch`` (STX-NM): the env fixtures below set and restore
``os.environ`` themselves, so what the code reads is the same object production
reads.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._store_errors import (
    StoreNotProvisionedError,
    StoreUnavailableError,
)
from scitex_cards._workspace import (
    ENV_WORKSPACE_ROOT,
    InvalidWorkspaceIdentity,
    provision_workspace_store,
    resolve_workspace_store,
)

_ABSENT = object()


@pytest.fixture
def workspace_root(tmp_path):
    """A real, empty workspace root, set in the real environment."""
    previous = os.environ.get(ENV_WORKSPACE_ROOT, _ABSENT)
    os.environ[ENV_WORKSPACE_ROOT] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        if previous is _ABSENT:
            os.environ.pop(ENV_WORKSPACE_ROOT, None)
        else:
            os.environ[ENV_WORKSPACE_ROOT] = previous


@pytest.fixture
def workspace_root_unset():
    """The deployment-misconfigured case: the root genuinely absent."""
    previous = os.environ.get(ENV_WORKSPACE_ROOT, _ABSENT)
    os.environ.pop(ENV_WORKSPACE_ROOT, None)
    try:
        yield
    finally:
        if previous is not _ABSENT:
            os.environ[ENV_WORKSPACE_ROOT] = previous


def _refusal(*segments) -> str:
    """The text ``resolve_workspace_store`` refuses ``segments`` with.

    Mirrors the helper convention elsewhere in this suite: the raise IS the
    expectation, so a test spends its one assertion on WHICH refusal it got. A
    bare ``pytest.raises(InvalidWorkspaceIdentity)`` would pass on any of them
    and prove none.
    """
    with pytest.raises(InvalidWorkspaceIdentity) as excinfo:
        resolve_workspace_store(*segments)
    return str(excinfo.value)


def _not_provisioned_message(*segments) -> str:
    """The text a resolve of an unprovisioned workspace refuses with."""
    with pytest.raises(StoreNotProvisionedError) as excinfo:
        resolve_workspace_store(*segments)
    return str(excinfo.value)


def _unavailable_message(*segments) -> str:
    """The text a resolve with no configured root refuses with."""
    with pytest.raises(StoreUnavailableError) as excinfo:
        resolve_workspace_store(*segments)
    return str(excinfo.value)


# ---------------------------------------------------------------------------
# The collision. This is the reason the signature changed.
# ---------------------------------------------------------------------------
def test_the_two_shapes_that_used_to_collide_now_differ(workspace_root):
    """The collision itself. hub's measured example, not an invented one.

    One of the two tests that fail when the join is mutated to concatenation
    (the other is ``test_each_segment_is_its_own_directory``). This one asserts
    the CONSEQUENCE — two tenants must not share a store — while that one
    asserts the STRUCTURE that guarantees it. Both are worth keeping: a future
    change could preserve the directory shape and still collide, or collide
    while preserving distinctness by accident.
    """
    # Arrange
    first = provision_workspace_store("user", "alice-my", "project")
    # Act
    second = provision_workspace_store("user", "alice", "my-project")
    # Assert
    assert first != second


def test_each_segment_is_its_own_directory(workspace_root):
    """Path components, not a joined string — so nothing needs escaping."""
    # Arrange
    expected = workspace_root / "org" / "acme" / "widgets"
    # Act
    store = provision_workspace_store("org", "acme", "widgets")
    # Assert
    assert store.parent.parent.parent == expected


def test_one_segment_still_resolves(workspace_root):
    """A one-dimensional consumer sees exactly the previous behaviour."""
    # Arrange
    provision_workspace_store("solo")
    # Act
    store = resolve_workspace_store("solo")
    # Assert
    assert store.exists()


# ---------------------------------------------------------------------------
# The two verbs, and the contract between them.
# ---------------------------------------------------------------------------
def test_resolve_finds_what_provision_created(workspace_root):
    """The contract an earlier draft broke: provision must satisfy resolve.

    That draft created only the parent directory while resolve tests for the
    FILE, so provision returned success and the next resolve raised. A
    provision that does not satisfy the resolver is a rename of the problem.
    """
    # Arrange
    provisioned = provision_workspace_store("user", "alice", "notes")
    # Act
    resolved = resolve_workspace_store("user", "alice", "notes")
    # Assert
    assert resolved == provisioned


def test_provision_is_idempotent(workspace_root):
    """Re-provisioning must never truncate a store that already holds cards."""
    # Arrange
    first = provision_workspace_store("user", "alice", "notes")
    # Act
    second = provision_workspace_store("user", "alice", "notes")
    # Assert
    assert second == first


def test_resolve_refuses_an_unprovisioned_workspace(workspace_root):
    """Never creates on miss: a typo must not become a new empty tenant."""
    # Arrange
    segments = ("user", "bob", "never-made")
    # Act
    message = _not_provisioned_message(*segments)
    # Assert
    assert "does not exist" in message


def test_an_unset_root_is_a_deployment_fault_not_a_tenant_one(
    workspace_root_unset,
):
    """StoreUnavailableError, NOT StoreNotProvisioned — the distinction matters.

    "Our deployment is misconfigured" must not render the new-tenant onboarding
    page to every user on the platform.
    """
    # Arrange
    segments = ("user", "alice", "notes")
    # Act
    message = _unavailable_message(*segments)
    # Assert
    assert ENV_WORKSPACE_ROOT in message


# ---------------------------------------------------------------------------
# Refusals. Each varies ONE thing; the helper spends the assertion on WHICH.
# ---------------------------------------------------------------------------
def test_no_segments_is_refused(workspace_root):
    """Zero segments would resolve to the ROOT — every tenant's store at once."""
    # Arrange
    segments = ()
    # Act
    message = _refusal(*segments)
    # Assert
    assert "at least one segment" in message


def test_a_traversal_segment_is_refused(workspace_root):
    """The allowlist has no dot, so `..` cannot form in any segment."""
    # Arrange
    segments = ("user", "..", "escape")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "segment 1" in message


def test_uppercase_is_refused_rather_than_folded(workspace_root):
    """Folding would map 'Alice' and 'alice' to ONE store.

    That is the second collision source, arriving through the fix for the
    first. Refusing surfaces it at resolve time instead of at read time.
    """
    # Arrange
    segments = ("user", "Alice", "notes")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "Uppercase is REFUSED" in message


def test_a_separator_inside_a_segment_is_refused(workspace_root):
    """A segment is an identity, never a path fragment."""
    # Arrange
    segments = ("user", "alice/../root")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "segment 1" in message


def test_a_non_string_segment_is_refused(workspace_root):
    """Non-strings cannot be validated by a regex and must not be coerced."""
    # Arrange
    segments = ("user", 42)
    # Act
    message = _refusal(*segments)
    # Assert
    assert "int" in message


def test_the_refusal_names_the_offending_position(workspace_root):
    """With three segments, WHICH one failed is what a caller needs."""
    # Arrange
    segments = ("user", "alice", "BAD")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "segment 2" in message


# EOF
