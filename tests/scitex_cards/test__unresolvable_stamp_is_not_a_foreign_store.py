#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""An unresolvable store stamp means "cannot tell", never "different store".

THE OUTAGE, 2026-07-28: the operator's board returned HTTP 500 on `/tasks` with
"REFUSING TO READ ... that database is stamped for a DIFFERENT store" — against
the very database the process was pointed at.

MEASURED ON THE HOST (the only place it reproduces):

    agent path exists on HOST: False
    host  path exists on HOST: True
    realpath host: /home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db
    stamped as:    /home/agent/.scitex/cards/cards.db

ONE bind-mounted file, two names. The stamp records the CONTAINER-side path. On
the host that path does not exist, so `_same_file`'s inode comparison cannot run,
it falls through to a realpath STRING compare, the two names never match, and the
guard concludes the store is foreign.

`_same_file`'s own docstring records the same class of false negative from
2026-07-20 — the inode check was added then, and the string fallback it kept is
reachable in exactly the cross-namespace case it was meant to solve.

Returning "different store" here asserts knowledge the process does not have. It
was pointed at this database explicitly; a name it cannot resolve is not evidence
that the database belongs to someone else.

NOT THE CURE. Store identity is a PATH, and a path is not identity when more than
one view can produce it (scitex-storage's formulation, and the day's most
load-bearing sentence). The repair is a uuid stamped in the store and compared
exactly — no namespace can re-spell it. Card:
scitex-cards-resolver-never-default-yaml-20260727.
"""

from __future__ import annotations

import sqlite3

import pytest

from scitex_cards import _dual_write


@pytest.fixture
def db(tmp_path):
    """A database file that exists, stamped by a helper below."""
    path = tmp_path / "cards.db"
    sqlite3.connect(path).close()
    return path


def _stamp(monkeypatch, value: str) -> None:
    """Force the stamp the guard will read, without a real schema."""
    monkeypatch.setattr(_dual_write, "_same_file", _dual_write._same_file, raising=True)
    import scitex_cards._db_freshness as freshness

    monkeypatch.setattr(freshness, "stamped_store_path", lambda _conn: value)


def test_a_stamp_from_another_namespace_does_not_refuse(monkeypatch, db):
    """The outage case: a path from a mount namespace this process cannot see.

    Ownership is UNDECIDABLE, and undecidable must not render as "foreign", or
    the store denies service to the process that was explicitly pointed at it.

    THE STAMP HERE IS DELIBERATELY NOT `/home/agent/...`. That is the real
    outage path, and the first version of this test used it — and PASSED
    VACUOUSLY inside the container, where `/home/agent` genuinely exists, so the
    unresolvable branch never ran. A test that depends on ambient filesystem
    layout measures the machine, not the code; it would have gone green here and
    red on the host, which is precisely the environment-coupling that let the
    original bug reach the operator. This path cannot exist anywhere.
    """
    # Arrange
    _stamp(monkeypatch, "/proc/self/no-such-mount-namespace/cards.db")

    # Act
    mirrors = _dual_write._db_mirrors_this_store(db, db)

    # Assert
    assert mirrors


def test_a_resolvable_but_genuinely_different_store_is_still_refused(
    monkeypatch, db, tmp_path
):
    """The guard must keep doing its job for the case it was built for.

    A stamp naming a DIFFERENT file that DOES exist here is real evidence of a
    foreign store, and loosening the namespace case must not loosen this one —
    otherwise the fix for a false negative manufactures a false positive.
    """
    # Arrange
    other = tmp_path / "someone-elses.db"
    sqlite3.connect(other).close()
    _stamp(monkeypatch, str(other))

    # Act
    mirrors = _dual_write._db_mirrors_this_store(db, db)

    # Assert
    assert not mirrors


def test_an_unstamped_database_stays_adoptable(monkeypatch, db):
    """Unchanged behaviour, pinned so the edit above did not alter it."""
    # Arrange
    _stamp(monkeypatch, "")

    # Act
    mirrors = _dual_write._db_mirrors_this_store(db, db)

    # Assert
    assert mirrors


# EOF
