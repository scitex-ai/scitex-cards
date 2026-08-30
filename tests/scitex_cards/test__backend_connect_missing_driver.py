#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A missing PostgreSQL driver must say what to install.

THIS IS THE FAILURE A DEFAULT INSTALL ACTUALLY HITS. psycopg is declared in the
``all`` / ``dev`` / ``postgres`` extras and NOT in ``mcp`` -- and ``mcp`` is
what both deployment paths install, the host venv and the container recipe. So
on a fresh machine the driver is simply absent, and the first thing to touch
PostgreSQL is the thing that finds out.

Measured 2026-07-31: every other check passed in exactly that state. The wheel
contained ``_backend_connect``, ``connect()`` dispatched correctly, the schema
built on a real server, the guards enforced. The code was right and the
environment could not run it. The bare ``ModuleNotFoundError: No module named
'psycopg'`` named the symptom and not one word about the fix.

So the contract under test is not "it raises" -- it already did that. It is
that the message carries the INSTALL COMMAND, because that is what turns a
confusing outage into a five-minute repair.
"""

from _banned import DRIVER, ENGINE  # noqa: F401

import sys

import pytest

from scitex_cards._backend_connect import connect

PG_URL = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"


@pytest.fixture
def psycopg_hidden():
    """Make ``import psycopg`` fail, the way a default install does.

    A real absence, not a patched function: the module is removed from
    ``sys.modules`` and a finder that refuses it is put at the FRONT of
    ``sys.meta_path``, so the import machinery genuinely cannot resolve it.
    That exercises the same code path a machine without the driver takes.
    """

    class _RefuseP:
        def find_spec(self, name, path=None, target=None):
            if name == "psycopg" or name.startswith("psycopg."):
                raise ModuleNotFoundError(f"No module named {name!r}")
            return None

    saved = {k: v for k, v in sys.modules.items() if k.startswith("psycopg")}
    for k in list(saved):
        del sys.modules[k]
    finder = _RefuseP()
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)


def test_a_missing_driver_still_raises(psycopg_hidden):
    # Arrange
    target = PG_URL

    # Act
    try:
        connect(target)
        raised = False
    except ModuleNotFoundError:
        raised = True

    # Assert
    assert raised


def test_the_error_names_the_install_command(psycopg_hidden):
    """The whole point: the message must be actionable, not just accurate."""
    # Arrange
    target = PG_URL

    # Act
    try:
        connect(target)
        message = ""
    except ModuleNotFoundError as exc:
        message = str(exc)

    # Assert
    assert "pip install 'scitex-cards[all]'" in message


def test_the_error_explains_why_a_default_install_lacks_it(psycopg_hidden):
    # Arrange
    target = PG_URL

    # Act
    try:
        connect(target)
        message = ""
    except ModuleNotFoundError as exc:
        message = str(exc)

    # Assert
    assert "mcp" in message


def test_the_error_reports_the_target_it_was_given(psycopg_hidden):
    """So a reader knows WHICH store triggered it, not merely that one did."""
    # Arrange
    target = PG_URL

    # Act
    try:
        connect(target)
        message = ""
    except ModuleNotFoundError as exc:
        message = str(exc)

    # Assert
    assert PG_URL in message


def test_sqlite_still_opens_without_the_driver(psycopg_hidden, tmp_path):
    """The positive control: hiding psycopg must not break the the retired engine path,
    which is every deployment today and must not need a database driver."""
    # Arrange
    target = tmp_path / "cards.db"
    target.touch()

    # Act
    conn = connect(str(target), read_only=False)

    # Assert
    assert conn.backend == ENGINE
    conn.close()


# EOF
