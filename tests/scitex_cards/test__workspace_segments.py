#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A workspace identity is SEGMENTS, and provisioning is a separate verb.

WHY SEGMENTS. scitex-hub's tenancy is two-dimensional — a tenant is
``(owner, project)`` — and flattening it into one separator-joined slug
COLLIDES, which they measured before building against it:

    owner "alice-my" + project "project"    ->  alice-my-project
    owner "alice"    + project "my-project" ->  alice-my-project

Two tenants, one identity, one store. Under ADR-0017 (a tenant is a STORE, not a
row) an identity collision IS a cross-tenant read — arrived at THROUGH the
sanctioned primitive, which is worse than around it because it looks compliant.

WHAT MAKES THE JOIN SAFE is that the separator is one the validator FORBIDS
inside a segment. ``-`` collides because it is legal in a component; ``/`` is
not legal in a component, so the join has exactly one parse. The property is
inherited from :data:`_IDENTITY_RE` rather than asserted independently, which is
why ``test_a_separator_inside_a_segment_is_refused`` guards the derivation and
not merely the input surface.

MUTATION-TESTED, and the result is measured rather than assumed: setting
``_SEGMENT_SEPARATOR`` to ``"-"`` fails exactly ONE of these fifteen tests —
``test_the_two_shapes_that_used_to_collide_now_differ``. Measured WITHOUT
``-x``; this suite's config stops at the first failure, and a claim about a
whole file cannot be read off a run that stopped early.

HOW IT FAILS CHANGED, and that is the finding rather than a detail. Before the
insert named its conflict target, the mutation provisioned both tenants without
error and the test failed on a bare inequality — meaning the second tenant had
silently received a handle to the first tenant's schema. Now PostgreSQL raises
``UniqueViolation`` on ``schema_name`` at provision time. The same defect went
from a silent cross-tenant alias to a loud refusal, which is the only version of
it that is safe to have.

A TENANT IS A SCHEMA. ``provision`` creates one and REGISTERS it; ``resolve``
reads the registry. Existence is a row, not a probe of the catalog — a schema
can be present because a half-finished provision created it, and "the container
is there" was the false positive the previous design kept hitting.

WHY TWO VERBS. ``resolve`` refuses to create — a resolver that creates on miss
turns a typo into a new empty tenant and the caller cannot tell that from a
workspace that genuinely existed. But refusing to create with no creation path
anywhere leaves every new tenant at a fail-closed raise, which is the pressure
that eventually softens the resolver. ``provision`` is that path, and it must
leave the store in a state ``resolve`` ACCEPTS — an earlier draft created only
the container and the very next resolve raised, which its own first test run
caught.

A real cluster, a real environment, a real store. No mocks and no
``monkeypatch`` (STX-NM): the env fixtures below set and restore ``os.environ``
themselves, so what the code reads is the same object production reads.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from scitex_cards._backend_connect import connect
from scitex_cards._store_errors import (
    StoreNotProvisionedError,
    StoreUnavailableError,
)
from scitex_cards._workspace import (
    ENV_WORKSPACE_DB,
    InvalidWorkspaceIdentity,
    provision_workspace_store,
    resolve_workspace_store,
)

_ABSENT = object()


@pytest.fixture
def workspace_cluster(new_store):
    """A real, empty cluster to carve tenants out of, set in the real environment.

    DROPS THE TENANT SCHEMAS IT CREATED, by reading them back out of the
    registry rather than by pattern-matching the catalog. A ``LIKE 'ws\\_%'``
    sweep would also delete the tenants of any test running beside this one
    under ``-n`` — the registry names exactly this test's own, because the
    registry itself lives in this test's throwaway schema.
    """
    cluster = new_store("workspace_cluster", bootstrap=False)
    previous = os.environ.get(ENV_WORKSPACE_DB, _ABSENT)
    os.environ[ENV_WORKSPACE_DB] = cluster
    try:
        yield cluster
    finally:
        if previous is _ABSENT:
            os.environ.pop(ENV_WORKSPACE_DB, None)
        else:
            os.environ[ENV_WORKSPACE_DB] = previous
        with contextlib.suppress(Exception):
            conn = connect(cluster, read_only=False, rows_by_name=True)
            try:
                rows = conn.fetchall(
                    "SELECT schema_name FROM scitex_cards_workspaces"
                )
                for row in rows:
                    conn.execute(
                        f'DROP SCHEMA IF EXISTS "{row["schema_name"]}" CASCADE'
                    )
                conn.commit()
            finally:
                conn.close()


