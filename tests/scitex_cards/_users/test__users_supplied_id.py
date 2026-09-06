#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Caller-supplied user ids + the canonical deterministic OIDC derivation.

Decision card ``cards-email-uniqueness-is-fleet-wide-not-per-host-20260814``
(2026-08-14): with the store synchronised across hosts, randomly minted
``u_*`` ids give the same human a DIFFERENT id on every host, and the sync's
do-nothing-on-conflict rule silently splits that identity at reconcile. The
fix is at birth — ``register_user(id=...)`` accepts a caller-supplied id in
the exact minted format, and :func:`scitex_cards._users.deterministic_user_id`
is THE single fleet-wide derivation (length-prefixed framing, sha256,
truncated) that scitex-hub calls instead of keeping a copy.

Real round-trips against a ``tmp_path`` YAML store — no mocks (Req STX-NM /
PA-306), same idiom as ``test__users.py``. Three-valued honesty is pinned:
created / already-existed-same (idempotent) / refused-different (fail-loud).
"""

from __future__ import annotations

import pytest

from scitex_cards import _users


# --------------------------------------------------------------------------- #
# register_user(id=...): created                                              #
# --------------------------------------------------------------------------- #
def test_register_user_accepts_caller_supplied_id(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    # Act
    user = _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Assert
    assert user.id == supplied


def test_caller_supplied_id_round_trips_on_reload(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Act
    reloaded = _users.get_user(supplied, store=store)
    # Assert
    assert reloaded is not None and reloaded.id == supplied


def test_default_none_still_mints_a_random_id(tmp_path):
    """``id=None`` (the default) keeps the previous behavior byte-identical."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    user = _users.register_user(kind="agent", names=["minted"], store=store)
    # Assert
    assert user.id.startswith("u_") and len(user.id) == len("u_") + 12


# --------------------------------------------------------------------------- #
# register_user(id=...): malformed id fails loud                              #
# --------------------------------------------------------------------------- #
def test_supplied_id_without_prefix_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="human", names=["x"], id="3f9a1c0b7e42", store=store
        )


def test_supplied_id_wrong_length_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="human", names=["x"], id="u_3f9a1c0b7e4", store=store
        )


def test_supplied_id_uppercase_hex_rejected(tmp_path):
    """``secrets.token_hex`` emits lowercase; the registry never holds an id
    the random path could not have produced."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="human", names=["x"], id="u_3F9A1C0B7E42", store=store
        )


def test_supplied_id_non_hex_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="human", names=["x"], id="u_3f9a1c0b7e4z", store=store
        )


def test_malformed_supplied_id_error_names_the_expected_format(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    # Act
    # Assert
    with pytest.raises(
        _users.UserValidationError, match="12 lowercase hex"
    ):
        _users.register_user(kind="human", names=["x"], id="bogus", store=store)


@pytest.fixture
def store_after_malformed_id_attempt(tmp_path):
    """A store that survived one registration attempt with a malformed id
    (the attempt raised, as pinned by the tests above)."""
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_users.UserValidationError):
        _users.register_user(kind="human", names=["x"], id="bogus", store=store)
    return store


def test_malformed_supplied_id_writes_nothing(store_after_malformed_id_attempt):
    # Arrange
    store = store_after_malformed_id_attempt
    # Act
    users = _users.list_users(store=store)
    # Assert
    assert users == []


# --------------------------------------------------------------------------- #
# register_user(id=...): already-existed-same is idempotent                   #
# --------------------------------------------------------------------------- #
def test_reregistering_same_identity_is_idempotent(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Act — same identity, same id: returns, never raises.
    again = _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Assert
    assert again.id == supplied


def test_idempotent_reregistration_does_not_duplicate_the_user(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Act
    users = _users.list_users(store=store)
    # Assert
    assert len(users) == 1


def test_idempotent_reregistration_returns_the_existing_record(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    first = _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Act
    again = _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Assert — the EXISTING record verbatim, not a re-stamped one.
    assert again.created_at == first.created_at


def test_reregistration_with_a_subset_of_names_is_idempotent(tmp_path):
    """A user that gained an alias since first registration still
    re-registers idempotently for any subset of its names."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human",
        names=["alice@example.com", "alice"],
        id=supplied,
        store=store,
    )
    # Act
    again = _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Assert
    assert again.id == supplied


# --------------------------------------------------------------------------- #
# register_user(id=...): refused-different fails loud, never merges           #
# --------------------------------------------------------------------------- #
def test_supplied_id_of_a_different_identity_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Act
    # Assert — same id, different name: NEVER silently adopted.
    with pytest.raises(
        _users.UserValidationError, match="different identity"
    ):
        _users.register_user(
            kind="human", names=["bob@example.com"], id=supplied, store=store
        )


