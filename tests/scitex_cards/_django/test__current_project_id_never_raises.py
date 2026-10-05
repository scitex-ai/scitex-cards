#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The shared header never 500s on project resolution, whatever the host did.

``views._current_project_id`` feeds the identity band every Cards page renders
(the fleet board and the DM page, neither of which is a project surface), so
its contract is total: a raising provider factory, a raising
``resolve_project``, no provider at all, and an scitex-ui predating the
project-scope API must each answer ``""`` rather than raise. The docstring has
always promised this; these cases pin it, because the promise is load-bearing
for two pages and nothing else in the suite exercised it.

The scope API is STUBBED, not imported. These cases turn on the failure half —
a provider that raises, a module that is absent — which the installed SDK
cannot supply, and the view reads the module from ``sys.modules`` at call
time, so a stub there is the exact seam the view consults. No store, no
provider process, no harness.
"""

from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_SCOPE_MODULE = "scitex_ui.project_scope"

_SLOT_MARKER = "data-stx-project-slot"


def _real_scope_module():
    """The installed scope API, or None on an SDK that predates it.

    Read WITHOUT importing: at fixture time ``sys.modules`` either holds the
    real module (already imported by the host environment) or nothing. A
    leftover test stub is never a donor — it is marked below.
    """
    candidate = sys.modules.get(_SCOPE_MODULE)
    if candidate is not None and not getattr(
        candidate, "_SCITEX_CARDS_TEST_STUB", False
    ):
        return candidate
    try:
        import importlib

        return importlib.import_module(_SCOPE_MODULE)
    except ImportError:
        return None


def _stub_scope(**behaviors):
    """A synthetic ``scitex_ui.project_scope`` carrying the given behaviors.

    The stub INHERITS the installed module's remaining surface instead of
    arriving empty: Django's template engine discovers tag libraries by
    walking ``scitex_ui.templatetags``, and that walk imports names (such as
    ``PROJECT_PROVIDER_META_NAME``) from this very module. A bare stub turns
    every template render into an ``InvalidTemplateLibrary`` — a failure of
    the harness, not of the view. Only the two callables the view consults
    are synthetic.
    """
    stub = types.ModuleType(_SCOPE_MODULE)
    # setattr rather than attribute assignment: a ModuleType carries arbitrary
    # names at runtime, and the stub is exactly that kind of dynamic carrier.
    setattr(stub, "_SCITEX_CARDS_TEST_STUB", True)
    donor = _real_scope_module()
    if donor is not None:
        for name in dir(donor):
            if name.startswith("__"):
                continue
            if name in ("host_project_provider", "resolve_project"):
                continue
            setattr(stub, name, getattr(donor, name))
    setattr(stub, "host_project_provider", behaviors.get("provider", lambda: None))
    setattr(
        stub,
        "resolve_project",
        behaviors.get(
            "resolve", lambda request, provider, explicit=None: None
        ),
    )
    return stub


@pytest.fixture
def stub_scope():
    """Install a synthetic scope API for one test, restoring it afterwards.

    ``sys.modules`` swap with try/finally — the same seam the degrades suite
    uses — because the audit forbids ``monkeypatch`` (PA-306) and the view
    imports the module at call time.
    """
    previous = sys.modules.get(_SCOPE_MODULE)
    stub = _stub_scope()
    sys.modules[_SCOPE_MODULE] = stub
    try:
        yield stub
    finally:
        if previous is None:
            sys.modules.pop(_SCOPE_MODULE, None)
        else:
            sys.modules[_SCOPE_MODULE] = previous


@pytest.fixture
def no_scope_api():
    """The deployment whose scitex-ui predates project scope: the import fails.

    A ``None`` entry makes ``from ... import ...`` raise ``ImportError``, which
    is exactly what an absent module raises.
    """
    previous = sys.modules.get(_SCOPE_MODULE)
    sys.modules[_SCOPE_MODULE] = None
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop(_SCOPE_MODULE, None)
        else:
            sys.modules[_SCOPE_MODULE] = previous


def _request(**query):
    request = RequestFactory().get("/dm", query, HTTP_HOST="127.0.0.1")
    return request


def test_a_raising_provider_factory_answers_empty(stub_scope):
    """The host's provider blew up; the band renders without a project."""
    # Arrange
    def _raise():
        raise RuntimeError("host provider exploded")

    setattr(stub_scope, "host_project_provider", _raise)
    # Act
    project = views._current_project_id(_request())
    # Assert
    assert project == ""


def test_a_raising_resolve_answers_empty(stub_scope):
    """Resolution blew up after a provider existed; still no raise."""
    # Arrange
    def _raise(request, provider, explicit=None):
        raise RuntimeError("host resolve exploded")

    setattr(stub_scope, "host_project_provider", lambda: object())
    setattr(stub_scope, "resolve_project", _raise)
    # Act
    project = views._current_project_id(_request())
    # Assert
    assert project == ""


def test_no_provider_answers_empty(stub_scope):
    """No host provider is a deployment shape, not an error to propagate."""
    # Arrange
    setattr(stub_scope, "host_project_provider", lambda: None)
    # Act
    project = views._current_project_id(_request())
    # Assert
    assert project == ""


def test_an_sdk_without_project_scope_answers_empty(no_scope_api):  # noqa: ARG001
    """The import itself fails on older SDKs; the header predates the API."""
    # Arrange
    request = _request()
    # Act
    project = views._current_project_id(request)
    # Assert
    assert project == ""


def test_an_explicit_project_is_stripped_before_resolve(stub_scope):
    """``?project=`` carries user whitespace; the SDK must not see it."""
    # Arrange
    seen = []
    setattr(stub_scope, "host_project_provider", lambda: object())

    def _record(request, provider, explicit=None):
        seen.append(explicit)
        return explicit

    setattr(stub_scope, "resolve_project", _record)
    # Act
    views._current_project_id(_request(project="  proj-x  "))
    # Assert
    assert seen == ["proj-x"]


def test_a_blank_explicit_project_resolves_as_no_preference(stub_scope):
    """Whitespace-only ``?project=`` is no explicit choice, not an empty id."""
    # Arrange
    seen = []
    setattr(stub_scope, "host_project_provider", lambda: object())

    def _record(request, provider, explicit=None):
        seen.append(explicit)
        return explicit

    setattr(stub_scope, "resolve_project", _record)
    # Act
    views._current_project_id(_request(project="   "))
    # Assert
    assert seen == [None]


def test_the_resolved_project_is_returned(stub_scope):
    """The happy path the guards above must not swallow: resolve wins."""
    # Arrange
    setattr(stub_scope, "host_project_provider", lambda: object())
    setattr(
        stub_scope,
        "resolve_project",
        lambda request, provider, explicit=None: explicit or "proj-visited",
    )
    # Act
    project = views._current_project_id(_request(project="proj-x"))
    # Assert
    assert project == "proj-x"


def test_the_dm_page_answers_200_while_the_provider_raises(stub_scope):
    """The page the operator reads DMs on survives a hostile host provider."""
    # Arrange
    def _raise():
        raise RuntimeError("host provider exploded on the DM page")

    setattr(stub_scope, "host_project_provider", _raise)
    request = RequestFactory().get("/dm", HTTP_HOST="127.0.0.1")
    # Act
    response = views.chat_page(request)
    # Assert
    assert (response.status_code, _SLOT_MARKER in response.content.decode()) == (
        200,
        False,
    )
