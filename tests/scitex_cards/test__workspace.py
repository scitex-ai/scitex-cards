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

import pytest

from scitex_cards._store_errors import StoreUnavailableError
from scitex_cards._workspace import (
    ENV_WORKSPACE_ROOT,
    InvalidWorkspaceIdentity,
    is_valid_identity,
    resolve_workspace_store,
)


@pytest.fixture
def workspace_root(tmp_path):
    """A real root with one provisioned workspace, restored afterwards."""
    saved = os.environ.get(ENV_WORKSPACE_ROOT)
    os.environ[ENV_WORKSPACE_ROOT] = str(tmp_path)
    store = tmp_path / "acme" / ".scitex" / "cards" / "cards.db"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"")
    yield tmp_path
    if saved is None:
        os.environ.pop(ENV_WORKSPACE_ROOT, None)
    else:
        os.environ[ENV_WORKSPACE_ROOT] = saved


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
    saved = os.environ.get(ENV_WORKSPACE_ROOT)
    os.environ.pop(ENV_WORKSPACE_ROOT, None)

    # Act
    try:
        resolve_workspace_store("acme")
        raised = None
    except StoreUnavailableError as exc:
        raised = exc
    finally:
        if saved is not None:
            os.environ[ENV_WORKSPACE_ROOT] = saved

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


def test_a_provisioned_workspace_resolves_under_the_root(workspace_root):
    # Arrange
    identity = "acme"

    # Act
    store = resolve_workspace_store(identity)

    # Assert
    assert store == workspace_root / "acme" / ".scitex" / "cards" / "cards.db"


def test_the_resolved_store_is_inside_the_root(workspace_root):
    """Pins the containment property itself, not just one expected string."""
    # Arrange
    identity = "acme"

    # Act
    store = resolve_workspace_store(identity)

    # Assert
    assert workspace_root.resolve() in store.resolve().parents


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
