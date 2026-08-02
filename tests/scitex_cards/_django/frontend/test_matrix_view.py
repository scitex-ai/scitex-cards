#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wire-format + template contract tests for the Matrix layout (ADR-0011 §8).

Operator build order (card ``scitex-cards-gui-matrix-view-20260717``):
four quadrants, humans DRAG cards to update the two axes, rank recomputes
(importance weighted ABOVE urgency) and the new order is shared with
agents immediately, quadrant occupancy tracked over time.

This PR is READ-ONLY (PR 1 of 3) and pins three things:

  1. ``/graph`` forwards ``urgency`` / ``importance`` / ``rank`` verbatim,
     so the matrix renders from the SAME wire format as every other layout
     rather than needing a second endpoint.
  2. The axes survive the store round-trip TODAY, before the schema-v5
     dataclass fields land — ``load_tasks`` returns raw YAML mappings and
     the validator has no unknown-key rejection. This is what lets PR 1
     ship ahead of the engine; if it ever stops being true, this test is
     the alarm rather than a silently empty matrix.
  3. The template wires the layout in (toggle + assets + valid layout).

The pure render logic lives in ``tests/scitex_cards/test__matrix.js``
(``node --test``), which requires the REAL served module — see that file.

Mocks-free (STX-NM / PA-306): a real tmp store on disk driven through the
real ``views.api_dispatch`` with a real ``RequestFactory``, pointed at the
store by ``?store=`` (same shape as ``handlers/test_priority.py``); the
template assertions just open the source. Lane-glob isolation comes from
the autouse ``_isolate_host_lane_globs`` fixture in
``tests/scitex_cards/conftest.py`` — do NOT re-set it here.

One assertion per test (STX-TQ007).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[4]

_STATIC = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
)

_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)

_MATRIX_JS = _STATIC / "14-matrix.js"
_MATRIX_CSS = _STATIC / "14-matrix.css"


# `scored` carries both axes + an engine rank; `bare` carries neither, which
# is what EVERY card looks like until the schema-v5 work lands.
_STORE_TEXT = (
    "tasks:\n"
    "  - {id: scored, title: A scored card, status: in_progress,"
    " urgency: 4, importance: 5, rank: 1}\n"
    "  - {id: bare, title: A card with no axes, status: in_progress}\n"
)


@pytest.fixture
def store():
    """Seed the canonical DB from the fixture doc; reset the board cache around it.

    The store is SQLite now: ``load_tasks`` / the ``/graph`` handler read and
    write the canonical DB and ignore the store path (it survives only as a
    provenance label). So parse the readable YAML fixture text into the doc the
    YAML used to hold, seed the canonical DB from it, and yield the PINNED store
    identity path (``SCITEX_CARDS_TASKS_YAML_SHARED``, == ``resolve_tasks_path(None)``)
    — never a tmp yaml, which would trip the "stamped for a DIFFERENT store"
    refusal. urgency/importance/rank ride through the ``card_json`` payload
    verbatim, so every axis assertion below round-trips unchanged.
    """
    from conftest import seed_db_from_doc

    from scitex_cards._yaml import safe_load

    doc = safe_load(_STORE_TEXT) or {}
    seed_db_from_doc(doc, os.environ["SCITEX_CARDS_DB"])
    _reset_cache()
    yield os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _reset_cache()


def _graph_nodes(store_path):
    """Drive views.api_dispatch for GET /graph and return {id: node}."""
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    if response.status_code != 200:
        raise AssertionError(f"GET /graph failed: {response.content!r}")
    payload = json.loads(response.content)
    return {n["id"]: n for n in payload["nodes"]}


def _hardcoded_colour(decl: str) -> bool:
    """True when a CSS declaration burns in a literal colour."""
    return bool(
        re.search(r"#[0-9a-fA-F]{3,8}\b", decl)
        or re.search(r"\b(?:white|black)\b", decl)
    )


def _matrix_css_colour_declarations() -> list[str]:
    css = _MATRIX_CSS.read_text(encoding="utf-8")
    return re.findall(
        r"^\s*(?:background|color|border[a-z-]*)\s*:\s*([^;]+);", css, re.M
    )


# ── 1. The wire format ────────────────────────────────────────────────────


def test_graph_forwards_the_urgency_axis(store):
    # Arrange
    # Act
    nodes = _graph_nodes(store)
    # Assert
    assert nodes["scored"]["urgency"] == 4


def test_graph_forwards_the_importance_axis(store):
    # Arrange
    # Act
    nodes = _graph_nodes(store)
    # Assert
    assert nodes["scored"]["importance"] == 5


def test_graph_forwards_the_engine_rank(store):
    # Arrange
    # Act
    nodes = _graph_nodes(store)
    # Assert
    assert nodes["scored"]["rank"] == 1


