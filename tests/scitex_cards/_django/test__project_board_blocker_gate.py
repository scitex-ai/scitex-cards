#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The BLOCKER GATE: a card may not be filed `blocked` without naming its gate.

WRITTEN BEFORE THE FIX, deliberately. Every test below FAILS on 79a0bf0a, and the
failure output is the evidence that they test something real rather than
restating the implementation:

  * the create form offers `blocked` and has no blocker field at all, so the page
    can file a card the store's own validator calls incomplete. Measured in CI
    from this suite's own store-backed write:
      TOLERATED (write-side): task 'ship-the-slice-...' is 'blocked' but names no
      blocker. A blocked card must state its gate so someone can clear it.
  * the store TOLERATES it, so nothing fails — the cost is a warning per write and
    a card nobody can clear without editing it by hand.

The vocabulary is the store's (`VALID_BLOCKERS`); this file asks for it rather than
restating it, because a second list is a second thing to forget.
"""

from __future__ import annotations

from scitex_cards._django.handlers import project_board as pb


class _User:
    def __init__(self, name: str = "alice"):
        self._name = name
        self.is_staff = False
        self.is_authenticated = True

    def get_username(self) -> str:
        return self._name


class _Request:
    def __init__(self, params=None):
        self.user = _User()
        self.GET = params or {}
        self.session = {}


class _Recorder:
    """A hand-written stand-in for the store writer, recording what it was sent."""

    def __init__(self):
        self.calls = []

    def __call__(self, **fields):
        self.calls.append(fields)
        return {"id": fields.get("id")}


def _card(blocker=None):
    row = {
        "id": "alice-a",
        "title": "Alice A",
        "status": "in_progress",
        "project": "proj-alpha",
        "assignee": "alice",
        "agent": "alice",
        "created_by": "alice",
        "priority": 2,
    }
    if blocker is not None:
        row["blocker"] = blocker
    return row


def _ready():
    """A resolved READY state — the shape the view hands the writer paths."""
    return pb.BoardState(state=pb.READY, principal="alice", is_staff=False, project="proj-alpha")


def _board(blocker=None):
    return pb.BoardState(
        state=pb.READY, principal="alice", is_staff=False, project="proj-alpha",
        rows=(_card(blocker),),
    )


def _request(path="/projects"):
    from django.test import RequestFactory

    request = RequestFactory().get(path, HTTP_HOST="127.0.0.1")
    request.user = _User()
    return request


def _render(state):
    return pb.render_project_board(_request(), state, host_picker_available=False)


def _card_page(state):
    return pb.render_project_card(_request("/projects/alice-a"), state, "alice-a")


def _row_with_blocker(blocker):
    return _card(blocker)


# --- the vocabulary comes from the store ------------------------------------


def test_the_blocker_vocabulary_is_the_stores_own():
    """A page-local list would drift from the validator that gates the write."""
    # Arrange
    from scitex_cards._task import VALID_BLOCKERS

    # Act
    asked_for = pb.canonical_blockers()
    # Assert
    assert asked_for == tuple(VALID_BLOCKERS)


# --- create ------------------------------------------------------------------


def test_create_refuses_blocked_without_a_gate():
    """The defect itself: a `blocked` card with no gate is work nobody can clear."""
    # Arrange
    form = {"title": "Ship the slice", "status": "blocked"}
    # Act
    result = pb.create_card(_ready(), form, add=_Recorder())
    # Assert
    assert result.ok is False


def test_the_refusal_says_what_to_do():
    """A refusal a reader cannot act on is only half a refusal."""
    # Arrange
    form = {"title": "Ship the slice", "status": "blocked"}
    # Act
    result = pb.create_card(_ready(), form, add=_Recorder())
    # Assert
    assert "blocker" in result.error.lower()


def test_create_accepts_blocked_with_a_canonical_gate():
    """With a gate named, `blocked` is a perfectly good status."""
    # Arrange
    recorder = _Recorder()
    form = {"title": "Ship the slice", "status": "blocked", "blocker": "dependency"}
    # Act
    result = pb.create_card(_ready(), form, add=recorder)
    # Assert
    assert (result.ok, recorder.calls[0]["blocker"]) == (True, "dependency")


def test_create_refuses_a_blocker_outside_the_vocabulary():
    """A free-text gate is a gate the next reader cannot filter or count."""
    # Arrange
    form = {"title": "Ship the slice", "status": "blocked", "blocker": "waiting-ish"}
    # Act
    result = pb.create_card(_ready(), form, add=_Recorder())
    # Assert
    assert result.ok is False


def test_a_card_that_is_not_blocked_needs_no_gate():
    """The rule is about `blocked`, not about the form."""
    # Arrange
    form = {"title": "Ship the slice", "status": "in_progress"}
    # Act
    result = pb.create_card(_ready(), form, add=_Recorder())
    # Assert
    assert result.ok is True


# --- update ------------------------------------------------------------------


def test_update_refuses_blocked_when_neither_the_form_nor_the_row_has_a_gate():
    """A card moved to `blocked` by the inline select must name its gate too."""
    # Arrange
    form = {"card_id": "alice-a", "status": "blocked"}
    # Act
    result = pb.update_card(_board(), form, update=_Recorder())
    # Assert
    assert result.ok is False


def test_update_accepts_blocked_when_the_row_already_names_a_gate():
    """A card that is ALREADY blocked keeps its gate: asking again is friction."""
    # Arrange
    row = _row_with_blocker("operator-decision")
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False,
                          project="proj-alpha", rows=(row,))
    form = {"card_id": "alice-a", "status": "blocked"}
    # Act
    result = pb.update_card(state, form, update=_Recorder())
    # Assert
    assert result.ok is True


def test_update_can_set_the_gate_on_a_card_that_had_none():
    """The way out of a gateless card is to name the gate."""
    # Arrange
    recorder = _Recorder()
    form = {"card_id": "alice-a", "status": "blocked", "blocker": "compute"}
    # Act
    result = pb.update_card(_board(), form, update=recorder)
    # Assert
    assert (result.ok, recorder.calls[0]["blocker"]) == (True, "compute")


def test_update_refuses_a_gate_outside_the_vocabulary():
    """Same vocabulary check as create."""
    # Arrange
    form = {"card_id": "alice-a", "status": "blocked", "blocker": "someday"}
    # Act
    result = pb.update_card(_board(), form, update=_Recorder())
    # Assert
    assert result.ok is False


# --- the forms ----------------------------------------------------------------


def test_the_create_form_offers_the_canonical_gates():
    """The control the reader needs must be on the page, not in the error text."""
    # Arrange
    state = _board()
    # Act
    body = _render(state).content.decode()
    # Assert
    assert 'data-stx-create-blocker' in body


def test_the_edit_form_offers_the_gates_and_preselects_the_current_one():
    """An editor that hides the current value invites a silent overwrite."""
    # Arrange
    row = _row_with_blocker("agent-wait")
    state = pb.BoardState(state=pb.READY, principal="alice", is_staff=False,
                          project="proj-alpha", rows=(row,))
    # Act
    body = _card_page(state=state).content.decode()
    # Assert
    assert (f'<option value="agent-wait" selected>' in body, 'data-stx-edit-blocker' in body) == (True, True)


# --- leaving `blocked` clears the gate (reviewer blocker #1, second half) ----


def test_leaving_blocked_clears_the_stale_gate():
    """The store's own gate rule is ONE-DIRECTIONAL: a blocked card must name a
    gate, and nothing forces the other direction. So a card moved back to
    `in_progress` keeps a blocker it is no longer gated by, and the board then
    reports a state that does not exist. The page must keep the direction the
    validator does not enforce."""
    # Arrange
    recorder = _Recorder()
    state = _board(blocker="operator-decision")
    form = {"card_id": "alice-a", "status": "in_progress"}
    # Act
    result = pb.update_card(state, form, update=recorder)
    # Assert
    assert (result.ok, recorder.calls[0].get("blocker")) == (True, "")


def test_a_card_that_stays_blocked_keeps_its_gate():
    """Clearing on exit must not clear on a no-op: the gate is still the answer
    to "what is this waiting on"."""
    # Arrange
    recorder = _Recorder()
    state = _board(blocker="compute")
    form = {"card_id": "alice-a", "status": "blocked"}
    # Act
    pb.update_card(state, form, update=recorder)
    # Assert
    assert "blocker" not in recorder.calls[0]


def test_an_explicit_gate_is_not_cleared_when_the_status_leaves_blocked():
    """A form that names a gate is stating something on purpose; the page does not
    overrule it."""
    # Arrange
    recorder = _Recorder()
    state = _board(blocker="compute")
    form = {"card_id": "alice-a", "status": "in_progress", "blocker": "compute"}
    # Act
    pb.update_card(state, form, update=recorder)
    # Assert
    assert recorder.calls[0]["blocker"] == "compute"
