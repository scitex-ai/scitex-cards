#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the min-client-version floor (operator directive 2026-07-21).

Background: on the same day a 0.17.1 project venv, a partial-0.17.4
container venv, and a 0.17.2 service venv each silently misbehaved against
the shared store — one of them served an EMPTY example board — and none of
the warnings were read. The operator's ruling: an outdated client must
ERROR the moment it opens the store, never merely warn.

``schema_meta.min_client_version`` is the floor; :func:`scitex_cards._db.connect`
is the ONE chokepoint both the read path (``_store.list_tasks`` -> ``_model.load_tasks``
-> ``_store._read_canonical_db_or_raise`` -> ``_db_export.export_doc`` -> ``_db.open_db``,
which is ``connect`` + ``init_schema``) and the write path
(``_db_mirror.mirror_doc_incremental`` opens the same way) funnel through — so gating it
there gates both without touching either module.

The S2 SQLite read accelerator (``_store_read_sqlite.list_tasks_sqlite``) this file
originally exercised as "the read path" is DELETED (2026-07-21, a separate incident:
its own freshness guard could never again pass once SQLite became canonical, so it
refused unconditionally and fell back to an empty YAML/example chain). ``list_tasks``
now has exactly one read path, and it is the one exercised below.

Every test gets its OWN scratch, schema-complete, floor-UNSET database via the
suite-wide ``_store_env_stays_pinned`` autouse fixture (``tests/conftest.py``);
``os.environ["SCITEX_CARDS_DB"]`` is that path.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from scitex_cards import _db, _db_mirror, _store
from scitex_cards._min_client_version import (
    ClientTooOldError,
    parse_version_tuple,
    read_floor,
    resolve_running_version,
    stamp_floor,
)


def _db_path() -> str:
    return os.environ["SCITEX_CARDS_DB"]


def _set_floor(version: str) -> None:
    """Stamp the floor via a RAW ``sqlite3`` connection.

    Deliberately bypasses ``_db.connect`` (and therefore its own gate): the
    production CLI verb (``_cli/_min_client_version.py``) goes through
    ``_db.connect`` and so inherits whatever floor is ALREADY set — which
    means a test that first sets a too-high floor could never lower it again
    through that path. This helper represents an operator fixing the raw
    file directly, not the normal CLI flow, so tests can freely move the
    floor up and down to exercise both sides of the gate.
    """
    import sqlite3

    conn = sqlite3.connect(str(_db_path()))
    try:
        stamp_floor(conn, version)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# parse_version_tuple                                                         #
# --------------------------------------------------------------------------- #
def test_parse_version_tuple_parses_a_plain_dotted_version():
    # Arrange
    version = "0.17.4"

    # Act
    parsed = parse_version_tuple(version)

    # Assert
    assert parsed == (0, 17, 4)


def test_parse_version_tuple_keeps_only_the_leading_digits_of_a_suffixed_segment():
    # Arrange
    version = "0.17.4-rc1"

    # Act
    parsed = parse_version_tuple(version)

    # Assert
    assert parsed == (0, 17, 4)


def test_parse_version_tuple_treats_a_non_numeric_segment_as_zero():
    # Arrange
    version = "0.0.local"

    # Act
    parsed = parse_version_tuple(version)

    # Assert
    assert parsed == (0, 0, 0)


def test_parse_version_tuple_orders_versions_the_way_a_reader_expects():
    # Arrange / Act / Assert
    assert parse_version_tuple("0.17.4") < parse_version_tuple("0.17.5")
    assert parse_version_tuple("0.9.9") < parse_version_tuple("0.17.0")
    assert parse_version_tuple("1.0.0") > parse_version_tuple("0.99.99")


# --------------------------------------------------------------------------- #
# resolve_running_version                                                     #
# --------------------------------------------------------------------------- #
def test_resolve_running_version_never_raises():
    # Arrange
    # Act
    version = resolve_running_version()

    # Assert
    assert isinstance(version, str) and version


def test_resolve_running_version_is_a_dotted_numeric_string():
    # Arrange
    # Act
    version = resolve_running_version()

    # Assert
    assert parse_version_tuple(version) >= (0, 0, 0)


# --------------------------------------------------------------------------- #
# read_floor / stamp_floor                                                    #
# --------------------------------------------------------------------------- #
def test_read_floor_is_none_on_a_fresh_database():
    # Arrange
    conn = _db.connect(_db_path())

    # Act
    floor = read_floor(conn)
    conn.close()

    # Assert
    assert floor is None


def test_stamp_floor_then_read_floor_round_trips():
    # Arrange
    conn = _db.connect(_db_path())

    # Act
    stamp_floor(conn, "1.2.3")
    conn.commit()
    floor = read_floor(conn)
    conn.close()

    # Assert
    assert floor == "1.2.3"


def test_stamp_floor_replaces_a_previously_set_floor():
    # Arrange
    conn = _db.connect(_db_path())
    stamp_floor(conn, "1.0.0")
    conn.commit()

    # Act
    stamp_floor(conn, "2.0.0")
    conn.commit()
    floor = read_floor(conn)
    conn.close()

    # Assert
    assert floor == "2.0.0"


