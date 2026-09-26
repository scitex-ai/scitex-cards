#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board opens FIRST and fills in behind itself (instant-open).

WHY THIS EXISTS (operator): ``/apps/cards/`` blocked on a server render of
the full board — measured 4.1 s TTFB cold on the hub mount (3.5 s
``get_board`` rebuild for ~8 k tasks inside the boot announce, plus the
``/graph`` payload itself at ~24 MB). First paint waited on all of it while
showing a bare ``loading…`` line.

The contract pinned here:

- the page shell (chrome + skeleton + the ``/graph`` call that actually
  paints the cards) needs NO store read, so ``board_v3_page`` must return
  while a slow rebuild is still running — the one-shot boot announce moved
  off the response path but still fires;
- first paint carries feedback: server-rendered skeleton cards + spinner
  with ``aria-busy``, retired the moment data (or a named state) lands;
- the board JSON is warmed cheaply: a mount-aware ``<link rel="prefetch">``
  for ``/graph`` in the head, plus hover/focus warming of the DM JSON
  behind the Board|DM switcher.

RequestFactory against the real view, following the repo's _django
view-test conventions (see test__board_v3_mount_prefix.py).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal, depends_on: [build]}\n"
    "  - {id: build, title: Build It, status: in_progress, priority: 1}\n"
)

_DJANGO_DIR = Path(views.__file__).resolve().parent
_TEMPLATE = _DJANGO_DIR / "templates" / "scitex_cards" / "board_v3.html"
_SKELETON_CSS = _DJANGO_DIR / "static" / "scitex_cards" / "board_v3" / "18-skeleton.css"
_BOARD_STATES_JS = (
    _DJANGO_DIR / "static" / "scitex_cards" / "board_v3" / "boardStates.js"
)


@pytest.fixture
def store():
    """Seed the canonical DB and reset the board cache around the test."""
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_STORE_DSN"])
    _reset_cache()
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()


@pytest.fixture
def board_html(store):
    """The board page as the standalone server serves it at a hub mount."""
    request = RequestFactory().get(f"/apps/cards/?store={store}")
    return views.board_v3_page(request).content.decode("utf-8")


# --- the shell must not wait on the store ---------------------------------


@pytest.fixture
def instant_open_probe(store):
    """One ``board_v3_page`` call against a slow announce target.

    The fake announce sleeps 5 s on the daemon thread — slower than the
    3 s budget — so a fast response proves the shell never waited for
    it. The fake arrives via the ``_announce`` seam (an injected
    collaborator, so the test substitutes no module globals).
    The once-per-process announce guard is reset around the probe so
    this request is the one that fires it.
    """
    started = threading.Event()

    def slow_announce(store_path=None, **kwargs):
        started.set()
        time.sleep(5)

    saved = views._TURN_URL_ANNOUNCED
    views._TURN_URL_ANNOUNCED = False
    try:
        request = RequestFactory().get(f"/apps/cards/?store={store}")
        begin = time.monotonic()
        response = views.board_v3_page(request, _announce=slow_announce)
        elapsed = time.monotonic() - begin
        yield SimpleNamespace(
            response=response, elapsed=elapsed, announced=started
        )
    finally:
        views._TURN_URL_ANNOUNCED = saved


def test_board_shell_answers_while_announce_still_running(instant_open_probe):
    """The boot announce's slow work must not gate the response status."""
    # Arrange
    probe = instant_open_probe
    # Act
    status = probe.response.status_code
    # Assert
    assert status == 200


def test_board_shell_returns_inside_budget_while_announce_sleeps(
    instant_open_probe,
):
    """A 5 s announce with a 3 s budget proves decoupling, not mere speed."""
    # Arrange
    probe = instant_open_probe
    # Act
    elapsed = probe.elapsed
    # Assert
    assert elapsed < 3


def test_board_shell_still_fires_announce_off_response_path(
    instant_open_probe,
):
    """The deferred announce still ran, off the response path."""
    # Arrange
    probe = instant_open_probe
    # Act
    fired = probe.announced.wait(timeout=15)
    # Assert
    assert fired


# --- first paint carries feedback ------------------------------------------


def test_board_shell_paints_skeleton_cards(board_html):
    """The columns canvas holds placeholder cards, not a bare line."""
    # Arrange
    body = board_html
    # Act
    skeleton = 'class="board-skeleton"' in body
    cards = body.count('class="sk-card"')
    # Assert
    assert (skeleton, cards) == (True, 6)


def test_board_shell_marks_columns_busy_until_data_lands(board_html):
    """Assistive tech must hear \"loading\" until a painter retires it."""
    # Arrange
    body = board_html
    # Act
    busy = 'id="columns" aria-busy="true"' in body
    live = 'role="status" aria-label="Loading board"' in body
    # Assert
    assert (busy, live) == (True, True)


def test_board_shell_spins_while_graph_is_in_flight(board_html):
    """A spinner joins the skeleton — motion feedback, not just shape."""
    # Arrange
    body = board_html
    # Act
    spinner = 'class="board-skeleton__spinner"' in body
    # Assert
    assert spinner


def test_board_retires_skeleton_busy_when_cards_paint():
    """loadGraph()'s happy path flips aria-busy off after render()."""
    # Arrange
    source = _TEMPLATE.read_text(encoding="utf-8")
    # Act
    retires = (
        'document.getElementById("columns")?.setAttribute("aria-busy", "false")'
        in source
    )
    # Assert
    assert retires


def test_board_state_painters_retire_skeleton_busy():
    """Empty/signed-out/error panels replace the skeleton, so they own the
    same aria-busy retirement — otherwise the canvas stays \"busy\" forever
    on exactly the states a resting viewer meets."""
    # Arrange
    source = _BOARD_STATES_JS.read_text(encoding="utf-8")
    # Act
    retires = source.count('setAttribute("aria-busy", "false")')
    # Assert — one per painter: load-state, empty-state, load-error.
    assert retires == 3


# --- the board JSON is warmed cheaply --------------------------------------


def test_board_shell_prefetches_graph_at_a_subpath_mount(store):
    """The hub mount prefetches its own /graph, not the root's."""
    # Arrange
    request = RequestFactory().get(f"/apps/cards/?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert '<link rel="prefetch" href="/apps/cards/graph"' in body


def test_board_shell_prefetches_graph_at_a_root_mount(store):
    """A root mount prefetches \"/graph\" — never a \"//graph\"."""
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert '<link rel="prefetch" href="/graph"' in body


def test_board_warms_dm_json_on_switcher_hover():
    """Hovering/focusing the DM switcher link warms /dm/threads through the
    single API_BASE const — a second door onto the same JSON the page's own
    poll would otherwise wait on."""
    # Arrange
    source = _TEMPLATE.read_text(encoding="utf-8")
    # Act
    warms = 'API_BASE + "/dm/threads"' in source
    # Assert
    assert warms


# --- the skeleton stylesheet ships where the page points -------------------


def test_skeleton_stylesheet_exists_where_the_page_points():
    """A {% static %} link to a missing file renders fine and 404s silently
    — the skeleton would unstyle with nothing saying why."""
    # Arrange
    css_path = _SKELETON_CSS
    # Act
    exists = css_path.is_file()
    # Assert
    assert exists


def test_skeleton_stylesheet_honours_reduced_motion():
    """Shimmer and spin must stand down when the viewer asks for less
    motion — feedback without vestibular cost."""
    # Arrange
    css = _SKELETON_CSS.read_text(encoding="utf-8")
    # Act
    honours = "prefers-reduced-motion" in css
    # Assert
    assert honours
