#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card whose blocker names a NON-CARD gate is not "blocked on nothing".

WHY THIS FILE EXISTS (2026-11-26): ``no_falsely_blocked`` reported 6 live cards
as blocked on nothing because every card in their ``depends_on`` had finished.
Four of them were ``blocker: operator-decision`` — the finished dependency was
history, the operator gate was live, and the owners' comments said so. The
check ignored the ``blocker`` field and its hint told owners to start work the
operator had not authorised. ``depends_on`` finishing is evidence about a
``dependency`` gate only.

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture.
"""

import os

import pytest
from conftest import seed_db_from_doc

from scitex_cards._health import _check_no_falsely_blocked
from scitex_cards._health_cards import _NON_CARD_BLOCKERS
from scitex_cards._task import VALID_BLOCKERS


def _store(blocker):
    card = {
        "id": "waiter",
        "title": "waiter",
        "status": "blocked",
        "depends_on": ["gate"],
    }
    if blocker is not None:
        card["blocker"] = blocker
    tasks = [{"id": "gate", "title": "gate", "status": "done"}, card]
    seed_db_from_doc({"tasks": tasks}, os.environ["SCITEX_STORE_DSN"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


@pytest.mark.parametrize("blocker", ["operator-decision", "compute", "agent-wait"])
def test_a_non_card_gate_is_not_flagged(blocker, postgres_dsn):
    # Arrange
    store = _store(blocker)
    # Act
    result = _check_no_falsely_blocked(store)
    # Assert
    assert result["ok"] is True


@pytest.mark.parametrize("blocker", ["dependency", "dep", "none", None])
def test_a_dependency_or_unnamed_gate_is_still_flagged(blocker, postgres_dsn):
    # Arrange — the original regression must still be caught.
    store = _store(blocker)
    # Act
    result = _check_no_falsely_blocked(store)
    # Assert
    assert result["ok"] is False


def test_every_non_card_blocker_is_a_real_blocker_value():
    # Arrange — a typo here would silently exempt nothing.
    valid = set(VALID_BLOCKERS)
    # Act
    unknown = _NON_CARD_BLOCKERS - valid
    # Assert
    assert unknown == set()
