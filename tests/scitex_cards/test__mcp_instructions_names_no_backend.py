#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The instructions must not name a storage backend or a default store path."""

from __future__ import annotations

import pytest

from scitex_cards._mcp_instructions import build_instructions

#: Spellings that assert a BACKEND or a DEFAULT PATH. Each one is a promise
#: this package would have to re-verify on every storage change.
FORBIDDEN = ("PostgreSQL", "postgresql", "cards.db", "YAML")

#: Both branches of the renderer. The unresolved one is rarely exercised in
#: production, which is exactly why it needs a test rather than a reader.
BRANCHES = ("scitex-cards", None)


@pytest.mark.parametrize("agent_id", BRANCHES)
@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_the_instructions_assert_no_backend_and_no_default_path(agent_id, forbidden):
    """Naming either is a claim that must be maintained; both have gone stale."""
    # Arrange
    text = build_instructions(agent_id)

    # Act
    present = forbidden in text

    # Assert
    assert not present, (
        f"instructions name {forbidden!r}, which goes stale on the next storage "
        "change and has twice already — say what resolve_store answers instead"
    )


@pytest.mark.parametrize("agent_id", BRANCHES)
def test_the_instructions_point_at_the_verb_that_cannot_go_stale(agent_id):
    """Removing the false claim is only safe if the true answer is reachable.

    Without this, the previous test could be satisfied by deleting the sentence
    entirely, leaving an agent with no way to learn which store it is on — which
    is the failure the sentence was written to prevent.
    """
    # Arrange
    text = build_instructions(agent_id)

    # Act
    names_the_verb = "resolve_store" in text

    # Assert
    assert names_the_verb


# EOF