def test_connect_opens_fine_on_a_totally_fresh_database_with_no_schema_meta_table_yet():
    """`connect()` runs BEFORE `init_schema()`, so a brand-new file has no
    `schema_meta` table at all — `read_floor` must treat that the same as a
    present-but-empty table: no floor, proceed."""
    # Arrange
    with tempfile.TemporaryDirectory() as d:
        fresh_path = Path(d) / "brand-new.db"

        # Act
        conn = _db.connect(fresh_path)

        # Assert
        assert conn is not None
        conn.close()


# --------------------------------------------------------------------------- #
# connect() — the DB-open chokepoint                                          #
# --------------------------------------------------------------------------- #
def test_connect_opens_fine_when_no_floor_is_set():
    # Arrange
    # Act
    conn = _db.connect(_db_path())

    # Assert
    assert conn is not None
    conn.close()


def test_connect_opens_fine_when_the_client_exactly_meets_the_floor():
    # Arrange
    running = resolve_running_version()
    _set_floor(running)

    # Act
    conn = _db.connect(_db_path())

    # Assert
    assert conn is not None
    conn.close()


def test_connect_opens_fine_when_the_client_is_newer_than_the_floor():
    # Arrange
    _set_floor("0.0.1")

    # Act
    conn = _db.connect(_db_path())

    # Assert
    assert conn is not None
    conn.close()


def test_connect_raises_client_too_old_when_the_client_is_below_the_floor():
    # Arrange
    _set_floor("9999.0.0")

    # Act / Assert
    with pytest.raises(ClientTooOldError):
        _db.connect(_db_path())


def test_the_too_old_error_names_both_the_running_and_the_floor_version():
    # Arrange
    running = resolve_running_version()
    _set_floor("9999.0.0")

    # Act
    with pytest.raises(ClientTooOldError) as exc_info:
        _db.connect(_db_path())

    # Assert
    message = str(exc_info.value)
    assert running in message
    assert "9999.0.0" in message


def test_the_too_old_error_names_the_wheel_upgrade_command():
    # Arrange
    _set_floor("9999.0.0")

    # Act
    with pytest.raises(ClientTooOldError) as exc_info:
        _db.connect(_db_path())

    # Assert
    assert "pip install -U scitex-cards" in str(exc_info.value)


def test_the_too_old_error_names_the_editable_checkout_upgrade_command():
    # Arrange
    _set_floor("9999.0.0")

    # Act
    with pytest.raises(ClientTooOldError) as exc_info:
        _db.connect(_db_path())

    # Assert
    message = str(exc_info.value)
    assert "uv pip install -e" in message
    assert "scitex-cards" in message


def test_the_too_old_error_never_mentions_yaml():
    # Arrange
    _set_floor("9999.0.0")

    # Act
    with pytest.raises(ClientTooOldError) as exc_info:
        _db.connect(_db_path())

    # Assert
    assert "yaml" not in str(exc_info.value).lower()


# --------------------------------------------------------------------------- #
# The gate covers BOTH the write path and the read path                       #
# --------------------------------------------------------------------------- #
def test_the_write_path_is_gated_the_same_as_connect():
    """`_db_mirror.mirror_doc_incremental` opens via `_db.open_db`, which is
    `_db.connect` + `init_schema` — the write chokepoint inherits the same
    floor check with no changes needed in `_db_mirror` itself."""
    # Arrange
    _set_floor("9999.0.0")
    doc = {"tasks": [{"id": "t1", "title": "T", "status": "deferred"}]}

    # Act / Assert
    with pytest.raises(ClientTooOldError):
        _db_mirror.mirror_doc_incremental(doc, _db_path(), store_path=_db_path())


def test_the_read_path_is_gated_the_same_as_connect():
    """`_store.list_tasks` -> `_model.load_tasks` ->
    `_store._read_canonical_db_or_raise` -> `_db_export.export_doc` ->
    `_db.open_db` opens via `_db.connect` — the read chokepoint inherits the
    same floor check. (The S2 SQLite read accelerator this test used to call
    directly, `_store_read_sqlite.list_tasks_sqlite`, is deleted; `list_tasks`
    is the one read path now.) The floor is stamped only AFTER a card is
    mirrored, so the read path is exercised against a genuinely populated,
    schema-complete database."""
    # Arrange — write one card while the floor is still unset.
    doc = {"tasks": [{"id": "t1", "title": "T", "status": "deferred"}]}
    _db_mirror.mirror_doc_incremental(doc, _db_path(), store_path=_db_path())
    _set_floor("9999.0.0")

    # Act / Assert
    with pytest.raises(ClientTooOldError):
        _store.list_tasks(scope="")


def test_the_read_path_still_works_once_the_floor_is_cleared_again():
    """Sanity: the gate is not a one-way trap — lowering the floor (or
    unstamping it) restores normal reads, proving the check re-runs on
    every connect rather than caching a bad verdict."""
    # Arrange
    doc = {"tasks": [{"id": "t1", "title": "T", "status": "deferred"}]}
    _db_mirror.mirror_doc_incremental(doc, _db_path(), store_path=_db_path())
    _set_floor("9999.0.0")
    with pytest.raises(ClientTooOldError):
        _store.list_tasks(scope="")

    # Act
    _set_floor("0.0.1")
    cards = _store.list_tasks(scope="")

    # Assert
    assert [c["id"] for c in cards] == ["t1"]


# EOF
