#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A store target written into a message names the store and never its secret.

MEASURED 2026-09-05: the read-side TOLERATED warning rendered the store as a
repr of the resolved DSN. hub's board mount, whose DSN carried its password
inline, printed that password to docker logs on its first page load, once per
legacy ``pending`` row, six minutes after the secret had been delivered through
a 0600 secrets path. The label had stripped the query string "because DSNs
carry credentials there"; the password is in the userinfo, before the ``@``.

These tests pin the one rendering (``describe_store_target``) and then check
the MESSAGES, not the helper: each site that used to interpolate the raw target
is driven with a password-bearing DSN and its output searched for the secret.
The password used is a sentinel no real store would carry, so a match is the
leak and nothing else. No monkeypatching: the env is set by a yield fixture
and every other input is the real function under the real input.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from scitex_cards._store_url import (
    UnrecognisedStoreTarget,
    describe_store_target,
    reject_attempted_dsn,
    reject_non_postgres_target,
)

SECRET = "hunter2sentinel9f3a"
INLINE_SECRET_DSN = f"postgresql://scitex_cards_ro:{SECRET}@scitex-primary:55432/scitex"
#: A server that refuses at once, reached with an inline secret: the open
#: fails and the failure MESSAGE is the thing under test.
REFUSING_DSN = f"postgresql://u:{SECRET}@127.0.0.1:1/db?connect_timeout=1"


# --------------------------------------------------------------------------- #
# The one rendering                                                            #
# --------------------------------------------------------------------------- #
def test_a_url_keeps_user_host_port_and_database():
    # Arrange
    dsn = INLINE_SECRET_DSN
    # Act
    shown = describe_store_target(dsn)
    # Assert
    assert shown == "postgresql://scitex_cards_ro@scitex-primary:55432/scitex"


def test_a_url_drops_its_query_string_too():
    # Arrange
    dsn = f"postgresql://u:{SECRET}@h:5/db?options=-csearch_path%3Dx&sslmode=require"
    # Act
    shown = describe_store_target(dsn)
    # Assert
    assert shown == "postgresql://u@h:5/db"


def test_a_password_containing_an_at_sign_is_still_dropped():
    # Arrange
    dsn = f"postgresql://u:{SECRET}%40more@h/db"
    # Act
    shown = describe_store_target(dsn)
    # Assert
    assert SECRET not in shown


def test_a_url_without_credentials_is_unchanged():
    # Arrange
    dsn = "postgres://scitex-primary:55433/scitex"
    # Act
    shown = describe_store_target(dsn)
    # Assert
    assert shown == dsn


def test_a_conninfo_keeps_every_keyword_but_password():
    # Arrange
    dsn = f"host=h port=5 dbname=db user=u password={SECRET} options=-csearch_path=x"
    # Act
    shown = describe_store_target(dsn)
    # Assert
    assert shown == "host=h port=5 dbname=db user=u options=-csearch_path=x"


def test_a_conninfo_quoted_password_is_dropped_whole():
    # Arrange
    dsn = f"host=h password='{SECRET} with space' user=u"
    # Act
    shown = describe_store_target(dsn)
    # Assert
    assert shown == "host=h user=u"


def test_a_non_dsn_target_is_returned_as_it_was():
    # Arrange
    targets = ["/tmp/cards.db", "${SCITEX_CARDS_DB}", "CHANGEME", ""]
    # Act
    shown = [describe_store_target(t) for t in targets]
    # Assert
    assert shown == targets


# --------------------------------------------------------------------------- #
# The messages                                                                 #
# --------------------------------------------------------------------------- #
@pytest.fixture
def malformed_dsn_refusal() -> str:
    """The refusal for a DSN that went through Path() - the mangled form."""
    try:
        reject_attempted_dsn(f"postgresql:/u:{SECRET}@h/db")
    except UnrecognisedStoreTarget as exc:
        return str(exc)
    pytest.fail("a mangled DSN was not refused")


def test_the_malformed_dsn_refusal_does_not_echo_the_password(malformed_dsn_refusal):
    # Arrange
    message = malformed_dsn_refusal
    # Act
    leaked = SECRET in message
    # Assert
    assert not leaked, message


