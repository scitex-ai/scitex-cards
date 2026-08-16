#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the canonical identity resolver (`scitex_cards._users._identity`).

Real fakes only — no mocks (Req STX-NM / PA-306). The pure
:func:`canonical_identity` is exercised with plain ``User`` snapshots; the
store-aware :func:`resolve_identity` round-trips against a ``tmp_path`` YAML
store; the ``SCITEX_TODO_STRICT_IDENTITY`` env gate is flipped via
``monkeypatch.setenv``.
"""

from __future__ import annotations

import pytest

from scitex_cards import _users
from scitex_cards._users import (
    ENV_STRICT_IDENTITY,
    IDENTITY_ALIASES,
    UnknownIdentityError,
    User,
    canonical_identity,
    resolve_identity,
    strict_identity_enabled,
)


# --------------------------------------------------------------------------- #
# (b) alias table — declared synonyms                                         #
# --------------------------------------------------------------------------- #
def _unknown_identity_message(name: str) -> str:
    """The text STRICT canonicalisation refuses an unknown name with.

    Lets each test below pin one property of that message without
    re-counting the raise itself as a second assertion.
    """
    with pytest.raises(UnknownIdentityError) as excinfo:
        canonical_identity(name, strict=True)
    return str(excinfo.value)


def test_alias_sac_to_scitex_agent_container():
    # Arrange
    raw = "sac"
    # Act
    canonical = canonical_identity(raw)
    # Assert
    assert canonical == "scitex-agent-container"


def test_alias_orochi_to_scitex_orochi():
    # Arrange
    raw = "orochi"
    # Act
    canonical = canonical_identity(raw)
    # Assert
    assert canonical == "scitex-orochi"


def test_alias_table_seeds_the_sac_synonym():
    # Arrange
    canonical = "scitex-agent-container"
    # Act
    aliases = IDENTITY_ALIASES[canonical]
    # Assert
    assert aliases == ("sac",)


def test_alias_table_seeds_the_orochi_synonym():
    # Arrange
    canonical = "scitex-orochi"
    # Act
    aliases = IDENTITY_ALIASES[canonical]
    # Assert
    assert aliases == ("orochi",)


# --------------------------------------------------------------------------- #
# (c) mechanical normalise — derivable drift                                  #
# --------------------------------------------------------------------------- #
def test_mechanical_strip_proj_prefix():
    # Arrange
    raw = "proj-scitex-dev"
    # Act
    canonical = canonical_identity(raw)
    # Assert
    assert canonical == "scitex-dev"


def test_mechanical_strip_proj_paper_prefix():
    """proj-paper- (the longer prefix) must win over proj-."""
    # Arrange
    raw = "proj-paper-neurovista"
    # Act
    canonical = canonical_identity(raw)
    # Assert
    assert canonical == "neurovista"


def test_mechanical_strip_trailing_host():
    # Arrange
    raw = "lead-ywata-note-win"
    # Act
    canonical = canonical_identity(raw)
    # Assert
    assert canonical == "lead"


def test_mechanical_strip_prefix_and_host_together():
    # Arrange
    raw = "proj-scitex-dev-ywata-note-win"
    # Act
    canonical = canonical_identity(raw)
    # Assert
    assert canonical == "scitex-dev"


# --------------------------------------------------------------------------- #
# (a) registered-names hit — exact match beats everything                     #
# --------------------------------------------------------------------------- #
#: A registered user carrying its CURRENT canonical name first and the old
#: one after it — the shape a rename leaves behind.
RENAMED_USER = User(
    id="u_000000000001",
    kind="agent",
    names=["scitex-dev", "proj-scitex-dev"],
)

#: A user reachable by three keys: stable id, host@name, and plain name.
HOSTED_USER = User(
    id="u_00000000abcd",
    kind="agent",
    names=["neurovista"],
    host_at_name="spartan@neurovista",
)


def test_registered_old_alias_returns_the_canonical_first_name():
    """An OLD alias resolves to the CURRENT canonical (first) name."""
    # Arrange
    raw = "proj-scitex-dev"
    # Act
    canonical = canonical_identity(raw, users=[RENAMED_USER])
    # Assert
    assert canonical == "scitex-dev"


def test_registered_name_hit_returns_canonical_first_name():
    # Arrange
    raw = "scitex-dev"
    # Act
    canonical = canonical_identity(raw, users=[RENAMED_USER])
    # Assert
    assert canonical == "scitex-dev"


def test_registered_id_hit_resolves_to_the_name():
    # Arrange
    raw = "u_00000000abcd"
    # Act
    canonical = canonical_identity(raw, users=[HOSTED_USER])
    # Assert
    assert canonical == "neurovista"


def test_registered_host_at_name_hit_resolves_to_the_name():
    # Arrange
    raw = "spartan@neurovista"
    # Act
    canonical = canonical_identity(raw, users=[HOSTED_USER])
    # Assert
    assert canonical == "neurovista"


def test_registered_hit_beats_alias_table():
    """If someone registers a user whose canonical name is 'sac' itself, the
    registry wins over the alias table (precedence a before b)."""
    # Arrange
    users = [User(id="u_00000000f00d", kind="agent", names=["sac"])]
    # Act
    canonical = canonical_identity("sac", users=users)
    # Assert
    assert canonical == "sac"


# --------------------------------------------------------------------------- #
# (d) unknown — strict raises with hint / non-strict returns input           #
# --------------------------------------------------------------------------- #
#: A name in neither the registry nor the alias table, and not derivable by
#: any mechanical normalise.
UNKNOWN_NAME = "totally-unknown-xyz"


def test_unknown_strict_raises_with_hint():
    # Arrange
    raw = UNKNOWN_NAME
    # Act
    # Assert
    with pytest.raises(UnknownIdentityError):
        canonical_identity(raw, strict=True)


def test_the_unknown_identity_error_names_the_input():
    """A message that omits the offending name cannot be acted on."""
    # Arrange
    raw = UNKNOWN_NAME
    # Act
    message = _unknown_identity_message(raw)
    # Assert
    assert raw in message


def test_the_unknown_identity_error_offers_a_next_step():
    # Arrange
    raw = UNKNOWN_NAME
    # Act
    message = _unknown_identity_message(raw)
    # Assert — an actionable hint: register the user, or add an alias.
    assert "register" in message or "alias" in message


def test_unknown_non_strict_returns_input_unchanged():
    # Arrange
    raw = UNKNOWN_NAME
    # Act
    canonical = canonical_identity(raw, strict=False)
    # Assert
    assert canonical == raw


def test_empty_name_always_raises():
    """Blank is never a name — not even with strict OFF."""
    # Arrange
    blank = "   "
    # Act
    # Assert
    with pytest.raises(UnknownIdentityError):
        canonical_identity(blank, strict=False)


# --------------------------------------------------------------------------- #
# idempotency — canonical(canonical(x)) == canonical(x)                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        "sac",
        "orochi",
        "proj-scitex-dev",
        "proj-paper-neurovista",
        "lead-ywata-note-win",
        "already-canonical",
    ],
)
def test_idempotency_holds_for_every_form(raw):
    # Arrange
    once = canonical_identity(raw, strict=False)
    # Act
    twice = canonical_identity(once, strict=False)
    # Assert
    assert once == twice


def test_idempotency_with_registry():
    # Arrange
    once = canonical_identity("proj-scitex-dev", users=[RENAMED_USER])
    # Act
    twice = canonical_identity(once, users=[RENAMED_USER])
    # Assert
    assert once == twice == "scitex-dev"


# --------------------------------------------------------------------------- #
# env gate — strict defaults OFF                                              #
# --------------------------------------------------------------------------- #
def test_strict_defaults_off(env):
    # Arrange
    env.delete(ENV_STRICT_IDENTITY)
    # Act
    enabled = strict_identity_enabled()
    # Assert
    assert enabled is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_strict_env_truthy(env, val):
    # Arrange
    env.set(ENV_STRICT_IDENTITY, val)
    # Act
    enabled = strict_identity_enabled()
    # Assert
    assert enabled is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "maybe"])
def test_strict_env_falsy(env, val):
    # Arrange
    env.set(ENV_STRICT_IDENTITY, val)
    # Act
    enabled = strict_identity_enabled()
    # Assert
    assert enabled is False


# --------------------------------------------------------------------------- #
# resolve_identity — store-aware wrapper, env-gated strict                    #
# --------------------------------------------------------------------------- #
def test_resolve_identity_registered_hit(tmp_path, env):
    # Arrange
    env.delete(ENV_STRICT_IDENTITY)
    store = tmp_path / "tasks.yaml"
    _users.register_user(
        kind="agent",
        names=["scitex-dev", "proj-scitex-dev"],
        store=store,
    )
    # Act
    canonical = resolve_identity("proj-scitex-dev", store=store)
    # Assert
    assert canonical == "scitex-dev"


def test_resolve_identity_non_strict_by_default(tmp_path, env):
    """Unknown + strict OFF (the default) → input returned, no raise."""
    # Arrange
    env.delete(ENV_STRICT_IDENTITY)
    store = tmp_path / "tasks.yaml"
    # Act
    canonical = resolve_identity("unregistered-xyz", store=store)
    # Assert
    assert canonical == "unregistered-xyz"


def test_resolve_identity_strict_env_flip_raises(tmp_path, env):
    # Arrange
    env.set(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(UnknownIdentityError):
        resolve_identity("unregistered-xyz", store=store)


def test_resolve_identity_alias_via_store(tmp_path, env):
    # Arrange
    env.delete(ENV_STRICT_IDENTITY)
    store = tmp_path / "tasks.yaml"
    # Act
    canonical = resolve_identity("sac", store=store)
    # Assert
    assert canonical == "scitex-agent-container"


# --------------------------------------------------------------------------- #
# resolve_user canonicalised retry (wired seam)                               #
# --------------------------------------------------------------------------- #
def test_resolve_user_canonicalises_drifted_name(tmp_path):
    """'proj-scitex-dev' is NOT in names[], but canonicalises to
    'scitex-dev' — so the drifted name must still find a user."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    # Act
    resolved = _users.resolve_user("proj-scitex-dev", store=store)
    # Assert
    assert resolved is not None


def test_the_canonicalised_hit_is_the_registered_user(tmp_path):
    """...and it is the SAME user, not a second record silently created."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    # Act
    resolved = _users.resolve_user("proj-scitex-dev", store=store)
    # Assert
    assert resolved.id == created.id


def test_resolve_user_unknown_still_none(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    # Act
    resolved = _users.resolve_user("nobody-here", store=store)
    # Assert
    assert resolved is None
