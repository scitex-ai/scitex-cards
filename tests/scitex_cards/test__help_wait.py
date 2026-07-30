#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the help-wait / help-clear card semantics.

The "agent is stuck waiting on the operator" card was lifted out of a
dotfiles Notification hook into the package (so scitex-todo owns the
contract; a schema drift can no longer break the hook silently). These
tests pin the exact card contract + idempotency.

Real fixtures (no mocks per STX-NM / PA-306) — a real temp ``tasks.yaml``
store is created and read back through the public Python API.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _help_wait, _store


# === help_wait: create ======================================================
class TestHelpWaitCreate:
    """A fresh card matches the byte-for-byte contract."""

    def test_creates_card_with_canonical_id(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["id"] == "help-alice-waiting"

    def test_card_title_names_the_waiting_agent(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["title"] == "[help] alice waiting on operator decision"

    def test_card_status_is_blocked(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["status"] == "blocked"

    def test_card_blocker_is_operator_decision(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["blocker"] == "operator-decision"

    def test_card_assignee_is_the_agent(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["assignee"] == "alice"

    def test_card_scope_is_the_agent_slice(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["scope"] == "agent:alice"

    def test_question_stored_in_note(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="merge or wait?")
        # Assert
        assert card["note"] == "merge or wait?"

    def test_empty_question_uses_placeholder(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", question="")
        # Assert
        assert card["note"] == _help_wait.HELP_WAIT_PLACEHOLDER

    def test_explicit_host_used(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice", host="spartan")
        # Assert
        assert card["host"] == "spartan"

    def test_host_defaults_to_a_nonempty_hostname(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice")
        # Assert
        assert card["host"] and isinstance(card["host"], str)

    def test_last_activity_is_utc_iso_z(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        card = _help_wait.help_wait(store, "alice")
        # Assert
        assert card["last_activity"].endswith("Z")

    def test_card_is_persisted_to_store(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        _help_wait.help_wait(store, "alice")
        rows = _store.list_tasks(store, scope="")
        # Assert
        assert {r["id"] for r in rows} == {"help-alice-waiting"}

    def test_blank_agent_raises_on_help_wait(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        blank_agent = "   "
        # Act
        # Assert — the raise IS the behaviour; act and assert are one statement.
        with pytest.raises(ValueError):
            _help_wait.help_wait(store, blank_agent)


# === help_wait: upsert (no duplicate) =======================================
class TestHelpWaitUpsert:
    """A re-run refreshes in place — exactly one card per agent."""

    def test_rerun_does_not_duplicate(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice", question="q1")
        # Act
        _help_wait.help_wait(store, "alice", question="q2")
        rows = [
            r
            for r in _store.list_tasks(store, scope="")
            if r["id"] == "help-alice-waiting"
        ]
        # Assert
        assert len(rows) == 1

    def test_rerun_refreshes_note_in_place(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice", question="q1")
        # Act
        card = _help_wait.help_wait(store, "alice", question="q2")
        # Assert
        assert card["note"] == "q2"

    def test_rerun_preserves_created_at(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        first = _help_wait.help_wait(store, "alice", question="q1")
        # Act
        second = _help_wait.help_wait(store, "alice", question="q2")
        # Assert
        assert second.get("created_at") == first.get("created_at")

    def test_distinct_agents_get_distinct_cards(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        _help_wait.help_wait(store, "alice")
        _help_wait.help_wait(store, "bob")
        rows = _store.list_tasks(store, scope="")
        # Assert
        assert {r["id"] for r in rows} == {
            "help-alice-waiting",
            "help-bob-waiting",
        }


# === help_clear =============================================================
class TestHelpClear:
    """Resolving the card sets done + clears the blocker; absent => no-op."""

    def test_clear_reports_the_card_as_cleared(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice", question="q1")
        # Act
        payload = _help_wait.help_clear(store, "alice")
        # Assert
        assert payload["cleared"] is True

    def test_clear_marks_the_card_done(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice", question="q1")
        # Act
        payload = _help_wait.help_clear(store, "alice")
        # Assert
        assert payload["task"]["status"] == "done"

    def test_clear_drops_the_blocker(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice", question="q1")
        # Act
        _help_wait.help_clear(store, "alice")
        card = _store.get_task(store, task_id="help-alice-waiting")
        # Assert
        assert "blocker" not in card

    def test_clear_is_noop_when_card_absent(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice")  # different agent present
        # Act
        payload = _help_wait.help_clear(store, "bob")
        # Assert
        assert payload == {"task_id": "help-bob-waiting", "cleared": False}

    def test_clear_is_noop_when_store_absent(self, tmp_path):
        # Arrange — an empty canonical store (no card seeded) is the SQLite
        # equivalent of the old "store file absent": clearing finds nothing.
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        payload = _help_wait.help_clear(store, "alice")
        # Assert
        assert payload["cleared"] is False

    def test_blank_agent_raises_on_help_clear(self, tmp_path):
        # Arrange
        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        blank_agent = ""
        # Act
        # Assert — the raise IS the behaviour; act and assert are one statement.
        with pytest.raises(ValueError):
            _help_wait.help_clear(store, blank_agent)


# === CLI verbs ==============================================================
class TestHelpWaitCli:
    """The `help-wait` / `help-clear` click verbs round-trip the contract."""

    def _runner(self):
        from click.testing import CliRunner

        return CliRunner()

    def test_help_wait_cli_exits_zero(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        result = self._runner().invoke(
            main,
            ["help-wait", "alice", "--question", "merge?"],
        )
        # Assert
        assert result.exit_code == 0

    def test_help_wait_cli_stores_the_question_in_note(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        self._runner().invoke(
            main,
            ["help-wait", "alice", "--question", "merge?"],
        )
        card = _store.get_task(store, task_id="help-alice-waiting")
        # Assert
        assert card["note"] == "merge?"

    def test_help_wait_cli_sets_the_operator_decision_blocker(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        self._runner().invoke(
            main,
            ["help-wait", "alice", "--question", "merge?"],
        )
        card = _store.get_task(store, task_id="help-alice-waiting")
        # Assert
        assert card["blocker"] == "operator-decision"

    def test_help_wait_cli_json_exits_zero(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        result = self._runner().invoke(
            main,
            ["help-wait", "alice", "--json"],
        )
        # Assert
        assert result.exit_code == 0

    def test_help_wait_cli_json_prints_the_card_id(self, tmp_path):
        # Arrange
        import json

        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        result = self._runner().invoke(
            main,
            ["help-wait", "alice", "--json"],
        )
        # Assert
        assert json.loads(result.output)["id"] == "help-alice-waiting"

    def test_help_clear_cli_exits_zero(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice")
        # Act
        result = self._runner().invoke(main, ["help-clear", "alice"])
        # Assert
        assert result.exit_code == 0

    def test_help_clear_cli_marks_the_card_done(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        _help_wait.help_wait(store, "alice")
        # Act
        self._runner().invoke(main, ["help-clear", "alice"])
        card = _store.get_task(store, task_id="help-alice-waiting")
        # Assert
        assert card["status"] == "done"

    def test_help_clear_cli_noop_exit_zero_when_absent(self, tmp_path):
        # Arrange
        from scitex_cards._cli import main

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        # Act
        result = self._runner().invoke(main, ["help-clear", "ghost"])
        # Assert
        assert result.exit_code == 0


# === MCP tools ==============================================================
class TestHelpWaitMcp:
    """The `help_wait` / `help_clear` MCP tools mirror the CLI."""

    def test_tools_in_tool_names(self):
        # Arrange
        pytest.importorskip("fastmcp")
        # Act
        from scitex_cards._mcp_server import TOOL_NAMES

        # Assert
        assert "help_wait" in TOOL_NAMES and "help_clear" in TOOL_NAMES

    def test_help_wait_tool_upserts(self, tmp_path):
        # Arrange
        import asyncio
        import json

        fastmcp = pytest.importorskip("fastmcp")
        _ = fastmcp
        from scitex_cards._mcp_skills import help_wait

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        fn = getattr(help_wait, "fn", None) or help_wait
        # Act
        out = asyncio.run(fn(agent="alice", question="q1", tasks_path=store))
        # Assert
        assert json.loads(out)["id"] == "help-alice-waiting"

    def test_help_clear_tool_resolves(self, tmp_path):
        # Arrange
        import asyncio
        import json

        fastmcp = pytest.importorskip("fastmcp")
        _ = fastmcp
        from scitex_cards._mcp_skills import help_clear, help_wait

        store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
        wfn = getattr(help_wait, "fn", None) or help_wait
        cfn = getattr(help_clear, "fn", None) or help_clear
        asyncio.run(wfn(agent="alice", tasks_path=store))
        # Act
        out = asyncio.run(cfn(agent="alice", tasks_path=store))
        # Assert
        assert json.loads(out)["cleared"] is True


# EOF
