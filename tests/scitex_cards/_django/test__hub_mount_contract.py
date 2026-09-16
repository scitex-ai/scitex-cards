#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hub-mount compatibility contract for the scitex-app floor.

The board pages (board_v3, standalone) extend ``scitex_app/app_shell.html``.
That template is built by the SIBLING scitex-app package and first SHIPPED IN
THE WHEEL at version 0.24.0 (scitex-app #189); 0.22.1 and 0.23.0 ship no Django
templates at all, so below the floor the board degrades to the static-graph
fallback instead of painting the shell — the silent wrong-board shape the
operator directly observed (and that Hub 0.20.0-alpha must not regress).

``test__board_shell_migration.py`` pins that OUR templates point at the shell
and that the views degrade cleanly when it is absent. THIS file pins the one
thing the Hub needs before it pins / deploys: the ``scitex-app`` dependency
floor in ``pyproject.toml`` is at or above the shell-bearing version. The Hub
declares ``scitex-app>=0.25.0``; 0.25.0 >= 0.24.0, so a floor pinned here is
satisfied by the Hub's app by construction. If someone lowers the floor below
0.24.0 (e.g. to "relax" the dependency) this test goes red BEFORE the board can
silently fall back to the graph.

Hermetic: it reads ``pyproject.toml`` as text via ``tomllib`` and the installed
scitex-app distribution via ``importlib.metadata``. No store, no database, no
network.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("django")

#: The first scitex-app wheel to ship ``app_shell.html``. The floor may not drop
#: below this or the board silently renders the graph fallback.
_SHELL_BEARING = (0, 24, 0)

#: The dependency line we are pinning, and the regex that pulls its lower bound
#: out of a PEP 440 specifier string (``>=0.24.0``, ``>=0.24,<1``, ``~=0.24``…).
_REQ_RE = re.compile(r"(scitex-app)\s*([^\s,]*)")
_LOWER_RE = re.compile(r"(?:>=|==|~=|===)\s*(\d+)\.(\d+)(?:\.(\d+))?")


def _pyproject_path() -> Path:
    """The repository ``pyproject.toml``.

    This file lives at ``tests/scitex_cards/_django/`` — three directories
    below the repo root — so the root is ``parents[3]``.
    """
    return Path(__file__).resolve().parents[3] / "pyproject.toml"


def _project_dependencies() -> list[str]:
    """Shared Arrange: the ``project.dependencies`` list, as plain strings."""
    data = tomllib.loads(_pyproject_path().read_text(encoding="utf-8"))
    return list(data.get("project", {}).get("dependencies", []))


def _scitex_app_lower_bound() -> tuple[int, int, int] | None:
    """Parse the lower bound of the ``scitex-app`` dependency, or None.

    Returns None when the specifier carries no lower bound (e.g. a bare
    ``scitex-app``) — a floorless pin is worse than a wrong one, so the caller
    treats None as a failure, not as "no constraint to check".
    """
    for dep in _project_dependencies():
        match = _REQ_RE.search(dep)
        if not match:
            continue
        lower = _LOWER_RE.search(match.group(2))
        if not lower:
            return None  # the line matched but pins no lower bound
        minor = int(lower.group(3)) if lower.group(3) else 0
        return (int(lower.group(1)), int(lower.group(2)), minor)
    return None


def _installed_scitex_app_version() -> tuple[int, int, int] | None:
    """Shared Arrange: the installed scitex-app version as a comparable triple.

    Returns None when the distribution is absent OR its version string is not
    PEP 440-parseable — the single fixture below turns that one outcome into a
    skip, so the tests stay one-assertion each (STX-TQ007).
    """
    try:
        installed = importlib.metadata.version("scitex-app")
    except importlib.metadata.PackageNotFoundError:
        return None
    parts = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", installed)
    if not parts:
        return None
    return (
        int(parts.group(1)),
        int(parts.group(2)),
        int(parts.group(3)) if parts.group(3) else 0,
    )


@pytest.fixture
def installed_version() -> tuple[int, int, int]:
    """The installed scitex-app version, or skip when it cannot be measured.

    scitex-app is an optional *runtime* presence for this hermetic contract:
    when it is not installed (or its version is unparseable) the board renders
    through the graceful-degradation seam that
    ``test__board_shell_migration.py`` covers, so the floor-vs-install check is
    genuinely not applicable here and skipping is the honest outcome — not a
    masked assertion.
    """
    version = _installed_scitex_app_version()
    if version is None:
        pytest.skip("scitex-app not installed (or version unparseable) here")
    return version


def test_scitex_app_floor_is_pinned() -> None:
    """Positive control: ``pyproject.toml`` actually declares a scitex-app pin.

    A regex that quietly matches nothing would let the floor test below pass
    vacuously; pin the scan's own reach first (STX-TQ007, one intent here).
    """
    # Arrange
    deps = _project_dependencies()
    # Act
    has_pin = any(_REQ_RE.search(dep) for dep in deps)
    # Assert
    assert has_pin, (
        f"project.dependencies={deps!r} declares no `scitex-app` requirement — "
        "the floor test would be vacuous, so fail here instead."
    )


def test_scitex_app_floor_is_at_least_the_shell_bearing_version() -> None:
    """The scitex-app floor is pinned at/above 0.24.0 (shell-bearing)."""
    # Arrange
    bound = _scitex_app_lower_bound()
    # Act
    sufficient = bound is not None and bound >= _SHELL_BEARING
    # Assert
    assert sufficient, (
        f"scitex-app lower bound is {bound!r}; the board needs >= "
        f"{_SHELL_BEARING} or it degrades to the static-graph fallback instead "
        "of the shell. 0.24.0 is the first wheel to ship app_shell.html "
        "(scitex-app #189). Do not lower this floor below the shell-bearing "
        "version."
    )


def test_hub_app_floor_satisfies_the_board_shell_floor(installed_version) -> None:
    """If scitex-app is installed, its version clears the shell-bearing floor.

    This is the Hub's own guarantee restated as an import test: the Hub pins
    ``scitex-app>=0.25.0``; when the real distribution is present in the
    environment it must be >= 0.24.0 so the board renders the shell, not the
    graph fallback.
    """
    # Arrange — the fixture measured the installed version (skipping when absent)
    version = installed_version
    # Act
    sufficient = version >= _SHELL_BEARING
    # Assert
    assert sufficient, (
        f"installed scitex-app {version!r} is below the shell-bearing floor "
        f"{_SHELL_BEARING}; the board would render the graph fallback, not the "
        "shell. The Hub's scitex-app>=0.25.0 must clear this."
    )
