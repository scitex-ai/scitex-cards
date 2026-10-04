"""Real request/template controls and separately managed public-view controls.

The source helper retains its production AST and filename. It calls the real
installed UI SDK. Tests named ``test_managed_`` require the existing canonical
PG fixtures; a neutral --noconftest subset must exclude them explicitly.
"""

from __future__ import annotations

import ast
import logging
import sys
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template import Engine, RequestContext
from django.template.backends.django import get_installed_libraries
from django.test import RequestFactory, override_settings

ROOT = Path(__file__).resolve().parents[3]
VIEWS = ROOT / "src/scitex_cards/_django/views.py"
TEMPLATES = ROOT / "src/scitex_cards/_django/templates"
GATES = [
    pytest.param(False, True, True, False, id="false-beats-debug-staff"),
    pytest.param(True, False, False, True, id="explicit-true"),
    pytest.param(None, False, True, True, id="authenticated-staff"),
    pytest.param(None, False, False, False, id="ordinary-user"),
    pytest.param(None, True, False, True, id="debug"),
]


def _eligibility(request):
    tree = ast.parse(VIEWS.read_text(), filename=str(VIEWS))
    node = next(n for n in tree.body if getattr(n, "name", "") ==
                "_cards_internal_chrome_enabled")
    namespace = {"logger": logging.getLogger("cards-standalone-source-control")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(VIEWS), "exec"),
         namespace)
    return namespace[node.name](request)


def _request(path, staff=False):
    request = RequestFactory().get(path)
    request.user = SimpleNamespace(is_authenticated=True, is_staff=staff,
                                   get_username=lambda: "browser-control-user")
    return request


def _render(request, template="standalone.html", *, include_decision=True,
            view_path="legacy/"):
    import scitex_app
    import scitex_ui
    from scitex_ui.branding import shell_context
    from scitex_ui.mount import mount_context

    engine = Engine(
        dirs=[TEMPLATES, Path(scitex_app.__file__).parent / "templates",
              Path(scitex_ui.__file__).parent / "templates"],
        libraries=get_installed_libraries(),
        context_processors=["scitex_ui.context_processors.element_inspector"],
    )
    context = {
        **shell_context("Cards", panes={"ai": "unused", "files": "unused",
                                       "viewer": "unused"}),
        **mount_context(request, view_path=view_path),
        "app_name": "scitex-cards", "api_base": request.path,
        "scitex_cards_version": version("scitex-cards"),
    }
    if include_decision:
        context["cards_internal_chrome_enabled"] = _eligibility(request)
    rendered_context = RequestContext(request)
    with rendered_context.push(context):
        return engine.get_template("scitex_cards/" + template).render(
            rendered_context)


class _MountParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.mounts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id") == "app-mount":
            self.mounts.append(attributes)


def _mount(html):
    parser = _MountParser()
    parser.feed(html)
    return parser.mounts


def _board_node():
    tree = ast.parse(VIEWS.read_text(), filename=str(VIEWS))
    return next(n for n in tree.body if getattr(n, "name", "") == "board_page")


def _observe_managed_view(request):
    from scitex_cards._django.views import board_page

    decisions, fallbacks = [], []
    previous = sys.getprofile()

    def observe(frame, event, result):
        if frame.f_code.co_filename != str(VIEWS):
            return
        if event == "return" and frame.f_code.co_name == "_cards_internal_chrome_enabled":
            decisions.append((type(result).__name__, result))
        if event == "call" and frame.f_code.co_name == "_render_static_graph_page":
            value = frame.f_locals["internal_chrome"]
            fallbacks.append((type(value).__name__, value))

    try:
        sys.setprofile(observe)
        response = board_page(request)
    finally:
        sys.setprofile(previous)
    html = response.content.decode()
    mounts = _mount(html)
    return {"status": response.status_code, "decisions": decisions,
            "fallbacks": fallbacks,
            "mount": mounts[0]["data-cards-internal-chrome"] if mounts else None}


