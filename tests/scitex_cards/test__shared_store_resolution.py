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
def shared_store(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SCITEX_STORE_DSN", _SHARED)
    monkeypatch.setenv(_RETIRED_STORE, _OTHER)
    monkeypatch.setenv(_RETIRED_INBOX, _OTHER)
    monkeypatch.setenv("SCITEX_CARDS_NOTIFY_DSN", _OTHER)
    return _SHARED


def test_ambient_card_and_inbox_state_use_the_primitive(shared_store: str) -> None:
    assert resolve_store_target() == shared_store
    assert resolve_dsn() == shared_store


def test_explicit_store_still_wins(shared_store: str) -> None:
    assert resolve_store_target(_OTHER) == _OTHER
    assert resolve_dsn(_OTHER) == _OTHER


def test_primitive_rejects_a_filesystem_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCITEX_STORE_DSN", "/tmp/cards.db")
    with pytest.raises(Exception, match="not a Postgres DSN"):
        resolve_store_target()


def test_cards_specific_store_variables_are_not_consulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCITEX_STORE_DSN", raising=False)
    monkeypatch.setenv(_RETIRED_STORE, _OTHER)
    monkeypatch.setenv(_RETIRED_INBOX, _OTHER)

    resolved = resolve_store_target()
    assert resolved != _OTHER
    assert ":55432/" in resolved
    assert resolve_dsn() == resolved
