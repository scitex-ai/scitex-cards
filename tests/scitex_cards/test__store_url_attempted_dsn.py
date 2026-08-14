#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A malformed DSN must not become a filename.

WHAT THIS SUITE IS DEFENDING, stated once so no future reader softens it into a
style preference: ``backend_of`` is total, so "I do not recognise this" has
never had anywhere to live, and the answer for anything unrecognised is SQLITE
-- which means a FILENAME, which ``_db.connect`` then creates with
``mkdir(parents=True)``. A wrong cards database that answers queries is the
failure this package keeps meeting, and it has now arrived three times through
three different spellings.

THE SUITE IS DELIBERATELY TWO-SIDED. Half of it asserts that malformed DSNs are
refused; the other half asserts that ordinary paths are STILL ACCEPTED. A guard
that refuses everything satisfies the first half completely, and that is exactly
how a store-target guard shipped broken before (the click decorator incident
recorded on
cards-store-resolution-falls-back-silently-instead-of-failing-loud-20260809).
The acceptance tests are the positive control and are not optional.
"""

import contextlib
from functools import partial
from pathlib import Path

import pytest

from scitex_cards._store_url import (
    BACKEND_SQLITE,
    UnrecognisedStoreTarget,
    backend_of,
    is_attempted_dsn,
    reject_attempted_dsn,
)

#: Every spelling that has actually reached this package and been mistaken for
#: a path. The first two are recorded incidents; the third is the mangled form
#: found on disk in this repository's own root on 2026-08-12.
MALFORMED = [
    ":55432",
    "127.0.0.1:55432",
    "postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards",
    "postgres:/host/db",
    "postgresq://scitex_cards@127.0.0.1:55432/scitex_cards",
    "mysql://scitex_cards@127.0.0.1:55432/scitex_cards",
]

#: Targets that MUST keep resolving to SQLite. Every store in service today is
#: an absolute path, so a false positive here is an outage.
REAL_PATHS = [
    "/home/agent/.scitex/cards/cards.db",
    "/srv/data/a=b/cards.db",
    "~/.scitex/cards/cards.db",
    "./cards.db",
    "../cards.db",
    "/tmp/weird:name/cards.db",
    "cards.db",
]

#: Well-formed servers -- the targets the migration is moving TO. The guard
#: firing on one of these would be an outage dressed as a safety feature.
VALID_DSNS = [
    "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards",
    "postgres://scitex_cards@127.0.0.1:55432/scitex_cards",
    "host=127.0.0.1 port=55432 dbname=scitex_cards user=scitex_cards",
]

SRC = Path(__file__).resolve().parents[2] / "src" / "scitex_cards"


@pytest.fixture
def refusal_message() -> str:
    """The text a lost operator actually reads."""
    try:
        reject_attempted_dsn(":55432")
    except UnrecognisedStoreTarget as exc:
        return str(exc)
    return ""


class TestMalformedTargetsAreRecognised:
    """The third answer exists and covers every spelling seen so far."""

    @pytest.mark.parametrize("target", MALFORMED)
    def test_a_malformed_dsn_is_recognised_as_one(self, target):
        # Arrange
        subject = target
        # Act
        verdict = is_attempted_dsn(subject)
        # Assert
        assert verdict is True

    @pytest.mark.parametrize("target", MALFORMED)
    def test_a_malformed_dsn_is_refused_at_the_door(self, target):
        # Arrange
        act = partial(reject_attempted_dsn, target)
        # Act
        expected = pytest.raises(UnrecognisedStoreTarget)
        # Assert
        with expected:
            act()


class TestOrdinaryPathsStillWork:
    """THE POSITIVE CONTROL. A guard that refuses everything passes the suite
    above and destroys every deployment in existence."""

    @pytest.mark.parametrize("target", REAL_PATHS)
    def test_a_path_is_not_an_attempted_dsn(self, target):
        # Arrange
        subject = target
        # Act
        verdict = is_attempted_dsn(subject)
        # Assert
        assert verdict is False

    @pytest.mark.parametrize("target", REAL_PATHS)
    def test_a_path_passes_the_door_untouched(self, target):
        # Arrange
        subject = target
        # Act
        result = reject_attempted_dsn(subject)
        # Assert
        assert result is None

    @pytest.mark.parametrize("target", REAL_PATHS)
    def test_a_path_still_classifies_as_sqlite(self, target):
        # Arrange
        subject = target
        # Act
        backend = backend_of(subject)
        # Assert
        assert backend == BACKEND_SQLITE


class TestWellFormedPostgresIsUntouched:
    """The guard must not fire on the targets the migration is moving TO."""

    @pytest.mark.parametrize("target", VALID_DSNS)
    def test_a_valid_dsn_is_not_flagged(self, target):
        # Arrange
        subject = target
        # Act
        verdict = is_attempted_dsn(subject)
        # Assert
        assert verdict is False

    @pytest.mark.parametrize("target", VALID_DSNS)
    def test_a_valid_dsn_passes_the_door(self, target):
        # Arrange
        subject = target
        # Act
        result = reject_attempted_dsn(subject)
        # Assert
        assert result is None


class TestTheRefusalSaysWhatToDo:
    """An error that names only the symptom costs the reader the diagnosis."""

    def test_the_message_names_the_offending_target(self, refusal_message):
        # Arrange
        needle = ":55432"
        # Act
        found = needle in refusal_message
        # Assert
        assert found is True

    def test_the_message_shows_an_accepted_form(self, refusal_message):
        # Arrange
        needle = "postgresql://"
        # Act
        found = needle in refusal_message
        # Assert
        assert found is True

    def test_the_message_never_teaches_port_5432(self, refusal_message):
        """Operator ruling: port 5432 is NEVER used for scitex, and every
        reference is a defect. An error message is the worst place for one --
        it teaches the wrong port to whoever is already lost.
        """
        # Arrange
        forbidden = ":5432/"
        # Act
        found = forbidden in refusal_message
        # Assert
        assert found is False


class TestNoFileIsManufactured:
    """The point of the whole change, asserted against the FILESYSTEM rather
    than against a predicate. Every other test here could pass while the tree
    still gets built."""

    def test_a_mangled_dsn_creates_no_directory_tree(self, tmp_path):
        """THE EXACT REPRODUCTION, and the shape matters. The production string
        is RELATIVE: ``Path("postgresql://h/d")`` collapses the double slash to
        ``postgresql:/h/d``, which ``mkdir(parents=True)`` then builds under
        whatever the working directory happens to be. That is why the artifact
        found on 2026-08-12 was sitting in the repository root rather than
        anywhere a store belongs.

        Written first with an ABSOLUTE path under tmp_path, where it failed --
        the anchor rule waved it through. The predicate grew ``_MANGLED_SEGMENT``
        for the absolutised case, and this test moved to the real shape.
        """
        # Arrange
        from scitex_cards._db import connect

        target = "postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards"
        # Act
        with contextlib.chdir(tmp_path), contextlib.suppress(UnrecognisedStoreTarget):
            connect(target)
        # Assert
        assert not (tmp_path / "postgresql:").exists()

    def test_a_mangled_dsn_raises_rather_than_returning_a_connection(self, tmp_path):
        # Arrange
        from scitex_cards._db import connect

        target = "postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards"
        # Act
        expected = pytest.raises(UnrecognisedStoreTarget)
        # Assert
        with expected, contextlib.chdir(tmp_path):
            connect(target)

    def test_an_absolutised_mangled_dsn_is_also_refused(self, tmp_path):
        """A caller that resolves the target before opening must not thereby
        launder it into an acceptable path."""
        # Arrange
        from scitex_cards._db import connect

        target = str(tmp_path / "postgresql:") + "/scitex_cards@127.0.0.1:55432/db"
        # Act
        with contextlib.suppress(UnrecognisedStoreTarget):
            connect(target)
        # Assert
        assert not (tmp_path / "postgresql:").exists()

    def test_a_bare_port_creates_nothing_in_the_working_directory(self, tmp_path):
        # Arrange
        from scitex_cards._db import connect

        before = sorted(p.name for p in tmp_path.iterdir())
        # Act
        with contextlib.suppress(UnrecognisedStoreTarget):
            connect(":55432")
        # Assert
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_a_real_path_is_still_created_and_usable(self, tmp_path):
        """POSITIVE CONTROL for the door: refusing everything would pass all
        three tests above."""
        # Arrange
        from scitex_cards._db import connect

        target = tmp_path / "nested" / "cards.db"
        # Act
        connect(target).close()
        # Assert
        assert target.exists()


class TestNonStringsAreNotGuessedAt:
    """A Path object is a path by construction; it cannot be a malformed DSN."""

    def test_a_path_object_is_not_an_attempted_dsn(self):
        # Arrange
        subject = Path("/home/agent/.scitex/cards/cards.db")
        # Act
        verdict = is_attempted_dsn(subject)
        # Assert
        assert verdict is False

    def test_none_is_not_an_attempted_dsn(self):
        # Arrange
        subject = None
        # Act
        verdict = is_attempted_dsn(subject)
        # Assert
        assert verdict is False


class TestTheGuardIsActuallyWiredIn:
    """Defining a guard and CALLING it are different facts, and only the second
    one protects anybody. A predicate with perfect unit tests still lets the
    tree get built if nothing invokes it at the door.

    These two tests read the source rather than the behaviour, which is a weak
    instrument and is chosen deliberately: the strong version would need a live
    store per door. They cost nothing and they fail loudly the day someone
    deletes a call site while keeping the function.
    """

    def test_db_connect_calls_the_guard(self):
        # Arrange
        source = SRC / "_db.py"
        # Act
        text = source.read_text(encoding="utf-8")
        # Assert
        assert "reject_attempted_dsn(target)" in text

    def test_backend_connect_calls_the_guard(self):
        # Arrange
        source = SRC / "_backend_connect.py"
        # Act
        text = source.read_text(encoding="utf-8")
        # Assert
        assert "reject_attempted_dsn(target)" in text


# EOF
