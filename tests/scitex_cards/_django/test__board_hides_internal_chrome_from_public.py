"""Real SDK/RequestContext controls; no Store or board-view execution.

Standalone finite execution uses --noconftest. The source projection loads only
the production eligibility helper, with its original filename/line numbers.
The actual board and installed shell templates render through Django's loader.
"""

from __future__ import annotations

import ast
from html.parser import HTMLParser
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.template import Engine, RequestContext
from django.template.backends.django import get_installed_libraries
from django.test import RequestFactory, override_settings

ROOT = Path(__file__).resolve().parents[3]
VIEWS = ROOT / "src/scitex_cards/_django/views.py"
TEMPLATES = ROOT / "src/scitex_cards/_django/templates"


def _helper(*, allow_predecessor=False):
    tree = ast.parse(VIEWS.read_text(), filename=str(VIEWS))
    node = next((n for n in tree.body if getattr(n, "name", "") ==
                 "_cards_internal_chrome_enabled"), None)
    if node is None and allow_predecessor:
        from scitex_sdk.ui.context_processors import element_inspector_enabled

        return element_inspector_enabled
    if node is None:
        raise LookupError("predecessor has no Cards eligibility helper")
    namespace = {"logger": logging.getLogger("cards-compass-source-control")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(VIEWS), "exec"),
         namespace)
    return namespace[node.name]


def _render(request, *, include_decision=True):
    import scitex_sdk.app
    import scitex_sdk.ui

    engine = Engine(
        dirs=[TEMPLATES, Path(scitex_sdk.app.__file__).parent / "templates",
              Path(scitex_sdk.ui.__file__).parent / "templates"],
        libraries=get_installed_libraries(),
        context_processors=["scitex_sdk.ui.context_processors.element_inspector"],
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
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert ('<div class="foot">' in html) is expected


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer_store_location(override, debug, staff, expected):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert ('id="store-path"' in html) is expected


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer_operator_schema(override, debug, staff, expected):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert ("operator schema (ADR-0007)" in html) is expected


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer_title_action(override, debug, staff, expected):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert 'id="at-title"' in html


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer_assignee_action(override, debug, staff, expected):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert 'id="at-assignee"' in html


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer_status_action(override, debug, staff, expected):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert 'id="at-status"' in html


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_footer_project_action(override, debug, staff, expected):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        html = _render(request)
    # Assert
    assert 'id="at-project"' in html


def test_absent_cards_decision_defaults_footer_off():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=True):
        html = _render(request, include_decision=False)
    # Assert
    assert '<div class="foot">' not in html


def test_absent_cards_decision_defaults_footer_off_store_location():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=True):
        html = _render(request, include_decision=False)
    # Assert
    assert 'id="store-path"' not in html


def test_hidden_footer_retains_safe_javascript_lookup():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=False):
        html = _render(request)
    # Assert
    assert 'const storePath = document.getElementById("store-path");' in html


def test_hidden_footer_retains_safe_javascript_lookup_null_guard():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=False):
        html = _render(request)
    # Assert
    assert 'if (storePath) storePath.textContent' in html


def test_hidden_footer_retains_safe_javascript_lookup_unguarded_access():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=False):
        html = _render(request)
    # Assert
    assert 'document.getElementById("store-path").textContent' not in html


def test_real_sdk_user_failure_hides_diagnostics(caplog):
    # Arrange
    class UnsupportedUser:
        @property
        def is_authenticated(self):
            raise NotImplementedError("user unavailable on this request")

    request = RequestFactory().get("/board")
    request.user = UnsupportedUser()
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=None):
        result = _helper()(request)
    # Assert
    assert result is False


def test_real_sdk_user_failure_hides_diagnostics_original_log(caplog):
    # Arrange
    class UnsupportedUser:
        @property
        def is_authenticated(self):
            raise NotImplementedError("user unavailable on this request")

    request = RequestFactory().get("/board")
    request.user = UnsupportedUser()
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=None):
        result = _helper()(request)
    # Assert
    assert "browser diagnostic eligibility unavailable" in caplog.text


def test_anonymous_request_defaults_off():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=None):
        result = _helper()(request)
    # Assert
    assert result is False


def _columns_chrome(html):
    """Read the decision emitted on the actual board's error-rendering target."""
    class ColumnsParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.decisions = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "div" and values.get("id") == "columns":
                self.decisions.append(values.get("data-cards-internal-chrome"))

    parser = ColumnsParser()
    parser.feed(html)
    return parser.decisions


@pytest.mark.parametrize("override,debug,staff,expected", [
    pytest.param(False, True, True, False, id="explicit-false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true-with-debug-false"),
    pytest.param(None, False, True, True, id="authenticated-staff-fallback"),
    pytest.param(None, False, False, False, id="ordinary-user-default-off"),
    pytest.param(None, True, False, True, id="debug-fallback"),
])
def test_real_request_gate_controls_error_columns_transport(
    override, debug, staff, expected,
):
    # Arrange
    request = RequestFactory().get("/apps/cards/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        observed = _columns_chrome(_render(request))
    # Assert
    assert observed == [str(expected).lower()]


def test_absent_cards_decision_defaults_error_columns_transport_false():
    # Arrange
    request = RequestFactory().get("/board")
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=True):
        observed = _columns_chrome(_render(request, include_decision=False))
    # Assert
    assert observed == ["false"]
