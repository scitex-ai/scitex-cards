"""Cards delegates picker visibility to the real SDK scope/auth contract.

The library is optional at the Cards dependency floor. The degradation suite
tests its absence; these cases exercise its actual implementation when shipped.
No store or project-list HTTP request is needed to render the canonical tag.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")
sdk = pytest.importorskip("scitex_ui.templatetags.scitex_project_picker")

from django.template import engines  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402

from scitex_cards._django.handlers import project_board as pb  # noqa: E402

PICKER = "data-stx-project-picker"
SLOT = "data-stx-project-slot"
MISSING = object()


class _User:
    is_staff = False

    def __init__(self, authenticated=True):
        self.is_authenticated = authenticated

    def get_username(self):
        return "alice"


def _request(authenticated=True):
    request = RequestFactory().get("/projects", HTTP_HOST="127.0.0.1")
    request.user = _User(authenticated)
    return request


def _context(scope=MISSING, **extra):
    context = {
        "api_base": "/",
        "active": "board",
        "page_title": "SciTeX Cards",
        "scitex_cards_version": "test",
        "cards_user": "alice",
        "cards_project_picker_available": True,
        "cards_current_project_id": "A",
        "current": "A",
    }
    if scope is not MISSING:
        context["app_scope"] = scope
    context.update(extra)
    return context


@pytest.fixture
def real_picker():
    """Restore the real library and discard compiled fixture tag functions."""
    engine = engines["django"].engine
    previous = engine.template_libraries.get("scitex_project_picker")

    def reset():
        for loader in engine.template_loaders:
            if hasattr(loader, "reset"):
                loader.reset()

    engine.template_libraries["scitex_project_picker"] = sdk.register
    reset()
    try:
        with override_settings(SCITEX_PROJECT_PROVIDER_URL="/synthetic-project-provider"):
            yield
    finally:
        if previous is None:
            engine.template_libraries.pop("scitex_project_picker", None)
        else:
            engine.template_libraries["scitex_project_picker"] = previous
        reset()


def _canonical(context, request):
    # Use the tag itself as the capability authority. A future canonical user
    # capability can emit markup without Cards adding a second scope policy.
    return sdk.scitex_project_picker({**context, "request": request}, current="A")


@pytest.mark.parametrize("active", ["board", "dm"])
@pytest.mark.parametrize("scope", [MISSING, "user", "", "project"])
def test_shared_header_follows_real_canonical_scope(real_picker, active, scope):
    # Arrange
    request = _request()
    context = _context(scope, active=active)
    canonical = _canonical(context, request)
    # Act
    html = render_to_string("scitex_cards/_page_header.html", context, request=request)
    # Assert
    assert (html.count(PICKER), html.count(SLOT), canonical in html) == (
        int(bool(canonical)), int(bool(canonical)), True
    )


@pytest.mark.parametrize("scope", [MISSING, "user", "project"])
def test_signed_out_header_has_no_empty_added_slot(real_picker, scope):
    # Arrange
    context = _context(scope)
    request = _request(False)
    # Act
    html = render_to_string(
        "scitex_cards/_page_header.html", context, request=request
    )
    # Assert
    assert (PICKER in html, SLOT in html) == (False, False)


def test_project_context_survives_omitted_caller_scope(real_picker):
    # Arrange
    context = _context("project", picker_slot=True)
    request = _request()
    # Act
    html = render_to_string(
        "scitex_cards/_project_picker.html", context, request=request
    )
    # Assert
    assert (html.count(PICKER), html.count(SLOT)) == (1, 1)


def test_shared_header_does_not_inherit_project_override(real_picker):
    # Arrange
    request = _request()
    context = _context("user", picker_scope="project")
    canonical = _canonical(context, request)
    # Act
    html = render_to_string("scitex_cards/_page_header.html", context, request=request)
    # Assert
    assert (html.count(PICKER), html.count(SLOT)) == (
        int(bool(canonical)), int(bool(canonical))
    )


def test_distinct_project_board_requests_project_scope(real_picker):
    # Arrange
    state = pb.BoardState(
        state=pb.NO_PROJECT,
        principal="alice",
        is_staff=False,
        project="A",
        projects=("A",),
    )
    # Render the actual caller under user scope. Only its explicit project
    # picker is expected; the user-scoped shared header stays canonical.
    request = _request()
    context = _context("user", board=state, host_picker_available=True)
    canonical = _canonical(context, request)
    # Act
    html = render_to_string("scitex_cards/project_board.html", context, request=request)
    # Assert
    assert (html.count(PICKER), html.count(SLOT), 'data-current="A"' in html) == (
        1 + int(bool(canonical)), int(bool(canonical)), True
    )


def test_provider_absence_does_not_leave_slot(real_picker):
    # Arrange
    context = _context("project")
    request = _request()
    # Act
    with override_settings(SCITEX_PROJECT_PROVIDER_URL=""):
        html = render_to_string(
            "scitex_cards/_page_header.html", context, request=request
        )
    # Assert
    assert (PICKER in html, SLOT in html) == (False, False)


def test_missing_library_flag_avoids_compiling_real_partial(real_picker):
    # Arrange
    engine = engines["django"].engine
    engine.template_libraries.pop("scitex_project_picker")
    for loader in engine.template_loaders:
        if hasattr(loader, "reset"):
            loader.reset()
    context = _context("project", cards_project_picker_available=False)
    request = _request()
    # Act
    html = render_to_string(
        "scitex_cards/_page_header.html", context, request=request
    )
    # Assert
    assert (PICKER in html, SLOT in html) == (False, False)
