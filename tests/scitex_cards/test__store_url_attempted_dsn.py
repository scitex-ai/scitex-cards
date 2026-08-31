#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A malformed DSN must not become a filename.

WHAT THIS SUITE IS DEFENDING, stated once so no future reader softens it into a
style preference: ``backend_of`` is total, so "I do not recognise this" once had
nowhere to live, and the answer for anything unrecognised was a FILENAME, which
``_db.connect`` then created with ``mkdir(parents=True)``. A wrong cards
database that answers queries is the failure this package keeps meeting, and it
arrived three times through three different spellings.

``backend_of`` is still total -- thirteen call sites branch on it -- but the
else-branch now answers ``BACKEND_UNSUPPORTED``, which is not the name of a
second engine; it is the symbol for "this target names no store I can open".
The refusal itself lives at the door, in ``reject_non_postgres_target``.

THE SUITE IS STILL DELIBERATELY TWO-SIDED, AND THE SECOND SIDE MOVED. Half of
it asserts that malformed DSNs are recognised as such; the other half is the
POSITIVE CONTROL, without which a guard that refuses everything passes the
first half completely -- which is exactly how a store-target guard shipped
broken before (the click decorator incident recorded on
cards-store-resolution-falls-back-silently-instead-of-failing-loud-20260809).

What the control asserts is what changed. It used to be "an ordinary path is
still ACCEPTED", because a path was a store. A path is not a store any more, so
that control would now be asserting the bug. Two things take its place, and
both are load-bearing:

  * an ordinary path is still not classified as an ATTEMPTED DSN -- the
    distinction survives the abolition, because the two produce different
    diagnostics and a lost operator reads the diagnostic; and
  * a well-formed DSN is still opened. That is the control that a guard
    refusing everything now fails.
"""

import contextlib
from functools import partial
from pathlib import Path

import pytest

from scitex_cards._store_url import (
    BACKEND_UNSUPPORTED,
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

#: Ordinary filesystem paths. None of them names a store any more, but each
#: MUST keep being told apart from a MALFORMED DSN: the two are refused with
#: different diagnostics, and the diagnostic is the only part an operator acts
#: on. A path misfiled as an attempted DSN sends them hunting for a typo in a
#: server address they never wrote.
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


class TestOrdinaryPathsAreStillDistinguishable:
    """HALF THE POSITIVE CONTROL, and the half that had to be restated.

    A path is no longer a store, so "an ordinary path still opens" is not a
    property to defend -- asserting it would pin the defect. What survives is
    the DISTINCTION: a path must not be misfiled as an attempted DSN, because
    the two carry different diagnostics and the diagnostic is the only part an
    operator acts on. The other half of the control -- that a well-formed DSN
    is still opened -- lives in :class:`TestNoFileIsManufactured` below, and is
    what a guard refusing everything now fails.
    """

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
    def test_a_path_classifies_as_unsupported(self, target):
        # Arrange
        subject = target
        # Act
        backend = backend_of(subject)
        # Assert
        assert backend == BACKEND_UNSUPPORTED


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

    def test_a_well_formed_dsn_is_still_opened(self):
        """POSITIVE CONTROL for the door: refusing everything would pass all
        three tests above.

        THIS CONTROL WAS INVERTED, not repaired. It used to open
        ``tmp_path / "nested" / "cards.db"`` and assert the file EXISTS
        afterwards -- i.e. it asserted that the door creates a database at an
        arbitrary filename, which is the precise behaviour that manufactured
        the three phantom stores this module is named after. Once a filename
        stopped naming a store, keeping that assertion would have meant
        pinning the defect as the requirement.

        The control still has to exist, because a door that refuses everything
        passes every refusal test above. So the accepted case is now the only
        target that IS a store: the throwaway schema the harness pinned.
        """
        # Arrange
        import os

        from scitex_cards._db import connect

        target = os.environ["SCITEX_CARDS_DB"]
        # Act
        conn = connect(target)
        backend = conn.backend
        conn.close()
        # Assert
        assert backend == "postgresql"


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

    THESE WERE SOURCE GREPS, AND TWO OF THE THREE NO LONGER NEED TO BE. Both
    read the text of a module for the literal ``reject_attempted_dsn(target)``
    -- a weak instrument, chosen when the strong version would have needed a
    live store per door. Two things changed that:

    * the door was renamed. ``reject_attempted_dsn`` is now COMPOSED INTO
      ``reject_non_postgres_target``, which runs it first and then refuses
      everything else that is not a DSN. A grep for the old call site fails on
      a wiring that is present and stricter than before, which is a false
      alarm, not a finding.
    * ``_db.connect`` no longer calls it at all. It delegates to
      ``_backend_connect.connect``, so the grep was asserting a call site the
      refactor legitimately moved -- and would keep failing however correct
      the code was.

    Since the refusal now happens BEFORE anything is opened, it no longer takes
    a live store to observe, so the two module-level greps become behavioural
    tests of the doors themselves. The one grep that survives is the one whose
    subject really is a call site rather than an outcome.
    """

    def test_db_connect_reaches_the_guard(self):
        """Behavioural, and it is the door callers actually use."""
        # Arrange
        target = ":55432"
        # Act
        # Assert -- opening is the act and the refusal is the observation.
        with pytest.raises(UnrecognisedStoreTarget):
            from scitex_cards._db import connect

            connect(target)

    def test_backend_connect_reaches_the_guard(self):
        # Arrange
        target = ":55432"
        # Act
        # Assert
        with pytest.raises(UnrecognisedStoreTarget):
            from scitex_cards._backend_connect import connect as backend_connect

            backend_connect(target)

    def test_the_composed_door_still_runs_the_attempted_dsn_diagnostic(self):
        """The composition is the part a rename could silently drop.

        A door that refused everything with ONE blanket message would pass both
        tests above while destroying the diagnostic: an operator who typed a
        server address and mistyped it would be told their target "does not
        name the store", and would go looking for a missing file. The two
        refusals must stay distinguishable, which is only true if
        ``reject_non_postgres_target`` still runs ``reject_attempted_dsn``
        first.
        """
        # Arrange
        from scitex_cards._store_url import reject_non_postgres_target

        def refusal(target: str) -> str:
            try:
                reject_non_postgres_target(target)
            except UnrecognisedStoreTarget as exc:
                return str(exc)
            return ""

        # Act
        mistyped_server = refusal(":55432")
        plain_path = refusal("/home/agent/.scitex/cards/cards.db")
        # Assert
        assert mistyped_server != plain_path


# EOF
