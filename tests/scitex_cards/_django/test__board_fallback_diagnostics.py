"""Source-qualified fallback presentation through genuine SDK eligibility.

These controls do not manufacture a successful Store or execute get_board.
The predecessor adapter compiles its exact existing body/meta/return nodes;
the candidate compiles the actual private presenter. No module replacement.
"""

from __future__ import annotations

import ast
import logging
from functools import lru_cache
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings

ROOT = Path(__file__).resolve().parents[3]
VIEWS = ROOT / "src/scitex_cards/_django/views.py"


@lru_cache(maxsize=1)
def _production_functions():
    errors = ROOT / "src/scitex_cards/_store_errors.py"
    namespace = {"logger": logging.getLogger("cards-compass-source-control")}
    exec(compile(errors.read_text(), str(errors), "exec"), namespace)
    tree = ast.parse(VIEWS.read_text(), filename=str(VIEWS))
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    presenter = nodes.get("_static_graph_markup")
    if presenter is None:
        original = nodes["_static_graph_page"]
        # Literal predecessor presentation tail, preserving all expressions.
        tail = original.body[-3:]
        assert isinstance(tail[0], ast.Assign)
        assert tail[0].targets[0].id == "body"
        presenter = ast.FunctionDef(
            name="_static_graph_markup",
            args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=n) for n in
                ("mermaid_src", "store", "count", "error", "internal_chrome")],
                kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=tail, decorator_list=[],
        )
        ast.copy_location(presenter, original)
        ast.fix_missing_locations(presenter)
    selected = [presenter]
    if "_cards_internal_chrome_enabled" in nodes:
        selected.append(nodes["_cards_internal_chrome_enabled"])
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(VIEWS), "exec"),
         namespace)
    return namespace


def _markup(error, *, enabled, mermaid="", store="", count=0):
    functions = _production_functions()
    request = RequestFactory().get("/board")
    with override_settings(DEBUG=not enabled, SCITEX_UI_ELEMENT_INSPECTOR=enabled):
        # The baseline lacked Cards' delegating helper; use the genuine SDK
        # resolver for its presentation controls, not a expected test boolean.
        from scitex_sdk.ui.context_processors import element_inspector_enabled
        resolve = functions.get("_cards_internal_chrome_enabled",
                                element_inspector_enabled)
        decision = resolve(request)
    return functions["_static_graph_markup"](mermaid, store, count, error, decision)


@pytest.mark.parametrize("kind,summary", [
    pytest.param("StoreUnavailableError", "The task store is not available on this server.",
                 id="unavailable-public-summary"),
    pytest.param("StoreNotProvisionedError", "No task store has been set up for this workspace yet.",
                 id="not-provisioned-distinct-summary"),
])
def test_public_typed_refusal_with_debug_true(kind, summary):
    # Arrange
    exc = _production_functions()[kind]("private /scratch/cards.db <secret>")
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert summary in html


@pytest.mark.parametrize("kind,summary", [
    pytest.param("StoreUnavailableError", "The task store is not available on this server.",
                 id="unavailable-public-summary"),
    pytest.param("StoreNotProvisionedError", "No task store has been set up for this workspace yet.",
                 id="not-provisioned-distinct-summary"),
])
def test_public_typed_refusal_with_debug_true_private_detail(kind, summary):
    # Arrange
    exc = _production_functions()[kind]("private /scratch/cards.db <secret>")
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert "private" not in html


@pytest.mark.parametrize("kind,summary", [
    pytest.param("StoreUnavailableError", "The task store is not available on this server.",
                 id="unavailable-public-summary"),
    pytest.param("StoreNotProvisionedError", "No task store has been set up for this workspace yet.",
                 id="not-provisioned-distinct-summary"),
])
def test_public_typed_refusal_with_debug_true_store_location(kind, summary):
    # Arrange
    exc = _production_functions()[kind]("private /scratch/cards.db <secret>")
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert "/scratch/cards.db" not in html


@pytest.mark.parametrize("kind,summary", [
    pytest.param("StoreUnavailableError", "The task store is not available on this server.",
                 id="unavailable-public-summary"),
    pytest.param("StoreNotProvisionedError", "No task store has been set up for this workspace yet.",
                 id="not-provisioned-distinct-summary"),
])
def test_public_typed_refusal_with_debug_true_graph_absence(kind, summary):
    # Arrange
    exc = _production_functions()[kind]("private /scratch/cards.db <secret>")
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert '<pre class="mermaid">' not in html


@pytest.mark.parametrize("kind,summary", [
    pytest.param("StoreUnavailableError", "The task store is not available on this server.",
                 id="unavailable-public-summary"),
    pytest.param("StoreNotProvisionedError", "No task store has been set up for this workspace yet.",
                 id="not-provisioned-distinct-summary"),
])
def test_public_typed_refusal_with_debug_true_refusal_marker(kind, summary):
    # Arrange
    exc = _production_functions()[kind]("private /scratch/cards.db <secret>")
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert 'class="err"' in html


def test_untyped_summary_attribute_is_not_trusted():
    # Arrange
    class UntypedFailure(RuntimeError):
        public_summary = "leaked /scratch/private <script>"

    # Act
    html = _markup(UntypedFailure("another private path"), enabled=False)
    # Assert
    assert "Failed to load task store." in html


