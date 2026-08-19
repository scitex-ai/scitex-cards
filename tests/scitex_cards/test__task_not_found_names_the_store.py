#!/usr/bin/env python3
"""A "no such card" error must name THE STORE IT SEARCHED, not a local file.

Until 2026-08-19 all seven raise sites interpolated their own local
``tasks_path`` / ``resolved`` variable — the LOCAL sidecar path — while the
lookup that had just failed ran against the resolved store. Measured on the
deployed 0.48.0 against a PostgreSQL fleet::

    resolve_store().resolved  ->  postgresql://scitex_cards@127.0.0.1:55432/scitex_cards
    comment_task(bad_id)      ->  task id '...' not found in
                                  /home/agent/.scitex/cards/tasks.yaml

THE NAMED VALUE PLAYED NO PART IN THE SEARCH. ``_read_write_doc(path)`` ignores
its argument entirely — its body is ``_read_canonical_db_or_raise()``, which
takes none — so that path served the file lock and this one string. ``_paths``
already said so in prose: "interpolates the path into an error message only".

That is worse than a vague message because it is actionable in the WRONG
DIRECTION: it sent a peer hunting a second store that does not exist, and cost
them a conclusion they had to retract to another agent.

THE STRUCTURAL TEST IS THE POINT. The behavioural ones below prove the message
is right today; only the source scan stops a seventh copy of the old sentence
from being written tomorrow, which is how six copies of it survived.
"""

import os
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards._store import TaskNotFoundError, _task_not_found
from scitex_cards._store_target import ENV_DB

#: A DSN whose password must never reach a log, and whose host must.
DSN_WITH_SECRET = "postgresql://cards_user:hunter2@10.0.0.7:55432/scitex_cards"


@pytest.fixture
def store_env():
    """Set the real ``$SCITEX_CARDS_DB``, restoring it on teardown.

    A real environment variable rather than a patched one: ``store_label``
    reads it through ``os.environ`` at call time and that lookup is the thing
    under test. ``monkeypatch`` is forbidden package-wide (STX-NM002).

    Safe against the live board BY CONSTRUCTION rather than by care:
    ``_task_not_found`` only NAMES the store, it never opens one, so nothing
    here can reach a database whatever this variable is set to.
    """
    before = os.environ.get(ENV_DB)

    def _set(value):
        os.environ[ENV_DB] = value

    yield _set
    if before is None:
        os.environ.pop(ENV_DB, None)
    else:
        os.environ[ENV_DB] = before


def test_the_error_names_the_store_that_was_searched(store_env):
    """The resolved store target appears in the message."""
    # Arrange
    store_env("postgresql://scitex_cards@127.0.0.1:55432/scitex_cards")
    # Act
    message = str(_task_not_found("no-such-card"))
    # Assert
    assert "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards" in message


def test_the_error_does_not_name_the_local_sidecar(store_env):
    """The local ``tasks.yaml`` sidecar is never offered as the store."""
    # Arrange
    store_env("postgresql://scitex_cards@127.0.0.1:55432/scitex_cards")
    # Act
    message = str(_task_not_found("no-such-card"))
    # Assert
    assert "tasks.yaml" not in message


def test_the_error_does_not_leak_the_password(store_env):
    """A DSN password must not reach the log this message lands in."""
    # Arrange
    store_env(DSN_WITH_SECRET)
    # Act
    message = str(_task_not_found("no-such-card"))
    # Assert
    assert "hunter2" not in message


def test_the_error_still_names_the_host_after_stripping(store_env):
    """Stripping the secret must not strip the answer the message exists for."""
    # Arrange
    store_env(DSN_WITH_SECRET)
    # Act
    message = str(_task_not_found("no-such-card"))
    # Assert
    assert "10.0.0.7:55432" in message


def test_the_error_is_a_task_not_found_error():
    """The builder returns the exception type every caller already catches."""
    # Arrange / Act
    built = _task_not_found("no-such-card")
    # Assert
    assert isinstance(built, TaskNotFoundError)


@pytest.fixture
def store_source_lines():
    """Every ``(path, lineno, text)`` in the shipped package source.

    Reads the INSTALLED package rather than a hard-coded repo path, so the scan
    follows the code that actually ships. Enumerated in full: an exclusion or a
    ``head`` here would let the offending line hide in what was filtered out.
    """
    root = Path(scitex_cards.__file__).parent
    return [
        (path.relative_to(root), n, text)
        for path in sorted(root.rglob("*.py"))
        for n, text in enumerate(path.read_text().splitlines(), start=1)
    ]


def test_no_raise_site_interpolates_a_local_path(store_source_lines):
    """No source line raises this error naming a local path variable.

    The mechanical barrier. A behavioural test passes the moment the seven
    known sites are fixed; this one fails the moment an eighth is written the
    old way, which is the failure mode that produced the defect.
    """
    # Arrange / Act
    offenders = [
        f"{path}:{lineno}: {text.strip()}"
        for path, lineno, text in store_source_lines
        if "TaskNotFoundError(" in text
        and ("{tasks_path}" in text or "{resolved}" in text)
    ]
    # Assert
    assert offenders == []
