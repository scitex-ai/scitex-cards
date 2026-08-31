#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` must MEASURE writability, never assert it.

`_verify_postgres_store` reported the store "readable, writable" from a word
that was a hardcoded literal in an f-string, so it could never be false: "a
gate that cannot fail is not a gate ... the same as deleting it, except worse:
the config still lists it and everyone believes it is working" (constitution
§2).

It is not hypothetical. On 2026-07-28 every card CREATE refused for any agent
without `$SCITEX_CARDS_DB` while `health` reported that same store readable AND
writable — the check that should have caught the outage was the reason it stayed
invisible. Reported by scitex-ui.

These tests pin the claim to reality: if the store cannot be written, the check
must FAIL and say so.

WHAT THE PROBE MEASURES NOW, AND WHY THIS FILE WAS REWRITTEN RATHER THAN
RENAMED. The three questions are the same three questions; every one of them
means something different once the store is a server:

    exists    was ``stat()``          -> does this database carry the schema
    readable  was "the file parses"   -> does ``COUNT(*)`` return
    writable  was a permission bit    -> ``has_table_privilege(...,'INSERT')``
                                        AND ``NOT pg_is_in_recovery()``

So the old fixtures — a file chmod'd 0o444, a writable file inside a 0o555
directory, and the ``-wal`` siblings that made the DIRECTORY matter — do not
model a harder case; they model a filesystem that is no longer in the path at
all. They are replaced by the conditions that can actually make a write fail
here, exercised against a REAL server on this test's own throwaway schema.

THE ``@skipif(os.geteuid() == 0)`` MARKER IS GONE, and that is a gain rather
than a loss of coverage. It guarded five of the seven tests because root
bypasses permission BITS, so on a container that runs as root — which is most
of this fleet — the read-only arms silently did not run. Database privileges
are not bypassed by the OS user: ``REVOKE INSERT`` binds
``ywatanabe__scitex-agent-container`` exactly as it binds anyone. Every arm now
runs everywhere.

