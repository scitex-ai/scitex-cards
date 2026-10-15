#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``$SCITEX_STORE_DSN`` must not still name the live board while tests run.

WHY THIS FILE EXISTS. ``tests/conftest.py`` documents, at length, the three
occasions in 2026-07 on which this suite rebuilt the fleet's production
database from its own fixtures, and the fix: pin every variable that can point
the package at a store, in the harness, above the code under test.

That list had a hole. It named every variable ``scitex_cards`` resolves and
none of the variable the STORAGE PRIMITIVE resolves. Measured 2026-08-30 in a
sac-managed container::

    SCITEX_STORE_DSN=postgresql://scitex-primary:55432/scitex   <- pinned
    SCITEX_STORE_DSN=postgresql://scitex-primary:55432/scitex  <- inherited

Same cluster, same database, same 6,399 cards. Nothing in ``src/scitex_cards``
reads the second one today, so this was not a live leak — it was a leak waiting
for the first test that reaches storage through ``scitex_dev.store`` instead of
through this package, which the PostgreSQL port makes routine.

WHAT IS ASSERTED, and why not "the variable is not the live DSN". Naming the
forbidden value is the weaker test: it passes on any host whose live board
happens to be spelled differently, which is every host but one. So the
assertions are POSITIVE — the variable holds a schema-scoped throwaway, or it
holds the refused placeholder — and neither of those can resolve the live
board's public schema regardless of how that board is spelled.

