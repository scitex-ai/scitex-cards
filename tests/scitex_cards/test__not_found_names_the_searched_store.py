#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A "not found" error must name the store it ACTUALLY SEARCHED.

Reported by scitex-ui 2026-08-18, from their container, with a control that
made the report precise: ``get_task`` on an id they had just created returned
the full card, so the LOOKUP was fine and only the failure text was wrong.

    get_task("<missing id>")
    -> "task id '...' not found in /home/agent/.scitex/cards/tasks.yaml"
    resolve_store()
    -> postgresql://scitex_cards@100.64.0.1:55432/scitex_cards

The message named a THIRD location — neither the resolved store nor the
``user_store`` the resolver reports. It came from ``_resolved_store(store)``,
a filesystem path the verbs resolve for LOCKING; on a Postgres deployment,
which is the normal one, that file is not the board and need not exist.

WHY A WRONG PATH HERE IS WORSE THAN A COSMETIC BUG. This package's own MCP
instructions warn that "opening a store file directly is how an abandoned one
gets mistaken for the live board" — and this message was directing a reader to
do exactly that, at the moment they were debugging a missing card, while
serving as their evidence. They would open the YAML, find it stale or absent,
and conclude something false about the board.

These tests assert the message names ``store_label(store)``, not the absence
of a particular string: asserting "tasks.yaml is not mentioned" would pass for
a message that named nothing at all, or named some third wrong thing.
"""

from __future__ import annotations

import pytest

from scitex_cards._model import _canonical_source_label
from scitex_cards._store import TaskNotFoundError, get_task

from conftest import seed_db_from_doc


@pytest.fixture()
def seeded_store(tmp_path, env):
    """A real store holding one card, pinned as the AMBIENT store.

    Pinned through ``SCITEX_CARDS_DB`` rather than passed as ``store=``,
    because the canonical read resolves the ambient chain and does NOT honour
    an explicit store argument — measured while writing these tests, and
    carded as cards-store-param-ignored-no-isolation-route-20260818. Seeding a
    database and handing it to ``store=`` produced a NOT-FOUND for a card that
    was demonstrably in the file, while the read went to the live board.
    """
    db_path = tmp_path / "cards.db"
    seed_db_from_doc(
        {"tasks": [{"id": "present-card", "title": "here", "status": "deferred"}]},
        db_path,
    )
    env.set("SCITEX_CARDS_DB", str(db_path))
    return str(db_path)


@pytest.fixture()
def miss_message(seeded_store):
    """The text of a real miss against a real store."""
    with pytest.raises(TaskNotFoundError) as excinfo:
        get_task(task_id="no-such-card")
    return str(excinfo.value)


def test_a_missing_id_names_the_store_that_was_searched(miss_message):
    """Guards the REGRESSION: the message must not drift back to a lock path.

    Stated honestly about its own strength — the message is BUILT from
    ``_canonical_source_label``, so this does not independently measure that
    the label is right. It measures that the message and the reader keep
    naming the same store, which is the property that failed: the old text
    interpolated a resolved YAML path while the rows came from Postgres.
    """
    # Arrange
    # Act
    # Assert
    assert _canonical_source_label() in miss_message


def test_a_missing_id_still_names_the_id(miss_message):
    # Arrange
    # Act
    # Assert — naming the store must not cost the other half of the message.
    assert "no-such-card" in miss_message


def test_the_control_a_present_id_is_still_found(seeded_store):
    """scitex-ui's own control, kept, and it EARNED its place immediately.

    The first version of this file passed the store as ``store=``. Both
    message tests went green and this control FAILED — ``get_task`` could not
    find a card that was sitting in the database it had just been handed.
    Without the control the file would have reported a working fix while
    every lookup in it raised for an unrelated reason, and the two assertions
    would have agreed with each other about a broken store.
    """
    # Arrange
    # Act
    task = get_task(task_id="present-card")

    # Assert
    assert task["id"] == "present-card"


# EOF