ONE ARM IS NOT COVERED, and it is named rather than quietly omitted:
``pg_is_in_recovery()``. Reaching it needs a STANDBY, which this suite cannot
create and must not borrow — every host's loopback 55432 is one, and pointing a
test at a real replica makes the suite's result depend on replication state.
The refusal branch it feeds is one ``if`` beside the INSERT branch that IS
covered here.
"""

from __future__ import annotations

import os
from typing import Iterator
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from scitex_cards._db import connect
from scitex_cards._health import _verify_postgres_store


def _dsn_as_role(dsn: str, role: str, password: str) -> str:
    """The same store, addressed as ``role``.

    Only the credentials change: the query string -- which carries the
    ``options=-csearch_path%3D...`` pin -- is passed through UNTOUCHED rather
    than re-encoded, because libpq does not read ``+`` as a space and a
    well-meaning round-trip through ``urlencode`` silently repoints the store
    at the wrong schema.
    """
    parts = urlsplit(dsn)
    netloc = f"{quote(role)}:{quote(password)}@{parts.hostname}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


@pytest.fixture
def writable_store() -> str:
    """The ordinary healthy case: this test's own schema-scoped store.

    The root ``conftest.py`` has already created the schema and installed the
    schema through the package's own doors, so this is a real, empty,
    schema-complete store — and it is dropped ``CASCADE`` when the test ends,
    which is what lets the two tests below mutate privileges freely.

    FAILS rather than skips when the pin is absent. A storage check that skips
    reads exactly like one that passed, which is the failure mode this whole
    module is about.
    """
    dsn = os.environ.get("SCITEX_CARDS_DB", "")
    if "search_path" not in dsn:
        pytest.fail(
            "the root conftest did not pin $SCITEX_CARDS_DB to a throwaway "
            f"PostgreSQL schema; it holds {dsn!r}.",
            pytrace=False,
        )
    return dsn


@pytest.fixture
def store_the_role_cannot_write(writable_store) -> "Iterator[str]":
    """A store that reads and parses fine but cannot be written.

    ``REVOKE INSERT`` from the CURRENT role, which is also the schema's owner.
    An owner's privileges are revocable like anyone else's — measured, not
    assumed — so this needs no second role and no grant fixture, and the whole
    schema is dropped afterwards regardless of how the test ends.

    EXCEPT FROM A SUPERUSER, WHICH CANNOT BE DENIED. ``REVOKE`` succeeds and
    changes nothing: ``has_table_privilege`` answers true for a superuser
    whatever the catalogue says, so the store stays writable and all three
    arms below assert against a healthy store. This module's own docstring
    celebrates deleting ``@skipif(os.geteuid() == 0)`` on the grounds that
    "database privileges are not bypassed by the OS user" — true, and it
    traded an OS-root bypass for a DATABASE-SUPERUSER one without noticing.

    IT IS NOT HYPOTHETICAL EITHER: CI's ``postgres:16`` service makes
    ``POSTGRES_USER`` the bootstrap superuser, so on CI these arms silently
    measured nothing while passing locally, where the fleet role is ordinary.
    Measured 2026-08-31 — three failures on CI, zero locally, same commit.

    So when the current role cannot be denied, a role that CAN is created for
    the test and dropped afterwards. Skipping was rejected deliberately: this
    module exists because "a check that cannot fail is not a check", and a
    storage arm that skips reads exactly like one that passed.
    """
    conn = connect(writable_store)
    role: "str | None" = None
    try:
        superuser = conn.execute(
            "SELECT rolsuper AS s FROM pg_roles WHERE rolname = current_user"
        ).fetchone()["s"]
        if not superuser:
            conn.execute("REVOKE INSERT ON tasks FROM current_user")
            conn.commit()
            target = writable_store
        else:
            schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
            role = f"cards_ro_{uuid4().hex[:12]}"
            password = uuid4().hex
            conn.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
            conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            conn.execute(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"'
            )
            conn.commit()
            target = _dsn_as_role(writable_store, role, password)
    finally:
        # CLOSED BEFORE THE YIELD, never after it. The schema is dropped
        # CASCADE at teardown and that statement BLOCKS on any live
        # transaction, so a handle held across the test turns a failure into
        # an indefinite hang that reads as a slow runner.
        conn.close()

    if role is None:
        yield target
        return

    try:
        yield target
    finally:
        # A ROLE IS NOT SCHEMA-SCOPED, so the CASCADE that removes everything
        # else leaves it behind; without this the cluster accumulates one
        # login role per run of this test.
        cleanup = connect(writable_store)
        try:
            cleanup.execute(f'DROP OWNED BY "{role}"')
            cleanup.execute(f'DROP ROLE IF EXISTS "{role}"')
            cleanup.commit()
        finally:
            cleanup.close()


@pytest.fixture
def store_without_the_schema(writable_store) -> "Iterator[str]":
    """A reachable server holding NO store — the "exists" question, restated.

    A server answering on the right address while carrying no cards table is
    the case a ``stat()`` could never have expressed, and it is the dangerous
    one: initialising it "to fix health" creates a SECOND store, which is how
    the board was destroyed on 2026-07-19.
    """
    conn = connect(writable_store)
    try:
        conn.execute("DROP TABLE tasks CASCADE")
        conn.commit()
    finally:
        conn.close()
    yield writable_store


def test_a_writable_store_passes_the_check(writable_store):
    # Arrange — a store this role can write.
    # Act
    result = _verify_postgres_store(writable_store)

    # Assert
    assert result["ok"] is True


def test_a_writable_store_is_described_as_writable(writable_store):
    # Arrange — a store this role can write.
    # Act
    result = _verify_postgres_store(writable_store)

    # Assert
    assert "writable" in result["detail"]


def test_a_store_the_role_cannot_write_fails_the_check(store_the_role_cannot_write):
    # Arrange — readable and queryable, but unwritable.
    # Act
    result = _verify_postgres_store(store_the_role_cannot_write)

    # Assert — the whole point: this must FAIL, not claim "writable".
    assert result["ok"] is False


def test_a_store_the_role_cannot_write_says_the_role_lacks_insert(
    store_the_role_cannot_write,
):
    # Arrange — readable and queryable, but unwritable.
    # Act
    result = _verify_postgres_store(store_the_role_cannot_write)

    # Assert — naming INSERT is what tells the reader which repair to make.
    assert "INSERT" in result["detail"]


def test_a_store_the_role_cannot_write_is_still_reported_readable(
    store_the_role_cannot_write,
):
    # Arrange — the two questions must not collapse into one. A store that
    # reads but cannot be written is a DIFFERENT incident from one that is
    # unreachable, and it takes a different repair.
    # Act
    result = _verify_postgres_store(store_the_role_cannot_write)

    # Assert
    assert "readable" in result["detail"]


def test_a_store_the_role_cannot_write_hints_at_the_grant(
    store_the_role_cannot_write,
):
    # Arrange — an error that only states what broke is half-written.
    # Act
    result = _verify_postgres_store(store_the_role_cannot_write)

    # Assert
    assert "GRANT" in (result["hint"] or "")


def test_a_server_holding_no_store_fails_the_check(store_without_the_schema):
    # Arrange — reachable, but it carries no cards.
    # Act
    result = _verify_postgres_store(store_without_the_schema)

    # Assert
    assert result["ok"] is False


def test_a_server_holding_no_store_says_which_table_is_missing(
    store_without_the_schema,
):
    # Arrange — reachable, but it carries no cards.
    # Act
    result = _verify_postgres_store(store_without_the_schema)

    # Assert
    assert "tasks" in result["detail"]


def test_a_server_holding_no_store_warns_against_initialising_it(
    store_without_the_schema,
):
    # Arrange — THE DANGEROUS REPAIR. "health says no tasks table" invites
    # creating one, and a fresh empty target that gets initialised becomes a
    # SECOND store rather than a fixed one.
    # Act
    result = _verify_postgres_store(store_without_the_schema)

    # Assert
    assert "second store" in (result["hint"] or "")


def test_a_target_that_is_not_a_store_fails_the_check(tmp_path):
    # Arrange — a filename names no store, and the door refuses it before the
    # filesystem is touched. The check must report that, not raise through.
    target = str(tmp_path / "cards.db")

    # Act
    result = _verify_postgres_store(target)

    # Assert
    assert result["ok"] is False


def test_a_target_that_is_not_a_store_creates_no_file(tmp_path):
    # Arrange — a health check that MANUFACTURES the store it is inspecting is
    # the 2026-08-12 artifact all over again, reached from the doctor's side.
    target = tmp_path / "cards.db"

    # Act
    _verify_postgres_store(str(target))

    # Assert
    assert target.exists() is False


# EOF