@pytest.mark.parametrize("path", ["/legacy/", "/apps/cards/legacy/"],
                         ids=["standalone", "hub-prefix"])
@pytest.mark.parametrize("override,debug,staff,expected", GATES)
def test_request_template_encodes_real_sdk_decision(path, override, debug, staff, expected):
    # Arrange
    request = _request(path, staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        mounts = _mount(_render(request))
    # Assert
    assert [m["data-cards-internal-chrome"] for m in mounts] == [str(expected).lower()]


@pytest.mark.parametrize("override,debug,staff,expected", GATES)
def test_request_decision_is_a_strict_boolean(override, debug, staff, expected):
    # Arrange
    request = _request("/legacy/", staff)
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        result = _eligibility(request)
    # Assert
    assert (type(result).__name__, result) == ("bool", expected)


@pytest.mark.parametrize("path", ["/legacy/", "/apps/cards/legacy/"],
                         ids=["standalone", "hub-prefix"])
def test_request_template_missing_cards_decision_defaults_false(path):
    # Arrange
    request = _request(path, True)
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=True):
        mounts = _mount(_render(request, include_decision=False))
    # Assert
    assert [m["data-cards-internal-chrome"] for m in mounts] == ["false"]


def test_request_unsupported_user_hides_diagnostics():
    # Arrange
    class UnsupportedUser:
        @property
        def is_authenticated(self):
            raise NotImplementedError("request identity unavailable")

    request = RequestFactory().get("/legacy/")
    request.user = UnsupportedUser()
    # Act
    with override_settings(DEBUG=False, SCITEX_UI_ELEMENT_INSPECTOR=None):
        decision = _eligibility(request)
    # Assert
    assert decision is False


def test_source_public_view_computes_eligibility_once():
    # Arrange
    node = _board_node()
    # Act
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and
             isinstance(n.func, ast.Name) and n.func.id == "_cards_internal_chrome_enabled"]
    # Assert
    assert len(calls) == 1


def test_source_public_view_shares_decision_with_template_and_fallback():
    # Arrange
    node = _board_node()
    # Act
    assignments = [n.value.id for n in ast.walk(node) if isinstance(n, ast.Assign)
                   and isinstance(n.value, ast.Name) and any(
                       isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                       and t.slice.value == "cards_internal_chrome_enabled" for t in n.targets)]
    fallback = [n.args[1].id for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "_render_static_graph_page"]
    # Assert
    assert (assignments, fallback) == (["internal_chrome"], ["internal_chrome"])


@pytest.mark.parametrize("override,debug,staff,expected", GATES)
def test_managed_public_view_uses_one_real_decision(new_store, override, debug, staff, expected):
    # Arrange
    request = _request("/apps/cards/legacy/", staff)
    request.scitex_store = new_store()
    # Act
    with override_settings(DEBUG=debug, SCITEX_UI_ELEMENT_INSPECTOR=override):
        observed = _observe_managed_view(request)
    # Assert
    assert observed == {"status": 200, "decisions": [("bool", expected)],
                        "fallbacks": [], "mount": str(expected).lower()}


@pytest.mark.parametrize("decision", [False, True], ids=["quiet", "diagnostic"])
def test_managed_template_failure_reuses_same_real_decision(new_store, tmp_path, decision):
    # Arrange
    request = _request("/apps/cards/legacy/", True)
    request.scitex_store = new_store()
    absent_templates = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
                         "DIRS": [str(tmp_path / "no-templates")], "APP_DIRS": False}]
    # Act
    with override_settings(DEBUG=True, SCITEX_UI_ELEMENT_INSPECTOR=decision,
                           TEMPLATES=absent_templates):
        observed = _observe_managed_view(request)
    # Assert
    assert observed == {"status": 200, "decisions": [("bool", decision)],
                        "fallbacks": [("bool", decision)], "mount": None}
