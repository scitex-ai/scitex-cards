#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE BOARD READS THE DATABASE, and refuses OUT LOUD when it cannot.

THE OUTAGE, 2026-07-29. The operator's board served 0 cards for over a day
while 2,654 sat in the canonical database. Nothing was lost and nothing
errored; ``services.get_board`` simply never asked the store::

    store_exists = resolved.exists()                              # the SIDECAR
    tasks = _load_global_tasks(resolved) if store_exists else []   # <- 0 cards

``resolved`` is ``resolve_tasks_path(None)`` — the ``tasks.yaml`` SIDECAR beside
the database, which holds ``groups:`` and nothing else. Its own docstring says
"NOT the store identity ... Card DATA lives in the database." Under SQLite
nothing creates that file, so the gate was permanently shut and the board took
the literal ``else []``. The fail-loud reader (``_read_canonical_db_or_raise``,
which cross-checks its export against ``COUNT(*)`` and raises on any
disagreement) was never entered, so no guard anywhere had an opinion.

WHY THIS IS THE DANGEROUS FAILURE AND NOT MERELY A BUG: the same branch also set
``empty_store=True``, which tells the frontend to render the normal, clean,
zero-card board INSTEAD of the red load-error banner. The result is not a broken
page — it is a healthy-looking empty board, the exact visual signature of the
wipe that took 2,138 cards on 2026-07-19. An error is recoverable and names
itself; a believable empty board is neither.

THE CONTRACT PINNED HERE
------------------------
* A populated store WITHOUT its YAML sidecar serves its cards. This is the live
  board's exact shape, and it is the regression: it fails on the old code.
* A store that cannot be READ raises, and the endpoint answers 500 carrying the
  store's own reason — never 200 with a task list.
* Emptiness is READ, never inferred: a real database holding no cards is a
  legitimate 200 + ``empty_store: true``.
* ``groups:`` still comes from the sidecar, because groups genuinely live
  there. That is the one thing whose absence may honestly render as empty.

RequestFactory against the real views, real SQLite stores, no mocks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers.graph import _graph_cache_reset  # noqa: E402
from scitex_cards._django.services import _reset_cache, get_board  # noqa: E402

_TWO_CARDS = {
    "tasks": [
        {"id": "alpha", "title": "Alpha", "status": "in_progress", "priority": 1},
        {"id": "beta", "title": "Beta", "status": "deferred", "priority": 2},
    ]
}


def _reset_board_caches() -> None:
    """Drop both in-process board caches so each test reads the store afresh."""
    _reset_cache()
    _graph_cache_reset()


def _tasks_payload() -> dict:
    """GET /tasks through the real dispatcher and return the decoded body."""
    request = RequestFactory().get("/tasks")
    return json.loads(views.api_dispatch(request, "tasks").content)


# --- a populated store with NO sidecar: the live board's exact shape ---------


@pytest.fixture
def populated_db_without_sidecar():
    """Cards in the canonical DB; no ``tasks.yaml`` beside it.

    This reproduces production rather than approximating it. The per-test
    scratch database comes from the top-level ``_store_env_stays_pinned``
    fixture; here we seed it and then make sure the sidecar is absent — which
    is what the deleted ``_django_store_identity_file_exists`` autouse fixture
    used to prevent, and precisely why the outage had no failing test.
    """
    from conftest import seed_db_from_doc

    _reset_board_caches()
    db = Path(os.environ["SCITEX_CARDS_DB"])
    seed_db_from_doc(_TWO_CARDS, db)
    sidecar = db.parent / "tasks.yaml"
    if sidecar.exists():
        sidecar.unlink()
    yield db
    _reset_board_caches()


def test_the_sidecar_really_is_absent_in_this_fixture(populated_db_without_sidecar):
    """POSITIVE CONTROL. Without it, the test below could pass for the wrong reason.

    Every assertion in this section is about what happens when the sidecar is
    MISSING. If some other fixture quietly created it, those tests would go
    green while measuring nothing at all — the instrument, not the code.
    """
    # Arrange
    sidecar = populated_db_without_sidecar.parent / "tasks.yaml"
    # Act
    present = sidecar.exists()
    # Assert
    assert present is False


def test_a_populated_store_without_its_sidecar_serves_its_cards(
    populated_db_without_sidecar,
):
    """THE REGRESSION. On the old code this list is empty and nothing complains."""
    # Arrange
    expected = ["alpha", "beta"]
    # Act
    payload = _tasks_payload()
    # Assert
    assert sorted(t["id"] for t in payload["tasks"]) == expected


def test_a_populated_store_without_its_sidecar_is_not_flagged_empty(
    populated_db_without_sidecar,
):
    """``empty_store`` is what suppresses the error banner — it must not lie."""
    # Arrange
    _ = populated_db_without_sidecar
    # Act
    payload = _tasks_payload()
    # Assert
    assert payload["empty_store"] is False


