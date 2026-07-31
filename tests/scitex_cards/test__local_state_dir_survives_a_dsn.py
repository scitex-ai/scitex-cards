#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The local state dir must resolve on EVERY backend (no mocks; real env).

Sibling of ``test__resolve_db_path_refuses_a_dsn.py``. That test pins the
refusal: ``resolve_db_path`` must not coerce a DSN into a ``Path``, because
``Path("postgresql://h/db")`` silently collapses to a relative
``postgresql:/h/db`` and manufactures an empty store. This test pins the other
half — the refusal must not take the LOCAL STATE DIR down with it.

Two axes that used to be one:

    store IDENTITY  - ``$SCITEX_CARDS_DB``; a path OR a server URL
    local state DIR - pidfiles, the delivery ledger, reminder state, and the
                      users/groups sidecar; ALWAYS a real directory

Measured 2026-07-31: because ``resolve_tasks_path`` derived the second from the
first, pointing the fleet at PostgreSQL made the whole query side raise before
it opened a connection — ``list_tasks`` died in path resolution while the write
and canonical-read paths were already DSN-aware. A server store has no
directory; local state still needs one.

The DSN here names port 1 deliberately: nothing listens there, so any code that
tries to CONNECT fails immediately rather than hanging on a timeout. These are
resolution tests — none of them should need a server at all, and the port is
how that stays true instead of merely intended.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._db import ENV_DB, ENV_DB_DEPRECATED
from scitex_cards._paths import _user_root, resolve_tasks_path, runtime_dir

DSN = "postgresql://someone@127.0.0.1:1/scitex_cards"


@pytest.fixture
def clean_store_env():
    """Save and restore the store-identity env vars around a test."""
    saved = {v: os.environ.get(v) for v in (ENV_DB, ENV_DB_DEPRECATED)}
    for v in (ENV_DB, ENV_DB_DEPRECATED):
        os.environ.pop(v, None)
    try:
        yield
    finally:
        for var, val in saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


class TestLocalStateDirOnAServerStore:
    """A PostgreSQL identity must still yield a usable local directory."""

    def test_resolve_tasks_path_does_not_raise_on_a_dsn(self, clean_store_env):
        # Arrange
        os.environ[ENV_DB] = DSN

        # Act
        try:
            resolved = resolve_tasks_path()
        except Exception as exc:  # noqa: BLE001 -- the regression IS any raise
            resolved = exc

        # Assert
        assert not isinstance(resolved, Exception), (
            f"resolve_tasks_path raised {type(resolved).__name__} on a server "
            f"store. Local state (pidfiles, ledger, reminder state, the "
            f"users/groups sidecar) is a filesystem concern and must resolve "
            f"whatever the cards live on."
        )

    def test_resolve_tasks_path_lands_under_the_user_root(self, clean_store_env):
        # Arrange
        os.environ[ENV_DB] = DSN

        # Act
        resolved = resolve_tasks_path()

        # Assert
        assert resolved.parent == _user_root(), (
            f"expected the ambient user root {_user_root()}, got "
            f"{resolved.parent}. A server store has no directory of its own, "
            f"so local state belongs at the same default a fresh install uses."
        )

    def test_resolved_container_is_absolute(self, clean_store_env):
        # Arrange
        os.environ[ENV_DB] = DSN

        # Act
        resolved = resolve_tasks_path()

        # Assert
        assert resolved.is_absolute(), (
            f"{resolved} is relative. This is the exact shape of the coercion "
            f"bug being guarded against: Path('postgresql://h/db') yields a "
            f"RELATIVE 'postgresql:/h/db', which is how an empty store gets "
            f"manufactured under the current working directory."
        )

    def test_the_dsn_does_not_leak_into_the_path(self, clean_store_env):
        # Arrange
        os.environ[ENV_DB] = DSN

        # Act
        resolved = resolve_tasks_path()

        # Assert
        assert "postgresql" not in str(resolved), (
            f"the DSN leaked into the local path: {resolved}"
        )

    def test_runtime_dir_resolves_on_a_server_store(self, clean_store_env):
        # Arrange
        os.environ[ENV_DB] = DSN

        # Act
        try:
            d = runtime_dir(create=False)
        except Exception as exc:  # noqa: BLE001 -- the regression IS any raise
            d = exc

        # Assert
        assert not isinstance(d, Exception), (
            f"runtime_dir raised {type(d).__name__} on a server store; "
            f"pidfiles and the delivery ledger have nowhere to live."
        )


