#!/usr/bin/env python3
"""Which database the DM tables resolve to, for each shape of ``store``.

``resolve_dm_db`` had NO test at all until 2026-08-19, and the defect that
bought was the one the abolition card was opened for: its middle tier does
path arithmetic on ``store``, so a PostgreSQL DSN threaded through ``store=``
came back as a plausible-looking FILE path. Reproduced before this module
existed::

    "/home/agent/.scitex/cards/tasks.yaml"
        --  /home/agent/.scitex/cards/cards.db          (correct)
    "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"
        --  postgresql:/scitex_cards@127.0.0.1:55432/cards.db

The second is not a near-miss, it is a new, empty database: the caller opens
it, the schema is created, and it answers queries with an empty board. The
regrown file found on disk that day held 15 tables and 3 rows, all of them
``schema_meta`` -- created and initialised, never written to.
"""

import pytest

from scitex_cards._db import DEFAULT_DB_FILENAME
from scitex_cards._dm.ids import resolve_dm_db
from scitex_cards._store_url import UnrecognisedStoreTarget

DSN = "postgresql://scitex_cards@127.0.0.1:55432/scitex_cards"


class TestResolveDmDbStoreTier:
    def test_a_path_store_still_puts_the_db_beside_it(self, tmp_path):
        """The tier's REASON, pinned: a tmp store must not reach the fleet DB.

        This is why the middle tier cannot simply be deleted. Every DM caller
        threads ``store=`` through, and falling back to the ambient chain would
        send a test's DMs into the live fleet database.
        """
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        got = resolve_dm_db(store=store)
        # Assert
        assert got == tmp_path / DEFAULT_DB_FILENAME

    def test_a_dsn_store_comes_back_as_the_same_dsn(self):
        """The regression. Against the old tier this returned a path."""
        # Arrange
        store = DSN
        # Act
        got = resolve_dm_db(store=store)
        # Assert
        assert got == DSN

    def test_a_dsn_store_does_not_name_a_cards_file(self):
        """The damage, stated separately from the fix.

        Worth its own assertion because equality above could be satisfied by a
        future tier that normalises the URL; what must never happen again is a
        FILENAME coming out of a server target.
        """
        # Arrange
        store = DSN
        # Act
        got = resolve_dm_db(store=store)
        # Assert
        assert DEFAULT_DB_FILENAME not in str(got)

    def test_a_libpq_keyword_dsn_store_comes_back_verbatim(self):
        """The keyword spelling is a SERVER too, not a filename.

        ``is_postgres_url`` covers it via ``_LIBPQ_KEYWORDS``, so ``backend_of``
        answers "postgresql" and this tier hands it back. Pinned separately
        because it is the one accepted form that carries no "://" -- read the
        tier by eye and it looks like the branch would miss it, and missing it
        would recreate the 2026-07-31 incident exactly: ``Path(...).parent`` of
        a keyword DSN is ``.``, so the DM tables would land in a RELATIVE
        ``cards.db`` in whatever directory the caller happened to be in.
        """
        # Arrange
        store = "host=127.0.0.1 port=55432 dbname=scitex_cards user=scitex_cards"
        # Act
        got = resolve_dm_db(store=store)
        # Assert
        assert got == store

    def test_the_mangled_dsn_that_path_leaves_behind_is_refused(self):
        """A target TRYING to name a server gets no silent fallback.

        This exact string is what the old tier RETURNED, so it is also what a
        caller would thread back in as ``store=`` on the next hop. One slash is
        missing: treated as a path it builds the phantom tree found untracked
        in the source repo on 2026-08-12, ten days after it was created.
        """
        # Arrange
        store = "postgresql:/scitex_cards@127.0.0.1:55432/scitex_cards"
        # Act
        # Assert
        with pytest.raises(UnrecognisedStoreTarget):
            resolve_dm_db(store=store)

    def test_an_explicit_db_still_outranks_the_store(self, tmp_path):
        # Arrange
        explicit = tmp_path / "elsewhere.db"
        # Act
        got = resolve_dm_db(explicit, store=DSN)
        # Assert
        assert got == explicit
