"""Exact settings-parser projection; genuine SDK, no environment activation.

The inspector tests compile the actual DEBUG assignment and presence block
with immutable environment inputs. Locale tests project the shared settings
and middleware declarations and observe genuine framework request negotiation.
The complete settings/public-exposure module is not imported or replaced.
"""

import ast
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.utils import translation
from django.utils.module_loading import import_string
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


def _locale_source_values():
    """Execute only the actual shared-language and middleware declarations."""
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    nodes = [node for node in tree.body if (
        isinstance(node, ast.ImportFrom) and node.module == "scitex_app.i18n"
    ) or (
        isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MIDDLEWARE"
            for target in node.targets
        )
    ) or (
        isinstance(node, ast.Expr)
        and ast.unparse(node) == "globals().update(i18n_settings())"
    )]
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"),
         namespace)
    return {name: namespace[name] for name in (
        "USE_I18N", "LANGUAGE_CODE", "LANGUAGES", "MIDDLEWARE"
    )}


def _framework_locale_response(accept_language, cookie):
    """Observe genuine middleware negotiation, independently of board views."""
    values = _locale_source_values()
    entry = next(item for item in values["MIDDLEWARE"]
                 if item == "django.middleware.locale.LocaleMiddleware")
    middleware_type = import_string(entry)
    request = RequestFactory().get(
        "/board-v3/", HTTP_ACCEPT_LANGUAGE=accept_language, HTTP_COOKIE=cookie
    )
    # A genuine framework URLconf avoids importing the board/store. The
    # context restores language state; the request's header/cookie selects it.
    with override_settings(**values, ROOT_URLCONF="django.conf.urls.i18n"), \
            translation.override(None):
        response = middleware_type(lambda request: HttpResponse("locale seam"))(
            request
        )
        return (
            request.LANGUAGE_CODE,
            response["Content-Language"],
            "Accept-Language" in response.get("Vary", ""),
        )


def test_shared_settings_offer_english_and_japanese():
    # Arrange
    # Act
    values = _locale_source_values()
    # Assert
    assert (values["USE_I18N"], values["LANGUAGE_CODE"], values["LANGUAGES"]) == (
        True, "en", [("en", "English"), ("ja", "日本語")]
    )


def test_shared_locale_preserves_password_and_security_order():
    # Arrange
    # Act
    values = _locale_source_values()
    # Assert
    assert values["MIDDLEWARE"] == [
        "django.middleware.gzip.GZipMiddleware",
        "scitex_cards._django._board_auth.BoardPasswordMiddleware",
        "django.middleware.security.SecurityMiddleware",
        "django.middleware.locale.LocaleMiddleware",
        "django.middleware.common.CommonMiddleware",
    ]


@pytest.mark.parametrize("header,cookie,expected", [
    pytest.param("en", "", "en", id="english-header"),
    pytest.param("ja", "", "ja", id="japanese-header"),
    pytest.param("xx", "", "en", id="unsupported-header"),
    pytest.param("en", "django_language=ja", "ja", id="japanese-cookie"),
    pytest.param("ja", "django_language=en", "en", id="english-cookie"),
    pytest.param("ja", "django_language=xx", "ja", id="unsupported-cookie"),
])
def test_framework_locale_uses_real_request_selection(header, cookie, expected):
    # Arrange
    # Act
    observed = _framework_locale_response(header, cookie)
    # Assert
    assert observed == (expected, expected, True)