class TestFileStoreResolutionIsUnchanged:
    """The SQLite path must behave exactly as it did before the split."""

    def test_container_sits_beside_the_database(self, clean_store_env, tmp_path):
        # Arrange
        db = tmp_path / "cards.db"
        os.environ[ENV_DB] = str(db)

        # Act
        resolved = resolve_tasks_path()

        # Assert
        assert resolved == tmp_path / "tasks.yaml", (
            f"expected the container beside the database, got {resolved}"
        )

    def test_explicit_argument_still_wins_outright(self, clean_store_env, tmp_path):
        # Arrange
        os.environ[ENV_DB] = DSN
        explicit = tmp_path / "elsewhere" / "tasks.yaml"

        # Act
        resolved = resolve_tasks_path(explicit)

        # Assert
        assert resolved == explicit, (
            f"an explicit path must win even when the ambient store is a "
            f"server, got {resolved}"
        )


class TestResolveStoreReportsTheBackend:
    """The 'which store am I on?' verb must survive the answer being a server."""

    def test_resolve_store_does_not_raise_on_a_dsn(self, clean_store_env):
        # Arrange
        from scitex_cards._store import resolve_store

        os.environ[ENV_DB] = DSN

        # Act
        try:
            info = resolve_store()
        except Exception as exc:  # noqa: BLE001 -- the regression IS any raise
            info = exc

        # Assert
        assert not isinstance(info, Exception), (
            f"resolve_store raised {type(info).__name__}. A diagnostic that "
            f"dies on the case being diagnosed reads as 'the store is broken'."
        )

    def test_backend_is_named_for_a_server(self, clean_store_env):
        # Arrange
        from scitex_cards._store import resolve_store

        os.environ[ENV_DB] = DSN

        # Act
        info = resolve_store()

        # Assert
        assert info["backend"] == "postgresql", (
            f"expected backend 'postgresql', got {info['backend']!r}"
        )

    def test_exists_is_none_rather_than_false_on_a_server(self, clean_store_env):
        # Arrange
        from scitex_cards._store import resolve_store

        os.environ[ENV_DB] = DSN

        # Act
        info = resolve_store()

        # Assert
        assert info["exists"] is None, (
            f"expected None (the question does not apply to a server), got "
            f"{info['exists']!r}. False is not a safe default here: it reads "
            f"as 'your store is missing' to whoever is debugging a cutover."
        )

    def test_resolved_target_is_reported_uncoerced(self, clean_store_env):
        # Arrange
        from scitex_cards._store import resolve_store

        os.environ[ENV_DB] = DSN

        # Act
        info = resolve_store()

        # Assert
        assert info["resolved"] == DSN, (
            f"expected the target as written, got {info['resolved']!r}"
        )

    def test_backend_is_sqlite_for_a_path(self, clean_store_env, tmp_path):
        # Arrange
        from scitex_cards._store import resolve_store

        db = tmp_path / "cards.db"
        db.touch()
        os.environ[ENV_DB] = str(db)

        # Act
        info = resolve_store()

        # Assert
        assert info["backend"] == "sqlite", (
            f"expected backend 'sqlite', got {info['backend']!r}"
        )

    def test_exists_stays_boolean_for_a_path(self, clean_store_env, tmp_path):
        # Arrange
        from scitex_cards._store import resolve_store

        db = tmp_path / "cards.db"
        db.touch()
        os.environ[ENV_DB] = str(db)

        # Act
        info = resolve_store()

        # Assert
        assert info["exists"] is True, (
            f"a file store must still answer the existence question, got "
            f"{info['exists']!r}"
        )


class TestStoreUuidReaderIsNotPathOnly:
    """A server identity must not read as 'absent' — that disarms the guard."""

    def test_unreachable_server_returns_none_without_raising(self):
        # Arrange
        from scitex_cards._store_uuid import store_uuid_at

        # Act
        try:
            got = store_uuid_at(DSN)
        except Exception as exc:  # noqa: BLE001 -- contract is "never raises"
            got = exc

        # Assert
        assert got is None, (
            f"store_uuid_at must report None for an unreachable server and "
            f"never raise (it is a reporting primitive), got {got!r}"
        )

    def test_unreachable_server_answers_promptly(self):
        # Arrange
        import time

        from scitex_cards._store_uuid import store_uuid_at

        unroutable = "postgresql://someone@192.0.2.1:5432/scitex_cards"

        # Act
        started = time.monotonic()
        store_uuid_at(unroutable)
        elapsed = time.monotonic() - started

        # Assert
        # A CEILING, not the configured value. libpq applies no connect timeout
        # by default, and this hung >40s against a dead port before the bound
        # existed. 192.0.2.0/24 is TEST-NET-1 (RFC 5737) — reserved and
        # unroutable, so it BLACKHOLES rather than refusing; a refused port
        # returns instantly and would pass even with the bug present, which is
        # what makes this address the real control. Asserting the exact timeout
        # would pin a value designed to be tuned; asserting "bounded" pins the
        # property that matters.
        assert elapsed < 30, (
            f"store_uuid_at blocked {elapsed:.1f}s on an unroutable server. It "
            f"backs `resolve-store`, which is run precisely when things are "
            f"broken — hanging there is not a lesser failure than answering "
            f"wrongly."
        )


# EOF