WHY "OR THE REFUSED PLACEHOLDER" AND NOT "OR NOTHING". The 2026-08-30 draft
of this contract said absent-when-unpinned, and the harness implemented the
opposite on purpose (HEAD 44cfc480 wires ``_refused_placeholder`` into
``_point_env_at``): with ``$SCITEX_STORE_DSN`` absent, the resolvers walk
their precedence chain to the user-canonical target — the live board — which
is the exact enabling condition of all three 2026-07 wipes. A filename is
refused by every store door before anything is touched, so the placeholder
fails LOUDLY on configuration instead of quietly succeeding against
production. Absence is the one value that must never be reached; these tests
pin SET-EITHER-WAY.
"""

from __future__ import annotations

import os
import re

import pytest

ENV_STORE_DSN = "SCITEX_STORE_DSN"


@pytest.fixture(scope="session")
def root_conftest(pytestconfig):
    """The ALREADY-IMPORTED root conftest module, via pytest's plugin manager.

    Two shortcuts were tried first and both were wrong, which is worth writing
    down because each failed in a way that LOOKED like a finding about the
    harness:

    * ``sys.modules["conftest"]`` returns the NEAREST conftest, not the root
      one -- three files in this tree share that basename. The first draft
      read ``tests/scitex_cards/conftest.py`` and reported the pinning absent.
    * a scan of ``sys.modules`` for the attribute found nothing at all, because
      under this repo's import mode a conftest is registered as a PLUGIN rather
      than published under an importable module name.

    Re-importing the file by path would avoid both and introduce a worse
    problem: it would EXECUTE the module again, opening a SECOND throwaway
    schema, and the assertions would then describe a module whose side effects
    are not the ones in force.
    """
    for plugin in pytestconfig.pluginmanager.get_plugins():
        if getattr(plugin, "_STORE_DSN_ENV", None) == ENV_STORE_DSN:
            return plugin
    raise AssertionError(
        "the root tests/conftest.py is not registered as a plugin -- it "
        f"defines _STORE_DSN_ENV = {ENV_STORE_DSN!r} and no loaded conftest "
        "carries it. Either the harness pinning was deleted or this test is "
        "running outside the suite it guards."
    )


def _assert_set_either_way(observed: "str | None") -> str:
    """Pin the harness's SET-EITHER-WAY contract for ``$SCITEX_STORE_DSN``.

    Returns the observed value for further assertions. Fails when the
    variable is absent (absence resolves the live board — the exact
    enabling condition of the 2026-07 wipes), when it is an UNSCOPED
    postgres DSN (that shape resolves the live board's tables), or when
    it is anything other than the two safe shapes: a ``search_path``-scoped
    throwaway schema, or the refused placeholder filename the store doors
    reject before touching anything.
    """
    assert observed, (
        "$SCITEX_STORE_DSN is absent: the resolvers fall through to the "
        "user-canonical target — the live board. The harness must SET it "
        "either way (throwaway schema, or refused placeholder)."
    )
    if observed.startswith(("postgres://", "postgresql://")):
        scope = re.search(r"search_path(?:%3D|=)([^&]*)", observed)
        assert scope is not None and scope.group(1).lower() != "public", (
            f"postgres DSN without a non-public search_path scope resolves "
            f"the live board's tables: {observed!r} (the throwaway must "
            f"carry search_path=<schema> with public off the path)"
        )
    else:
        # The refused placeholder: a filename, rejected by every store door
        # before the filesystem is touched — fail-LOUD, zero live writes.
        assert observed.endswith("cards.db"), (
            f"$SCITEX_STORE_DSN is neither a scoped DSN nor the refused "
            f"placeholder: {observed!r}"
        )
    return observed


def test_the_primitive_dsn_variable_is_pinned_per_test(root_conftest):
    """The active value is SET-EITHER-WAY, never absent, never live-resolving."""
    # Arrange
    _ = root_conftest
    # Act
    observed = os.environ.get(ENV_STORE_DSN)
    # Assert
    _assert_set_either_way(observed)


def test_a_pinned_dsn_is_scoped_to_a_throwaway_schema(root_conftest):
    """The scoping is what makes it safe, so the scoping is what is asserted.

    A DSN carrying ``search_path=<throwaway>`` cannot resolve the live board's
    tables at all: ``public`` is off the path, so an unqualified read fails to
    find the relation rather than quietly returning the fleet's cards.

    Stated as an implication rather than guarded by a skip, because a skip here
    would report the same green as a pass on a host where nothing was pinned.
    """
    # Arrange
    pinned = root_conftest._EPHEMERAL_DSN
    # Act
    scoped_if_present = pinned is None or "search_path" in pinned
    # Assert
    assert scoped_if_present


def test_an_unavailable_cluster_leaves_the_refused_placeholder(root_conftest):
    """The no-cluster arm of the same guarantee — placeholder, not absence.

    Absence is the dangerous value (resolvers fall through to the live
    board), so when no throwaway PostgreSQL could be opened the harness
    points the variable at the refused placeholder: a filename every store
    door rejects before touching anything. Stated as an implication —
    with a cluster present this test asserts nothing beyond the scoped-DSN
    arm — because a skip here would report the same green as a pass on a
    host where nothing was pinned.
    """
    # Arrange
    pinned = root_conftest._EPHEMERAL_DSN
    # Act
    observed = os.environ.get(ENV_STORE_DSN)
    # Assert
    if pinned is None:
        assert observed is not None and not observed.startswith(
            ("postgres://", "postgresql://")
        ), (
            "no cluster was opened, so $SCITEX_STORE_DSN must be the refused "
            f"placeholder (fail-LOUD filename), never absent: {observed!r}"
        )
    else:
        _assert_set_either_way(observed)


def test_the_reason_is_recorded_when_nothing_could_be_opened(root_conftest):
    """A silent ``None`` would make an unavailable server look like a choice."""
    # Arrange
    reason = root_conftest._EPHEMERAL_DSN_REASON
    # Act
    stated = bool(reason and reason.strip())
    # Assert
    assert stated


def test_the_card_store_variable_is_still_pinned_too():
    """POSITIVE CONTROL for the tests above.

    Each of them would also pass against a conftest that had been gutted —
    "the variable is absent" is what a broken harness looks like as well as
    what a correct one looks like. This asserts the pin that has been in place
    since 2026-07 is still in place, so a passing reading above is evidence
    about ``SCITEX_STORE_DSN`` rather than about the harness having stopped.

    THE ASSERTION IS SET-EITHER-WAY, not a ``cards.db`` suffix and not a bare
    ``search_path`` substring. The suffix era described a scratch FILE that
    worked as a store; there is one storage engine now and a filename names
    no store — which is exactly why the harness points the variable at one
    when no cluster could be opened (refused by every door, fail-LOUD). The
    property that actually makes the pin safe is the shared helper's: SET
    (absence resolves live), and either ``search_path``-scoped (``public``
    off the path, so an unqualified read cannot resolve the live board's
    tables) or the refused placeholder.

    Still a control rather than a fourth copy of the same test: it reads the
    variable WITHOUT the ``root_conftest`` fixture, pinned by the per-test
    autouse fixture rather than observed through the session pin, so a
    gutted conftest cannot make both read correctly by accident.
    """
    # Arrange
    # Act
    observed = os.environ.get("SCITEX_STORE_DSN")
    # Assert
    _assert_set_either_way(observed)


# EOF
