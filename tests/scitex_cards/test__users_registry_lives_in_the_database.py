#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The user registry lives in the SHARED database, and a card write leaves it.

WHY THIS FILE EXISTS AT ALL IS THE POINT OF IT.

The registry was unreadable and unwritable on the deployed backend for an
unknown length of time, while the whole users suite was green. It was green
honestly: every store under ``tests/scitex_cards/_users/`` is an EXPLICIT
``tmp_path / "tasks.yaml"``, the registry code is correct against a file, and
so the tests proved a true thing about a backend nobody runs. Measured
2026-08-17 in a sac container with ``$SCITEX_CARDS_DB`` set to the fleet
server::

    resolve_tasks_path(None)   /home/agent/.scitex/cards/tasks.yaml
    exists()                   False           registered users   0
    mountinfo   .../overlays/scitex-cards/upper/home/agent -> /home/agent

That path is the container's PRIVATE overlay, so even a working write would
have produced a registry only one agent could read. The defect was not a
missing test for a known behaviour — it was that NO test looked at the
backend where the behaviour was wrong. Every test below is therefore AMBIENT
(``store=None``), which is the only form that resolves to the shared board.

THE WIPE GUARD IS THE ONE TO KEEP

``test_a_card_write_leaves_the_registry_intact`` is not incidental coverage.
``_db_mirror._sync_sections`` used to list ``users`` in ``_SECTION_KEYS``,
where an ORDINARY CARD WRITE issues ``DELETE FROM users`` and rebuilds the
table from the writer's own document — gated on a hash comparing that
document against the previous one, never against the table. A writer holding
a document exported before the first registration computes ``hash(None)``,
mismatches, and rebuilds from nothing:

    DELETE FROM user_names; DELETE FROM users; _insert_users(conn, None) -> 0

The registry is then gone, silently, with the card write reporting success.
It was harmless only while the table was empty — an empty table makes the
exporter omit the key (``if users:``), so every document hashed identically
and the gate never opened. Populating the registry, which is this change's
whole purpose, is exactly what breaks that fixed point. Same shape as the
#780 notifications incident, and neutralised the same way.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import seed_db_from_doc

from scitex_cards._db import connect

_STORE_ENV = "SCITEX_CARDS_DB"


def _registry_ids(db: Path) -> set[str]:
    conn = connect(db)
    try:
        return {r["id"] for r in conn.execute("SELECT id FROM users")}
    finally:
        conn.close()


@pytest.fixture()
def ambient_db_store(tmp_path: Path):
    """An empty board reachable AMBIENTLY — the shape the deployment has.

    The environment variable is the subject here, not scaffolding: the
    registry only reaches the database when nothing names a store, so a test
    that passed ``store=`` would take the file branch and re-prove what the
    existing suite already proves.
    """
    db = tmp_path / "cards.db"
    seed_db_from_doc({"tasks": []}, db)
    before = os.environ.get(_STORE_ENV)
    os.environ[_STORE_ENV] = str(db)
    try:
        yield db
    finally:
        if before is None:
            os.environ.pop(_STORE_ENV, None)
        else:
            os.environ[_STORE_ENV] = before


@pytest.fixture()
def registered(ambient_db_store: Path):
    """One agent registered ambiently."""
    from scitex_cards import _users

    user = _users.register_user(kind="agent", names=["registry-probe"])
    return ambient_db_store, user


def test_an_ambient_registration_reaches_the_database(registered) -> None:
    # Arrange
    db, user = registered
    # Act
    stored = _registry_ids(db)
    # Assert
    assert user.id in stored


def test_an_ambient_registration_resolves_by_name(registered) -> None:
    # Arrange
    from scitex_cards import _users

    # Act
    resolved = _users.resolve_user("registry-probe")
    # Assert
    assert resolved is not None


def test_an_ambient_registration_is_visible_to_a_fresh_read(registered) -> None:
    # Arrange
    from scitex_cards import _users

    # Act
    loaded = _users.load_users()
    # Assert
    assert [u.names[0] for u in loaded] == ["registry-probe"]


def test_a_card_write_leaves_the_registry_intact(registered) -> None:
    """The #780-shaped wipe: an ordinary card write must not rebuild `users`."""
    # Arrange
    from scitex_cards import add_task

    db, user = registered
    # Act
    add_task(
        id="t-after-registration",
        title="ordinary card write",
        assignee="registry-probe",
    )
    # Assert
    assert user.id in _registry_ids(db)