def test_supplied_id_kind_mismatch_rejected(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    # Act
    # Assert
    with pytest.raises(
        _users.UserValidationError, match="different identity"
    ):
        _users.register_user(
            kind="agent", names=["alice@example.com"], id=supplied, store=store
        )


@pytest.fixture
def store_after_refused_id_collision(tmp_path):
    """A store holding alice under a supplied id, after a REFUSED attempt to
    re-register that id for bob (the attempt raised, as pinned above)."""
    store = tmp_path / "tasks.yaml"
    supplied = "u_3f9a1c0b7e42"
    _users.register_user(
        kind="human", names=["alice@example.com"], id=supplied, store=store
    )
    with pytest.raises(_users.UserValidationError):
        _users.register_user(
            kind="human", names=["bob@example.com"], id=supplied, store=store
        )
    return store, supplied


def test_refused_id_collision_does_not_merge_names(
    store_after_refused_id_collision,
):
    # Arrange
    store, supplied = store_after_refused_id_collision
    # Act
    existing = _users.get_user(supplied, store=store)
    # Assert — the refused registration left the existing identity untouched.
    assert existing.names == ["alice@example.com"]


def test_supplied_id_with_a_name_owned_by_another_user_rejected(tmp_path):
    """A FRESH id whose requested name belongs to someone else hits the
    existing cross-registry name-uniqueness path unchanged."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="human", names=["taken"], store=store)
    # Act
    # Assert
    with pytest.raises(
        _users.UserValidationError, match="already belongs"
    ):
        _users.register_user(
            kind="human", names=["taken"], id="u_3f9a1c0b7e42", store=store
        )


# --------------------------------------------------------------------------- #
# deterministic_user_id: the canonical fleet-wide derivation                  #
# --------------------------------------------------------------------------- #
def test_deterministic_user_id_has_the_exact_minted_shape():
    # Arrange
    # Act
    uid = _users.deterministic_user_id("https://accounts.google.com", "12345")
    # Assert
    assert uid.startswith("u_") and len(uid) == len("u_") + 12


def test_deterministic_user_id_is_deterministic():
    # Arrange
    args = ("https://accounts.google.com", "12345")
    # Act
    first, second = (
        _users.deterministic_user_id(*args),
        _users.deterministic_user_id(*args),
    )
    # Assert
    assert first == second


def test_deterministic_user_id_differs_by_subject():
    # Arrange
    issuer = "https://accounts.google.com"
    # Act
    a, b = (
        _users.deterministic_user_id(issuer, "12345"),
        _users.deterministic_user_id(issuer, "67890"),
    )
    # Assert
    assert a != b


def test_deterministic_user_id_pinned_vector():
    """The derivation is a FLEET-WIDE contract — a changed framing or hash
    silently re-keys every OIDC-derived identity. Pin one full vector:
    sha256('27:https://accounts.google.com|29:107691...')[:12]."""
    # Arrange
    issuer = "https://accounts.google.com"
    subject = "10769150350006150715113082367"
    # Act
    uid = _users.deterministic_user_id(issuer, subject)
    # Assert
    assert uid == "u_f7c0de337d6d"


def test_separator_ambiguity_cannot_merge_two_people():
    """Twin of scitex-hub's test of the same name (decision card, 2026-08-14):
    naive ``f"{issuer}|{subject}"`` framing collides ``("https://x", "a|b")``
    with ``("https://x|a", "b")`` — two people, one board identity. The
    length-prefixed framing keeps them distinct; this test fails if anyone
    "simplifies" the framing back."""
    # Arrange
    # Act
    a, b = (
        _users.deterministic_user_id("https://x", "a|b"),
        _users.deterministic_user_id("https://x|a", "b"),
    )
    # Assert
    assert a != b


def test_deterministic_user_id_is_accepted_by_register_user(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    uid = _users.deterministic_user_id("https://orcid.org", "0000-0001-2345")
    # Act
    user = _users.register_user(
        kind="human", names=["alice@example.com"], id=uid, store=store
    )
    # Assert
    assert user.id == uid


def test_deterministic_user_id_rejects_empty_issuer():
    # Arrange
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.deterministic_user_id("", "12345")


def test_deterministic_user_id_rejects_empty_subject():
    # Arrange
    # Act
    # Assert
    with pytest.raises(_users.UserValidationError):
        _users.deterministic_user_id("https://accounts.google.com", "")


# EOF
