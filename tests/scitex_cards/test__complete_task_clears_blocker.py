#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Completing a blocked card clears its gate.

A done card still naming an unresolved blocker is incoherent, and
``_validate_tasks`` refuses the whole document because of it — so
``complete_task`` leaving the blocker in place did not merely look untidy, it
made the save impossible.

Measured on the live ``*/15`` reconcile cron 2026-08-01: one card
(``ci-runner-gitconfig-lock-collision``, legitimately blocked on an operator
decision, PR merged anyway) stopped the sweep from closing ANY card, because
validation covers the whole document.

``resolve_task`` has always cleared the blocker. These tests pin the two closing
verbs to the same behaviour.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._db import open_db
from scitex_cards._store import get_task
from scitex_cards._store_lifecycle import complete_task
from scitex_cards._store_mutate import add_task

_MANAGED = ("SCITEX_CARDS_AGENT_ID", "SCITEX_CARDS_DB", "HOME", "SCITEX_DIR")


@pytest.fixture
def store_with_blocked_card(tmp_path):
    """A real store holding one blocked card."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    os.environ.pop("SCITEX_DIR", None)
    os.environ["HOME"] = str(tmp_path)
    os.environ["SCITEX_CARDS_AGENT_ID"] = "test-agent"
    (tmp_path / ".scitex" / "cards").mkdir(parents=True)
    store = tmp_path / "cards.db"
    os.environ["SCITEX_CARDS_DB"] = str(store)
    os.chdir(tmp_path)
    open_db(str(store)).close()

    add_task(
        None,
        id="blocked-card",
        title="[probe] blocked on an operator decision",
        status="blocked",
        blocker="operator-decision",
        assignee="test-agent",
        created_by="test-agent",
    )

    yield store

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestCompletingABlockedCard:
    def test_the_save_succeeds_rather_than_failing_validation(
        self, store_with_blocked_card
    ):
        # Arrange
        before = get_task(None, "blocked-card")

        # Act
        complete_task(None, "blocked-card")

        # Assert
        assert before.get("blocker") == "operator-decision"

    def test_the_card_ends_up_done(self, store_with_blocked_card):
        # Arrange
        complete_task(None, "blocked-card")

        # Act
        after = get_task(None, "blocked-card")

        # Assert
        assert after.get("status") == "done"

    def test_the_blocker_is_gone(self, store_with_blocked_card):
        # Arrange
        complete_task(None, "blocked-card")

        # Act
        after = get_task(None, "blocked-card")

        # Assert
        assert not after.get("blocker")


class TestAnUnblockedCardIsUnaffected:
    def test_completing_an_open_card_still_works(self, store_with_blocked_card):
        # Arrange
        add_task(
            None,
            id="open-card",
            title="[probe] no blocker",
            status="in_progress",
            assignee="test-agent",
            created_by="test-agent",
        )

        # Act
        complete_task(None, "open-card")

        # Assert
        assert get_task(None, "open-card").get("status") == "done"
