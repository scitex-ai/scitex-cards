#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The SDK project picker degrades on a deployment whose scitex-ui predates it.

WHAT WAS MEASURED, on this tree before the fix. ``project_board.html`` opened
with ``{% load static scitex_project_picker scitex_paths %}``, and ``{% load %}``
of a library that is not installed is a ``TemplateSyntaxError`` raised while the
template **COMPILES** — so ``render_project_board`` raised for EVERY state, the
page answered 500 rather than a board or a refusal, and the docstring of
``_host_picker_available`` ("keeps a missing scitex-ui API a graceful degradation
... instead of a TemplateSyntaxError on a deployment whose SDK predates project
scope") described a fallback branch that could never execute, because the page
died before any context flag was read. Measured 2026-09-27 against scitex-ui
0.20.2, which the declared floor ``scitex-ui>=0.11.1`` permits: 49 tests in this
directory failed on that compile error, and 22 pass once the load is conditional.

The failure, verbatim::

    KeyError: 'scitex_project_picker'
      django/template/defaulttags.py:1067 in find_library
    ...
      django/template/base.py:199 in compile_nodelist

WHY THE FIX IS A PARTIAL AND NOT A FLAG. A context flag is read while the
template RENDERS; a ``{% load %}`` is resolved while it COMPILES, which is
strictly earlier. No flag can reach it. The library is therefore loaded in
``_project_picker.html``, reached ONLY through an ``{% include %}`` inside an
``{% if %}`` — and a partial that is never included is never compiled. The first
test below is the structural guard on that arrangement, because the defect is not
"a missing fallback", it is "the load is in the wrong file".

scitex-writer ships the same partial for the same measured reason (its
``views._project_picker_available``), so this is the fleet's shape rather than a
local arrangement.

THE LIBRARY IS SIMULATED, NOT IMPORTED. scitex-ui 0.20.2 is what is installed on
this host and it ships no ``templatetags/scitex_project_picker.py``; the fleet's
CI resolves a newer one. Registering a stub in the engine's own registry — the
exact dict that both ``{% load %}`` and ``views._project_picker_library_registered``
consult — exercises BOTH branches on EITHER version, which importing the real
library could not do, and it is what lets these tests pass on a host whose SDK
has no project scope at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django import template  # noqa: E402
from django.template import engines  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.handlers import project_board as pb  # noqa: E402

_TEMPLATES = Path(views.__file__).resolve().parent / "templates" / "scitex_cards"

#: The tag library scitex-ui ships from 0.22.0 on.
_TAG_LIBRARY = "scitex_project_picker"

#: ``{% load ... scitex_project_picker ... %}`` — the load tag itself, anywhere.
#: The ``templatetag openblock`` idiom the docstrings use to SPEAK about the tag
#: does not match: ``{%`` there is followed by ``templatetag``, not ``load``.
_LOADS_LIBRARY = re.compile(r"\{%\s*load\b[^%]*\b" + _TAG_LIBRARY + r"\b[^%]*%\}")

#: The one file allowed to name the library in a load tag.
_PARTIAL = "_project_picker.html"

#: The SDK's own markup hook, emitted by scitex-ui's ``_project_picker.html``;
#: the stub below emits it too, so an assertion is about the slot being REACHED
#: rather than about the SDK's stylesheet links.
_SDK_MARKER = "data-stx-project-picker"

#: The standalone fallback form's hook, from project_board.html itself.
_STANDALONE_MARKER = "data-stx-picker-select"

#: The slot the shared header contributes, present only when the picker is.
_SLOT_MARKER = "data-stx-project-slot"

#: The two states that are NOT 200 — the page's own status contract, read from
#: the handler rather than restated here.
_STATES = (
    pb.NO_PROJECT,
    pb.DENIED,
    pb.UNAVAILABLE,
    pb.EMPTY,
    pb.FILTERED_EMPTY,
    pb.READY,
)


class _User:
    def __init__(self, name: str = "alice"):
        self._name = name
        self.is_staff = False
        self.is_authenticated = True

    def get_username(self) -> str:
        return self._name


def _request(path: str = "/projects"):
    from django.test import RequestFactory

    request = RequestFactory().get(path, HTTP_HOST="127.0.0.1")
    request.user = _User()
    return request


def _state(state: str, **overrides):
    fields = {
        "state": state,
        "principal": "alice",
        "is_staff": False,
        "project": "proj-alpha",
        "projects": ("proj-alpha",),
    }
    fields.update(overrides)
    return pb.BoardState(**fields)


def _render_board(state, **kwargs) -> str:
    return pb.render_project_board(_request(), state, **kwargs).content.decode("utf-8")


def _render_header() -> str:
    """The shared band, rendered with the context BOTH top-level pages provide."""
    return render_to_string(
        "scitex_cards/_page_header.html",
        {
            "api_base": "/",
            "active": "board",
            "page_title": "SciTeX Cards",
            "scitex_cards_version": "0.0.0",
            "cards_user": "alice",
            "cards_project_picker_available": True,
            "cards_current_project_id": "proj-alpha",
        },
    )


class _StubLibrary:
    """The registered stub, plus what it was asked to render.

    A holder rather than attributes hung off Django's ``Library``: the registry
    stores a ``Library``, but the CALL RECORD is this test's bookkeeping and does
    not belong on Django's own class.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.library = template.Library()
        self.library.simple_tag(name=_TAG_LIBRARY)(self._render)

    def _render(self, scope=None, current="", **_ignored):
        """Answer the SDK's markup hook.

        ``mark_safe`` because the SDK's tag renders a markup FRAGMENT, not a
        value to escape — Django escapes a ``simple_tag``'s return otherwise, and
        the escaped form would make every assertion here about entities rather
        than about the slot.
        """
        self.calls.append({"scope": scope, "current": current})
        fragment = (
            f'<div {_SDK_MARKER} data-scope="{scope}" data-current="{current}"></div>'
        )
        return mark_safe(fragment)  # noqa: S308 - a literal this test controls


#: The fixture resets compiled templates as it swaps this library, so a partial
#: compiled by another test cannot keep calling that test's SDK tag function.
_STUB = _StubLibrary()


def _reset_template_loaders(engine):
    """Discard compiled tag functions when a fixture changes their registry."""
    for loader in engine.template_loaders:
        reset = getattr(loader, "reset", None)
        if reset is not None:
            reset()


@pytest.fixture
def sdk_library():
    """Register the STUB SDK — both halves the picker's availability turns on.

    THE TAG LIBRARY: the same dict the real one occupies, so ``{% load %}``
    resolves and ``views._project_picker_library_registered`` answers True.

    AND ``scitex_ui.project_scope``, which is the SEPARATE half — an installed
    scitex-ui can in principle ship one without the other, and on this host
    (0.20.2) it ships neither. Without this module ``host_project_provider_url``
    raises ImportError and the availability flag is false no matter what the
    settings say, so the "picker present" branch would be untestable here rather
    than merely untested.

    The stub reads the SETTING for the provider URL, the way the real
    ``host_project_provider_url`` does, so ``override_settings`` stays the
    mechanism that turns a host provider on — not a patch of our own function.

    Both halves are restored afterwards, so "no SDK project scope" remains the
    default state of the suite.
    """
    import sys
    import types

    engine = engines["django"].engine
    previous_library = engine.template_libraries.get(_TAG_LIBRARY)
    previous_module = sys.modules.get("scitex_ui.project_scope")
    stub_sdk = types.ModuleType("scitex_ui.project_scope")

    def _provider_url() -> str:
        from django.conf import settings

        return str(getattr(settings, "SCITEX_PROJECT_PROVIDER_URL", "") or "")

    class _Provider:
        def list_projects(self, request=None):
            return []

        def last_visited(self, request=None):
            return None

        def remember(self, request, project_id):
            return None

    # setattr rather than attribute assignment: a ModuleType carries arbitrary
    # names at runtime, and the stub is exactly that kind of dynamic carrier.
    setattr(stub_sdk, "host_project_provider_url", _provider_url)
    setattr(
        stub_sdk,
        "host_project_provider",
        lambda: _Provider() if _provider_url() else None,
    )
    setattr(
        stub_sdk,
        "resolve_project",
        lambda request, provider, explicit=None: (
            explicit or provider.last_visited(request)
        ),
    )

    _STUB.calls.clear()
    engine.template_libraries[_TAG_LIBRARY] = _STUB.library
    sys.modules["scitex_ui.project_scope"] = stub_sdk
    _reset_template_loaders(engine)
    try:
        yield _STUB
    finally:
        if previous_library is None:
            engine.template_libraries.pop(_TAG_LIBRARY, None)
        else:
            engine.template_libraries[_TAG_LIBRARY] = previous_library
        if previous_module is None:
            sys.modules.pop("scitex_ui.project_scope", None)
        else:
            sys.modules["scitex_ui.project_scope"] = previous_module
        _reset_template_loaders(engine)


@pytest.fixture
def no_sdk_library():
    """Evict the REAL tag library for one test, restoring it afterwards.

    The absence-premise tests pin behaviour "without the library", but the
    suite also runs where scitex-ui SHIPS it (0.22.0+, including the fleet
    CI) — there the premise is false unless a test establishes it, and the
    un-evicted render takes the include path: the real tag, with no provider
    behind it, renders nothing, so the body carries NEITHER marker. Eviction
    goes through the engine registry, the exact dict ``{% load %}`` and
    ``views._project_picker_library_registered`` consult, so the render sees
    "library absent" exactly as a pre-0.22.0 deployment would.
    """
    engine = engines["django"].engine
    previous = engine.template_libraries.pop(_TAG_LIBRARY, None)
    _reset_template_loaders(engine)
    try:
        yield
    finally:
        if previous is not None:
            engine.template_libraries[_TAG_LIBRARY] = previous
        _reset_template_loaders(engine)


# --- the load lives in the partial, and nowhere else -----------------------


def test_only_the_picker_partial_loads_the_sdk_tag_library():
    """THE structural guard. The defect was never a missing fallback — it was
    the ``{% load %}`` sitting in a page top level, where no guard can reach it.

    Naming the offender in the assertion is deliberate: a bare "False" tells the
    next reader that something is wrong, not which file to open.
    """
    # Arrange
    sources = sorted(_TEMPLATES.rglob("*.html"))
    # Act
    loading = sorted(p.name for p in sources if _LOADS_LIBRARY.search(p.read_text("utf-8")))
    # Assert
    assert loading == [_PARTIAL]


def test_the_shared_header_reaches_the_picker_through_a_guarded_include():
    """The header renders on BOTH top-level pages, so a top-level load there
    took the operator's fleet board and DM down with it, not just the
    project-scoped page."""
    # Arrange
    source = (_TEMPLATES / "_page_header.html").read_text("utf-8")
    # Act
    guarded = (
        '{% if cards_project_picker_available %}' in source,
        '{% include "scitex_cards/_project_picker.html"' in source,
    )
    # Assert
    assert guarded == (True, True)


def test_the_project_board_reaches_the_sdk_picker_through_the_same_partial():
    """One partial, two callers — a second copy of the tag is a second thing to
    remember to guard."""
    # Arrange
    source = (_TEMPLATES / "project_board.html").read_text("utf-8")
    # Act
    reaches = '{% include "scitex_cards/_project_picker.html"' in source
    # Assert
    assert reaches


# --- absent library: the page renders, it does not 500 ---------------------


@pytest.mark.parametrize("state", _STATES)
def test_every_state_answers_its_own_status_with_the_sdk_library_absent(state):
    """Before the fix this raised for all six, so the page had ONE behaviour —
    a 500 — and the six distinct states the MVP exists to distinguish were
    unreachable on any deployment below the SDK the code assumed."""
    # Arrange
    expected = pb.STATUS_FOR_STATE.get(state, 200)
    # Act
    status = pb.render_project_board(_request(), _state(state)).status_code
    # Assert
    assert status == expected


def test_the_standalone_form_still_answers_when_the_sdk_library_is_absent():
    """Degrading to nothing would trade a 500 for a dead end. The fallback the
    provider guard was always meant to reach is now reachable."""
    # Arrange
    state = _state(pb.NO_PROJECT, projects=("proj-alpha",))
    # Act
    body = _render_board(state, host_picker_available=False)
    # Assert
    assert _STANDALONE_MARKER in body


def test_a_host_provider_cannot_conjure_the_sdk_picker_without_the_library(
    no_sdk_library,  # noqa: ARG001 - the fixture IS the arrangement (eviction)
):
    """The library half is NOT overridable, and it has to be: a caller that
    claims a host provider while the library is missing would otherwise rebuild
    the compile-time crash this fix removes.

    The fixture evicts the library first: the premise is "without the
    library", and on an SDK that ships it (0.22.0+) only an eviction makes
    that true.
    """
    # Arrange
    state = _state(pb.NO_PROJECT, projects=("proj-alpha",))
    # Act
    body = _render_board(state, host_picker_available=True)
    # Assert
    assert (_SDK_MARKER in body, _STANDALONE_MARKER in body) == (False, True)


def test_the_shared_header_renders_unchanged_when_the_picker_is_unavailable():
    """The band is on every Cards page. When the picker cannot render, the
    header must be exactly what it was before the slot existed."""
    # Arrange
    # Act
    html = render_to_string(
        "scitex_cards/_page_header.html",
        {
            "api_base": "/",
            "active": "board",
            "page_title": "SciTeX Cards",
            "scitex_cards_version": "0.0.0",
            "cards_user": "alice",
            "cards_project_picker_available": False,
        },
    )
    # Assert
    assert (_SLOT_MARKER in html, _SDK_MARKER in html) == (False, False)


def test_the_slot_appears_exactly_when_the_views_own_flag_says_it_can():
    """End to end on the SDK this host actually has, and the loop that matters:
    the header is fed the flag the VIEW computes — not one this test picked —
    and the slot appears if and only if that flag is true. On a deployment whose
    scitex-ui predates the picker the flag is false, the partial is never
    compiled, and the band still renders; before the fix the load sat at the top
    level of the page, so this same host raised instead.
    """
    # Arrange
    flag = (
        views._project_picker_library_registered()
        and views._host_project_provider_registered()
    )
    # Act
    html = render_to_string(
        "scitex_cards/_page_header.html",
        {
            "api_base": "/",
            "active": "board",
            "page_title": "SciTeX Cards",
            "scitex_cards_version": "0.0.0",
            "cards_user": "alice",
            "cards_project_picker_available": flag,
            "cards_current_project_id": "",
        },
    )
    # Assert
    assert (_SLOT_MARKER in html, "stx-cards-header" in html) == (flag, True)


# --- the view supplies the keys the partial reads --------------------------


def test_the_shell_context_carries_the_two_keys_the_slot_reads():
    """The partial can be perfect and the slot still never render if the VIEW
    forgets the context. Both keys, named, in the one dict every Cards page
    builds — so neither page can quietly lose them.
    """
    # Arrange
    request = _request("/")
    # Act
    context = views._cards_shell_context(request, "/")
    # Assert
    assert (
        context.get("cards_project_picker_available"),
        context.get("cards_current_project_id"),
    ) == (False, "")


def test_the_dm_page_renders_without_the_slot_on_an_sdk_without_project_scope():
    """The OTHER page the shared header renders on. A defect here is not a
    project-board defect, it is every Cards page — which is why the header is
    exercised through the page rather than only through the partial."""
    # Arrange
    from django.test import RequestFactory

    # Act
    response = views.chat_page(RequestFactory().get("/dm", HTTP_HOST="127.0.0.1"))
    body = response.content.decode("utf-8")
    # Assert
    assert (response.status_code, _SLOT_MARKER in body) == (200, False)


def test_the_dm_page_renders_the_picker_once_when_the_sdk_provides_one(
    sdk_library,  # noqa: ARG001
):
    """One header, one selector, on the page the operator reads DMs on."""
    # Arrange
    from django.test import RequestFactory, override_settings

    request = RequestFactory().get("/dm", HTTP_HOST="127.0.0.1")
    # Act
    with override_settings(SCITEX_PROJECT_PROVIDER_URL="/api_project_scope"):
        body = views.chat_page(request).content.decode("utf-8")
    # Assert
    assert (body.count(_SLOT_MARKER), body.count(_SDK_MARKER)) == (1, 1)


# --- the library present: the slot is reached, once ------------------------


def test_the_picker_flag_follows_the_engine_registry(sdk_library):  # noqa: ARG001
    """The flag reads the registry ``{% load %}`` reads, not "is some module
    importable" — the two can disagree, and when they do the page crashes."""
    # Arrange
    # Act
    registered = views._project_picker_library_registered()
    # Assert
    assert registered


def test_the_flag_is_false_when_the_library_is_not_registered(
    no_sdk_library,  # noqa: ARG001 - establish absence on SDKs that ship it
):
    """The other half of the same claim, and the state this host is in."""
    # Arrange
    # Act
    registered = views._project_picker_library_registered()
    # Assert
    assert not registered


def test_the_project_board_reaches_the_sdk_picker_when_the_library_is_registered(
    sdk_library,  # noqa: ARG001
):
    """The path the fix must not have broken: with a host provider AND the
    library, the SDK's selector renders and the leaf ships no second one."""
    # Arrange
    state = _state(pb.NO_PROJECT, projects=("proj-alpha",))
    # Act
    body = _render_board(state, host_picker_available=True)
    # Assert
    assert (_SDK_MARKER in body, _STANDALONE_MARKER in body) == (True, False)


def test_the_shared_header_renders_the_picker_into_its_own_slot(
    sdk_library,  # noqa: ARG001
):
    """The slot the shared header contributes — identity on the left, the
    canonical selector pushed to the right edge."""
    # Arrange
    # Act
    html = _render_header()
    # Assert
    assert (_SLOT_MARKER in html, _SDK_MARKER in html) == (True, True)


def test_the_shared_header_shows_the_project_the_request_resolved_to(
    sdk_library,  # noqa: ARG001
):
    """The slot is fed the RESOLVED project, not a template-side guess — the
    precedence belongs to scitex-ui's ``resolve_project``."""
    # Arrange
    # Act
    html = _render_header()
    # Assert
    assert 'data-current="proj-alpha"' in html


def test_the_shared_header_loads_the_library_exactly_once(sdk_library):
    """Two calls to the SDK tag would be the second selector the SDK exists to
    prevent; the stub records its own invocations."""
    # Arrange
    # Act
    _render_header()
    # Assert
    assert len(sdk_library.calls) == 1
