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

    SCITEX_CARDS_DB=postgresql://scitex-primary:55432/scitex   <- pinned
    SCITEX_STORE_DSN=postgresql://scitex-primary:55432/scitex  <- inherited

Same cluster, same database, same 6,399 cards. Nothing in ``src/scitex_cards``
reads the second one today, so this was not a live leak — it was a leak waiting
for the first test that reaches storage through ``scitex_dev.store`` instead of
through this package, which the PostgreSQL port makes routine.

WHAT IS ASSERTED, and why not "the variable is not the live DSN". Naming the
forbidden value is the weaker test: it passes on any host whose live board
happens to be spelled differently, which is every host but one. So the
assertions are POSITIVE — the variable holds a schema-scoped throwaway, or it
holds nothing — and neither of those can be the live board's public schema
regardless of how that board is spelled.
"""

from __future__ import annotations

import os

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


def test_the_primitive_dsn_variable_is_not_inherited(root_conftest):
    """It is either pinned to a throwaway or removed — never left as it was."""
    # Arrange
    pinned = root_conftest._EPHEMERAL_DSN
    # Act
    observed = os.environ.get(ENV_STORE_DSN)
    # Assert
    assert observed == pinned


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


def test_an_unavailable_cluster_leaves_no_dsn_at_all(root_conftest):
    """The other arm of the same guarantee — removal, not inheritance."""
    # Arrange
    pinned = root_conftest._EPHEMERAL_DSN
    # Act
    absent_if_unpinned = pinned is not None or ENV_STORE_DSN not in os.environ
    # Assert
    assert absent_if_unpinned


def test_the_reason_is_recorded_when_nothing_could_be_opened(root_conftest):
    """A silent ``None`` would make an unavailable server look like a choice."""
    # Arrange
    reason = root_conftest._EPHEMERAL_DSN_REASON
    # Act
    stated = bool(reason and reason.strip())
    # Assert
    assert stated


def test_the_card_store_variable_is_still_pinned_too():
    """POSITIVE CONTROL for the three tests above.

    Each of them would also pass against a conftest that had been gutted —
    "the variable is absent" is what a broken harness looks like as well as
    what a correct one looks like. This asserts the pin that has been in place
    since 2026-07 is still in place, so an empty reading above is evidence
    about ``SCITEX_STORE_DSN`` rather than about the harness having stopped.
    """
    # Arrange
    expected_suffix = "cards.db"
    # Act
    observed = os.environ.get("SCITEX_CARDS_DB", "")
    # Assert
    assert observed.endswith(expected_suffix)


# EOF
