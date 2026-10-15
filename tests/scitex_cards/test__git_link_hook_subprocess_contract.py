#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The git-hook stdio contract at the real process boundary.

WHY THIS FILE EXISTS, from a measurement rather than a style preference.
``tests/scitex_cards/test__output_content_contract.py`` pins the PS-220
byte contract IN-PROCESS (``capsys``): it proves ``write_content`` emits
the payload plus newline on stdout with empty stderr. The shell hook does
not call Python functions — it spawns ``python -m scitex_cards._git_link
card-id <branch> <msg>`` and captures the PROCESS stdout
(``card_id=$(… card-id <branch> <msg>)``). An in-process pin cannot see
what only exists at the process boundary: import-time stderr leaks, a
non-zero exit code, or an extra startup line on stdout. This file pins
that boundary with real subprocesses, so a future change that keeps the
capsys pins green while breaking the hook fails here instead.

Card ``cards-audit-4-develop-inherited-errors-ps220-ps234-20261012``
owns the repair; these tests guard its consumer contract.

STRICT scitex doctrine: no unittest.mock, no monkeypatch, AAA markers each
on its own line, one assertion per test. ``subprocess`` spawns the real
entry point (precedent: behavior proofs on the card); no store is touched
— every test is pure and PG-free.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _run_git_link(*args: str) -> subprocess.CompletedProcess[str]:
    """Spawn the real hook entry point and capture its stdio."""
    return subprocess.run(
        [sys.executable, "-m", "scitex_cards._git_link", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_hook_card_id_stdout_is_the_bare_id_plus_newline():
    # Arrange
    # Act
    proc = _run_git_link("card-id", "feat/my-card-123", "/dev/null")
    # Assert
    assert proc.stdout == "my-card-123\n"


def test_hook_card_id_stderr_is_empty():
    # Arrange
    # Act
    proc = _run_git_link("card-id", "feat/my-card-123", "/dev/null")
    # Assert
    assert proc.stderr == ""


def test_hook_card_id_exits_zero():
    # Arrange
    # Act
    proc = _run_git_link("card-id", "feat/my-card-123", "/dev/null")
    # Assert
    assert proc.returncode == 0


def test_hook_card_id_adhoc_branch_emits_nothing_on_stdout():
    # Arrange — an ad-hoc branch carries no card id: SOFT skip, not error
    # Act
    proc = _run_git_link("card-id", "develop", "/dev/null")
    # Assert
    assert proc.stdout == ""


def test_hook_card_id_adhoc_branch_exits_zero():
    # Arrange — the SOFT skip must not fail the hook caller
    # Act
    proc = _run_git_link("card-id", "develop", "/dev/null")
    # Assert
    assert proc.returncode == 0


def test_hook_emit_event_stdout_is_single_line_json():
    # Arrange
    # Act
    proc = _run_git_link(
        "emit-event",
        "--repo",
        "R",
        "--branch",
        "feat/my-card-123",
        "--sha",
        "abc123",
        "--trigger",
        "push",
    )
    # Assert
    assert len(proc.stdout.strip().splitlines()) == 1


def test_hook_emit_event_json_carries_the_card_id():
    # Arrange
    # Act
    proc = _run_git_link(
        "emit-event",
        "--repo",
        "R",
        "--branch",
        "feat/my-card-123",
        "--sha",
        "abc123",
        "--trigger",
        "push",
    )
    # Assert
    assert json.loads(proc.stdout)["card_ids"] == ["my-card-123"]


def test_hook_emit_event_stderr_is_empty():
    # Arrange
    # Act
    proc = _run_git_link(
        "emit-event",
        "--repo",
        "R",
        "--branch",
        "feat/my-card-123",
        "--sha",
        "abc123",
        "--trigger",
        "push",
    )
    # Assert
    assert proc.stderr == ""


def test_hook_emit_event_adhoc_branch_emits_nothing():
    # Arrange — no card id resolves: SOFT skip, the shell hook never calls
    # the consumer on empty output
    # Act
    proc = _run_git_link(
        "emit-event",
        "--repo",
        "R",
        "--branch",
        "develop",
        "--sha",
        "abc123",
        "--trigger",
        "push",
    )
    # Assert
    assert proc.stdout == ""


def test_stdio_pins_are_not_vacuous_against_a_noisy_process():
    # Arrange — the assertions above must be able to FAIL: prove the
    # strictness check detects stdio noise on a synthetic noisy command
    # Act
    noisy = subprocess.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('leak')"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Assert
    assert noisy.stderr != ""


def _write_msg_file(tmp_path, body: str) -> str:
    """Write a commit-message file and return its path (hook reads files)."""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(body, encoding="utf-8")
    return str(msg)


def test_hook_card_id_trailer_fallback_stdout_is_the_bare_id_plus_newline(
    tmp_path,
):
    # Arrange — an ad-hoc branch carries no id; the Card: trailer opts in
    msg = _write_msg_file(
        tmp_path, "Subject line\n\nBody here.\n\nCard: my-card-123\n"
    )
    # Act
    proc = _run_git_link("card-id", "develop", msg)
    # Assert
    assert proc.stdout == "my-card-123\n"


def test_hook_card_id_trailer_fallback_stderr_is_empty(tmp_path):
    # Arrange
    msg = _write_msg_file(
        tmp_path, "Subject line\n\nBody here.\n\nCard: my-card-123\n"
    )
    # Act
    proc = _run_git_link("card-id", "develop", msg)
    # Assert
    assert proc.stderr == ""


def test_hook_card_id_trailer_fallback_exits_zero(tmp_path):
    # Arrange — the fallback is a SOFT resolve, not an error path
    msg = _write_msg_file(
        tmp_path, "Subject line\n\nBody here.\n\nCard: my-card-123\n"
    )
    # Act
    proc = _run_git_link("card-id", "develop", msg)
    # Assert
    assert proc.returncode == 0


def test_hook_card_id_msg_file_without_trailer_stays_a_soft_skip(tmp_path):
    # Arrange — a message file with no Card: trailer on an ad-hoc branch
    # must not inject bytes: empty output stays the SOFT skip signal
    msg = _write_msg_file(tmp_path, "Subject line\n\nNo trailer here.\n")
    # Act
    proc = _run_git_link("card-id", "develop", msg)
    # Assert
    assert proc.stdout == ""