def test_unscored_card_emits_none_for_urgency(store):
    # Arrange
    # defaulting a missing axis to 0 (or the scale midpoint) would
    # place the card at a coordinate nobody chose and render it as an
    # operator judgement that was never made. 14-matrix.js treats None as
    # UNSCORED and puts the card in its tray instead.
    # Act
    nodes = _graph_nodes(store)
    # Assert
    assert nodes["bare"]["urgency"] is None


def test_unscored_card_emits_none_for_importance(store):
    # Arrange
    # Act
    nodes = _graph_nodes(store)
    # Assert
    assert nodes["bare"]["importance"] is None


def test_unscored_card_emits_none_for_rank(store):
    # Arrange
    # Act
    nodes = _graph_nodes(store)
    # Assert
    assert nodes["bare"]["rank"] is None


def test_urgency_survives_the_store_before_schema_v5(store):
    # Arrange
    # ``load_tasks`` returns the raw YAML mappings (not Task
    # round-trips) and the validator rejects no unknown keys, so the axes
    # reach the handler already. If a future strict-schema change starts
    # dropping unknown keys, this fails LOUDLY here instead of the matrix
    # silently rendering every card as unscored.
    from scitex_cards._model import load_tasks

    # Act
    tasks = {t["id"]: t for t in load_tasks(store)}
    # Assert
    assert tasks["scored"]["urgency"] == 4


def test_importance_survives_the_store_before_schema_v5(store):
    # Arrange
    from scitex_cards._model import load_tasks

    # Act
    tasks = {t["id"]: t for t in load_tasks(store)}
    # Assert
    assert tasks["scored"]["importance"] == 5


def test_unscored_card_has_no_axis_key_in_the_store(store):
    # Arrange
    from scitex_cards._model import load_tasks

    # Act
    tasks = {t["id"]: t for t in load_tasks(store)}
    # Assert
    # absent, never coerced to a value.
    assert "urgency" not in tasks["bare"]


# ── 2. The template wiring ────────────────────────────────────────────────


def test_matrix_is_registered_as_a_valid_layout():
    # Arrange
    # Act
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Assert
    assert 'VALID_LAYOUTS = ["timeline", "wall", "graph", "matrix"]' in src


def test_matrix_layout_has_a_toggle_control():
    # Arrange
    # Act
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Assert
    assert 'id="f-layout-matrix"' in src


def test_matrix_toggle_switches_the_layout():
    # Arrange
    # Act
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Assert
    assert "onLayoutChange('matrix')" in src


def test_the_template_loads_the_matrix_script():
    # Arrange
    # Act
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Assert
    assert "board_v3/14-matrix.js" in src


def test_the_template_loads_the_matrix_stylesheet():
    # Arrange
    # Act
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Assert
    assert "board_v3/14-matrix.css" in src


def test_the_matrix_render_falls_back_when_the_module_is_absent():
    # Arrange
    # same stance as Wall: a deferred script that hasn't executed
    # must not blank the GUI, so the dispatch guards on window.STX.matrix.
    # Act
    src = _TEMPLATE.read_text(encoding="utf-8")
    # Assert
    assert 'layout === "matrix" && window.STX && window.STX.matrix' in src


# ── 3. The theme + lane contracts ─────────────────────────────────────────


def test_matrix_css_declares_colours_worth_checking():
    # Arrange
    # Act
    decls = _matrix_css_colour_declarations()
    # Assert
    # guards the scan below from silently passing on an empty match.
    assert decls, "expected colour declarations to check"


def test_matrix_css_uses_theme_tokens_not_hardcoded_colors():
    # Arrange
    # dark mode is the default the operator reads in; a hardcoded
    # literal would burn a light-mode colour into their view.
    decls = _matrix_css_colour_declarations()
    # Act
    offenders = [decl for decl in decls if _hardcoded_colour(decl)]
    # Assert
    assert offenders == [], f"hardcoded colours in 14-matrix.css: {offenders!r}"


def test_the_matrix_module_does_not_weight_importance():
    # Arrange
    # ADR-0011 §1: rank is COMPUTED by the engine (scitex-cards'
    # package lane), never asserted and never recomputed in the GUI. The
    # equivalent JS-side guard lives in test__matrix.js; this one makes the
    # boundary visible to a Python-only test run.
    # Act
    src = _MATRIX_JS.read_text(encoding="utf-8")
    # Assert
    assert "w_i" not in src


def test_the_matrix_module_does_not_weight_urgency():
    # Arrange
    # Act
    src = _MATRIX_JS.read_text(encoding="utf-8")
    # Assert
    assert "w_u" not in src
