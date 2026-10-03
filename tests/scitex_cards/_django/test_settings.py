"""Exact settings-parser projection; genuine SDK, no environment activation.

Only the actual DEBUG assignment and inspector presence block are compiled.
Explicit immutable environment inputs exercise their stdlib Mapping protocol;
the complete settings/public-exposure module is not imported or replaced.
"""

import ast
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
from django.test import RequestFactory, override_settings
from scitex_ui.context_processors import element_inspector_enabled

SOURCE = (Path(__file__).resolve().parents[3] /
          "src/scitex_cards/_django/settings.py")


def _source_values(environment):
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    nodes = [n for n in tree.body if
             (isinstance(n, ast.Assign) and any(
                 isinstance(t, ast.Name) and t.id == "DEBUG" for t in n.targets))
             or (isinstance(n, ast.If) and
                 '"SCITEX_UI_ELEMENT_INSPECTOR" in os.environ' in
                 ast.get_source_segment(SOURCE.read_text(), n))]
    namespace = {"os": SimpleNamespace(environ=MappingProxyType(environment))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"),
         namespace)
    return namespace


@pytest.mark.parametrize("value,expected", [
    pytest.param("false", False, id="lower-false"),
    pytest.param("False", False, id="mixed-false"),
    pytest.param("FALSE", False, id="upper-false"),
    pytest.param("", False, id="empty-present"),
    pytest.param("0", False, id="zero"),
    pytest.param("1", False, id="one-is-not-true"),
    pytest.param("yes", False, id="no-new-yes-parser"),
    pytest.param(" true ", False, id="no-new-strip-parser"),
    pytest.param("true", True, id="lower-true"),
    pytest.param("True", True, id="mixed-true"),
    pytest.param("TRUE", True, id="upper-true"),
])
def test_present_key_uses_existing_boolean_parser(value, expected):
    # Arrange
    # Act
    values = _source_values({"SCITEX_UI_ELEMENT_INSPECTOR": value})
    # Assert
    assert values["SCITEX_UI_ELEMENT_INSPECTOR"] is expected


@pytest.mark.parametrize("value,expected", [
    pytest.param("false", False, id="lower-false"),
    pytest.param("False", False, id="mixed-false"),
    pytest.param("FALSE", False, id="upper-false"),
    pytest.param("", False, id="empty-present"),
    pytest.param("0", False, id="zero"),
    pytest.param("1", False, id="one-is-not-true"),
    pytest.param("yes", False, id="no-new-yes-parser"),
    pytest.param(" true ", False, id="no-new-strip-parser"),
    pytest.param("true", True, id="lower-true"),
    pytest.param("True", True, id="mixed-true"),
    pytest.param("TRUE", True, id="upper-true"),
])
def test_present_key_uses_existing_boolean_parser_boolean_type(value, expected):
    # Arrange
    # Act
    values = _source_values({"SCITEX_UI_ELEMENT_INSPECTOR": value})
    # Assert
    assert isinstance(values["SCITEX_UI_ELEMENT_INSPECTOR"], bool)


@pytest.mark.parametrize("debug", ["true", "false"])
def test_absent_key_leaves_setting_absent(debug):
    # Arrange
    # Act
    values = _source_values({"DJANGO_DEBUG": debug})
    # Assert
    assert "SCITEX_UI_ELEMENT_INSPECTOR" not in values


@pytest.mark.parametrize("debug", ["true", "false"])
def test_absent_key_leaves_setting_absent_debug_fallback(debug):
    # Arrange
    # Act
    values = _source_values({"DJANGO_DEBUG": debug})
    # Assert
    assert values["DEBUG"] is (debug == "true")


def test_parsed_false_wins_over_real_debug_and_staff_gate():
    # Arrange
    values = _source_values({"SCITEX_UI_ELEMENT_INSPECTOR": "False",
                             "DJANGO_DEBUG": "true"})
    request = RequestFactory().get("/board")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
    # Act
    with override_settings(DEBUG=values["DEBUG"],
                           SCITEX_UI_ELEMENT_INSPECTOR=values.get(
                               "SCITEX_UI_ELEMENT_INSPECTOR")):
        result = element_inspector_enabled(request)
    # Assert
    assert result is False