def test_a_stale_document_write_leaves_the_registry_intact(registered) -> None:
    """The specific race: a writer whose document predates the registration.

    This is the one that actually fired. The document is read BEFORE the
    user exists, so it carries no ``users`` key at all; writing it back is
    what used to hash as ``None``, mismatch, and delete the table.
    """
    # Arrange
    from scitex_cards._db_mirror import mirror_doc_incremental

    db, user = registered
    conn = connect(db)
    # Act
    try:
        mirror_doc_incremental({"tasks": []}, db, conn=conn)
        conn.commit()
    finally:
        conn.close()
    # Assert
    assert user.id in _registry_ids(db)


def test_an_explicit_store_writes_no_yaml_file(
    ambient_db_store: Path, tmp_path: Path
) -> None:
    """Naming a store NEVER produces a file. It selects a database.

    This assertion is the inverse of the one it replaces, deliberately. I
    first wrote `test_an_explicit_store_still_writes_the_file`, which passed
    against a design where an explicit store kept the YAML behaviour — and
    that design was wrong: it split the registry across two homes, so a
    registration made with `store=` was invisible to a resolution made
    without it. The test did not catch that, because it asserted on the
    branch rather than on the outcome.

    A `…/tasks.yaml` store is a display LABEL (`_paths` builds it as
    `resolve_db_path(None).parent / "tasks.yaml"`; the YAML tier died in
    #512), so the registry inverts it to the sibling database. If this file
    ever appears again, either a file branch came back or a label reached
    `open_db` raw and SQLite created a phantom store at the label's path —
    both of which have already happened once each on this branch.
    """
    # Arrange
    from scitex_cards import _users

    named = tmp_path / "tasks.yaml"
    # Act
    _users.register_user(kind="agent", names=["file-probe"], store=named)
    # Assert
    assert not named.exists()


def test_an_explicit_store_reaches_its_sibling_database(
    ambient_db_store: Path, tmp_path: Path
) -> None:
    """...and the registration is retrievable from that database."""
    # Arrange
    from scitex_cards import _users

    named = tmp_path / "tasks.yaml"
    # Act
    user = _users.register_user(kind="agent", names=["file-probe"], store=named)
    # Assert
    assert user.id in _registry_ids(tmp_path / "cards.db")


def test_an_explicit_store_stays_out_of_the_database(
    ambient_db_store: Path, tmp_path: Path
) -> None:
    """...and does not leak onto the shared board while doing it."""
    # Arrange
    from scitex_cards import _users

    named = tmp_path / "tasks.yaml"
    # Act
    _users.register_user(kind="agent", names=["file-probe"], store=named)
    # Assert
    assert _registry_ids(ambient_db_store) == set()


def test_a_user_json_cannot_carry_is_refused(ambient_db_store: Path) -> None:
    """A NULL payload is an UNREADABLE ROW, so the write must not happen.

    `_db_sections` encodes registry rows with the encoder #893 replaced on
    the card path only; reaching it with a non-serialisable value would store
    ``record_json = NULL``, and every later read of the registry — by every
    agent — would then refuse.
    """
    # Arrange
    import datetime as dt

    from scitex_cards._db_users import save_users_rows

    row = {"id": "u-bad", "kind": "agent", "names": ["bad"], "at": dt.date.today()}
    # Act / Assert — pytest.raises IS the assertion here
    with pytest.raises(TypeError):
        # Assert
        save_users_rows([row])


def test_the_refusal_names_the_user_it_refused(ambient_db_store: Path) -> None:
    """The id is the actionable half: a caller needs to know WHICH write died."""
    # Arrange
    import datetime as dt

    from scitex_cards._db_users import save_users_rows

    row = {"id": "u-bad", "kind": "agent", "names": ["bad"], "at": dt.date.today()}
    message = ""
    # Act
    try:
        save_users_rows([row])
    except TypeError as exc:
        message = str(exc)
    # Assert
    assert "u-bad" in message


def test_a_refused_user_leaves_no_row(ambient_db_store: Path) -> None:
    # Arrange
    import contextlib
    import datetime as dt

    from scitex_cards._db_users import save_users_rows

    row = {"id": "u-bad", "kind": "agent", "names": ["bad"], "at": dt.date.today()}
    # Act
    with contextlib.suppress(TypeError):
        save_users_rows([row])
    # Assert
    assert _registry_ids(ambient_db_store) == set()


# EOF
