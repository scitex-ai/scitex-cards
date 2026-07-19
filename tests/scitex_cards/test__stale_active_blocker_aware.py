#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The stale sweep must not nudge an owner about work they cannot move.

Regression cover for the 2026-07-12 finding: ``detect_stale_active`` keyed on
STATUS alone (``{in_progress, blocked}``) and ignored the BLOCKER, so every
blocked card nudged its owner every 2 h — including cards blocked on a
dependency, a compute job, another agent, or an operator decision. All 8 of
scitex-todo's own "stale" cards were externally blocked; not one was
actionable. 12 identical nudges a day about work you are powerless to advance
is not a signal, it is training to ignore the channel — which is precisely how
the REAL nudge gets missed.

The fix partitions the ``blocked`` rows:
  * owner-actionable  -> the TIGHT clock ("why haven't you moved this?")
  * externally blocked -> the LENIENT clock ("has your blocker cleared?")
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards._stale_active import (
    EXTERNAL_BLOCKERS,
    blocked_external_nudge_line,
    detect_blocked_external,
    detect_stale_active,
    is_externally_blocked,
    is_owner_actionable,
    stale_active_nudge_line,
)

NOW = _dt.datetime(2026, 7, 12, 12, 0, 0, tzinfo=_dt.timezone.utc)
LONG_AGO = "2026-07-01T00:00:00Z"  # ~11 days -> stale on ANY threshold here


def _card(cid: str, status: str, blocker: str | None = None, **kw) -> dict:
    t = {
        "id": cid,
        "title": cid,
        "status": status,
        "agent": "alice",
        "last_activity": kw.pop("last_activity", LONG_AGO),
    }
    if blocker is not None:
        t["blocker"] = blocker
    t.update(kw)
    return t


# --------------------------------------------------------------------------
# The classifier.
# --------------------------------------------------------------------------


#: The two classifier predicates are complements over the ``blocked`` rows, so
#: every case below is asserted twice — once as "is this outside the owner's
#: control?" and once as "is this the owner's move?". They are split into
#: sibling tests rather than doubled up, because when the partition breaks it
#: matters WHICH half broke: a false `is_externally_blocked` silences a real
#: nudge, while a false `is_owner_actionable` restores the 12-a-day spam.
@pytest.mark.parametrize("blocker", sorted(EXTERNAL_BLOCKERS))
def test_every_external_blocker_is_outside_the_owners_control(blocker):
    # Arrange
    card = _card("c", "blocked", blocker)
    # Act
    external = is_externally_blocked(card)
    # Assert
    assert external is True


@pytest.mark.parametrize("blocker", sorted(EXTERNAL_BLOCKERS))
def test_every_external_blocker_makes_the_card_un_actionable(blocker):
    # Arrange
    card = _card("c", "blocked", blocker)
    # Act
    actionable = is_owner_actionable(card)
    # Assert — the owner cannot move it, so the tight clock must not chase them.
    assert actionable is False


def test_legacy_dep_alias_is_classified_external_not_dropped_to_tight_clock():
    """``dep`` is the legacy spelling of ``dependency``.

    A not-yet-normalized row must NOT fall through to the tight clock — that
    would nudge the owner every 2 h purely because of a spelling variant.
    """
    # Arrange
    card = _card("c", "blocked", "dep")
    # Act
    external = is_externally_blocked(card)
    # Assert
    assert external is True


def test_blocked_with_no_blocker_named_is_not_external():
    # Arrange
    card = _card("c", "blocked")
    # Act
    external = is_externally_blocked(card)
    # Assert — nobody named a wall, so none is assumed on the owner's behalf.
    assert external is False


def test_blocked_with_no_blocker_named_stays_owner_actionable():
    # Arrange
    card = _card("c", "blocked")
    # Act
    actionable = is_owner_actionable(card)
    # Assert — nobody said WHY it is blocked, and saying so IS the owner's job.
    assert actionable is True


def test_blocked_none_is_not_external():
    # Arrange
    card = _card("c", "blocked", "none")
    # Act
    external = is_externally_blocked(card)
    # Assert — an explicit ``none`` is the absence of a wall, not a wall.
    assert external is False


