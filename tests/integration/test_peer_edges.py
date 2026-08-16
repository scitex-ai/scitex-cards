#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-edge INTEGRATION + DEGRADATION tests for scitex-cards's OPTIONAL peers.

scitex-cards's Django board (``scitex_cards._django``) wires into two *optional*
sibling SciTeX packages. Both edges are guarded in source so a lean
``pip install scitex-cards`` (no ``[web]``/``[dev]`` extras) still works:

Edge 1 — ``scitex-app`` (``_django/apps.py``)
    ``ScitexCardsConfig`` inherits ``scitex_app._django.ScitexAppConfig`` when
    scitex-app is installed (so the board registers as a scitex-hub module),
    and falls back to Django's plain ``AppConfig`` on ``ImportError`` otherwise.

Edge 2 — ``scitex-ui`` (``_django/settings.py``)
    ``scitex_ui`` is appended to ``INSTALLED_APPS`` only when importable (shared
    Django shell components), and the ``scitex_ui.context_processors.\
element_inspector`` context processor is added only when that submodule exists
    (``scitex-ui>=0.5.0``) — an older scitex-ui must degrade gracefully.

This file mirrors the canonical reference template
``scitex-io/tests/integration/test_figrecipe_edge.py``:

  1. INTEGRATION (peer PRESENT): exercise the real collaborator, guarded with
     ``pytest.importorskip`` so the suite stays green on minimal installs.

  2. DEGRADATION (peer ABSENT): simulate the dependency missing in a hermetic,
     reversible way (a ``sys.modules`` snapshot/restore fixture; for scitex-ui's
     submodule, a reversible emptying of the parent ``__path__``), then assert
     the *documented, caller-safe* contract holds — the board keeps working and
     no opaque traceback escapes.

Conventions honoured (kept consistent with the rest of the suite):
  - One assertion per test; shared setup lifted into fixtures.
  - Explicit Arrange / Act / Assert markers.
  - No ``monkeypatch`` / ``mocker``: the absent-peer fixtures hand-swap
    ``sys.modules`` / ``__path__`` and restore them on teardown.

Discovered degradation contracts (empirically verified against the installed
peers via the project's interpreter):
  - scitex-app absent -> ``ScitexCardsConfig`` is a subclass of Django's plain
    ``django.apps.AppConfig`` (NOT of any scitex_app class). The board still
    registers as an ordinary Django app.
  - scitex-ui present but ``context_processors`` submodule absent -> settings
    import cleanly, ``scitex_ui`` stays in ``INSTALLED_APPS``, and NO
    ``element_inspector`` context processor is wired.
"""

from __future__ import annotations

import importlib
import sys

import pytest

# The whole edge surface lives under Django; on a lean install (no web extra)
# there is nothing to integrate against, so skip the module cleanly.
pytest.importorskip("django")


def _configure_django_once():
    """Point Django at the standalone board settings and call setup() once."""
    import os

    import django
    from django.conf import settings

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")
    if not settings.configured:
        django.setup()


# ===========================================================================
# Edge 1: scitex-app
# ===========================================================================
# ---------------------------------------------------------------------------
# 1a. INTEGRATION  —  scitex-app PRESENT
# ---------------------------------------------------------------------------
def test_board_appconfig_subclasses_scitex_app_when_present():
    """With scitex-app installed, the board AppConfig is a ScitexAppConfig."""
    # Arrange
    scitex_app_django = pytest.importorskip("scitex_app._django")
    from scitex_cards._django.apps import ScitexCardsConfig

    # Act
    is_subclass = issubclass(ScitexCardsConfig, scitex_app_django.ScitexAppConfig)
    # Assert
    assert is_subclass


# ---------------------------------------------------------------------------
# 1b. DEGRADATION  —  scitex-app ABSENT
# ---------------------------------------------------------------------------
@pytest.fixture
def scitex_app_absent():
    """Reload ``scitex_cards._django.apps`` with scitex-app made unimportable.

    Hermetic and reversible:
      1. snapshot the whole ``sys.modules`` so teardown restores it exactly;
      2. evict ``scitex_app`` (+ submodules) and the consumer module
         (``scitex_cards._django.apps``), then shadow ``scitex_app`` with
         ``None`` so a fresh ``import scitex_app`` raises ImportError;
      3. reload the consumer so it re-runs its ``try/except ImportError``
         guard under the missing peer.

    Yields the freshly reloaded ``apps`` module.
    """
    import scitex_cards._django.apps  # noqa: F401 (ensure importable first)

    snapshot = dict(sys.modules)

    def _evict(name: str) -> bool:
        return (
            name == "scitex_app"
            or name.startswith("scitex_app.")
            or name == "scitex_cards._django.apps"
        )

    for name in [n for n in list(sys.modules) if _evict(n)]:
        del sys.modules[name]
    sys.modules["scitex_app"] = None  # type: ignore[assignment]
    reloaded = importlib.import_module("scitex_cards._django.apps")

    try:
        yield reloaded
    finally:
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]
        sys.modules.update(snapshot)


def test_scitex_app_absent_fixture_blocks_the_import(scitex_app_absent):
    """Sanity: under the fixture, ``import scitex_app`` really does fail."""
    # Arrange
    _ = scitex_app_absent
    # Act
    module_name = "scitex_app"
    # Assert
    with pytest.raises(ImportError):
        importlib.import_module(module_name)


def test_board_appconfig_falls_back_to_plain_django_appconfig(scitex_app_absent):
    """Without scitex-app, ScitexCardsConfig subclasses Django's plain AppConfig."""
    # Arrange
    from django.apps import AppConfig

    # Act
    is_plain_django_appconfig = issubclass(
        scitex_app_absent.ScitexCardsConfig, AppConfig
    )
    # Assert
    assert is_plain_django_appconfig


def test_board_appconfig_keeps_board_label_without_scitex_app(scitex_app_absent):
    """The degraded AppConfig still carries the board's registration label."""
    # Arrange
    config = scitex_app_absent.ScitexCardsConfig
    # Act
    label = config.label
    # Assert
    assert label == "scitex_cards_board"


# ===========================================================================
# Edge 2: scitex-ui
# ===========================================================================
# ---------------------------------------------------------------------------
# 2a. INTEGRATION  —  scitex-ui PRESENT
# ---------------------------------------------------------------------------
@pytest.fixture
def settings_with_scitex_ui_present():
    """Reload the board settings with the real (present) scitex-ui; yield it."""
    pytest.importorskip("scitex_ui")
    _configure_django_once()

    snapshot = dict(sys.modules)
    sys.modules.pop("scitex_cards._django.settings", None)
    reloaded = importlib.import_module("scitex_cards._django.settings")

    try:
        yield reloaded
    finally:
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]
        sys.modules.update(snapshot)


