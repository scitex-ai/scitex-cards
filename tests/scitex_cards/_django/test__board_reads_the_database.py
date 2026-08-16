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
* A store that cannot be READ raises, and the endpoint answers with the store's
  own reason — never 200 with a task list.
* The STATUS distinguishes two different answers, which used to share 500
  (changed 2026-08-06 after scitex-hub measured the cost on live scitex.ai):
  an ABSENT store is a configuration state and answers 4xx with a typed
  ``reason``, while a store that exists and cannot be parsed is a genuine fault
  and stays 5xx. Collapsing them told visitors the product was down, made
  clients retry forever (11 console errors a minute), and left a real outage
  indistinguishable from a deployment nobody had configured.
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


def test_an_absent_store_is_not_reported_as_a_server_fault(unreadable_store):
    """REPLACES ``test_a_store_that_cannot_be_read_answers_500``, deliberately.

    That test said "an unreadable store is a server-side fault, and it must say
    so", and it was right about the second half and wrong about the first.
    scitex-hub measured the cost on live scitex.ai: a signed-in visitor whose
    deployment has no store configured got /graph, /rev and /timeline all 500,
    and the client re-polled into 11 console errors in one minute.

    "No store here" is a CONFIGURATION state, not an outage. Answering 5xx tells
    the visitor the product is down, tells the client to retry forever, and makes
    a real outage indistinguishable from this steady state in 5xx monitoring.

    Inverting a deliberate contract-pin with the reason is the honest response to
    the day it changes — the same move this repo already made for the ``?store=``
    write seam.
    """
    # Arrange
    request = RequestFactory().get("/tasks")

    # Act
    response = views.api_dispatch(request, "tasks")

    # Assert
    assert response.status_code == views.STORE_ABSENT_STATUS


def test_an_absent_store_never_answers_5xx(unreadable_store):
    """The monitoring property, pinned independently of which 4xx we chose.

    If this ever goes 5xx again, a real outage is once more indistinguishable
    from a deployment that was simply never configured — which is the harm, and
    it survives any later change of mind about 403-vs-404.
    """
    # Arrange
    request = RequestFactory().get("/tasks")

    # Act
    response = views.api_dispatch(request, "tasks")

    # Assert
    assert response.status_code < 500


def test_an_absent_store_is_machine_readable_without_string_matching(
    unreadable_store,
):
    """A client must distinguish this from any other 4xx without reading prose.

    The human sentence is free to change and has changed twice; this
    discriminator is the part callers may depend on.
    """
    # Arrange
    request = RequestFactory().get("/tasks")

    # Act
    payload = json.loads(views.api_dispatch(request, "tasks").content)

    # Assert
    assert payload["reason"] == views.STORE_ABSENT_REASON


def test_an_absent_store_still_refuses_rather_than_inventing_a_board(
    unreadable_store,
):
    """THE GUARD IS NOT WEAKENED — only the status code changed.

    This is the fear worth pinning: the guard is what stopped 2,138 cards being
    overwritten on 2026-07-19, and a "fix" that answered 200 with an empty board
    would satisfy every other test here while reintroducing exactly that. An
    empty board must remain impossible to obtain from an absent store.
    """
    # Arrange
    request = RequestFactory().get("/tasks")

    # Act
    response = views.api_dispatch(request, "tasks")

    # Assert
    assert response.status_code != 200


@pytest.fixture
def corrupt_store(env, tmp_path):
    """A store file that EXISTS and is not a database — a real fault, not config.

    The distinction this fixture exists to exercise was measured rather than
    assumed: an ABSENT store raises ``StoreUnavailableError`` while a CORRUPT one
    raises ``DatabaseError`` ("file is not a database"). Had both produced the
    same type, the 4xx branch would have been swallowing genuine corruption and
    reporting it to monitoring as "merely unconfigured" — so this is written with
    real bytes on disk rather than by patching, which would have proven only that
    the patch worked.
    """
    _reset_board_caches()
    broken = tmp_path / "corrupt" / "cards.db"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"this is not a sqlite database at all")
    env.set("SCITEX_CARDS_DB", str(broken))
    yield broken
    _reset_board_caches()


