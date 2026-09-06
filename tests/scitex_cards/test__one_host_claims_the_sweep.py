#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One board should produce one digest, however many hosts sweep it.

MEASURED 2026-09-06 on the live fleet: BACKLOG digests arrived stamped
"[computed on scitex-compute-01]", "[computed on scitex-compute-03]" and
"[computed on scitex-compute-04]" within minutes of each other, and a
BLOCKED-CHECK from one host landed in the same minute as a BACKLOG from
another. Three notifyd daemons sweep one shared store; each keeps its cadence
in a local variable reset on every restart, so their phases collide and every
owner is nudged two or three times for one board.

A CLAIM RATHER THAN A HELD LOCK, and the tests below are shaped around why.
The cadence stamp is the state, so nothing depends on a live owner: a crashed
winner never refreshes it, the stamp ages out, and the next host claims. A held
lock owned by a WEDGED winner would block every other host forever while
logging like a healthy quiet sweep — trading visible duplicates for a silent
outage, which is the worse of the two by a distance.

THE SCOPE TEST IS THE ONE THAT LOOKS LIKE HOUSEKEEPING AND IS NOT.
``save_sections`` soft-deletes every row of a scope absent from its payload, so
a claim sharing the nudge scope is tombstoned by the next ``save_nudge_state``
— silently, after which every host sweeps again while the code still looks
fixed. That is the likeliest way a correct-looking implementation of this
disarms itself, so it is pinned rather than trusted.
"""

import datetime as _dt

from scitex_cards._db_sweep_state import (
    SCOPE_NUDGES,
    claim_sweep,
    load_sections,
    save_sections,
)

SWEEP = "pending-backlog"
CADENCE = 30.0


def _stamp(minutes_from_now: float) -> str:
    when = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=minutes_from_now)
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_the_first_host_to_ask_gets_the_sweep(new_store):
    # Arrange
    store = new_store()
    # Act
    got = claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Assert
    assert got is True


def test_a_second_host_asking_immediately_is_refused(new_store):
    """THE DEFECT. Three daemons ticking together must not all sweep."""
    # Arrange
    store = new_store()
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Act
    second = claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Assert
    assert second is False


def test_a_third_host_is_refused_too(new_store):
    """Pinned separately: the fleet has three notifyds, not two."""
    # Arrange
    store = new_store()
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Act
    third = claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Assert
    assert third is False


def test_the_sweep_is_claimable_again_after_its_cadence(new_store):
    """THE OTHER HALF: a claim must expire, or the sweep stops forever.

    Asked from a moment past the cadence rather than by waiting, so the test
    pins the arithmetic instead of the clock.
    """
    # Arrange
    store = new_store()
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Act
    later = claim_sweep(
        SWEEP, cadence_minutes=CADENCE, store=store, now=_stamp(CADENCE + 1)
    )
    # Assert
    assert later is True


def test_a_different_sweep_is_not_blocked_by_this_one(new_store):
    """The lock is keyed on the sweep NAME; backlog and blocked-check are two."""
    # Arrange
    store = new_store()
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Act
    other = claim_sweep("blocked-check", cadence_minutes=CADENCE, store=store)
    # Assert
    assert other is True


def test_a_claim_survives_a_nudge_state_write(new_store):
    """THE DISARM TEST. Without its own scope this goes red — and silently.

    ``save_sections`` replaces a scope wholesale, soft-deleting anything absent
    from its payload. A claim written into the nudge scope would be tombstoned
    by the very next nudge-state write, after which every host sweeps again
    while every line of this feature still looks correct.
    """
    # Arrange
    store = new_store()
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    save_sections(SCOPE_NUDGES, {"some-section": {"k": {"v": 1}}}, store=store)
    # Act
    still_claimed = claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Assert
    assert still_claimed is False


def test_the_nudge_state_is_unharmed_by_a_claim(new_store):
    """And the converse: claiming must not disturb the state it lives beside."""
    # Arrange
    store = new_store()
    save_sections(SCOPE_NUDGES, {"some-section": {"k": {"v": 1}}}, store=store)
    claim_sweep(SWEEP, cadence_minutes=CADENCE, store=store)
    # Act
    sections = load_sections(SCOPE_NUDGES, ("some-section",), store=store)
    # Assert
    assert sections["some-section"]["k"]["v"] == 1


# EOF
