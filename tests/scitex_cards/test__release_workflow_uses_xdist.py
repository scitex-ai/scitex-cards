#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The tag gate uses the same bounded xdist mechanism as required CI."""

from pathlib import Path

_RELEASE = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/pypi-publish-and-github-release-on-tag.yml"
)


def test_release_installs_xdist_explicitly():
    # Arrange
    workflow = _RELEASE.read_text(encoding="utf-8")
    # Act
    install_is_explicit = (
        'uv pip install --python .venv/bin/python "pytest-xdist>=3.0.0"'
        in workflow
    )
    # Assert
    assert install_is_explicit


def test_release_sets_the_auto_worker_override():
    # Arrange
    workflow = _RELEASE.read_text(encoding="utf-8")
    # Act
    override_is_set = "export PYTEST_XDIST_AUTO_NUM_WORKERS=" in workflow
    # Assert
    assert override_is_set


def test_release_derives_workers_from_the_runner_affinity():
    # Arrange
    workflow = _RELEASE.read_text(encoding="utf-8")
    # Act
    affinity_is_used = "len(os.sched_getaffinity(0))" in workflow
    # Assert
    assert affinity_is_used


def test_release_runs_the_suite_through_xdist():
    # Arrange
    workflow = _RELEASE.read_text(encoding="utf-8")
    invocation = ".venv/bin/python -m pytest tests/ -n auto"
    # Act
    xdist_is_invoked = invocation in workflow
    # Assert
    assert xdist_is_invoked


def test_release_keeps_the_coverage_reports():
    # Arrange
    workflow = _RELEASE.read_text(encoding="utf-8")
    # Act
    coverage_is_kept = (
        '--cov="src/$PKG" --cov-report=xml --cov-report=term' in workflow
    )
    # Assert
    assert coverage_is_kept


# EOF