@pytest.fixture
def workspace_db_unset():
    """The deployment-misconfigured case: the cluster genuinely unconfigured."""
    previous = os.environ.get(ENV_WORKSPACE_DB, _ABSENT)
    os.environ.pop(ENV_WORKSPACE_DB, None)
    try:
        yield
    finally:
        if previous is not _ABSENT:
            os.environ[ENV_WORKSPACE_DB] = previous


@pytest.fixture
def who() -> str:
    """A tenant slug UNIQUE TO THIS TEST, valid under the identity regex.

    A fixed identity derives a fixed, database-global schema name, so every
    xdist worker provisioning "user/alice/notes" shared ONE schema: one worker's
    teardown dropped it while another's open ran - the CI flake measured on
    #964/#965 (two tests of this module, two legs, two PRs). A per-test slug
    makes the schema this test's alone; `workspace_cluster` still drops it.
    """
    return "t" + uuid.uuid4().hex[:10]


def _registered_segments(cluster, schema_suffix_of):
    """The ``segments`` array as the registry stored it, for one tenant."""
    conn = connect(cluster, read_only=True, rows_by_name=True)
    try:
        return conn.fetchone(
            "SELECT segments FROM scitex_cards_workspaces WHERE schema_name = ?",
            [schema_suffix_of],
        )["segments"]
    finally:
        conn.close()


def _schema_of(dsn: str) -> str:
    """The schema a provisioned DSN is scoped to."""
    return dsn.rsplit("search_path%3D", 1)[1]


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
    """The text a resolve with no configured cluster refuses with."""
    with pytest.raises(StoreUnavailableError) as excinfo:
        resolve_workspace_store(*segments)
    return str(excinfo.value)


# ---------------------------------------------------------------------------
# The collision. This is the reason the signature changed.
# ---------------------------------------------------------------------------
def test_the_two_shapes_that_used_to_collide_now_differ(workspace_cluster, who):
    """The collision itself. hub's measured example, not an invented one.

    Asserts the CONSEQUENCE — two tenants must not share a store — while
    ``test_the_registry_stores_segment_boundaries`` asserts the STRUCTURE that
    guarantees it. Both are worth keeping: a future change could preserve the
    structure and still collide, or collide while preserving distinctness by
    accident.
    """
    # Arrange
    first = provision_workspace_store("user", f"{who}-my", "project")
    # Act
    second = provision_workspace_store("user", who, "my-project")
    # Assert
    assert first != second


def test_the_registry_stores_segment_boundaries(workspace_cluster, who):
    """A text[] of three elements, not one joined string.

    The structural half of the collision guarantee, and it is stored rather
    than merely derived: the PRIMARY KEY is the ARRAY, so two identities that
    happened to share a digest would still be different keys. The digest makes
    the schema name unique; this makes the identity unique.
    """
    # Arrange
    store = provision_workspace_store("org", who, "widgets")
    # Act
    segments = _registered_segments(workspace_cluster, _schema_of(store))
    # Assert
    assert segments == ["org", who, "widgets"]


