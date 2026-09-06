#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every notification must say which host computed it.

THE INCIDENT, 2026-08-20. Two notifyd daemons, one on compute-04 and one on
ywata-note-win, each resolve ``127.0.0.1:55432`` to their OWN database. Both are
correct about the store they read, and neither says which store that is::

    16:59:51Z  backlog nudge  ->  "deferred"    true on ywata-note-win
    17:11:02Z  blocked-check  ->  "blocked"     true on compute-04

One card, eleven minutes apart, two contradictory labels, neither of them wrong.

THE COST WAS NOT THE WRONG LABEL, IT WAS THE UNREASONABLENESS. Three agents
re-derived this by hand across one day. One built a predictive model from eight
samples and retracted it ten minutes later. Two proposed remedies aimed at the
delivery path -- "clear the stale timestamp", "re-validate status at delivery" --
and both were inert, because a notification that does not name its store cannot
be reasoned about: every available hypothesis assumes ONE store and asks what
happened to a row inside it.

WHY THE HOSTNAME AND NOT ``store_uuid``. The obvious field is the wrong one:
``store_uuid`` lives in a ``schema_meta`` row, so ``pg_dump`` copies it, and
three of this fleet's stores answer the same value. Stamping it would have
printed identically from both daemons above.

WHY IT IS ASSERTED AT THE ENQUEUE POINT. The digest, escalations, backlog nudge
and blocked-check are composed in four different modules. Labelling each body
builder is a rule every future notification type must remember; stamping at the
one function they all pass through is a rule none of them can miss.

No mocks (STX-NM002): ``_safe_enqueue`` already takes the enqueue callable as a
parameter, so a recording function is the module's own seam, not a swap.
"""

from __future__ import annotations

import datetime as dt
import socket

from scitex_cards._reminder_enqueue import _safe_enqueue

NOW = dt.datetime(2026, 8, 20, 17, 0, 0, tzinfo=dt.timezone.utc)


def _recorder():
    """A real callable that records what it was handed. Returns (fn, sink)."""
    sink: dict = {}

    def _enqueue(recipient_key, **kwargs):
        sink.update(kwargs)
        sink["recipient_key"] = recipient_key
        return {"id": "n_test"}

    return _enqueue, sink


def _enqueue_once(body: str) -> dict:
    fn, sink = _recorder()
    _safe_enqueue(fn, "someone", "digest", "card-1", body, NOW, None)
    return sink


class TestTheBodyNamesTheComputingHost:
    def test_the_enqueued_body_carries_a_computed_on_marker(self):
        # Arrange
        body = "Assigned-card digest: ACT ON THESE 1"

        # Act
        sent = _enqueue_once(body)

        # Assert
        assert "computed on" in sent["body"]

    def test_it_names_THIS_host_and_not_a_placeholder(self):
        # A marker that says "unknown" on a healthy host would satisfy the test
        # above while carrying no information — which is the whole defect.
        # Arrange
        expected = socket.gethostname()

        # Act
        sent = _enqueue_once("body")

        # Assert
        assert expected in sent["body"]

    def test_the_original_body_survives_the_stamp(self):
        # Arrange
        body = "ACT ON THESE 3 (highest priority, longest ignored)"

        # Act
        sent = _enqueue_once(body)

        # Assert
        assert body in sent["body"]


class TestTheStampAppliesToEveryNotificationType:
    """The point of stamping at the choke point rather than per body builder."""

    def test_a_backlog_nudge_body_is_stamped_too(self):
        # Arrange
        body = "BACKLOG: 8 card(s) deferred and waiting"

        # Act
        fn, sink = _recorder()
        _safe_enqueue(fn, "someone", "reminder", "(backlog)", body, NOW, None)

        # Assert
        assert "computed on" in sink["body"]

    def test_a_blocked_check_body_is_stamped_too(self):
        # Arrange
        body = "BLOCKED-CHECK: 3 card(s) blocked >24h"

        # Act
        fn, sink = _recorder()
        _safe_enqueue(fn, "someone", "blocked-check", "(blocked)", body, NOW, None)

        # Assert
        assert "computed on" in sink["body"]


class TestDeliveryStillSucceeds:
    """CONTROL: the stamp must not cost a notification.

    A labelling change that silently drops deliveries would be strictly worse
    than the ambiguity it fixes, so the return contract is asserted separately
    rather than assumed from the body checks above.
    """

    def test_enqueue_still_reports_success(self):
        # Arrange
        fn, _ = _recorder()

        # Act
        ok = _safe_enqueue(fn, "someone", "digest", "card-1", "body", NOW, None)

        # Assert
        assert ok is True


# EOF
