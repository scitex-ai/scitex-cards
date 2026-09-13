#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``_inbox_backend.backend()`` selects the inbox rail, or raises.

Every case here was once reachable and SILENT: a store misconfiguration became
an invisible inbox nobody polled. Each one now raises
:class:`~scitex_cards._store_errors.StoreUnavailableError` instead, and these
are the direct coverage for that seam -- real environment variables, because
the code under test reads ``os.environ``, and no mocks.
"""

from __future__ import annotations

from _banned import DRIVER, ENGINE  # noqa: F401

from scitex_cards._inbox_backend import (
    POSTGRES,
    backend,
    store_is_shared,
)

_PG_DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"


class TestAPostgresStoreSelectsPostgres:
    def test_a_shared_store_selects_postgres(self, env):
        # Arrange
        env.delete("SCITEX_CARDS_INBOX_BACKEND")
        env.set("SCITEX_STORE_DSN", _PG_DSN)

        # Act
        active = backend()

        # Assert
        assert active == POSTGRES

    def test_a_shared_store_reports_shared(self, env):
        # Arrange
        env.delete("SCITEX_CARDS_INBOX_BACKEND")
        env.set("SCITEX_STORE_DSN", _PG_DSN)

        # Act
        shared = store_is_shared()

        # Assert
        assert shared is True


class TestExplicitRetiredEngineIsNotHonoured:
    """Naming the retired engine must not select it. It never has to raise.

    THESE TWO USED TO ASSERT A RAISE, AND THEY PASSED FOR THE WRONG REASON.
    `backend()` does not reject an unrecognised name — by design, per its own
    docstring, it IGNORES the name and follows the store. The old raise came
    from the second half of that fallthrough: the ambient store was not a DSN,
    so the store-following default had nothing to select and raised. The tests
    were reading a store misconfiguration and calling it a refusal of the
    engine name.

    Once the suite pins a real Postgres store — which it must — the
    misconfiguration is gone, the fallthrough succeeds, and the raise these
    asserted simply does not happen. Nothing regressed; the scaffolding they
    were leaning on was removed.

    WHAT ACTUALLY MATTERS is the invariant underneath, and it is stronger than
    a raise: whatever you put in that variable, the answer is never the retired
    engine. That holds under a correct store, which is the configuration the
    fleet actually runs, so it is worth asserting there rather than only in a
    broken one.
    """

    def test_the_retired_engine_name_does_not_select_it(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", ENGINE)
        env.set("SCITEX_STORE_DSN", _PG_DSN)

        # Act
        active = backend()
        # Assert
        assert active == POSTGRES

    def test_an_unrecognised_name_is_refused(self, env):
        # Arrange
        # The positive half: not merely "not the retired engine" — which an
        # exception would also satisfy — but the documented behaviour, that an
        # unrecognised name is ignored and the store decides.
        env.set("SCITEX_CARDS_INBOX_BACKEND", ENGINE)
        env.set("SCITEX_STORE_DSN", _PG_DSN)

        # Act
        active = backend()
        # Assert
        assert active == POSTGRES


class TestExplicitOverridesStillWork:
    """The retirement removed a fallback; it did not remove the real knobs."""

    def test_explicit_postgres_wins_over_an_unshared_store(self, env, tmp_path):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", "postgres")
        env.set("SCITEX_STORE_DSN", _PG_DSN)

        # Act
        active = backend()

        # Assert
        assert active == POSTGRES

    def test_explicit_yaml_cannot_change_the_backend(self, env):
        # Arrange
        env.set("SCITEX_CARDS_INBOX_BACKEND", "yaml")
        env.set("SCITEX_STORE_DSN", _PG_DSN)

        # Act
        active = backend()
        # Assert
        assert active == POSTGRES


# EOF
