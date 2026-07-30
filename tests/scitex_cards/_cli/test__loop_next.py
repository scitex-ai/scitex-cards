#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``scitex-todo next`` CLI verb (the pull-side of the self-
consuming board loop).

Real `CliRunner` invocations against the per-test store (no mocks
per STX-NM / PA-306). Covers:

  - happy path: prints the canonical text line
  - --json: prints a JSON-decodable task dict
  - no candidate: exits non-zero with a clear stderr message
  - --assignee filters down
  - --mine + SCITEX_TODO_AGENT_ID env round-trip
  - --mine without env: ClickException
  - --assignee + --mine mutually exclusive
  - --auto-claim flips status to in_progress + stamps a comment

The coverage audit (proj-scitex-todo overnight, lead a2a `1397f103`)
flagged `_cli/_loop.py` at 33% with no dedicated tests; this file
adds end-to-end coverage of the `next` verb side. The `watch` verb
(long-running poll loop) is covered via `_wake_watcher` integration
elsewhere.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._loop import next_cmd
from scitex_cards._store import add_task, list_tasks

#: The same four cards the YAML fixture used to spell out, seeded through the
#: public write verb now that SQLite is the only store.
_CARDS = (
    dict(
        id="a-deferred",
        title="A deferred",
        status="deferred",
        agent="proj-alpha",
        priority=1,
    ),
    dict(
        id="a-deferred-low",
        title="A deferred low",
        status="deferred",
        agent="proj-alpha",
        priority=30,
    ),
    dict(
        id="b-deferred",
        title="B deferred",
        status="deferred",
        agent="proj-beta",
        priority=2,
    ),
    dict(
        id="a-blocked",
        title="A blocked",
        status="blocked",
        blocker="operator-decision",
        agent="proj-alpha",
        priority=1,
    ),
)


@pytest.fixture
def store():
    for card in _CARDS:
        add_task(**card)
    yield None


def _load():
    return {t["id"]: t for t in list_tasks()}


class TestHappyPath:
    """Without a filter the verb prints the top runnable task line."""

    def test_exit_code_zero_when_candidate_exists(self, store):
        # Arrange
        # Act
        result = CliRunner().invoke(next_cmd, [])
        # Assert
        assert result.exit_code == 0

    def test_output_contains_top_priority_id(self, store):
        # Arrange — a-deferred (priority=1) is the global top.
        # Act
        result = CliRunner().invoke(next_cmd, [])
        # Assert
        assert "a-deferred" in result.output


class TestJsonOutput:
    """`--json` emits a decodable dict on the picked task."""

    def test_json_output_decodes_to_dict(self, store):
        # Arrange
        # Act
        result = CliRunner().invoke(next_cmd, ["--json"])
        payload = json.loads(result.output)
        # Assert
        assert isinstance(payload, dict)

    def test_json_output_includes_id_field(self, store):
        # Arrange
        # Act
        result = CliRunner().invoke(next_cmd, ["--json"])
        payload = json.loads(result.output)
        # Assert
        assert "id" in payload


class TestNoCandidate:
    """When no task matches, exit 1 with a stderr message."""

    def test_no_candidate_exits_nonzero(self):
        # Arrange — the per-test store starts empty; nothing is seeded.
        # Act
        result = CliRunner().invoke(next_cmd, [])
        # Assert
        assert result.exit_code != 0


class TestAssigneeFilter:
    """`--assignee X` filters to only that agent's tasks."""

    def test_assignee_beta_returns_only_b(self, store):
        # Arrange — agent=proj-beta only owns b-deferred.
        # Act
        result = CliRunner().invoke(next_cmd, ["--assignee", "proj-beta", "--json"])
        payload = json.loads(result.output)
        # Assert
        assert payload["id"] == "b-deferred"


class TestMineFlag:
    """`--mine` reads SCITEX_TODO_AGENT_ID from the env."""

    def test_mine_with_env_resolves_to_agent(self, store, env):
        # Arrange
        env.set("SCITEX_TODO_AGENT_ID", "proj-beta")
        # Act
        result = CliRunner().invoke(next_cmd, ["--mine", "--json"])
        payload = json.loads(result.output)
        # Assert
        assert payload["id"] == "b-deferred"

    def test_mine_without_env_raises_click_exception(self, store, env):
        # Arrange
        env.delete("SCITEX_TODO_AGENT_ID")
        # Act
        result = CliRunner().invoke(next_cmd, ["--mine"])
        # Assert
        assert result.exit_code != 0


class TestMutuallyExclusive:
    """`--assignee` + `--mine` together is a usage error."""

    def test_both_assignee_and_mine_errors(self, store):
        # Arrange
        # Act
        result = CliRunner().invoke(
            next_cmd,
            [
                "--assignee",
                "proj-alpha",
                "--mine",
            ],
        )
        # Assert
        assert result.exit_code != 0


class TestAutoClaim:
    """`--auto-claim` atomically flips status to in_progress + stamps a
    starting comment."""

    def test_auto_claim_flips_status_to_in_progress(self, store):
        # Arrange
        # Act
        CliRunner().invoke(
            next_cmd,
            [
                "--assignee",
                "proj-alpha",
                "--auto-claim",
            ],
        )
        # Assert
        assert _load()["a-deferred"]["status"] == "in_progress"

    def test_auto_claim_appends_starting_comment(self, store):
        # Arrange
        # Act
        CliRunner().invoke(
            next_cmd,
            [
                "--assignee",
                "proj-alpha",
                "--auto-claim",
            ],
        )
        comments = _load()["a-deferred"].get("comments") or []
        # Assert — at least one comment exists with the auto-claim marker.
        assert any("auto-claim" in (c.get("text") or "") for c in comments)
