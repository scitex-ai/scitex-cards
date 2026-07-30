#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_cards._store_wip`` — the WIP gate's add-path
enforcement, and the emergency-recording exemption it now carries.

Real stores on disk, real ``add_task`` (no mocks; STX-NM / PA-306). AAA +
one-assertion-per-test (STX-TQ002 / STX-TQ007).

The incident these pin (2026-07-12): the operator escalated a P0 and the board
REFUSED the card — "WIP gate refuses add: … Close existing tasks before adding
more." A throughput cap was sitting on the emergency-recording path, and its
cheapest workaround was to close cards you had not finished. Priority <= 1 is
now never gated; a card admitted over the cap says so, on the card.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._model import TaskValidationError
from scitex_cards._store import add_task, get_task
from scitex_cards._store_wip import (
    EXEMPT_PRIORITY_MAX,
    OVERRIDE_COMMENT_KIND,
    is_priority_exempt,
)
from scitex_cards._throughput import ENV_WIP_LIMIT

#: The escalated card from the 2026-07-12 incident — the P0 the board refused.
#: Reused verbatim by every test that files an emergency over the cap.
INCIDENT_CARD = {
    "title": "[P0] fleet-wide config/state-loss hazard",
    "status": "in_progress",
    "priority": 1,
    "agent": "a",
}

#: An ORDINARY over-cap card: no priority, no emergency, must be refused.
ORDINARY_CARD = {"title": "just more work", "status": "in_progress", "agent": "a"}


def _refusal_message(store, card_id: str) -> str:
    """The text ``add_task`` refuses an over-cap ORDINARY card with.

    The three ``TestRefusalMessageIsActionable`` tests each pin one property
    of this ONE message, so the raise is driven once — here — instead of
    being re-counted as a second assertion inside every sibling test.
    """
    with pytest.raises(TaskValidationError) as excinfo:
        add_task(id=card_id, store=store, **ORDINARY_CARD)
    return str(excinfo.value)


@pytest.fixture()
def over_cap(env):
    """An agent ``a`` sitting FAR over its refuse threshold (limit 1 → 2x = 2).

    Yields the STORE path. Seeded by writing the 8 in-flight rows DIRECTLY
    into the canonical SQLite DB (``seed_db_from_doc``): the gate itself
    refuses seeding past 2x through ``add_task``, which is the point — the
    seed must bypass the gate the tests then exercise. Returns the pinned
    STORE identity path (NOT the DB path — see the migration playbook's
    STORE-PATH RULE), which the harness's per-test DB is stamped for.
    """
    from conftest import seed_db_from_doc

    env.set(ENV_WIP_LIMIT, "1")
    doc = {
        "tasks": [
            {
                "id": f"wip-{i}",
                "title": f"in flight {i}",
                "status": "in_progress",
                "agent": "a",
                "assignee": "a",
            }
            for i in range(8)
        ]
    }
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


class TestPriorityExemptPredicate:
    """LOWER priority = MORE urgent. The exemption band is P0/P1."""

    def test_priority_one_is_exempt(self):
        """21 of 28 incident cards on the live store are priority 1 — THE
        value the gate must never block."""
        # Arrange
        priority = 1
        # Act
        exempt = is_priority_exempt(priority)
        # Assert
        assert exempt is True

    def test_priority_zero_is_exempt(self):
        """0 is the most urgent value present."""
        # Arrange
        priority = 0
        # Act
        exempt = is_priority_exempt(priority)
        # Assert
        assert exempt is True

    def test_priority_three_is_not_exempt(self):
        """Ordinary work stays capped."""
        # Arrange
        priority = 3
        # Act
        exempt = is_priority_exempt(priority)
        # Assert
        assert exempt is False

    def test_missing_priority_is_not_exempt(self):
        """The exemption is for DECLARED emergencies."""
        # Arrange
        priority = None
        # Act
        exempt = is_priority_exempt(priority)
        # Assert
        assert exempt is False

    def test_high_number_is_not_exempt(self):
        """Priority 8 is the LEAST important card, not the most; exempting it
        would have been exactly backwards."""
        # Arrange
        priority = 8
        # Act
        exempt = is_priority_exempt(priority)
        # Assert
        assert exempt is False

    def test_true_is_not_exempt(self):
        """bool is an int in Python; a truthy flag fumbled into `priority`
        must not buy an emergency exemption."""
        # Arrange
        priority = True
        # Act
        exempt = is_priority_exempt(priority)
        # Assert
        assert exempt is False

    def test_exempt_band_is_p0_p1(self):
        """Pin the constant itself."""
        # Arrange
        expected_max = 1
        # Act
        band_max = EXEMPT_PRIORITY_MAX
        # Assert
        assert band_max == expected_max


