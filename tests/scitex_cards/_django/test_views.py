#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the scitex-todo board Django views (real requests, no mocks).

Uses Django's RequestFactory against a real tmp_path task store passed via the
``?store=`` query param, so handlers exercise the real ``load_tasks`` /
``build_mermaid`` / ``STATUS_STYLE`` path end to end.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402

_STORE_TEXT = (
    "tasks:\n"
    "  - {id: north, title: North Star, status: goal, depends_on: [build]}\n"
    "  - {id: build, title: Build It, status: in_progress, priority: 1}\n"
    "  - {id: gate, title: Gate, status: blocked, blocks: [build]}\n"
)


@pytest.fixture
def store(tmp_path):
    """Write a real tmp task store and reset the board cache around the test."""
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _graph_json(store_path):
    """Drive views.api_dispatch for the graph endpoint and parse the JSON."""
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    return response.status_code, json.loads(response.content)


def test_graph_endpoint_returns_ok_status(store):
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    # Assert
    assert response.status_code == 200


def test_graph_endpoint_returns_all_nodes(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert {n["id"] for n in payload["nodes"]} == {"north", "build", "gate"}


def test_graph_endpoint_includes_depends_on_edge(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert {"source": "build", "target": "north", "kind": "depends_on"} in payload[
        "edges"
    ]


def test_graph_endpoint_includes_blocks_edge(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert {"source": "gate", "target": "build", "kind": "blocks"} in payload["edges"]


def test_graph_endpoint_exposes_priority_on_node(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    build = next(n for n in payload["nodes"] if n["id"] == "build")
    # Assert
    assert build["priority"] == 1


def test_graph_endpoint_colors_goal_gold(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert payload["status_colors"]["goal"]["fill"] == "#ffe082"


def test_graph_endpoint_includes_mermaid_source(store):
    # Arrange
    store_path = store
    # Act
    _status, payload = _graph_json(store_path)
    # Assert
    assert payload["mermaid"].startswith("flowchart TB")


def test_ping_endpoint_returns_ok_without_store():
    # Arrange
    request = RequestFactory().get("/ping")
    # Act
    response = views.api_dispatch(request, "ping")
    # Assert
    assert json.loads(response.content) == {"status": "ok"}


def test_unknown_endpoint_returns_404(store):
    # Arrange
    request = RequestFactory().get(f"/bogus?store={store}")
    # Act
    response = views.api_dispatch(request, "bogus")
    # Assert
    assert response.status_code == 404


def test_board_page_serves_react_shell_when_built(store):
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    response = views.board_page(request)
    # Assert
    assert b"scitex_cards/assets/index.js" in response.content


def test_tasks_endpoint_returns_raw_task_list(store):
    # Arrange
    request = RequestFactory().get(f"/tasks?store={store}")
    # Act
    response = views.api_dispatch(request, "tasks")
    payload = json.loads(response.content)
    # Assert
    assert [t["id"] for t in payload["tasks"]] == ["north", "build", "gate"]


def test_favicon_view_returns_ok_status():
    # Arrange
    request = RequestFactory().get("/favicon.ico")
    # Act
    response = views.favicon_view(request)
    # Assert
    assert response.status_code == 200


def test_favicon_view_sets_svg_content_type():
    # Arrange
    request = RequestFactory().get("/favicon.ico")
    # Act
    response = views.favicon_view(request)
    # Assert
    assert response["Content-Type"] == "image/svg+xml"


def test_favicon_view_body_is_scitex_s_svg():
    # Arrange
    request = RequestFactory().get("/favicon.ico")
    # Act
    response = views.favicon_view(request)
    # Drain FileResponse iterator into bytes.
    body = b"".join(response.streaming_content)
    # Assert — the bundled scitex-dev brand SVG declares id="scitex-logo".
    assert b'id="scitex-logo"' in body


def test_standalone_template_declares_no_favicon_of_its_own(store):
    """SINGLE SOURCE: the brand mark is scitex-ui's, and only scitex-ui's.

    This inverts an earlier pin (operator 3683) that required our own
    ``<link rel="icon">`` here. The shell template gained a default favicon in
    scitex-ui 0.7.1, so declaring a second one meant two copies of one mark,
    free to drift apart — with ours winning by document order, which made
    scitex-ui's copy the one that silently went stale. If the icon is wrong,
    fix it in scitex-ui; do not re-add a link here.
    """
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    response = views.board_page(request)
    # Assert — our copy is not linked; the shell's default is what renders.
    assert b"scitex_cards/favicon.svg" not in response.content


@pytest.mark.parametrize(
    "forbidden",
    [b"{#", b"#}", b"One source, not two copies", b"fix it in scitex-ui"],
)
def test_standalone_template_does_not_leak_django_comment(store, forbidden):
    """The favicon comment must NOT render as visible page text.

    Django's ``{# … #}`` syntax is single-line only — a multi-line block
    leaks into the rendered HTML. We use ``{% comment %} … {% endcomment %}``
    instead. Each parametrized row guards one raw delimiter / leaked phrase.
    """
    # Arrange
    request = RequestFactory().get(f"/?store={store}")
    # Act
    body = views.board_page(request).content
    # Assert — the raw comment delimiter / body text must not leak.
    assert forbidden not in body


# --- nested-graph drill-down: parent field on graph payload ---------------


_NESTED_STORE_TEXT = (
    "tasks:\n"
    "  - {id: hub, title: Hub, status: goal}\n"
    "  - {id: child-a, title: Child A, status: pending, parent: hub}\n"
    "  - {id: child-b, title: Child B, status: in_progress, parent: hub}\n"
    "  - {id: solo, title: Solo top-level, status: pending}\n"
)


@pytest.fixture
def nested_store(tmp_path):
    """Tmp store with a parent + two children + an unrelated top-level node."""
    path = tmp_path / "tasks-nested.yaml"
    path.write_text(_NESTED_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


@pytest.mark.parametrize(
    "node_id, expected_parent",
    [
        ("child-a", "hub"),
        ("child-b", "hub"),
        ("hub", None),
        ("solo", None),
    ],
)
def test_graph_endpoint_exposes_parent_field_per_node(
    nested_store, node_id, expected_parent
):
    # Arrange
    request = RequestFactory().get(f"/graph?store={nested_store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    by_id = {n["id"]: n for n in payload["nodes"]}
    # Assert — children carry their parent id; hub/solo carry null.
    assert by_id[node_id]["parent"] == expected_parent


def test_graph_endpoint_includes_parent_key_on_every_node(store):
    """A store with NO `parent` field anywhere still emits the key per node,
    so the frontend can treat it as always-present."""
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    # Assert
    for node in payload["nodes"]:
        assert "parent" in node


def test_graph_endpoint_defaults_parent_to_null_when_absent_in_yaml(store):
    """With no `parent` in the YAML, every node's parent falls back to null,
    so the frontend can safely default to the top-level view."""
    # Arrange
    request = RequestFactory().get(f"/graph?store={store}")
    # Act
    response = views.api_dispatch(request, "graph")
    payload = json.loads(response.content)
    # Assert
    for node in payload["nodes"]:
        assert node["parent"] is None


# --- rev: cheap change-poll fingerprint -----------------------------------


def test_rev_endpoint_returns_ok(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    response = views.api_dispatch(request, "rev")
    # Assert
    assert response.status_code == 200


def test_rev_endpoint_reports_task_count(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    payload = json.loads(views.api_dispatch(request, "rev").content)
    # Assert — the seeded store has three tasks.
    assert payload["count"] == 3


def test_rev_endpoint_includes_positive_mtime(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    payload = json.loads(views.api_dispatch(request, "rev").content)
    # Assert — mtime is a positive number the frontend can fingerprint on.
    assert isinstance(payload["mtime"], (int, float)) and payload["mtime"] > 0


def test_rev_endpoint_includes_a_numeric_asset_rev(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    payload = json.loads(views.api_dispatch(request, "rev").content)
    # Assert — asset_rev is a number (max template mtime) so the frontend can
    # hard-reload the pane when the board GUI code changes.
    assert isinstance(payload["asset_rev"], (int, float))


def test_rev_endpoint_asset_rev_is_positive(store):
    # Arrange
    request = RequestFactory().get(f"/rev?store={store}")
    # Act
    payload = json.loads(views.api_dispatch(request, "rev").content)
    # Assert
    assert payload["asset_rev"] > 0


# --- board_v3 first-paint status-color CSS vars (SSOT, kill 4-bucket) ------

_ALL_STATUSES = (
    "goal",
    "done",
    "in_progress",
    "blocked",
    "pending",
    "deferred",
    "failed",
)


def test_status_colors_projects_all_seven_statuses():
    """_status_colors() projects the SSOT STATUS_STYLE into exactly 7 keys.

    The board's color layer is single-sourced from this map; if a status is
    dropped the FE silently loses a distinct color and the 4-bucket collapse
    sneaks back in.
    """
    # Arrange
    from scitex_cards._django.handlers.graph import _status_colors

    # Act
    colors = _status_colors()
    # Assert
    assert set(colors) == set(_ALL_STATUSES)


def test_status_colors_each_entry_declares_fill_stroke_dashed():
    """Every projected status carries the three fields the FE CSS vars need."""
    # Arrange
    from scitex_cards._django.handlers.graph import _status_colors

    # Act
    colors = _status_colors()
    # Assert
    for status, entry in colors.items():
        assert set(entry) == {"fill", "stroke", "dashed"}, status


def test_status_colors_dashed_flag_is_a_boolean():
    """`dashed` must be a real bool so the template can branch on it."""
    # Arrange
    from scitex_cards._django.handlers.graph import _status_colors

    # Act
    colors = _status_colors()
    # Assert
    for status, entry in colors.items():
        assert isinstance(entry["dashed"], bool), status


def test_board_v3_page_renders_status_color_vars_block(store):
    """board_v3 ships the server-rendered #status-color-vars <style> block."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content
    # Assert
    assert b'id="status-color-vars"' in body


def test_board_v3_page_emits_a_fill_var_for_every_status(store):
    """All 7 statuses get a --status-fill var in first paint.

    Pins that the color layer is per-RAW-status (not the legacy 4 buckets),
    sourced from STATUS_STYLE via _status_colors().
    """
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f"--status-fill-{status}:" in body, status


def test_board_v3_page_emits_a_stroke_var_for_every_status(store):
    """All 7 statuses get a --status-stroke var in first paint."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f"--status-stroke-{status}:" in body, status


def test_board_v3_page_emits_a_border_var_for_every_status(store):
    """All 7 statuses get a --status-border var in first paint."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f"--status-border-{status}:" in body, status


def test_board_v3_page_deferred_status_border_is_dashed(store):
    """`deferred` is the only dashed status — its border var must read dashed.

    This is the SSOT differentiator (STATUS_STYLE deferred carries `5 3`),
    so it must survive the template projection.
    """
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert "--status-border-deferred: dashed;" in body


def test_board_v3_page_goal_status_border_is_solid(store):
    """The other side of the dashed differentiator: `goal` stays solid."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    assert "--status-border-goal: solid;" in body


def test_board_v3_page_renders_status_legend(store):
    """board_v3 ships the single-sourced status color legend container."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content
    # Assert
    assert b'id="status-legend"' in body


def test_board_v3_legend_has_chip_for_every_status(store):
    """The legend renders one swatch+label chip per SSOT status.

    Pins that the legend iterates the SAME `status_colors` context (so it
    auto-updates with STATUS_STYLE) — each status gets a stable
    `data-legend-status="<s>"` chip + the status name as its label.
    """
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f'data-legend-status="{status}"' in body, status


def test_board_v3_legend_swatches_reference_the_fill_var(store):
    """Each legend swatch reads the per-status --status-fill CSS var.

    The swatch must single-source off the first-paint vars (not inline
    hex), so the legend color tracks the cards/timeline/mermaid exactly.
    """
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f"var(--status-fill-{status})" in body, status


def test_board_v3_legend_swatches_reference_the_border_var(store):
    """Each legend swatch reads the per-status --status-border CSS var."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f"var(--status-border-{status}" in body, status


def test_board_v3_legend_swatches_reference_the_stroke_var(store):
    """Each legend swatch reads the per-status --status-stroke CSS var."""
    # Arrange
    request = RequestFactory().get(f"/board_v3?store={store}")
    # Act
    body = views.board_v3_page(request).content.decode("utf-8")
    # Assert
    for status in _ALL_STATUSES:
        assert f"var(--status-stroke-{status})" in body, status


# EOF
