#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release/PR store-contract drift guard: the release tag's ``test`` job
provisions the SAME PostgreSQL the required PR matrix does.

WHY THIS FILE EXISTS (measured 2026-09-15). The tag-driven release workflow
(``pypi-publish-and-github-release-on-tag.yml``) runs its own ``test`` job
instead of reusing the org ``pytest-matrix`` workflow. After #1005 moved the
ambient store resolver to the fleet primary (``scitex-primary``), that job ran
bare ``pytest tests/`` on the self-hosted pool with NO store: the runner's
ambient DSN reached for ``scitex-primary`` (unresolvable from CI), the conftest
throwaway-schema guard fired, and 3.11/3.12 failed with 15 failed + 10 errors
while 3.13 passed by runner luck. Run 34996423777 (v0.53.0) is the instance;
v0.52.1's push-tag run failed the same way and was published via
``workflow_dispatch`` instead. The PR leg (the required check) was green the
whole time because it provisions a ``postgres:16`` service + a per-job
``SCITEX_STORE_DSN`` via the org reusable workflow with ``postgres: true``.

The defect is not "the release job runs a different test" — it is that the
release job can go red/green INDEPENDENTLY of the PR job, on the same commit,
because the two legs carry different store contracts. A green PR and a red
release (or a red PR and a green release) is exactly how a release ships that
the required checks never certified.

This file pins the release ``test`` job's store contract so it CANNOT drift
from the matrix leg's:

  * the job declares a ``postgres`` service (image ``postgres:16``),
  * it pins ``SCITEX_STORE_DSN`` to the per-job service (127.0.0.1:5432), and
  * it clears the ambient fleet store/inbox DSNs first, so the runner's baked-in
    fleet state cannot contaminate the result.

Each assertion is one intent (STX-TQ007). The workflow is read as YAML, so a
broken/indented edit fails the load, not silently passes a string match.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("yaml")

import yaml  # noqa: E402

#: The tag-driven release workflow — the one that runs on ``v*`` tag push and
#: whose ``test`` job must carry the same store contract as the PR matrix.
_RELEASE_WORKFLOW = "pypi-publish-and-github-release-on-tag.yml"


def _workflows_dir() -> Path:
    """The repository ``.github/workflows/`` — two levels above this test."""
    return Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _release_workflow_text() -> str:
    path = _workflows_dir() / _RELEASE_WORKFLOW
    if not path.exists():
        pytest.fail(
            f"release workflow {path!s} not found — the drift guard has nothing "
            "to read, so every assertion below would be vacuous"
        )
    return path.read_text(encoding="utf-8")


def _release_test_job() -> dict:
    """The ``jobs.test`` mapping of the release workflow, parsed as YAML."""
    wf = yaml.safe_load(_release_workflow_text())
    return wf["jobs"]["test"]


def test_release_workflow_declares_a_postgres_service() -> None:
    """The release ``test`` job starts a postgres service container."""
    # Arrange
    job = _release_test_job()
    # Act
    services = job.get("services", {})
    # Assert
    assert "postgres" in services, (
        f"release test job declares services={services!r}; it must start a "
        "`postgres` service container so the store-backed tests have a throwaway "
        "PostgreSQL, exactly as the required PR matrix does (postgres: true)."
    )


def test_release_postgres_service_is_the_matrix_image() -> None:
    """The release postgres service uses the same image as the PR matrix."""
    # Arrange
    job = _release_test_job()
    image = job.get("services", {}).get("postgres", {}).get("image")
    # Act
    matches = image == "postgres:16"
    # Assert
    assert matches, (
        f"release test job postgres service image={image!r}; the org "
        "reusable workflow's `postgres: true` service uses postgres:16, so the "
        "two legs must provision the same server (a different major could have "
        "different behaviour the PR leg never tested)."
    )


def test_release_postgres_service_is_health_checked() -> None:
    """The service has a health check, so steps don't race server startup."""
    # Arrange
    job = _release_test_job()
    options = job.get("services", {}).get("postgres", {}).get("options", "")
    # Act
    healthy = "pg_isready" in options
    # Assert
    assert healthy, (
        f"release test job postgres service options={options!r}; without a "
        "health check the test steps race the server's startup and fail with a "
        "connection error that reads exactly like a real defect."
    )


def _step_texts(job: dict) -> list[str]:
    """The ``run:`` bodies of every step in the job, in order."""
    return [
        step.get("run", "")
        for step in job.get("steps", [])
        if step.get("run")
    ]


def test_release_test_job_pins_scitex_store_dsn_to_the_service() -> None:
    """The release job sets SCITEX_STORE_DSN to the per-job service."""
    # Arrange
    job = _release_test_job()
    # Act
    pin_re = re.compile(r"SCITEX_STORE_DSN=postgresql://[^@\s]+@127\.0\.0\.1:5432/[^#\s]+")
    pinned = any(pin_re.search(text) for text in _step_texts(job))
    # Assert
    assert pinned, (
        "release test job has no step pinning SCITEX_STORE_DSN to the per-job "
        "service (127.0.0.1:5432); the conftest would then fall back to a "
        "runner-private cluster or reach for an ambient fleet DSN — the exact "
        "drift that took 3.11/3.12 down on v0.53.0."
    )


def test_release_test_job_clears_the_ambient_fleet_dsns() -> None:
    """The release job unsets the ambient fleet store/inbox DSNs first."""
    # Arrange
    job = _release_test_job()
    # Act
    cleared = any(
        "unset" in text and "SCITEX_STORE_DSN" in text
        for text in _step_texts(job)
    )
    # Assert
    assert cleared, (
        "release test job never clears the ambient fleet DSNs "
        "(SCITEX_STORE_DSN / SCITEX_CARDS_DB / SCITEX_CARDS_NOTIFY_DSN); a "
        "runner with a fleet DSN baked in would contaminate the result, so the "
        "job's green/red would depend on which runner it landed on."
    )


def test_release_test_job_runs_on_a_hosted_runner() -> None:
    """The release ``test`` job runs on a hosted runner, not the shared pool.

    A fixed service port (5432) is a shared resource on the self-hosted pool,
    where two concurrent opt-in jobs would contend for it. The org reusable
    workflow's own comment says a ``postgres`` opt-in should pass
    ``runs_on: ["ubuntu-latest"]``; the matrix leg already does. Pinning the
    release leg to a hosted runner is what makes the port safe.
    """
    # Arrange
    job = _release_test_job()
    runs_on = str(job.get("runs-on", ""))
    # Act
    hosted = "ubuntu-latest" in runs_on
    # Assert
    assert hosted, (
        f"release test job runs-on={runs_on!r}; a postgres service on a fixed "
        "host port is a shared resource that contends on the self-hosted pool, "
        "so the job must run on a hosted runner (ubuntu-latest), as the PR "
        "matrix leg does."
    )