def test_get_board_returns_the_cards_when_only_the_sidecar_is_missing(
    populated_db_without_sidecar,
):
    """Stated at the service layer too: the sidecar does not gate the card read."""
    # Arrange
    _ = populated_db_without_sidecar
    # Act
    board = get_board()
    # Assert
    assert len(board.tasks) == 2


# --- a store that cannot be read: RAISE, never an empty list ----------------


@pytest.fixture
def unreadable_store(env, tmp_path):
    """Point the canonical store at a database that does not exist.

    A missing database is the plainest form of "cannot read the store". The
    package already has the right verdict for it
    (``_read_canonical_db_or_raise``: "A MISSING DB IS NOT AN EMPTY STORE");
    what these tests pin is that the BOARD propagates that verdict instead of
    absorbing it into a blank page.
    """
    _reset_board_caches()
    missing = tmp_path / "no-such-store" / "cards.db"
    env.set("SCITEX_CARDS_DB", str(missing))
    env.set("SCITEX_TODO_DB", str(missing))
    yield missing
    _reset_board_caches()


def test_a_store_that_cannot_be_read_raises(unreadable_store):
    """No empty fallback at the service layer. Refusing is the correct answer."""
    # Arrange
    _ = unreadable_store
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        get_board()


def test_a_store_that_cannot_be_read_answers_500(unreadable_store):
    """An unreadable store is a server-side fault, and it must say so."""
    # Arrange
    request = RequestFactory().get("/tasks")
    # Act
    response = views.api_dispatch(request, "tasks")
    # Assert
    assert response.status_code == 500


def test_the_failure_body_names_the_store_it_could_not_read(unreadable_store):
    """The reason travels to the operator, not just to the journal.

    The board template reads ``payload.error`` off a non-OK response and paints
    it in the load-error panel, so a message here is the difference between "the
    board is down" and a diagnosis. The old code threw this away: it swallowed
    FileNotFoundError into a fixed 400 "No task store found.", and let every
    other load failure escape into an HTML error page the frontend cannot parse.
    """
    # Arrange
    expected_fragment = str(unreadable_store)
    # Act
    payload = _tasks_payload()
    # Assert
    assert expected_fragment in payload["error"]


def test_a_store_that_cannot_be_read_never_answers_with_a_task_list(unreadable_store):
    """THE WIPE SHAPE, WRITTEN AS A TEST.

    200-with-zero-cards is strictly worse than a visible refusal: it looks
    healthy, it is indistinguishable from a real wipe, and a read-modify-write
    against it is how 2,138 cards were lost. There must be no cards key at all.
    """
    # Arrange
    _ = unreadable_store
    # Act
    payload = _tasks_payload()
    # Assert
    assert "tasks" not in payload


# --- emptiness is READ, not inferred ----------------------------------------


@pytest.fixture
def real_but_empty_db():
    """The per-test scratch database, bootstrapped and holding no cards."""
    _reset_board_caches()
    yield Path(os.environ["SCITEX_CARDS_DB"])
    _reset_board_caches()


def test_a_real_store_holding_no_cards_is_served_not_refused(real_but_empty_db):
    """A genuinely empty board is a legitimate state — read, and therefore true."""
    # Arrange
    request = RequestFactory().get("/tasks")
    # Act
    response = views.api_dispatch(request, "tasks")
    # Assert
    assert response.status_code == 200


def test_a_real_store_holding_no_cards_is_flagged_empty(real_but_empty_db):
    """This is the ONLY way ``empty_store`` may become True: by reading zero."""
    # Arrange
    _ = real_but_empty_db
    # Act
    payload = _tasks_payload()
    # Assert
    assert payload["empty_store"] is True


# --- groups: the one thing the sidecar legitimately gates -------------------


@pytest.fixture
def db_with_sidecar_groups():
    """Cards in the database, ``groups:`` in the sidecar beside it."""
    from conftest import seed_db_from_doc

    _reset_board_caches()
    db = Path(os.environ["SCITEX_CARDS_DB"])
    seed_db_from_doc(_TWO_CARDS, db)
    sidecar = db.parent / "tasks.yaml"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        "groups:\n  - id: cluster-one\n    label: Cluster One\n"
        "    projects: [scitex-cards]\n",
        encoding="utf-8",
    )
    yield sidecar
    _reset_board_caches()


def test_groups_still_come_from_the_yaml_sidecar(db_with_sidecar_groups):
    """Splitting the card read off the sidecar must not orphan the group read."""
    # Arrange
    request = RequestFactory().get("/graph")
    # Act
    payload = json.loads(views.api_dispatch(request, "graph").content)
    # Assert
    assert [g["id"] for g in payload["groups"]] == ["cluster-one"]


def test_an_absent_sidecar_yields_no_groups_rather_than_an_error(
    populated_db_without_sidecar,
):
    """Groups may honestly degrade to empty — they LIVE in the file that is gone."""
    # Arrange
    request = RequestFactory().get("/graph")
    # Act
    payload = json.loads(views.api_dispatch(request, "graph").content)
    # Assert
    assert payload["groups"] == []


# EOF