def test_the_malformed_dsn_refusal_still_names_the_target(malformed_dsn_refusal):
    # Arrange
    message = malformed_dsn_refusal
    # Act: the mangled shape survives (one slash, the user, the host), the secret does not
    named = "'postgresql:/u@h/db'" in message
    # Assert
    assert named, message


@pytest.fixture
def non_store_refusal() -> str:
    try:
        reject_non_postgres_target("/tmp/pytest-of-nobody/store0/cards.db")
    except UnrecognisedStoreTarget as exc:
        return str(exc)
    pytest.fail("a filesystem path was not refused")


def test_the_non_store_refusal_keeps_a_path_diagnosable(non_store_refusal):
    # Arrange
    message = non_store_refusal
    # Act
    named = "/tmp/pytest-of-nobody/store0/cards.db" in message
    # Assert
    assert named, message


def test_the_health_open_failure_does_not_echo_the_password():
    # Arrange
    from scitex_cards._health_store import _verify_postgres_store

    # Act
    report = _verify_postgres_store(REFUSING_DSN)
    # Assert
    assert SECRET not in report["detail"] + str(report.get("hint"))


def test_the_health_identity_failure_does_not_echo_the_password():
    # Arrange
    from scitex_cards._health_store_identity import _identity_on_postgres

    # Act
    report = _identity_on_postgres(REFUSING_DSN)
    # Assert
    assert SECRET not in report["detail"] + str(report.get("hint"))


@pytest.fixture
def store_env_with_inline_secret() -> Iterator[str]:
    """$SCITEX_CARDS_DB pointing at a DSN that carries its password inline,
    restored on teardown. The label under test resolves from this variable."""
    from scitex_cards._db import ENV_DB

    previous = os.environ.get(ENV_DB)
    os.environ[ENV_DB] = INLINE_SECRET_DSN
    try:
        yield INLINE_SECRET_DSN
    finally:
        if previous is None:
            os.environ.pop(ENV_DB, None)
        else:
            os.environ[ENV_DB] = previous


def test_the_tolerated_read_label_does_not_echo_the_password(store_env_with_inline_secret):
    # Arrange
    from scitex_cards._model import _canonical_source_label

    # Act
    label = _canonical_source_label()
    # Assert
    assert SECRET not in label, label


def test_the_tolerated_read_label_still_names_the_store(store_env_with_inline_secret):
    # Arrange
    from scitex_cards._model import _canonical_source_label

    # Act
    label = _canonical_source_label()
    # Assert
    assert label == "<postgres:postgresql://scitex_cards_ro@scitex-primary:55432/scitex>"


def test_the_cli_store_label_does_not_echo_the_password(store_env_with_inline_secret):
    # Arrange
    from scitex_cards._store_target import store_label

    # Act
    label = store_label(None)
    # Assert
    assert label == "postgresql://scitex_cards_ro@scitex-primary:55432/scitex"


# --------------------------------------------------------------------------- #
# The source-level guard: no site interpolates a raw target again              #
# --------------------------------------------------------------------------- #
def test_no_store_module_interpolates_a_raw_target_into_text():
    """A grep over the modules that render store targets. A new f-string that
    writes ``{target!r}`` or ``{target}`` without the helper is the regression
    this catches before a consumer's log does."""
    # Arrange
    import re
    from pathlib import Path

    import scitex_cards

    root = Path(scitex_cards.__file__).parent
    watched = [
        "_backend_connect.py", "_store_url.py", "_store_pin.py",
        "_store_canonical_read.py", "_health_store.py", "_health_store_identity.py",
        "_health_backend_mode.py", "_health_write_target.py", "_model.py",
        "_cli/_admin.py", "_cli/_min_client_version.py",
    ]
    raw = re.compile(r"\{target(!r)?\}")
    # Act
    offenders = [
        f"{name}:{lineno}"
        for name in watched
        for lineno, line in enumerate((root / name).read_text().splitlines(), 1)
        if raw.search(line) and "describe_store_target" not in line and not line.lstrip().startswith("#")
    ]
    # Assert
    assert offenders == [], offenders


# EOF
