#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``delivery_confirmed`` health check.

Regression cover for the silence measured 2026-07-29: this agent's operator DMs
never arrived for weeks. The inbox held 228 rows and ZERO unseen — enqueued,
consumed, gone — because the agent spec allowlisted ``server:scitex-cards`` while
``.mcp.json`` registers this server as ``scitex-cards``, so Claude Code read
every push and discarded it. ``channel_capable``, ``channel_drain`` and the
drain itself were all green: the drain ack'd on ``send()`` RETURNING, which is
not a delivery receipt and never was.

With the push and the confirmation stored as separate facts, that outage has a
shape you can query: hundreds of rows stamped ``pushed_at``, none stamped
``confirmed_at``. This check turns that shape red and hands the reader the two
causes it can have plus how to tell them apart.

THREE-VALUED. "nothing is unconfirmed" and "I cannot tell" are different
answers, so the never-drained and unreadable cases report UNKNOWN (``ok=None``)
rather than passing — a check that measured nothing must not read as a check
that found nothing.

No mocks (STX-NM / PA-306): a real store, real inbox records, real receipts, and
a genuinely broken inbox — a corrupt sidecar — so the read really fails and the
result does not depend on the test user's privileges.

SQLite is RETIRED as an inbox backend (operator ruling 2026-08-23); the file
break-glass backend (``SCITEX_CARDS_INBOX_BACKEND=yaml``) is the only
non-server option left, and it is what production runs when the store is not
PostgreSQL. This suite's conftest already pins it for every test.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards import _inbox
from scitex_cards._health import health
from scitex_cards._health_delivery import (
    PUSH_CONFIRM_GRACE_SECONDS,
    check_delivery_confirmed,
)
from scitex_cards._inbox_receipt import record_confirmation, record_push

AGENT = "delivery-agent"

#: The one real non-server inbox backend left. SQLite was retired 2026-08-23.
BACKENDS = ("yaml",)

#: The push instant every fixture stamps, and a "now" five hours later.
PUSHED_AT_STAMP = "2026-07-29T07:00:00Z"
LATER = _dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _enqueue(store, index=1):
    """One real unseen notification; returns its record."""
    return _inbox.enqueue(
        AGENT,
        event_type="commented",
        card_id=f"card-{index}",
        body=f"operator DM {index}",
        actor="operator",
        ts=f"2026-07-29T06:0{index}:00Z",
        store=store,
    )


def _push_two(store):
    """Two records pushed long ago and confirmed by nobody — the outage."""
    for index in (1, 2):
        record = _enqueue(store, index)
        record_push(AGENT, [record["id"]], at=PUSHED_AT_STAMP, store=store)


@pytest.fixture(params=BACKENDS)
def backend(request, env):
    """Run the test body on each real inbox backend, in turn."""
    env.set("SCITEX_CARDS_INBOX_BACKEND", request.param)
    return {"name": request.param, "env": env}


@pytest.fixture()
def store(backend, tmp_path):
    """A real store path on the currently-selected backend."""
    return tmp_path / "tasks.yaml"


@pytest.fixture()
def unconfirmed(store):
    """The outage: two pushes, no confirmations, judged five hours later."""
    _push_two(store)
    return check_delivery_confirmed(AGENT, store, now=LATER)


@pytest.fixture()
def confirmed(store):
    """One record pushed long ago and confirmed by the recipient."""
    record = _enqueue(store)
    record_push(AGENT, [record["id"]], at=PUSHED_AT_STAMP, store=store)
    record_confirmation(AGENT, [record["id"]], at="2026-07-29T07:00:05Z", store=store)
    return check_delivery_confirmed(AGENT, store, now=LATER)


# --------------------------------------------------------------------------- #
# the incident shape: pushed, never confirmed                                  #
# --------------------------------------------------------------------------- #
def test_a_pushed_but_unconfirmed_notification_fails_the_check(unconfirmed):
    # Arrange
    # Act
    verdict = unconfirmed["ok"]
    # Assert
    assert verdict is False


def test_the_failure_counts_every_unconfirmed_notification(unconfirmed):
    # Arrange
    # Act
    detail = unconfirmed["detail"]
    # Assert
    assert "2 notification(s) were PUSHED and never CONFIRMED" in detail


def test_the_failure_reports_how_long_the_oldest_push_has_waited(unconfirmed):
    # Arrange — five hours between the stamp and this test's `now`.
    # Act
    detail = unconfirmed["detail"]
    # Assert
    assert "18000s ago" in detail


def test_the_hint_names_the_allowlist_as_the_likely_cause(unconfirmed):
    # Arrange — an operator cannot act on "delivery is broken".
    # Act
    hint = unconfirmed["hint"]
    # Assert
    assert "--dangerously-load-development-channels" in hint


def test_the_hint_names_the_agent_spec_field_an_operator_edits(unconfirmed):
    # Arrange
    # Act
    hint = unconfirmed["hint"]
    # Assert
    assert "channels:" in hint


def test_the_hint_says_which_check_to_read_to_confirm_the_cause(unconfirmed):
    # Arrange — "how to check" is half of an actionable hint.
    # Act
    hint = unconfirmed["hint"]
    # Assert
    assert "channel_reaches_session" in hint


def test_the_hint_names_the_other_possible_cause_too(unconfirmed):
    # Arrange — a name match still leaves a consumer that never confirms.
    # Act
    hint = unconfirmed["hint"]
    # Assert
    assert "ack_notifications" in hint


def test_the_hint_says_a_restart_is_required(unconfirmed):
    # Arrange — the allowlist is read at launch, so editing alone changes nothing.
    # Act
    hint = unconfirmed["hint"]
    # Assert
    assert "RESTART" in hint