def test_untyped_summary_attribute_is_not_trusted_private_detail():
    # Arrange
    class UntypedFailure(RuntimeError):
        public_summary = "leaked /scratch/private <script>"

    # Act
    html = _markup(UntypedFailure("another private path"), enabled=False)
    # Assert
    assert "private" not in html


def test_untyped_summary_attribute_is_not_trusted_script_absence():
    # Arrange
    class UntypedFailure(RuntimeError):
        public_summary = "leaked /scratch/private <script>"

    # Act
    html = _markup(UntypedFailure("another private path"), enabled=False)
    # Assert
    assert "<script>" not in html


@pytest.mark.parametrize("kind", ["StoreUnavailableError", "RuntimeError"])
def test_enabled_diagnosis_with_debug_false_is_escaped_once(kind):
    # Arrange
    classes = {"RuntimeError": RuntimeError, **_production_functions()}
    # Act
    html = _markup(classes[kind]('private <script> & "detail"'), enabled=True)
    # Assert
    assert "private &lt;script&gt; &amp; &quot;detail&quot;" in html


@pytest.mark.parametrize("kind", ["StoreUnavailableError", "RuntimeError"])
def test_enabled_diagnosis_with_debug_false_is_escaped_once_double_escape_absence(kind):
    # Arrange
    classes = {"RuntimeError": RuntimeError, **_production_functions()}
    # Act
    html = _markup(classes[kind]('private <script> & "detail"'), enabled=True)
    # Assert
    assert "&amp;lt;script" not in html


@pytest.mark.parametrize("kind", ["StoreUnavailableError", "RuntimeError"])
def test_enabled_diagnosis_with_debug_false_is_escaped_once_raw_script_absence(kind):
    # Arrange
    classes = {"RuntimeError": RuntimeError, **_production_functions()}
    # Act
    html = _markup(classes[kind]('private <script> & "detail"'), enabled=True)
    # Assert
    assert "<script> &" not in html


@pytest.mark.parametrize("enabled", [False, True])
def test_location_visibility_preserves_count_and_graph(enabled):
    # Explicit presenter inputs qualify presentation only, never Store success.
    # Arrange
    # Act
    html = _markup(None, enabled=enabled, mermaid="graph TD;", count=3,
                   store="/scratch/<private>&cards")
    # Assert
    assert "3 tasks" in html


@pytest.mark.parametrize("enabled", [False, True])
def test_location_visibility_preserves_count_and_graph_mermaid_graph(enabled):
    # Explicit presenter inputs qualify presentation only, never Store success.
    # Arrange
    # Act
    html = _markup(None, enabled=enabled, mermaid="graph TD;", count=3,
                   store="/scratch/<private>&cards")
    # Assert
    assert '<pre class="mermaid">graph TD;</pre>' in html


@pytest.mark.parametrize("enabled", [False, True])
def test_location_visibility_preserves_count_and_graph_store_location(enabled):
    # Explicit presenter inputs qualify presentation only, never Store success.
    # Arrange
    # Act
    html = _markup(None, enabled=enabled, mermaid="graph TD;", count=3,
                   store="/scratch/<private>&cards")
    # Assert
    assert ("/scratch/" in html) is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_location_visibility_preserves_count_and_graph_escaped_location(enabled):
    # Explicit presenter inputs qualify presentation only, never Store success.
    # Arrange
    # Act
    html = _markup(None, enabled=enabled, mermaid="graph TD;", count=3,
                   store="/scratch/<private>&cards")
    # Assert
    assert ("&lt;private&gt;&amp;cards" in html) is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_location_visibility_preserves_count_and_graph_raw_location_absence(enabled):
    # Explicit presenter inputs qualify presentation only, never Store success.
    # Arrange
    # Act
    html = _markup(None, enabled=enabled, mermaid="graph TD;", count=3,
                   store="/scratch/<private>&cards")
    # Assert
    assert "<private>" not in html


def test_typed_public_summary_is_escaped_once():
    # Arrange
    exc = _production_functions()["StoreUnavailableError"](
        "private", public_summary='Unavailable <workspace> & "retry"')
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert "Unavailable &lt;workspace&gt; &amp; &quot;retry&quot;" in html


def test_typed_public_summary_is_escaped_once_double_escape_absence():
    # Arrange
    exc = _production_functions()["StoreUnavailableError"](
        "private", public_summary='Unavailable <workspace> & "retry"')
    # Act
    html = _markup(exc, enabled=False)
    # Assert
    assert "&amp;lt;workspace" not in html


def test_unknown_refusal_is_not_empty_success():
    # Arrange
    # Act
    html = _markup(RuntimeError("private diagnostic"), enabled=False)
    # Assert
    assert '<p class="err">Failed to load task store.</p>' in html


def test_unknown_refusal_is_not_empty_success_success_meta_absence():
    # Arrange
    # Act
    html = _markup(RuntimeError("private diagnostic"), enabled=False)
    # Assert
    assert 'class="meta"' not in html


def test_unknown_refusal_is_not_empty_success_graph_absence():
    # Arrange
    # Act
    html = _markup(RuntimeError("private diagnostic"), enabled=False)
    # Assert
    assert '<pre class="mermaid">' not in html
