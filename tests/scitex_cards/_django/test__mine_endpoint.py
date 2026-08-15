#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/mine`` SERVES ONE PERSON'S CARDS, and refuses rather than guessing.

The data half of the phone view (card
``cards-gui-phone-view-own-cards-20260814``). Two properties matter more than
the rest, and both are about what the endpoint must NOT do:

1. IT MUST NOT SERVE SOMEBODY ELSE'S CARDS. A "my cards" view whose filter
   silently widens is not a broken feature -- it is a disclosure, and on a
   public scitex.ai it looks identical to the feature working.
2. IT MUST NOT ANSWER AN UNIDENTIFIED CALLER WITH A BOARD. The refusal is
   typed so the page can explain itself; a 200 with everything in it would be
   the same disclosure wearing a success code.

Real SQLite stores seeded through the suite's own ``seed_db_from_doc``, real
``RequestFactory`` requests, real signed cookies. No mocks anywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.core import signing  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from scitex_cards._django._board_login import (  # noqa: E402
    COOKIE_NAME,
    SIGNING_SALT,
)
from scitex_cards._django.handlers.mine import mine_view  # noqa: E402

_ME = "ywatanabe"
_SOMEONE_ELSE = "another-person"

#: A board holding cards for two different people, plus one closed card of
#: mine. Every assertion below is about which of these six come back.
_TWO_PEOPLES_CARDS = {
    "tasks": [
        {
            "id": "mine-active",
            "title": "Mine, in progress",
            "status": "in_progress",
            "assignee": _ME,
            "priority": 1,
            "last_activity": "2026-08-14T10:00:00Z",
        },
        {
            "id": "mine-blocked",
            "title": "Mine, blocked",
            "status": "blocked",
            "blocker": "operator-decision",
            "assignee": _ME,
            "priority": 2,
            "last_activity": "2026-08-14T09:00:00Z",
        },
        {
            "id": "mine-deferred",
            "title": "Mine, deferred",
            "status": "deferred",
            "assignee": _ME,
            "last_activity": "2026-08-13T09:00:00Z",
        },
        {
            "id": "mine-done",
            "title": "Mine, finished last month",
            "status": "done",
            "assignee": _ME,
            "last_activity": "2026-07-14T09:00:00Z",
        },
        {
            "id": "theirs-active",
            "title": "Not mine, in progress",
            "status": "in_progress",
            "assignee": _SOMEONE_ELSE,
            "priority": 1,
            "last_activity": "2026-08-14T11:00:00Z",
        },
        {
            "id": "theirs-blocked",
            "title": "Not mine, blocked",
            "status": "blocked",
            # A blocked card must name its gate -- the store's validator warns
            # on read otherwise, and seed data that trips a real validator is
            # a fixture describing a board that could not exist.
            "blocker": "dependency",
            "assignee": _SOMEONE_ELSE,
            "last_activity": "2026-08-14T08:00:00Z",
        },
    ]
}


def _as(name: str | None, *, query: str = ""):
    """A request identified as ``name`` via a REAL signed board cookie.

    ``None`` builds an unidentified request -- the rung-4 case. Identity comes
    through the cookie rather than the environment so each test carries its own
    subject and no ambient configuration can make one pass for another's
    reason.
    """
    request = RequestFactory().get(f"/mine{query}")
    if name is not None:
        request.COOKIES[COOKIE_NAME] = signing.dumps(
            {"v": 2, "sub": name}, salt=SIGNING_SALT
        )
    return request


def _payload(request) -> dict:
    """Call the real view and decode its body."""
    return json.loads(mine_view(request).content)


@pytest.fixture
def two_peoples_board():
    """A real store holding cards owned by two different people.

    The scratch database comes from the suite's ``_store_env_stays_pinned``
    fixture; this seeds it through the same helper the rest of the _django
    tests use, so the endpoint reads a genuine store rather than a fixture's
    idea of one.
    """
    from conftest import seed_db_from_doc

    db = Path(os.environ["SCITEX_CARDS_DB"])
    seed_db_from_doc(_TWO_PEOPLES_CARDS, db)
    return db


@pytest.fixture(autouse=True)
def no_ambient_identity():
    """Keep ``SCITEX_CARDS_IDENTITY`` out of these tests.

    A developer machine exporting it would identify the "unidentified"
    requests below and turn every refusal assertion green for the wrong
    reason. Restored on teardown.
    """
    from scitex_cards._django._board_identity import ENV_IDENTITY

    original = os.environ.get(ENV_IDENTITY)
    os.environ.pop(ENV_IDENTITY, None)
    yield
    if original is not None:
        os.environ[ENV_IDENTITY] = original


# --- the property the feature exists for ----------------------------------


def test_my_own_cards_come_back(two_peoples_board):
    """The happy path: the three open cards assigned to me."""
    # Arrange
    expected = {"mine-active", "mine-blocked", "mine-deferred"}
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert {c["id"] for c in payload["cards"]} == expected


def test_another_persons_cards_never_come_back(two_peoples_board):
    """THE DISCLOSURE TEST. A widened filter fails HERE, loudly."""
    # Arrange
    theirs = {"theirs-active", "theirs-blocked"}
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert {c["id"] for c in payload["cards"]}.isdisjoint(theirs)