def test_blocked_none_is_owner_actionable():
    # Arrange
    card = _card("c", "blocked", "none")
    # Act
    actionable = is_owner_actionable(card)
    # Assert — "I looked, there is no blocker" makes it the owner's move.
    assert actionable is True


def test_in_progress_is_owner_actionable():
    # Arrange
    card = _card("c", "in_progress")
    # Act
    actionable = is_owner_actionable(card)
    # Assert
    assert actionable is True


def test_done_is_not_active_at_all():
    # Arrange
    card = _card("c", "done")
    # Act
    actionable = is_owner_actionable(card)
    # Assert
    assert actionable is False


# --------------------------------------------------------------------------
# The two sweeps.
# --------------------------------------------------------------------------


def test_tight_sweep_never_reports_an_externally_blocked_card_however_old():
    """The core regression: an owner is not nagged about a wall they cannot move."""
    # Arrange
    tasks = [_card("blocked-on-dep", "blocked", "dependency")]
    # Act
    got = detect_stale_active(tasks, now=NOW)
    # Assert
    assert got == {}


def test_tight_sweep_still_reports_forgotten_in_progress_work():
    """The true signal must survive the fix — this is what the sweep is FOR."""
    # Arrange
    tasks = [_card("forgotten", "in_progress")]
    # Act
    got = detect_stale_active(tasks, now=NOW)
    # Assert
    assert [c.id for c in got["alice"]] == ["forgotten"]


def test_tight_sweep_reports_blocked_with_no_reason_given():
    # Arrange
    tasks = [_card("why-blocked", "blocked")]
    # Act
    got = detect_stale_active(tasks, now=NOW)
    # Assert — naming the gate is the owner's own move, so chase them for it.
    assert [c.id for c in got["alice"]] == ["why-blocked"]


def test_lenient_sweep_reports_the_externally_blocked_card():
    # Arrange
    tasks = [_card("blocked-on-dep", "blocked", "dependency")]
    # Act
    got = detect_blocked_external(tasks, now=NOW)
    # Assert
    assert [c.id for c in got["alice"]] == ["blocked-on-dep"]


def test_lenient_sweep_holds_fire_inside_its_threshold():
    """Blocked an hour ago is not yet worth a "has it cleared?" ping."""
    # Arrange
    fresh = _card(
        "just-blocked",
        "blocked",
        "dependency",
        last_activity="2026-07-12T11:30:00Z",  # 30 min before NOW
    )
    # Act
    got = detect_blocked_external([fresh], now=NOW)
    # Assert
    assert got == {}


def test_lenient_sweep_ignores_in_progress():
    # Arrange
    tasks = [_card("wip", "in_progress")]
    # Act
    got = detect_blocked_external(tasks, now=NOW)
    # Assert — in_progress is the tight sweep's business, never this one's.
    assert got == {}


# --------------------------------------------------------------------------
# The invariant that makes the split safe.
# --------------------------------------------------------------------------


#: The invariant that makes the split safe, asserted four ways over ONE board
#: covering every blocked shape. No card may appear in BOTH sweeps: if they
#: overlapped, the "fix" would just double the noise it set out to remove — the
#: owner would get nagged on the tight clock AND asked about the blocker on the
#: lenient one, for the same card. Disjointness alone is not enough, so the
#: exact membership of each half is pinned too (an empty sweep is trivially
#: disjoint from everything), plus that a terminal card joins neither.
def _every_blocked_shape():
    return [
        _card("wip", "in_progress"),
        _card("blocked-unexplained", "blocked"),
        _card("blocked-none", "blocked", "none"),
        _card("blocked-dep", "blocked", "dependency"),
        _card("blocked-dep-legacy", "blocked", "dep"),
        _card("blocked-op", "blocked", "operator-decision"),
        _card("blocked-compute", "blocked", "compute"),
        _card("blocked-agent", "blocked", "agent-wait"),
        _card("done", "done"),
    ]


def _tight_ids(tasks):
    return {c.id for v in detect_stale_active(tasks, now=NOW).values() for c in v}


def _lenient_ids(tasks):
    return {c.id for v in detect_blocked_external(tasks, now=NOW).values() for c in v}


def test_the_two_sweeps_never_double_report_a_card():
    # Arrange
    tasks = _every_blocked_shape()
    # Act
    overlap = _tight_ids(tasks) & _lenient_ids(tasks)
    # Assert — an overlap would double the noise the split exists to remove.
    assert overlap == set(), "a card was reported by BOTH sweeps"