# --------------------------------------------------------------------------- #
# a confirmed notification is not a fault                                      #
# --------------------------------------------------------------------------- #
def test_a_confirmed_notification_passes_the_check(confirmed):
    # Arrange
    # Act
    verdict = confirmed["ok"]
    # Assert
    assert verdict is True


def test_a_passing_delivery_check_carries_no_hint(confirmed):
    # Arrange — a hint on a passing check is noise.
    # Act
    hint = confirmed["hint"]
    # Assert
    assert hint is None


def test_a_push_still_inside_the_grace_window_is_not_yet_a_fault(store):
    # Arrange
    record = _enqueue(store)
    record_push(AGENT, [record["id"]], at=PUSHED_AT_STAMP, store=store)
    soon = _dt.datetime(2026, 7, 29, 7, 1, 0, tzinfo=_dt.timezone.utc)
    # Act
    result = check_delivery_confirmed(AGENT, store, now=soon)
    # Assert
    assert result["ok"] is True


def test_the_grace_window_is_long_enough_to_survive_ordinary_lag():
    # Arrange — a healthy consumer confirms within one 5s poll interval.
    # Act
    grace = PUSH_CONFIRM_GRACE_SECONDS
    # Assert
    assert grace >= 300


# --------------------------------------------------------------------------- #
# UNKNOWN: no evidence is not the same answer as no problem                    #
# --------------------------------------------------------------------------- #
def test_an_inbox_the_drain_never_touched_reports_unknown(store):
    # Arrange — records exist, but nothing has ever been pushed.
    _enqueue(store)
    # Act
    result = check_delivery_confirmed(AGENT, store, now=LATER)
    # Assert
    assert result["ok"] is None


def test_the_never_drained_case_says_it_is_unknown_in_words(store):
    # Arrange
    _enqueue(store)
    # Act
    result = check_delivery_confirmed(AGENT, store, now=LATER)
    # Assert
    assert result["detail"].startswith("unknown:")


def test_the_never_drained_case_still_hands_back_a_next_step(store):
    # Arrange
    _enqueue(store)
    # Act
    result = check_delivery_confirmed(AGENT, store, now=LATER)
    # Assert
    assert result["hint"]


def test_an_empty_inbox_reports_unknown_rather_than_healthy(store):
    # Arrange — nothing enqueued at all.
    # Act
    result = check_delivery_confirmed(AGENT, store, now=LATER)
    # Assert
    assert result["ok"] is None


def test_an_unresolved_agent_id_reports_unknown(store):
    # Arrange
    # Act
    result = check_delivery_confirmed(None, store, now=LATER)
    # Assert
    assert result["ok"] is None


def _break_the_inbox(backend, store, tmp_path):
    """Make the file backend's inbox genuinely unreadable: a sidecar holding
    bytes that are not JSON. Does not depend on file permissions, so it does
    not silently succeed when the suite runs as root.
    """
    from scitex_cards._inbox import _INBOXES_FILENAME

    sidecar = store.parent / _INBOXES_FILENAME
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text("{ this is not json", encoding="utf-8")
    return None


@pytest.fixture()
def unreadable_inbox(backend, store, tmp_path):
    """A real push, and THEN an inbox that can no longer be read.

    The push comes first on purpose: without it the check would take the
    never-drained branch and report UNKNOWN for the wrong reason, passing this
    test while measuring nothing.
    """
    record = _enqueue(store)
    record_push(AGENT, [record["id"]], at=PUSHED_AT_STAMP, store=store)
    override = _break_the_inbox(backend["name"], store, tmp_path)
    if override:
        backend["env"].set(*override)
    return check_delivery_confirmed(AGENT, store, now=LATER)


def test_an_unreadable_inbox_reports_unknown_rather_than_healthy(unreadable_inbox):
    # Arrange — the doctor must not pass a patient it could not examine.
    # Act
    verdict = unreadable_inbox["ok"]
    # Assert
    assert verdict is None


def test_an_unreadable_inbox_says_it_could_not_read_it(unreadable_inbox):
    # Arrange
    # Act
    detail = unreadable_inbox["detail"]
    # Assert
    assert "could not be read" in detail


def test_an_unreadable_inbox_still_hands_back_a_next_step(unreadable_inbox):
    # Arrange
    # Act
    hint = unreadable_inbox["hint"]
    # Assert
    assert hint


# --------------------------------------------------------------------------- #
# wired into the doctor                                                        #
# --------------------------------------------------------------------------- #
def _named_check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_the_doctor_runs_the_delivery_check(store):
    # Arrange
    # Act
    report = health(store=store, agent_id=AGENT)
    # Assert
    assert _named_check(report, "delivery_confirmed")


def test_the_doctor_reports_the_outage_as_a_failing_check(store):
    # Arrange — the same two-record outage, reached through health() this time.
    _push_two(store)
    # Act
    report = health(store=store, agent_id=AGENT)
    # Assert
    assert _named_check(report, "delivery_confirmed")["ok"] is False


def test_the_doctor_marks_the_whole_run_unhealthy_on_that_outage(store):
    # Arrange
    _push_two(store)
    # Act
    report = health(store=store, agent_id=AGENT)
    # Assert
    assert report["ok"] is False


def test_an_unknown_delivery_check_does_not_mark_the_run_unhealthy(store):
    # Arrange — nothing pushed, so the check cannot tell; that is not a fault.
    _enqueue(store)
    # Act
    report = health(store=store, agent_id=AGENT)
    # Assert
    assert _named_check(report, "delivery_confirmed")["ok"] is None


# EOF