def test_the_other_person_sees_only_their_own(two_peoples_board):
    """SYMMETRY, so the filter cannot be passing by naming one hard-coded owner."""
    # Arrange
    expected = {"theirs-active", "theirs-blocked"}
    # Act
    payload = _payload(_as(_SOMEONE_ELSE))
    # Assert
    assert {c["id"] for c in payload["cards"]} == expected


def test_the_payload_names_the_viewer_it_answered_for(two_peoples_board):
    """So a page (and a bug report) can state whose board it is showing."""
    # Arrange
    expected = _ME
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert payload["viewer"]["name"] == expected


# --- closed work is out of the way by default ------------------------------


def test_finished_cards_are_left_out_by_default(two_peoples_board):
    """The phone answers "what is on my plate", not "what have I ever done"."""
    # Arrange
    closed = "mine-done"
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert closed not in {c["id"] for c in payload["cards"]}


def test_finished_cards_are_available_on_request(two_peoples_board):
    """...but they are not HIDDEN -- ``?closed=1`` includes them."""
    # Arrange
    closed = "mine-done"
    # Act
    payload = _payload(_as(_ME, query="?closed=1"))
    # Assert
    assert closed in {c["id"] for c in payload["cards"]}


def test_asking_for_closed_cards_still_excludes_other_people(two_peoples_board):
    """The ``?closed=1`` branch must not be a second, unfiltered read path."""
    # Arrange
    theirs = {"theirs-active", "theirs-blocked"}
    # Act
    payload = _payload(_as(_ME, query="?closed=1"))
    # Assert
    assert {c["id"] for c in payload["cards"]}.isdisjoint(theirs)


# --- an unidentified caller gets a refusal, not a board --------------------


def test_an_unidentified_request_is_refused(two_peoples_board):
    """403, not 200. The status alone must distinguish the two outcomes."""
    # Arrange
    request = _as(None)
    # Act
    response = mine_view(request)
    # Assert
    assert response.status_code == 403


def test_an_unidentified_request_receives_no_cards_at_all(two_peoples_board):
    """THE ONE THAT MATTERS. The refusal body must not carry the board with it."""
    # Arrange
    request = _as(None)
    # Act
    body = json.loads(mine_view(request).content)
    # Assert
    assert "cards" not in body


def test_a_refusal_names_a_machine_readable_reason(two_peoples_board):
    """The page branches on this to say "not linked yet" vs "no login here"."""
    # Arrange
    request = _as(None)
    # Act
    body = json.loads(mine_view(request).content)
    # Assert
    assert body["reason"] == "anonymous"


def test_a_refusal_explains_itself_to_a_human(two_peoples_board):
    """A refusal a user cannot act on is a dead end, not an answer."""
    # Arrange
    request = _as(None)
    # Act
    body = json.loads(mine_view(request).content)
    # Assert
    assert body["detail"].strip() != ""


# --- ordering, counts and shape -------------------------------------------


def test_work_in_progress_sorts_above_everything_else(two_peoples_board):
    """The phone's first line should be what I am actually doing."""
    # Arrange
    expected = "mine-active"
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert payload["cards"][0]["id"] == expected


def test_blocked_work_sorts_above_deferred_work(two_peoples_board):
    """Stuck work needs a decision; deferred work has already had one."""
    # Arrange
    order = ["mine-active", "mine-blocked", "mine-deferred"]
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert [c["id"] for c in payload["cards"]] == order


def test_the_counts_summarise_the_same_cards_that_were_returned(
    two_peoples_board,
):
    """A summary that disagrees with the list is worse than no summary."""
    # Arrange
    payload = _payload(_as(_ME))
    # Act
    counted = sum(payload["counts"].values())
    # Assert
    assert counted == len(payload["cards"])


def test_the_total_matches_the_cards_returned(two_peoples_board):
    """Same contract as the counts, for the number the page prints in its header."""
    # Arrange
    payload = _payload(_as(_ME))
    # Act
    total = payload["total"]
    # Assert
    assert total == len(payload["cards"])


def test_absent_fields_are_omitted_rather_than_sent_as_null(two_peoples_board):
    """"No deadline" and "this build forgot deadlines" must stay distinguishable."""
    # Arrange
    payload = _payload(_as(_ME))
    # Act
    deferred = next(c for c in payload["cards"] if c["id"] == "mine-deferred")
    # Assert
    assert "priority" not in deferred


def test_a_card_carries_the_fields_the_phone_renders(two_peoples_board):
    """Positive control for the allowlist -- an empty projection would pass above."""
    # Arrange
    required = {"id", "title", "status"}
    # Act
    payload = _payload(_as(_ME))
    # Assert
    assert required <= set(payload["cards"][0])


def test_a_card_body_is_not_shipped_to_the_phone(two_peoples_board):
    """The allowlist exists so mobile data does not pay for note/comment text."""
    # Arrange
    payload = _payload(_as(_ME))
    # Act
    fields = set(payload["cards"][0])
    # Assert
    assert fields.isdisjoint({"note", "comments", "task"})


# --- method contract -------------------------------------------------------


def test_a_write_method_is_rejected(two_peoples_board):
    """READ-ONLY, like every other named GET endpoint on this board."""
    # Arrange
    request = RequestFactory().post("/mine")
    # Act
    response = mine_view(request)
    # Assert
    assert response.status_code == 405


# EOF
