#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Django view tests for ``GET /fleet/ci-status``.

No mocks (STX-NM/PA-306) and no monkeypatch (PA-306). Drives the view
via Django's RequestFactory against the real ``fleet_config_load`` +
``fetch_repo_ci_status`` path; env / cwd manipulation routes through the
suite's :func:`env` fixture (``tests/scitex_cards/conftest.py``).

Trick: we don't want CI test runs to call out to GitHub over the wire,
so we configure the watched-repo list with slugs whose adapter call
RAISES locally — that exercises the per-repo error trap (the
operator-facing fail-loud-per-pill contract) without needing network.

Contract pinned here:

  1. Endpoint returns 200 with a JSON object shaped
     ``{"repos": [...], "config": {"repos": [...]}}``.
  2. One bad repo becomes ``{"slug": ..., "error": "<msg>"}`` — does
     NOT blank the whole strip.
  3. Malformed config returns 500 — the whole strip is unconfigurable,
     and the FE renders a single "no CI status configured" footnote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django.handlers.fleet import (  # noqa: E402
    fleet_ci_status_view,
)


def _isolate_home(env, tmp_path: Path) -> None:
    """Point HOME at tmp_path so we never read the operator's real
    dashboard.json. Also clear any leaked env override from a sibling
    test."""
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")


def test_endpoint_returns_200_with_repos_shape_status_code(env, tmp_path) -> None:
    """Configure ONE slug. The adapter will RAISE (invalid slug shape,
    so no network is touched) — the per-repo error trap converts that
    into ``{slug, error}``. The OVERALL response is still 200 + a JSON
    document the FE can render."""
    # Arrange
    _isolate_home(env, tmp_path)
    # Use an invalid-shape slug so the adapter raises synchronously on
    # the input check (no network needed). The shape pin is the same
    # one tested in ``test__gh_ci.py::test_invalid_slug_shape_raises``.
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "bad-slug-no-slash")

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    assert response.status_code == 200


def test_endpoint_returns_200_with_repos_shape_set(env, tmp_path) -> None:
    """Configure ONE slug. The adapter will RAISE (invalid slug shape,
    so no network is touched) — the per-repo error trap converts that
    into ``{slug, error}``. The OVERALL response is still 200 + a JSON
    document the FE can render."""
    # Arrange
    _isolate_home(env, tmp_path)
    # Use an invalid-shape slug so the adapter raises synchronously on
    # the input check (no network needed). The shape pin is the same
    # one tested in ``test__gh_ci.py::test_invalid_slug_shape_raises``.
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "bad-slug-no-slash")

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    assert set(data.keys()) >= {"repos", "config"}


def test_endpoint_returns_200_with_repos_shape_repos(env, tmp_path) -> None:
    """Configure ONE slug. The adapter will RAISE (invalid slug shape,
    so no network is touched) — the per-repo error trap converts that
    into ``{slug, error}``. The OVERALL response is still 200 + a JSON
    document the FE can render."""
    # Arrange
    _isolate_home(env, tmp_path)
    # Use an invalid-shape slug so the adapter raises synchronously on
    # the input check (no network needed). The shape pin is the same
    # one tested in ``test__gh_ci.py::test_invalid_slug_shape_raises``.
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "bad-slug-no-slash")

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    assert data["config"]["repos"] == ["bad-slug-no-slash"]


def test_endpoint_returns_200_with_repos_shape_isinstance(env, tmp_path) -> None:
    """Configure ONE slug. The adapter will RAISE (invalid slug shape,
    so no network is touched) — the per-repo error trap converts that
    into ``{slug, error}``. The OVERALL response is still 200 + a JSON
    document the FE can render."""
    # Arrange
    _isolate_home(env, tmp_path)
    # Use an invalid-shape slug so the adapter raises synchronously on
    # the input check (no network needed). The shape pin is the same
    # one tested in ``test__gh_ci.py::test_invalid_slug_shape_raises``.
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "bad-slug-no-slash")

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    assert isinstance(data["repos"], list)


def test_endpoint_returns_200_with_repos_shape_len(env, tmp_path) -> None:
    """Configure ONE slug. The adapter will RAISE (invalid slug shape,
    so no network is touched) — the per-repo error trap converts that
    into ``{slug, error}``. The OVERALL response is still 200 + a JSON
    document the FE can render."""
    # Arrange
    _isolate_home(env, tmp_path)
    # Use an invalid-shape slug so the adapter raises synchronously on
    # the input check (no network needed). The shape pin is the same
    # one tested in ``test__gh_ci.py::test_invalid_slug_shape_raises``.
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "bad-slug-no-slash")

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    assert len(data["repos"]) == 1