class TestIncidentIsAlwaysRecordable:
    """priority <= 1 is never gated — no flag to remember mid-outage."""

    def test_p1_in_progress_card_is_accepted_over_the_cap(self, over_cap):
        """The agent is at 8 in-flight against a refuse threshold of 2."""
        # Arrange
        card_id = "p0-config-loss"
        # Act
        rec = add_task(id=card_id, store=over_cap, **INCIDENT_CARD)
        # Assert
        assert rec["id"] == card_id

    def test_p0_in_progress_card_is_accepted_over_the_cap(self, over_cap):
        # Arrange
        card = dict(INCIDENT_CARD, title="[P0] production is on fire", priority=0)
        # Act
        rec = add_task(id="p0-zero", store=over_cap, **card)
        # Assert
        assert rec["priority"] == 0

    def test_normal_card_is_still_refused_over_the_cap(self, over_cap):
        """The cap must still work for ordinary new work."""
        # Arrange
        card = dict(ORDINARY_CARD, priority=3)
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="WIP gate refuses add"):
            add_task(id="ordinary", store=over_cap, **card)

    def test_unprioritised_card_is_still_refused_over_the_cap(self, over_cap):
        """No priority at all is not an emergency."""
        # Arrange
        card = dict(ORDINARY_CARD)
        # Act
        # Assert
        with pytest.raises(TaskValidationError, match="WIP gate refuses add"):
            add_task(id="unprioritised", store=over_cap, **card)


class TestBypassIsLoudNotSilent:
    """A card admitted over the cap must SAY SO, so abuse self-reports."""

    def test_bypassed_card_carries_the_audit_stamp(self, over_cap):
        # Arrange
        card_id = "p1-stamped"
        # Act
        rec = add_task(id=card_id, store=over_cap, **INCIDENT_CARD)
        # Assert
        kinds = [c.get("kind") for c in rec.get("comments") or []]
        assert OVERRIDE_COMMENT_KIND in kinds

    def test_audit_stamp_records_the_wip_count_and_limit(self, over_cap):
        # Arrange
        card_id = "p1-counted"
        # Act
        rec = add_task(id=card_id, store=over_cap, **INCIDENT_CARD)
        # Assert — "8 tasks in_progress at insert time (limit 1; ...)".
        text = rec["comments"][0]["text"]
        assert "8 tasks in_progress" in text and "limit 1" in text

    def test_audit_stamp_is_persisted_not_just_returned(self, over_cap):
        # Arrange
        card_id = "p1-persisted"
        add_task(id=card_id, store=over_cap, **INCIDENT_CARD)
        # Act — read it back off the store; the board renders THIS, not the
        # in-memory return value.
        persisted = get_task(store=over_cap, task_id=card_id)
        # Assert
        kinds = [c.get("kind") for c in persisted.get("comments") or []]
        assert OVERRIDE_COMMENT_KIND in kinds

    def test_card_under_the_cap_is_not_stamped(self, env):
        """A P1 filed by an agent with room to spare is ordinary; the stamp
        must mean "a bypass happened", not "someone typed priority 1"."""
        # Arrange — the harness bootstraps an EMPTY canonical DB per test, so
        # agent ``a`` starts with zero in-flight cards; no seeding needed.
        env.set(ENV_WIP_LIMIT, "10")
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        card = dict(INCIDENT_CARD, title="[P1] urgent but the board is calm")
        # Act
        rec = add_task(id="p1-roomy", store=store, **card)
        # Assert
        assert not rec.get("comments")


class TestRefusalMessageIsActionable:
    """The old text ("Close existing tasks before adding more") is what
    pressured agents to falsely close cards during an outage."""

    def test_refusal_names_the_priority_escape_hatch(self, over_cap):
        # Arrange
        hatch = "priority <= 1"
        # Act
        message = _refusal_message(over_cap, "ordinary-2")
        # Assert
        assert hatch in message

    def test_refusal_names_the_deferred_blocked_escape_hatch(self, over_cap):
        # Arrange
        hatch = "deferred or blocked is never gated"
        # Act
        message = _refusal_message(over_cap, "ordinary-3")
        # Assert
        assert hatch in message

    def test_refusal_does_not_tell_you_to_close_cards(self, over_cap):
        """The falsify-your-board incentive, removed at the source."""
        # Arrange
        removed = "Close existing tasks before adding more"
        # Act
        message = _refusal_message(over_cap, "ordinary-4")
        # Assert
        assert removed not in message
