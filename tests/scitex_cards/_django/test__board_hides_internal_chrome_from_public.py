"""Real SDK/RequestContext controls; no Store or board-view execution.

Standalone finite execution uses --noconftest. The source projection loads only
the production eligibility helper, with its original filename/line numbers.
The actual board and installed shell templates render through Django's loader.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.template import Engine, RequestContext
from django.test import RequestFactory, override_settings

ROOT = Path(__file__).resolve().parents[3]
VIEWS = ROOT / "src/scitex_cards/_django/views.py"
TEMPLATES = ROOT / "src/scitex_cards/_django/templates"


def _helper(*, allow_predecessor=False):
    tree = ast.parse(VIEWS.read_text(), filename=str(VIEWS))
    node = next((n for n in tree.body if getattr(n, "name", "") ==
                 "_cards_internal_chrome_enabled"), None)
    if node is None and allow_predecessor:
        from scitex_ui.context_processors import element_inspector_enabled

        return element_inspector_enabled
    if node is None:
        raise LookupError("predecessor has no Cards eligibility helper")
    namespace = {"logger": logging.getLogger("cards-compass-source-control")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(VIEWS), "exec"),
         namespace)
    return namespace[node.name]


def _render(request, *, include_decision=True):
    import scitex_app
    import scitex_ui

    engine = Engine(
        dirs=[TEMPLATES, Path(scitex_app.__file__).parent / "templates",
              Path(scitex_ui.__file__).parent / "templates"],
        libraries={"static": "django.templatetags.static"},
        context_processors=["scitex_ui.context_processors.element_inspector"],
    )
    context = {"api_base": "/apps/cards/", "app_name": "scitex-cards",
               "cards_project_picker_available": False}
    if include_decision:
        # Production resolver, never an expected test flag.
        context["cards_internal_chrome_enabled"] = _helper(
            allow_predecessor=True)(request)
    rendered_context = RequestContext(request)
    with rendered_context.push(context):
        return engine.get_template("scitex_cards/board_v3.html").render(
            rendered_context)


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer(override, debug, staff, expected):
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    assert ('<div class="foot">' in html) is expected
    assert ('id="store-path"' in html) is expected
    assert ("operator schema (ADR-0007)" in html) is expected
    assert 'id="at-title"' in html
    assert 'id="at-assignee"' in html
    assert 'id="at-status"' in html
    assert 'id="at-project"' in html


def test_absent_cards_decision_defaults_footer_off():
    request = RequestFactory().get("/board")
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=True):
        html = _render(request, include_decision=False)
    assert '<div class="foot">' not in html
    assert 'id="store-path"' not in html


def test_hidden_footer_retains_safe_javascript_lookup():
    request = RequestFactory().get("/board")
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=False):
        html = _render(request)
    assert 'const storePath = document.getElementById("store-path");' in html
    assert 'if (storePath) storePath.textContent' in html
    assert 'document.getElementById("store-path").textContent' not in html


def test_real_sdk_user_failure_hides_diagnostics(caplog):
    class UnsupportedUser:
        @property
        def is_authenticated(self):
            raise NotImplementedError("user unavailable on this request")

    request = RequestFactory().get("/board")
    request.user = UnsupportedUser()
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=None):
        assert _helper()(request) is False
    assert "browser diagnostic eligibility unavailable" in caplog.text


def test_anonymous_request_defaults_off():
    request = RequestFactory().get("/board")
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=None):
        assert _helper()(request) is False