def test_a_genuinely_broken_store_is_still_a_server_fault(corrupt_store):
    """The change must not swallow real outages into a reassuring 4xx.

    Only "there is no store here" is a configuration state. A store that exists
    and cannot be parsed is a fault, and if it answered 4xx this fix would have
    turned the monitoring rail off in the other direction — quieter, and wrong.
    """
    # Arrange
    request = RequestFactory().get("/tasks")

    # Act
    response = views.api_dispatch(request, "tasks")

    # Assert
    assert response.status_code == 500


@pytest.fixture
def unreachable_postgres(env):
    """A PostgreSQL target whose SERVER IS NOT THERE — an outage, not config.

    THIS IS THE CONTROL ``corrupt_store`` COULD NOT BE, and the difference is the
    whole reason :class:`StoreNotProvisionedError` exists. ``corrupt_store``
    raises ``DatabaseError`` — a DIFFERENT type from absence — so it exercises a
    path that was never in doubt and goes green while the path that SHARES the
    type with absence goes untested. Before the subclass, an unreachable server
    and a tenant who never had a store both raised ``StoreUnavailableError``, so
    moving absence off 5xx would have moved a dead database off 5xx with it.

    A control must fail by the SAME MECHANISM as the hazard, not merely in the
    same neighbourhood. So this points at a genuinely closed TCP port and lets
    ``connect()`` fail for real, rather than patching it to raise — patching
    would prove only that the patch worked, and would not notice if a future
    edit made that call site raise the subclass.

    THE PORT IS ALLOCATED, NOT GUESSED. Binding to port 0 and closing yields a
    port the OS has just confirmed is free; a hard-coded number could silently
    be in use on some host and turn this into a test of whatever answered.
    ``connect_timeout`` is set so a DROPping firewall fails the test rather than
    hanging the suite — an unbounded wait is not a passing test, it is no test.
    """
    import socket

    _reset_board_caches()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    target = (
        f"postgresql://scitex_cards@127.0.0.1:{port}/scitex_cards"
        f"?connect_timeout=2"
    )
    env.set("SCITEX_CARDS_DB", target)
    yield port
    _reset_board_caches()


def test_the_unreachable_port_really_refuses(unreachable_postgres):
    """Positive control ON the control: prove nothing is listening there.

    Without this, a port that happened to be occupied would make the outage test
    below pass for the wrong reason — and a test that passes for the wrong reason
    is the failure mode this entire change set is about.
    """
    # Arrange
    import contextlib
    import functools
    import socket

    sock = socket.socket()
    sock.settimeout(2)

    # Act
    dial = functools.partial(sock.connect, ("127.0.0.1", unreachable_postgres))

    # Assert
    with contextlib.closing(sock), pytest.raises(OSError):
        dial()


def test_an_unreachable_postgres_is_still_a_server_fault(unreachable_postgres):
    """A database server that is DOWN must never read as "no store here".

    This is the inversion the subclass prevents. Absence is a configuration state
    that renders onboarding and must not be retried; an unreachable server is an
    OUTAGE that must stay in 5xx monitoring and must be retried. They arrived at
    the view as one type, so classifying absence as 4xx would have dropped real
    outages out of alerting and rendered a setup page over a dead database —
    silently, and silence is indistinguishable from health.

    If a future edit raises ``StoreNotProvisionedError`` at the connect-failure
    site in ``_store_canonical_read``, this test fails. That is its job.
    """
    # Arrange
    request = RequestFactory().get("/tasks")

    # Act
    response = views.api_dispatch(request, "tasks")

    # Assert
    assert response.status_code >= 500


def test_an_unknown_endpoint_404_carries_no_reason(unreadable_store):
    """The two 404s must stay distinguishable BY FIELD, not by status alone.

    ``store_absent`` answers 404, and :func:`api_dispatch` already answers 404
    for an unknown endpoint. That collision was the strongest argument for 403,
    and it is answered by the typed ``reason`` rather than dismissed — but only
    while the OTHER 404 stays bare. Pin it here, or the discriminator is a
    convention that one helpful future edit ("let us add a reason to every error
    response") re-collapses silently.

    Run against ``unreadable_store`` deliberately: this is the situation where
    BOTH 404s could plausibly apply, so it is where the distinction has to hold.
    """
    # Arrange
    request = RequestFactory().get("/not_a_real_endpoint")

    # Act
    response = views.api_dispatch(request, "not_a_real_endpoint")
    payload = json.loads(response.content)

    # Assert
    assert response.status_code == 404 and "reason" not in payload, (
        f"unknown-endpoint 404 must carry no `reason` discriminator, got "
        f"{payload!r}"
    )


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
