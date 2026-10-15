#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the bundled PreToolUse hook that redirects Claude's built-in
``TaskCreate`` / ``TaskUpdate`` / ``TaskList`` tools to scitex-cards
(op-12038 single-shared-store doctrine).

No mocks (STX-NM / PA-306): invoke the real script under bash via
``subprocess.run`` with a JSON event on stdin, exactly as Claude Code
would.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from scitex_cards._cli._skills import _skills_root
from scitex_cards._task import ABOLISHED_STATUSES, VALID_STATUSES

#: Derived, never spelled: the bundled skills directory was renamed
#: ``scitex-cards`` -> ``scitex-cards`` on 2026-07-20 and every hardcoded copy of
#: the path broke at once. ``_skills_root`` is the one resolver.
HOOK_PATH = (
    _skills_root()
    / "hooks"
    / "pre-tool-use"
    / "redirect_claude_tasklist_to_scitex_cards.sh"
)


def _run(payload: str, env_extra: dict[str, str] | None = None):
    """Run the hook with ``payload`` on stdin and return CompletedProcess."""
    env = dict(os.environ)
    # Default-clear the opt-out so tests are deterministic; specific cases
    # set it explicitly via env_extra.
    env.pop("CC_ALLOW_CLAUDE_TASKLIST", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


def test_hook_script_exists_at_bundled_path():
    # Arrange
    # Act
    exists = HOOK_PATH.is_file()
    # Assert
    assert exists, f"hook script missing at {HOOK_PATH}"


def test_taskcreate_is_blocked_with_nonzero_exit():
    # Arrange
    payload = '{"tool_name": "TaskCreate"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode != 0


def test_taskupdate_is_blocked_with_nonzero_exit():
    # Arrange
    payload = '{"tool_name": "TaskUpdate"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode != 0


def test_tasklist_is_blocked_with_nonzero_exit():
    # Arrange
    payload = '{"tool_name": "TaskList"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode != 0


def test_read_tool_passes_through_with_zero_exit():
    # Arrange
    payload = '{"tool_name": "Read"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode == 0


def test_bash_tool_passes_through_with_zero_exit():
    # Arrange
    payload = '{"tool_name": "Bash"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode == 0


def test_block_stderr_names_scitex_cards_so_operator_can_confirm_copy():
    # Arrange
    payload = '{"tool_name": "TaskCreate"}'
    # Act
    result = _run(payload)
    # Assert
    assert "scitex-cards" in result.stderr


def test_opt_out_env_var_lets_taskcreate_through():
    # Arrange
    payload = '{"tool_name": "TaskCreate"}'
    # Act
    result = _run(payload, env_extra={"CC_ALLOW_CLAUDE_TASKLIST": "1"})
    # Assert
    assert result.returncode == 0


def _suggested_statuses(stderr: str) -> list[str]:
    # Arrange-free helper: every `--status <value>` the block message
    # suggests an agent type. `list-tasks --status` is free text, so a
    # suggestion naming an abolished status fails SILENTLY (matches only
    # legacy rows) rather than loudly — pin the values, not the exit code.
    return re.findall(r"--status\s+(\S+)", stderr)


def test_block_message_suggests_at_least_one_status_filter():
    # Arrange
    payload = '{"tool_name": "TaskList"}'
    # Act
    result = _run(payload)
    # Assert
    assert _suggested_statuses(result.stderr) != []


def test_block_message_status_filters_are_all_live_statuses():
    # Arrange
    payload = '{"tool_name": "TaskList"}'
    # Act
    result = _run(payload)
    # Assert
    assert all(s in VALID_STATUSES for s in _suggested_statuses(result.stderr))


def test_block_message_status_filters_name_no_abolished_status():
    # Arrange
    payload = '{"tool_name": "TaskList"}'
    # Act
    result = _run(payload)
    # Assert
    assert not any(s in ABOLISHED_STATUSES for s in _suggested_statuses(result.stderr))


def test_block_message_list_tasks_suggests_deferred_backlog():
    # Arrange: `deferred` replaced `pending` as the not-yet-started state
    # when pending was abolished 2026-07-10.
    payload = '{"tool_name": "TaskList"}'
    # Act
    result = _run(payload)
    # Assert
    assert "--status deferred" in result.stderr
