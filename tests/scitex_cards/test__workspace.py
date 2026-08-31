#!/usr/bin/env python3
"""Tenant isolation lives in one function, so the hostile inputs get tested here.

This resolver is the single point where one workspace is kept out of another's
cards, and scitex-hub reports that tenant isolation is the operator's first security
requirement. So the weight of this file is on what must be REFUSED, not on the happy
path - a resolver that returns the right answer for good input and something for bad
input is not an isolation boundary.

scitex-db asked for a denylist (reject ``/``, ``.db``, ``.yaml``). These tests pin an
ALLOWLIST instead, because ``..`` contains no slash, ``%2e%2e%2f`` contains no
literal slash, and a denylist at a security boundary fails by omission. The cases
below are the ones a denylist would have let through.
"""

import os
from urllib.parse import urlsplit

import pytest

from scitex_cards._store_errors import StoreUnavailableError
from scitex_cards._workspace import (
    ENV_WORKSPACE_DB,
    InvalidWorkspaceIdentity,
    is_valid_identity,
    provision_workspace_store,
    resolve_workspace_store,
)


@pytest.fixture
def workspace_root(new_store):
    """A real cluster with one provisioned workspace, restored afterwards.

    Named `workspace_root` still, because every test below asks it for the same
    thing: somewhere isolated with `acme` already provisioned. What it hands back
    changed from a directory to a cluster DSN when a tenant became a SCHEMA.
    """
    cluster = new_store("workspace", bootstrap=False)
    saved = os.environ.get(ENV_WORKSPACE_DB)
    os.environ[ENV_WORKSPACE_DB] = cluster
    provision_workspace_store("acme")
    yield cluster
    if saved is None:
        os.environ.pop(ENV_WORKSPACE_DB, None)
    else:
        os.environ[ENV_WORKSPACE_DB] = saved


# === what a valid identity is ============================================


@pytest.mark.parametrize("ok", ["acme", "a", "acme-corp", "acme_corp", "team1", "a1b2"])
def test_slug_shaped_identities_are_accepted(ok):
    # Arrange
    value = ok

    # Act
    valid = is_valid_identity(value)

    # Assert
    assert valid is True


# === what must be refused - the denylist gaps ============================


@pytest.mark.parametrize(
    "hostile",
    [
        "../other",  # classic traversal, contains a slash
        "..",  # traversal with NO slash - a slash denylist misses this
        "....//other",  # doubled dots
        "%2e%2e%2fother",  # url-encoded traversal, no literal slash
        "/etc/passwd",  # absolute
        "acme/../other",  # traversal mid-string
        "acme\\other",  # Windows separator, not a forward slash
        "acme\x00",  # NUL byte
        "acme ",  # trailing space
        " acme",  # leading space
        "acme\n",  # trailing newline
        "acme\nother",  # embedded newline - would defeat an unanchored regex
        "ACME",  # uppercase: one workspace must not have two spellings
        "-acme",  # leading punctuation
        "_acme",
        "acme.db",  # looks like a file
        "acme.yaml",
        "base/acme/.scitex/cards/tasks.yaml",  # exactly what hub used to inject
        "",  # empty
        "a" * 64,  # over length
    ],
)
def test_hostile_or_path_shaped_identities_are_refused(hostile):
    # Arrange
    value = hostile

    # Act
    valid = is_valid_identity(value)

    # Assert
    assert valid is False, f"accepted a hostile identity: {value!r}"


@pytest.mark.parametrize("wrong_type", [None, 42, 3.5, ["acme"], {"a": 1}, b"acme"])
def test_non_strings_are_refused(wrong_type):
    """A bytes identity is the subtle one - it has a length and looks stringy."""
    # Arrange
    value = wrong_type

    # Act
    valid = is_valid_identity(value)

    # Assert
    assert valid is False


def test_resolving_a_path_shaped_identity_raises_the_caller_fault_type(workspace_root):
    """InvalidWorkspaceIdentity, not StoreUnavailableError: the CALLER passed the
    wrong kind of value, which is a 400 not a 500."""
    # Arrange
    hostile = "../acme"

    # Act
    try:
        resolve_workspace_store(hostile)
        raised = None
    except InvalidWorkspaceIdentity as exc:
        raised = exc

    # Assert
    assert raised is not None


def test_the_refusal_does_not_echo_the_rejected_value(workspace_root):
    """A rejected identity may be an injection payload; do not repeat it into logs."""
    # Arrange
    hostile = "../../etc/passwd"

    # Act
    try:
        resolve_workspace_store(hostile)
        message = ""
    except InvalidWorkspaceIdentity as exc:
        message = str(exc)

    # Assert
    assert "etc/passwd" not in message


# === fail closed =========================================================


def test_an_unconfigured_root_refuses_rather_than_using_the_ambient_store():
    """THE ISOLATION TEST. A fallback here would serve one tenant another's cards."""
    # Arrange
    saved = os.environ.get(ENV_WORKSPACE_DB)
    os.environ.pop(ENV_WORKSPACE_DB, None)

    # Act
    try:
        resolve_workspace_store("acme")
        raised = None
    except StoreUnavailableError as exc:
        raised = exc
    finally:
        if saved is not None:
            os.environ[ENV_WORKSPACE_DB] = saved

    # Assert
    assert raised is not None


def test_an_unprovisioned_workspace_refuses(workspace_root):
    # Arrange
    unknown = "notacustomer"

    # Act
    try:
        resolve_workspace_store(unknown)
        raised = None
    except StoreUnavailableError as exc:
        raised = exc

    # Assert
    assert raised is not None


# === the happy path, last because it is the least interesting ============


def test_a_provisioned_workspace_resolves_to_what_provision_made(workspace_root):
    # Arrange
    identity = "acme"

    # Act
    store = resolve_workspace_store(identity)

    # Assert
    assert store == provision_workspace_store(identity)


def test_the_resolved_store_is_scoped_to_its_own_schema(workspace_root):
    """Pins the isolation property itself, not one expected string.

    The old form asserted a filesystem location under the root. Containment is
    still the property that matters, but it is now carried by `search_path`: the
    handle names one schema, so a statement made on it cannot reach another
    tenant's tables without qualifying them. That IS the boundary (ADR-0017).
    """
    # Arrange
    identity = "acme"

    # Act
    store = resolve_workspace_store(identity)

    # Assert
    assert "search_path%3Dws_" in store


def test_the_resolved_store_stays_on_the_configured_cluster(workspace_root):
    """The other half of containment: the right schema on the RIGHT server.

    Scoping to a tenant schema means nothing if the handle points at a
    different cluster, which is exactly what a stale or half-applied
    configuration produces.
    """
    # Arrange
    identity = "acme"

    # Act
    store = resolve_workspace_store(identity)

    # Assert
    # SERVER AND DATABASE, not a string prefix. `startswith` looked equivalent
    # and was not: the cluster DSN can carry more than one `options` parameter,
    # only the last of which libpq honours, so the resolved DSN legitimately
    # differs from the cluster string before the schema is even appended.
    # Comparing the parts that actually name the server says what this test
    # means without depending on how the query string was assembled.
    assert urlsplit(store)[:3] == urlsplit(workspace_root)[:3]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
