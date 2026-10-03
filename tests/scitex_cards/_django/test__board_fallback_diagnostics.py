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
        from scitex_ui.context_processors import element_inspector_enabled
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
    exc = _production_functions()[kind]("private /scratch/cards.db <secret>")
    html = _markup(exc, enabled=False)
    assert summary in html
    assert "private" not in html
    assert "/scratch/cards.db" not in html
    assert '<pre class="mermaid">' not in html
    assert 'class="err"' in html


def test_untyped_summary_attribute_is_not_trusted():
    class UntypedFailure(RuntimeError):
        public_summary = "leaked /scratch/private <script>"

    html = _markup(UntypedFailure("another private path"), enabled=False)
    assert "Failed to load task store." in html
    assert "private" not in html
    assert "<script>" not in html


@pytest.mark.parametrize("kind", ["StoreUnavailableError", "RuntimeError"])
def test_enabled_diagnosis_with_debug_false_is_escaped_once(kind):
    classes = {"RuntimeError": RuntimeError, **_production_functions()}
    html = _markup(classes[kind]('private <script> & "detail"'), enabled=True)
    assert "private &lt;script&gt; &amp; &quot;detail&quot;" in html
    assert "&amp;lt;script" not in html
    assert "<script> &" not in html


@pytest.mark.parametrize("enabled", [False, True])
def test_location_visibility_preserves_count_and_graph(enabled):
    # Explicit presenter inputs qualify presentation only, never Store success.
    html = _markup(None, enabled=enabled, mermaid="graph TD;", count=3,
                   store="/scratch/<private>&cards")
    assert "3 tasks" in html
    assert '<pre class="mermaid">graph TD;</pre>' in html
    assert ("/scratch/" in html) is enabled
    assert ("&lt;private&gt;&amp;cards" in html) is enabled
    assert "<private>" not in html


def test_typed_public_summary_is_escaped_once():
    exc = _production_functions()["StoreUnavailableError"](
        "private", public_summary='Unavailable <workspace> & "retry"')
    html = _markup(exc, enabled=False)
    assert "Unavailable &lt;workspace&gt; &amp; &quot;retry&quot;" in html
    assert "&amp;lt;workspace" not in html


def test_unknown_refusal_is_not_empty_success():
    html = _markup(RuntimeError("private diagnostic"), enabled=False)
    assert '<p class="err">Failed to load task store.</p>' in html
    assert 'class="meta"' not in html
    assert '<pre class="mermaid">' not in html