def test_one_segment_still_resolves(workspace_cluster, who):
    """A one-dimensional consumer sees exactly the previous behaviour.

    Asserts the tenant's own card tables are QUERYABLE, not merely that a name
    came back: provision's contract is that it builds the store, and a DSN
    string is true of a schema that was never populated.
    """
    # Arrange
    provision_workspace_store(who)
    # Act
    store = resolve_workspace_store(who)
    # Assert
    conn = connect(store, read_only=True, rows_by_name=True)
    try:
        assert conn.fetchone("SELECT count(*) AS n FROM tasks")["n"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The two verbs, and the contract between them.
# ---------------------------------------------------------------------------
def test_resolve_finds_what_provision_created(workspace_cluster, who):
    """The contract an earlier draft broke: provision must satisfy resolve.

    That draft created only the container while resolve tested for what goes
    inside it, so provision returned success and the next resolve raised. A
    provision that does not satisfy the resolver is a rename of the problem.
    """
    # Arrange
    provisioned = provision_workspace_store("user", who, "notes")
    # Act
    resolved = resolve_workspace_store("user", who, "notes")
    # Assert
    assert resolved == provisioned


def test_provision_is_idempotent(workspace_cluster, who):
    """Re-provisioning must never truncate a store that already holds cards."""
    # Arrange
    first = provision_workspace_store("user", who, "notes")
    # Act
    second = provision_workspace_store("user", who, "notes")
    # Assert
    assert second == first


def test_resolve_refuses_an_unprovisioned_workspace(workspace_cluster):
    """Never creates on miss: a typo must not become a new empty tenant."""
    # Arrange
    segments = ("user", "bob", "never-made")
    # Act
    message = _not_provisioned_message(*segments)
    # Assert
    assert "is not registered" in message


def test_an_unset_cluster_is_a_deployment_fault_not_a_tenant_one(
    workspace_db_unset,
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
    assert ENV_WORKSPACE_DB in message


def test_a_bad_identity_is_refused_before_the_cluster_is_consulted(
    workspace_db_unset,
):
    """Ordering, and it is load-bearing rather than incidental.

    A malformed identity is the CALLER's fault whatever our deployment looks
    like. Reading configuration first would mask every 400 as a 500 on any
    deployment that happens to be unconfigured — and that is the deployment a
    new environment starts in, so the masking would be worst exactly where the
    diagnostic matters most.
    """
    # Arrange
    segments = ("user", "Alice", "notes")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "Uppercase is REFUSED" in message


# ---------------------------------------------------------------------------
# Refusals. Each varies ONE thing; the helper spends the assertion on WHICH.
# ---------------------------------------------------------------------------
def test_no_segments_is_refused(workspace_cluster):
    """Zero segments would resolve to the ROOT — every tenant's store at once."""
    # Arrange
    segments = ()
    # Act
    message = _refusal(*segments)
    # Assert
    assert "at least one segment" in message


def test_a_traversal_segment_is_refused(workspace_cluster):
    """The allowlist has no dot, so `..` cannot form in any segment."""
    # Arrange
    segments = ("user", "..", "escape")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "segment 1" in message


def test_uppercase_is_refused_rather_than_folded(workspace_cluster):
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


def test_a_separator_inside_a_segment_is_refused(workspace_cluster):
    """A segment is an identity, never a location fragment.

    THIS ONE GUARDS THE DERIVATION, not just the input surface: the join is
    unambiguous only because ``/`` cannot occur inside a segment. Loosening the
    validator to admit it would reintroduce the collision at the top of this
    file, and this is the test that would go red.
    """
    # Arrange
    segments = ("user", "alice/../root")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "segment 1" in message


def test_a_non_string_segment_is_refused(workspace_cluster):
    """Non-strings cannot be validated by a regex and must not be coerced."""
    # Arrange
    segments = ("user", 42)
    # Act
    message = _refusal(*segments)
    # Assert
    assert "int" in message


def test_the_refusal_names_the_offending_position(workspace_cluster):
    """With three segments, WHICH one failed is what a caller needs."""
    # Arrange
    segments = ("user", "alice", "BAD")
    # Act
    message = _refusal(*segments)
    # Assert
    assert "segment 2" in message


def test_two_identities_cannot_share_a_schema(workspace_cluster, who):
    """The UNIQUE constraint behind the digest, exercised directly.

    THE SECOND LINE OF DEFENCE, and it is only reachable because the insert
    names its conflict target. A bare ``ON CONFLICT DO NOTHING`` absorbs this
    violation too, and provision then returns the FIRST tenant's schema to the
    second tenant with no error anywhere — measured, by mutating the separator
    so two identities derived one digest. Asserting the constraint directly
    keeps that guard honest without requiring a digest collision to observe it.
    """
    # Arrange
    import psycopg

    provision_workspace_store("user", who, "notes")
    taken = _schema_of(resolve_workspace_store("user", who, "notes"))
    # Act
    conn = connect(workspace_cluster, read_only=False, rows_by_name=True)
    # Assert
    try:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO scitex_cards_workspaces (segments, schema_name) "
                "VALUES (?, ?)",
                [["user", "bob", "notes"], taken],
            )
    finally:
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# A tenant schema this role cannot use is a collision, not a provision.
# ---------------------------------------------------------------------------


@pytest.fixture
def unusable_tenant_schema(workspace_cluster, who):
    """The tenant schema for ``(who,)`` already present with USAGE revoked.

    THE REAL SHAPE OF THE LEFTOVER, reproduced under one role: on the fleet
    primary the schema for "acme" was left behind by another agent's test run,
    owned by that agent's role, and this role had no USAGE on it - PostgreSQL
    silently drops an unusable schema from the effective search_path, and the
    first CREATE TABLE failed with "no schema has been selected to create in".
    A second role is not available to a test, but an owner may revoke its own
    USAGE, and ``has_schema_privilege`` then answers exactly as it did for the
    foreign-owned schema. Dropped on teardown; the owner can always drop.
    """
    from scitex_cards._workspace import _schema_for

    schema = _schema_for((who,))
    conn = connect(workspace_cluster, read_only=False, rows_by_name=True)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM current_user')
        conn.commit()
        yield schema
    finally:
        conn.rollback()
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        conn.close()


def test_a_tenant_schema_this_role_cannot_use_is_refused(unusable_tenant_schema, who):
    # Arrange
    from scitex_cards._workspace import WorkspaceSchemaNotUsable

    identity = who
    # Act
    with pytest.raises(WorkspaceSchemaNotUsable):
        # Assert: the raise is the assertion
        provision_workspace_store(identity)


@pytest.fixture
def unusable_schema_refusal(unusable_tenant_schema, who) -> str:
    from scitex_cards._workspace import WorkspaceSchemaNotUsable

    try:
        provision_workspace_store(who)
    except WorkspaceSchemaNotUsable as exc:
        return str(exc)
    pytest.fail("a tenant schema without USAGE was accepted as provisioned")


def test_the_refusal_names_the_schema(unusable_schema_refusal, unusable_tenant_schema):
    # Arrange
    message = unusable_schema_refusal
    # Act
    named = unusable_tenant_schema in message
    # Assert
    assert named, message


def test_the_refusal_names_the_current_role(unusable_schema_refusal, workspace_cluster):
    # Arrange
    conn = connect(workspace_cluster, read_only=True, rows_by_name=True)
    try:
        me = conn.fetchone("SELECT current_user AS me")["me"]
    finally:
        conn.close()
    # Act
    named = me in unusable_schema_refusal
    # Assert
    assert named, unusable_schema_refusal


def test_an_unusable_schema_is_not_registered(unusable_schema_refusal, workspace_cluster, who):
    # Arrange
    _ = unusable_schema_refusal
    # Act: the refusal fires before the registry table is even created, so
    # "not registered" is either no table at all or no row for this identity
    conn = connect(workspace_cluster, read_only=True, rows_by_name=True)
    try:
        table = conn.fetchone("SELECT to_regclass('scitex_cards_workspaces') AS t")["t"]
        registered = (
            conn.fetchone(
                "SELECT count(*) AS n FROM scitex_cards_workspaces WHERE segments = ?",
                [[who]],
            )["n"]
            if table is not None
            else 0
        )
    finally:
        conn.close()
    # Assert
    assert registered == 0


# EOF
