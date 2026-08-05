#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scope='agent:<id>'`` names an OWNER; every other scope names a LENS.

Exact string equality on scope, combined with this package's own MCP
instructions telling every agent to "call list_tasks with scope='agent:<id>'
to see only your slice", hid work from the people responsible for it. A card a
PEER filed against you — under ``fleet``, under ``ecosystem``, or under no
scope, which is what most filings do — was excluded from your own slice.

Measured on the live store the day of the fix: 438 open cards owned by an agent
were invisible to that agent's scoped query, across 37 owners, 394 of them for
the sole reason that nobody set a scope. The ``lead`` agent had 12 hidden and 0
visible, so its slice query returned an empty board while it held work.

The failure is silent by construction: a filter returning fewer rows is
indistinguishable from a board holding fewer cards. Three agents
(scitex-agent-container, scitex-ui, scitex-app) reported it independently, none
of them looking for it, all of them having followed the instruction and
reported "my slice" with confidence.

These pin the distinction in both directions — a lens must NOT start behaving
like an owner, or the fix trades a silent omission for a silent inclusion,
which is harder to notice because the number goes UP.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _store

MINE = "scitex-ui"
THEIRS = "scitex-hpc"


@pytest.fixture
def board():
    """Cards owned by MINE under four different lenses, plus one owned by THEIRS."""
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(
        store,
        id="own-scope",
        title="filed under my own scope",
        scope=f"agent:{MINE}",
        assignee=MINE,
    )
    _store.add_task(
        store, id="no-scope", title="peer filed it with no scope", assignee=MINE
    )
    _store.add_task(
        store,
        id="fleet-scope",
        title="peer filed it under fleet",
        scope="fleet",
        assignee=MINE,
    )
    _store.add_task(
        store,
        id="agent-field",
        title="mine via the agent field",
        scope="ecosystem",
        agent=MINE,
    )
    _store.add_task(
        store,
        id="not-mine",
        title="someone else's work",
        scope=f"agent:{THEIRS}",
        assignee=THEIRS,
    )
    return store


def test_a_card_filed_under_my_own_scope_is_in_my_slice(board):
    """The pre-existing behaviour, unchanged — this is the regression anchor."""
    # Arrange
    my_slice = f"agent:{MINE}"

    # Act
    rows = _store.list_tasks(board, scope=my_slice)

    # Assert
    assert "own-scope" in {r["id"] for r in rows}


def test_a_card_with_no_scope_at_all_is_in_its_owners_slice(board):
    """394 of the 438 hidden cards were exactly this: nobody set a scope.

    Absent is not "not mine". Collapsing the two produced the whole hidden set.
    """
    # Arrange
    my_slice = f"agent:{MINE}"

    # Act
    rows = _store.list_tasks(board, scope=my_slice)

    # Assert
    assert "no-scope" in {r["id"] for r in rows}


def test_a_card_a_peer_filed_under_fleet_reaches_its_owner(board):
    """``fleet`` is what a peer reasonably picks when filing cross-cutting work.

    Which made the cards most likely to be hidden the ones filed BY OTHERS —
    the worst possible selection bias for a work queue, since those are exactly
    the ones their owner has not already thought about.
    """
    # Arrange
    my_slice = f"agent:{MINE}"

    # Act
    rows = _store.list_tasks(board, scope=my_slice)

    # Assert
    assert "fleet-scope" in {r["id"] for r in rows}


def test_ownership_via_the_agent_field_counts_too(board):
    """``agent`` and ``assignee`` both name an owner; neither alone is enough."""
    # Arrange
    my_slice = f"agent:{MINE}"

    # Act
    rows = _store.list_tasks(board, scope=my_slice)

    # Assert
    assert "agent-field" in {r["id"] for r in rows}


def test_another_agents_card_stays_out_of_my_slice(board):
    """The fix must not turn a slice into the whole board.

    The rejected alternative — surface every unscoped card in EVERYONE's view —
    fails safe but buries each agent under other people's work, and a human
    filtering that by hand reintroduces the omission wearing different clothes.
    """
    # Arrange
    my_slice = f"agent:{MINE}"

    # Act
    rows = _store.list_tasks(board, scope=my_slice)

    # Assert
    assert "not-mine" not in {r["id"] for r in rows}


def test_a_lens_scope_still_matches_exactly(board):
    """``fleet`` is a VIEW: only cards filed under it are in it."""
    # Arrange
    lens = "fleet"

    # Act
    rows = _store.list_tasks(board, scope=lens)

    # Assert
    assert {r["id"] for r in rows} == {"fleet-scope"}


def test_a_lens_scope_does_not_absorb_cards_by_ownership(board):
    """Querying a lens must not pull in an owner's other cards."""
    # Arrange
    lens = "ecosystem"

    # Act
    rows = _store.list_tasks(board, scope=lens)

    # Assert
    assert {r["id"] for r in rows} == {"agent-field"}


def test_the_summary_counts_the_same_set_as_the_listing(board):
    """``summarize_tasks`` shares ``_match``, so the two must never disagree.

    A summary counting a different population than the listing is how an agent
    concludes its board is empty while the listing would have shown work.
    """
    # Arrange
    listed = _store.list_tasks(board, scope=f"agent:{MINE}")

    # Act
    summary = _store.summarize_tasks(board, scope=f"agent:{MINE}")

    # Assert
    assert summary["total"] == len(listed)


def test_an_empty_owner_after_the_prefix_matches_nothing_by_ownership(board):
    """``scope="agent:"`` names no one, so it must not match every card.

    A bare prefix is malformed input, and the dangerous reading is "everyone" —
    that would hand a caller the entire board through a filter that looks like
    a restriction.
    """
    # Arrange
    malformed = "agent:"

    # Act
    rows = _store.list_tasks(board, scope=malformed)

    # Assert
    assert rows == []


# EOF
