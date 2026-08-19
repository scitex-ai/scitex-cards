#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The creator door must refuse an unperformed substitution as an author.

ASK #2 OF THE 2026-07-19 INCIDENT CARD, finally implemented: "NEVER persist an
unexpanded ${...}. Validate the resolved identity before write: if it starts
with '${' or is empty, RAISE."

The incident's own words name the asymmetry these tests close: "dm_send FAILS
LOUD, add_task FAILS SILENT". The channel door (`_channel_identity`) has
rejected a leading `$` since then; this door — the one that writes
`created_by` — did not, and 15 rows on the live board still carry the literal
`${SCITEX_CARDS_AGENT_ID}` as their author.

The env fixture sets and restores the REAL `os.environ` entry rather than
patching production internals, so what these tests exercise is the same
resolution path a live agent takes.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._model import TaskValidationError
from scitex_cards._store_identity import (
    ENV_AGENT,
    _default_agent,
    _resolve_creator_or_raise,
)

BRACE_FORM = "${SCITEX_CARDS_AGENT_ID}"
BARE_FORM = "$SCITEX_CARDS_AGENT_ID"


@pytest.fixture
def agent_env():
    """Set the real board-identity env var; restore whatever was there."""
    original = os.environ.get(ENV_AGENT)

    def _set(value):
        if value is None:
            os.environ.pop(ENV_AGENT, None)
        else:
            os.environ[ENV_AGENT] = value

    yield _set

    if original is None:
        os.environ.pop(ENV_AGENT, None)
    else:
        os.environ[ENV_AGENT] = original


def _refusal_message(value):
    """The refusal text for ``value``, so a message test needs ONE assert."""
    try:
        _resolve_creator_or_raise(value)
    except TaskValidationError as exc:
        return str(exc)
    return ""


def test_brace_placeholder_is_refused():
    # Arrange
    placeholder = BRACE_FORM
    # Act
    act = lambda: _resolve_creator_or_raise(placeholder)  # noqa: E731
    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_bare_dollar_placeholder_is_refused():
    # Arrange — the form .mcp.json never expands, so it arrives verbatim
    placeholder = BARE_FORM
    # Act
    act = lambda: _resolve_creator_or_raise(placeholder)  # noqa: E731
    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_refusal_names_the_offending_value():
    # Arrange
    placeholder = BRACE_FORM
    # Act
    message = _refusal_message(placeholder)
    # Assert
    assert placeholder in message


def test_refusal_points_at_the_mcp_config():
    # Arrange
    placeholder = BARE_FORM
    # Act
    message = _refusal_message(placeholder)
    # Assert
    assert ".mcp.json" in message


def test_placeholder_arriving_through_the_env_is_refused(agent_env):
    # Arrange — reproduces the incident: the launcher exported the literal
    agent_env(BRACE_FORM)
    # Act
    act = lambda: _resolve_creator_or_raise(None)  # noqa: E731
    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_a_real_agent_id_is_still_accepted(agent_env):
    # Arrange — control: the guard must not refuse ordinary identities
    agent_env("scitex-cards")
    # Act
    resolved = _resolve_creator_or_raise(None)
    # Assert
    assert resolved == "scitex-cards"


def test_a_dollar_later_in_the_id_is_accepted():
    # Arrange — control: only a LEADING '$' is the placeholder shape
    odd_but_real = "agent$42"
    # Act
    resolved = _resolve_creator_or_raise(odd_but_real)
    # Assert
    assert resolved == odd_but_real


def test_empty_still_raises_the_unresolved_error(agent_env):
    # Arrange — pre-existing behaviour must be untouched
    agent_env(None)
    # Act
    act = lambda: _resolve_creator_or_raise(None)  # noqa: E731
    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_unknown_sentinel_still_raises(agent_env):
    # Arrange
    agent_env(None)
    # Act
    act = lambda: _resolve_creator_or_raise("unknown")  # noqa: E731
    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_default_agent_shares_the_guard(agent_env):
    # Arrange — the completion/comment door delegates to the same resolver
    agent_env(BRACE_FORM)
    # Act
    act = lambda: _default_agent(None)  # noqa: E731
    # Assert
    with pytest.raises(TaskValidationError):
        act()


def test_store_reexport_is_the_guarded_resolver():
    # Arrange — historical `from ._store import _resolve_creator_or_raise`
    from scitex_cards import _store

    # Act
    module = _store._resolve_creator_or_raise.__module__
    # Assert
    assert module == "scitex_cards._store_identity"

# EOF
