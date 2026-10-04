#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PS-212 e2e layer: slow end-to-end workflows against real subsystems.

Gated by ``RUN_E2E=1`` and SKIPPED by default. The cases below drive the
REAL installed CLI binary (``python -m scitex_cards``) as a subprocess against
a REAL throwaway PostgreSQL schema (the ``postgres_dsn`` session fixture,
dropped CASCADE at session end) — add → list → done → list — so they need a
writable cluster and must not run on every PR.

PA-307: exactly one ``assert`` per test, with ``# Arrange`` / ``# Act`` /
``# Assert`` markers in order. Shared setup lives in ``_stage_*`` helpers
that raise (never ``assert``) so each test still owns a single behaviour:
later stages re-run the earlier CLI steps as arrangement, which is cheap
enough for a RUN_E2E-only layer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_E2E") != "1",
        reason="e2e: set RUN_E2E=1 to run against real subsystems",
    ),
]


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scitex_cards", *args],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def _fresh_env(postgres_dsn: str) -> dict[str, str]:
    env = os.environ.copy()
    env["SCITEX_STORE_DSN"] = postgres_dsn
    env["SCITEX_CARDS_AGENT_ID"] = "agent:e2e-probe"
    return env


def _check(cmd: subprocess.CompletedProcess[str], what: str) -> None:
    if cmd.returncode != 0:
        raise RuntimeError(f"{what} failed: {cmd.stderr[-2000:]}")


def _stage_stored_card(env: dict[str, str], card_id: str) -> None:
    """Arrange a stored card via init + add; raises on any setup failure."""
    _check(_cli(env, "init-store"), "init-store")
    _check(
        _cli(env, "add", card_id, "e2e round-trip probe", "--agent", "e2e-probe"),
        "add",
    )


def _ids_in_open_list(env: dict[str, str]) -> list[str]:
    listed = _cli(env, "list-tasks", "--json")
    _check(listed, "list-tasks")
    return [t.get("id") for t in json.loads(listed.stdout)]


def _ids_in_done_list(env: dict[str, str]) -> list[str]:
    listed = _cli(env, "list-tasks", "--status", "done", "--json")
    _check(listed, "list-tasks --status done")
    return [t.get("id") for t in json.loads(listed.stdout)]


def test_add_accepts_a_new_card(postgres_dsn: str) -> None:
    # Arrange
    env = _fresh_env(postgres_dsn)
    card_id = "e2e-" + uuid.uuid4().hex[:8]
    _check(_cli(env, "init-store"), "init-store")
    # Act
    added = _cli(env, "add", card_id, "e2e round-trip probe", "--agent", "e2e-probe")
    # Assert
    assert added.returncode == 0


def test_list_shows_the_added_card(postgres_dsn: str) -> None:
    # Arrange
    env = _fresh_env(postgres_dsn)
    card_id = "e2e-" + uuid.uuid4().hex[:8]
    _stage_stored_card(env, card_id)
    # Act
    ids = _ids_in_open_list(env)
    # Assert
    assert card_id in ids


def test_done_marks_the_card_done(postgres_dsn: str) -> None:
    # Arrange
    env = _fresh_env(postgres_dsn)
    card_id = "e2e-" + uuid.uuid4().hex[:8]
    _stage_stored_card(env, card_id)
    # Act
    _check(_cli(env, "done", card_id), "done")
    ids = _ids_in_done_list(env)
    # Assert
    assert card_id in ids
