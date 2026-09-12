"""Cards shared state has one primitive-owned PostgreSQL identity."""

from __future__ import annotations

import pytest

from scitex_cards._inbox_postgres import resolve_dsn
from scitex_cards._store_target import resolve_store_target


_SHARED = "postgresql://cards@127.0.0.1:55432/scitex"
_OTHER = "postgresql://cards@127.0.0.1:59999/wrong"
_RETIRED_STORE = "SCITEX_CARDS_" + "DB"
_RETIRED_INBOX = "SCITEX_CARDS_" + "INBOX_DSN"


@pytest.fixture
def shared_store(env) -> str:
    env.set("SCITEX_STORE_DSN", _SHARED)
    env.set(_RETIRED_STORE, _OTHER)
    env.set(_RETIRED_INBOX, _OTHER)
    env.set("SCITEX_CARDS_NOTIFY_DSN", _OTHER)
    return _SHARED


def test_ambient_card_and_inbox_state_use_the_primitive(shared_store: str) -> None:
    # Arrange
    expected = (shared_store, shared_store)
    # Act
    observed = (resolve_store_target(), resolve_dsn())
    # Assert
    assert observed == expected


def test_explicit_store_still_wins(shared_store: str) -> None:
    # Arrange
    expected = (_OTHER, _OTHER)
    # Act
    observed = (resolve_store_target(_OTHER), resolve_dsn(_OTHER))
    # Assert
    assert observed == expected


def test_primitive_rejects_a_filesystem_store(env) -> None:
    # Arrange
    env.set("SCITEX_STORE_DSN", "/tmp/cards.db")
    # Act
    # Assert
    with pytest.raises(Exception, match="not a Postgres DSN"):
        resolve_store_target()


def test_cards_specific_store_variables_are_not_consulted(
    env,
) -> None:
    # Arrange
    env.delete("SCITEX_STORE_DSN")
    env.set(_RETIRED_STORE, _OTHER)
    env.set(_RETIRED_INBOX, _OTHER)
    # Act
    resolved = resolve_store_target()
    observed = (resolved != _OTHER, ":55432/" in resolved, resolve_dsn())
    # Assert
    assert observed == (True, True, resolved)