def test_settings_installs_scitex_ui_app_when_present(
    settings_with_scitex_ui_present,
):
    """scitex-ui is registered in INSTALLED_APPS when it is importable."""
    # Arrange
    settings = settings_with_scitex_ui_present
    # Act
    installed = "scitex_ui" in settings.INSTALLED_APPS
    # Assert
    assert installed


def test_settings_wires_element_inspector_when_context_processors_present(
    settings_with_scitex_ui_present,
):
    """The element-inspector context processor is wired when the submodule exists."""
    # Arrange
    pytest.importorskip("scitex_ui.context_processors")
    settings = settings_with_scitex_ui_present
    ctx_processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    # Act
    wired = any("element_inspector" in cp for cp in ctx_processors)
    # Assert
    assert wired


# ---------------------------------------------------------------------------
# 2b. DEGRADATION  —  scitex-ui present but context_processors submodule ABSENT
# ---------------------------------------------------------------------------
@pytest.fixture
def settings_with_scitex_ui_context_processors_absent():
    """Reload settings with scitex-ui present but its ``context_processors`` gone.

    This is the contract the source comment explicitly promises: an *older*
    ``scitex-ui`` (< 0.5.0) that lacks the ``context_processors`` submodule must
    degrade gracefully rather than raising on settings import.

    Hermetic and reversible:
      1. snapshot ``sys.modules``;
      2. evict the cached ``context_processors`` submodule and the settings
         module;
      3. temporarily empty the parent ``scitex_ui.__path__`` so the submodule
         becomes undiscoverable (``find_spec`` returns ``None``, exactly as for
         a genuinely-absent submodule of a present parent) while the parent
         package itself stays importable;
      4. reload settings under that condition.
    Teardown restores ``__path__`` and the ``sys.modules`` table exactly.

    Yields the freshly reloaded settings module.
    """
    scitex_ui = pytest.importorskip("scitex_ui")
    _configure_django_once()

    snapshot = dict(sys.modules)
    original_path = list(scitex_ui.__path__)

    sys.modules.pop("scitex_ui.context_processors", None)
    sys.modules.pop("scitex_cards._django.settings", None)
    scitex_ui.__path__[:] = []  # make the submodule undiscoverable
    try:
        reloaded = importlib.import_module("scitex_cards._django.settings")
        yield reloaded
    finally:
        scitex_ui.__path__[:] = original_path
        for name in list(sys.modules):
            if name not in snapshot:
                del sys.modules[name]
        sys.modules.update(snapshot)


def test_context_processors_fixture_hides_the_submodule(
    settings_with_scitex_ui_context_processors_absent,
):
    """Sanity: under the fixture, the context_processors submodule is gone."""
    # Arrange
    _ = settings_with_scitex_ui_context_processors_absent
    # Act
    spec = importlib.util.find_spec("scitex_ui.context_processors")
    # Assert
    assert spec is None


def test_settings_import_clean_without_context_processors(
    settings_with_scitex_ui_context_processors_absent,
):
    """Settings still import cleanly when scitex-ui lacks context_processors."""
    # Arrange
    settings = settings_with_scitex_ui_context_processors_absent
    # Act
    has_templates = hasattr(settings, "TEMPLATES")
    # Assert
    assert has_templates


def test_settings_skips_element_inspector_without_context_processors(
    settings_with_scitex_ui_context_processors_absent,
):
    """No element-inspector context processor is wired when the submodule is absent."""
    # Arrange
    settings = settings_with_scitex_ui_context_processors_absent
    ctx_processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    # Act
    wired = any("element_inspector" in cp for cp in ctx_processors)
    # Assert
    assert not wired


def test_settings_keeps_scitex_ui_app_without_context_processors(
    settings_with_scitex_ui_context_processors_absent,
):
    """scitex-ui stays in INSTALLED_APPS even when its newer submodule is absent."""
    # Arrange
    settings = settings_with_scitex_ui_context_processors_absent
    # Act
    installed = "scitex_ui" in settings.INSTALLED_APPS
    # Assert
    assert installed


# EOF