def test_the_tight_sweep_claims_exactly_the_owner_actionable_rows():
    # Arrange
    tasks = _every_blocked_shape()
    # Act
    tight = _tight_ids(tasks)
    # Assert
    assert tight == {"wip", "blocked-unexplained", "blocked-none"}


def test_the_lenient_sweep_claims_exactly_the_externally_blocked_rows():
    # Arrange
    tasks = _every_blocked_shape()
    # Act
    lenient = _lenient_ids(tasks)
    # Assert
    assert lenient == {
        "blocked-dep",
        "blocked-dep-legacy",
        "blocked-op",
        "blocked-compute",
        "blocked-agent",
    }


def test_a_done_card_is_reported_by_neither_sweep():
    # Arrange
    tasks = _every_blocked_shape()
    # Act
    reported = _tight_ids(tasks) | _lenient_ids(tasks)
    # Assert — the partition covers the ACTIVE rows only.
    assert "done" not in reported


# --------------------------------------------------------------------------
# Wording — the lines must say something the recipient can act on.
# --------------------------------------------------------------------------


#: Telling an owner to "reconcile" a card they cannot move is an instruction
#: they cannot follow. The blocker-check line must instead ASK whether the wall
#: is still up — and it must say which card, under a tag the reader can filter
#: on. The four tests below split those four requirements over one composed
#: line, because "the wording is wrong" is only actionable if it says HOW.
def _blocked_check_line():
    cards = detect_blocked_external(
        [_card("blocked-on-dep", "blocked", "dependency")], now=NOW
    )["alice"]
    return blocked_external_nudge_line("alice", cards)


#: The stale-active line used to claim it covered "in_progress/blocked", which
#: became a lie once externally-blocked cards were excluded. A nudge that
#: misdescribes its own scope teaches the reader to distrust it, so the tag,
#: the honest scope wording, and the card id are each pinned separately.
def _stale_active_line():
    cards = detect_stale_active([_card("forgotten", "in_progress")], now=NOW)["alice"]
    return stale_active_nudge_line("alice", cards)


def test_blocked_check_line_carries_its_own_tag():
    # Arrange
    line = _blocked_check_line()
    # Act
    tag = "BLOCKED-CHECK"
    # Assert — a distinct tag is what lets a reader triage the two sweeps apart.
    assert tag in line


def test_blocked_check_line_asks_whether_the_blocker_cleared():
    # Arrange
    line = _blocked_check_line()
    # Act
    question = "has the blocker cleared?"
    # Assert — a question they CAN answer, not a task they cannot do.
    assert question in line


def test_blocked_check_line_names_the_blocked_card():
    # Arrange
    line = _blocked_check_line()
    # Act
    card_id = "blocked-on-dep"
    # Assert
    assert card_id in line


def test_blocked_check_line_does_not_order_a_reconcile():
    # Arrange
    line = _blocked_check_line()
    # Act
    tight_sweep_verb = "reconcile"
    # Assert — it must NOT borrow the tight sweep's reprimanding verb.
    assert tight_sweep_verb not in line


def test_stale_active_line_carries_its_own_tag():
    # Arrange
    line = _stale_active_line()
    # Act
    tag = "STALE-ACTIVE"
    # Assert
    assert tag in line


def test_stale_active_line_names_its_narrowed_scope_honestly():
    # Arrange
    line = _stale_active_line()
    # Act
    scope_wording = "you can act on now"
    # Assert — the line describes the set it ACTUALLY swept.
    assert scope_wording in line


def test_stale_active_line_names_the_forgotten_card():
    # Arrange
    line = _stale_active_line()
    # Act
    card_id = "forgotten"
    # Assert
    assert card_id in line


def test_line_composers_are_still_importable_from_the_original_module():
    """The split moved them to ``_stale_active_lines``; the re-export must hold
    so notifyd / the CLI / out-of-tree importers keep working unchanged."""
    # Arrange
    from scitex_cards._stale_active import (  # noqa: F401
        NUDGE_ID_CAP,
        pending_backlog_nudge_line,
    )

    # Act
    cap = NUDGE_ID_CAP
    # Assert — the re-exported name resolves to the real constant, not a stub.
    assert cap == 12
