#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`__version__` must answer the CODE being run, not the env it was installed in.

MEASURED BEFORE THIS FIX, one host, one checkout, four answers:

    /uvwork/venv-agent  (editable into the repo)   0.51.3
    repo .venv          (editable into the repo)   0.53.0
    /uvwork/venv-py311  (editable into the repo)   0.53.0
    /opt/venv-sac       (a site-packages copy)     0.52.1
    pyproject.toml      (the tree itself)          0.53.1

`importlib.metadata.version()` answers THE VERSION THE ENV WAS INSTALLED AT, and
for an editable install that number freezes at install time while the code keeps
moving — so the board's leaf band, the DM page's title and every `--version`
call reported a release that was not the code in front of them. That is the same
user-visible lie as incident-version-string-lies-orphaned-distinfo-20260712 (an
orphaned dist-info froze scitex-todo at 0.7.26 while the code ran newer); the
mechanism here is FROZEN EDITABLE METADATA rather than an orphan, and the fix has
the same shape: ask the thing that cannot drift.

AFTER: the three editable envs answer 0.53.1 (the tree they import), and
/opt/venv-sac keeps 0.52.1 — correctly, because a site-packages copy's code and
metadata shipped together and there is no pyproject above it.

EVERY TEST HERE IS HERMETIC: no store, no network, no monkeypatch. The two
injection seams (`read_version`, `tree_file`) exist precisely so both branches
are reachable — a resolver with an unreachable branch is one nobody has run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scitex_cards import _resolve_version, _version_from_tree

#: This checkout's own pyproject — the tree the tests are running from.
_TREE_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _TREE_ROOT / "pyproject.toml"

#: A module file that sits under NO pyproject: the site-packages shape.
_NO_TREE = "somewhere/else/site-packages/scitex_cards/__init__.py"


def _declared_version() -> str:
    found = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', _PYPROJECT.read_text())
    assert found is not None, "this checkout declares no version; the guard is moot"
    return found.group(1)


def test_the_tree_declares_the_version_this_suite_is_running_from():
    """Anchor: if this fails, every other assertion here is comparing nothing."""
    # Arrange
    declared = _declared_version()
    # Act
    observed = _resolve_version()
    # Assert
    assert observed == declared


def test_the_tree_lookup_finds_the_declaration_from_the_package_file():
    """src-layout: the pyproject is THREE levels up, and walking finds it."""
    # Arrange
    package_file = Path(__file__).resolve().parents[2] / "src" / "scitex_cards" / "__init__.py"
    # Act
    found = _version_from_tree(package_file=str(package_file))
    # Assert
    assert found == _declared_version()


def test_no_pyproject_above_the_package_means_no_tree_answer():
    """A site-packages copy has no tree, and must fall through, not guess."""
    # Arrange
    absent = _NO_TREE
    # Act
    found = _version_from_tree(package_file=absent)
    # Assert
    assert found is None


def test_the_tree_beats_the_installed_metadata():
    """The whole point: a stale editable metadata must not win."""
    # Arrange
    stale_metadata = lambda name: "0.51.3"  # noqa: E731 — the measured stale value
    # Act
    observed = _resolve_version(read_version=stale_metadata)
    # Assert
    assert observed == _declared_version()


def test_without_a_tree_the_metadata_is_still_the_answer():
    """A wheel in site-packages answers its own metadata, which shipped with it."""
    # Arrange
    metadata = lambda name: "9.9.9"  # noqa: E731
    # Act
    observed = _resolve_version(read_version=metadata, tree_file=_NO_TREE)
    # Assert
    assert observed == "9.9.9"


def test_an_uninstalled_package_still_answers_the_local_sentinel():
    """The last resort, reachable now without rewriting the stdlib module."""
    # Arrange
    def missing(name: str) -> str:
        from importlib.metadata import PackageNotFoundError

        raise PackageNotFoundError(name)

    # Act
    observed = _resolve_version(read_version=missing, tree_file=_NO_TREE)
    # Assert
    assert observed == "0.0.0+local"


# EOF