def _ci_status_for_bad_repos(env, tmp_path):
    """Run the CI-status view with two malformed slugs; return parsed JSON."""
    _isolate_home(env, tmp_path)
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "bad-slug-no-slash,also-bad")
    request = RequestFactory().get("/fleet/ci-status")
    response = fleet_ci_status_view(request)
    return response, json.loads(response.content)


def test_per_repo_error_returns_200(env, tmp_path) -> None:
    """One bad repo must NOT blank the page — the response is still 200."""
    # Arrange
    # Act
    response, _data = _ci_status_for_bad_repos(env, tmp_path)
    # Assert
    assert response.status_code == 200


def test_per_repo_error_keeps_both_slugs_in_order(env, tmp_path) -> None:
    # Arrange
    # Act
    _response, data = _ci_status_for_bad_repos(env, tmp_path)
    # Assert
    assert [r["slug"] for r in data["repos"]] == ["bad-slug-no-slash", "also-bad"]


def test_per_repo_error_traps_each_repo_with_error_string(env, tmp_path) -> None:
    """Each bad repo becomes ``{slug, error}`` rather than killing the page."""
    # Arrange
    # Act
    _response, data = _ci_status_for_bad_repos(env, tmp_path)
    # Assert
    assert all(
        isinstance(repo.get("error"), str) and repo["error"] for repo in data["repos"]
    )


def test_empty_config_returns_200_with_empty_list_status_code(env, tmp_path) -> None:
    """No repos configured = empty list + 200 (NOT 500). The FE hides
    the strip with a "no CI status configured" footnote."""
    # Arrange
    _isolate_home(env, tmp_path)
    # No env override, no file -> empty list.
    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)
    # Assert
    data = json.loads(response.content)
    assert response.status_code == 200


def test_empty_config_returns_200_with_empty_list_repos(env, tmp_path) -> None:
    """No repos configured = empty list + 200 (NOT 500). The FE hides
    the strip with a "no CI status configured" footnote."""
    # Arrange
    _isolate_home(env, tmp_path)
    # No env override, no file -> empty list.
    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)
    # Assert
    data = json.loads(response.content)
    assert data["config"]["repos"] == []


def test_empty_config_returns_200_with_empty_list_repos_2(env, tmp_path) -> None:
    """No repos configured = empty list + 200 (NOT 500). The FE hides
    the strip with a "no CI status configured" footnote."""
    # Arrange
    _isolate_home(env, tmp_path)
    # No env override, no file -> empty list.
    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)
    # Assert
    data = json.loads(response.content)
    assert data["repos"] == []


def test_malformed_config_returns_500_status_code(env, tmp_path) -> None:
    """A broken ``dashboard.json`` is the one error that DOES blank the
    strip — the whole thing is unconfigurable. Fail-loud per harness."""
    # Arrange
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "cards"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.json").write_text(
        '{"fleet": {"ci_status": {"repos": [a, b,',
        encoding="utf-8",
    )

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    # The message should mention the file so the operator can find it.
    assert response.status_code == 500


def test_malformed_config_returns_500_data_contains(env, tmp_path) -> None:
    """A broken ``dashboard.json`` is the one error that DOES blank the
    strip — the whole thing is unconfigurable. Fail-loud per harness."""
    # Arrange
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "cards"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.json").write_text(
        '{"fleet": {"ci_status": {"repos": [a, b,',
        encoding="utf-8",
    )

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    # The message should mention the file so the operator can find it.
    assert "error" in data


def test_malformed_config_returns_500_error_contains(env, tmp_path) -> None:
    """A broken ``dashboard.json`` is the one error that DOES blank the
    strip — the whole thing is unconfigurable. Fail-loud per harness."""
    # Arrange
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "cards"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.json").write_text(
        '{"fleet": {"ci_status": {"repos": [a, b,',
        encoding="utf-8",
    )

    request = RequestFactory().get("/fleet/ci-status")
    # Act
    response = fleet_ci_status_view(request)

    # Assert
    data = json.loads(response.content)
    # The message should mention the file so the operator can find it.
    assert "dashboard.json" in data["error"]
