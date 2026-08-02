#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``scitex_cards_board`` management command (no mocks).

Mirrors ``src/scitex_cards/_django/management/commands/scitex_cards_board.py``.
Exercises the real ``Command`` argument parser (no server is started — we only
assert the CLI contract), so the board's ``--tasks`` / ``--port`` / ``--no-browser``
options stay stable.

Env-precedence coverage for ``_apply_tasks_env`` is included so a regression
that reverts to the pre-2026-06-05 behaviour (``--tasks`` ignored, project
store wins) is caught by the suite. Following the "No ``monkeypatch`` /
``mocker``" convention of the rest of the suite, env mutation is restored
by hand on teardown.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("django")

from scitex_cards._django.management.commands.scitex_cards_board import (  # noqa: E402
    Command,
    _apply_tasks_env,
)


_ENV_KEY = "SCITEX_CARDS_DB"


@pytest.fixture
def env_isolated():
    """Save/restore ``$SCITEX_CARDS_DB`` around a test.

    We deliberately do NOT use ``monkeypatch`` (see suite-wide convention in
    ``tests/integration/test_peer_edges.py``). The fixture pops the key on
    entry so each test starts with a clean slate, and restores the original
    value (or absence) on teardown.
    """
    original = os.environ.pop(_ENV_KEY, None)
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(_ENV_KEY, None)
        else:
            os.environ[_ENV_KEY] = original


def _parse(argv):
    """Build the command's real argparse parser and parse ``argv``."""
    parser = Command().create_parser("manage.py", "scitex_cards_board")
    return parser.parse_args(argv)


def test_board_command_defaults_port_to_8051():
    # Arrange
    argv = []
    # Act
    options = _parse(argv)
    # Assert
    assert options.port == 8051


def test_board_command_defaults_tasks_to_empty_string():
    # Arrange
    argv = []
    # Act
    options = _parse(argv)
    # Assert
    assert options.tasks == ""


def test_board_command_defaults_no_browser_false():
    # Arrange
    argv = []
    # Act
    options = _parse(argv)
    # Assert
    assert options.no_browser is False


def test_board_command_parses_custom_port():
    # Arrange
    argv = ["--port", "9090"]
    # Act
    options = _parse(argv)
    # Assert
    assert options.port == 9090


def test_board_command_parses_tasks_path():
    # Arrange
    argv = ["--tasks", "/tmp/tasks.yaml"]
    # Act
    options = _parse(argv)
    # Assert
    assert options.tasks == "/tmp/tasks.yaml"


def test_board_command_no_browser_flag_sets_true():
    # Arrange
    argv = ["--no-browser"]
    # Act
    options = _parse(argv)
    # Assert
    assert options.no_browser is True


def test_board_command_help_mentions_board():
    # Arrange
    command = Command()
    # Act
    help_text = command.help
    # Assert
    assert "board" in help_text.lower()


def test_apply_tasks_env_sets_env_var_for_non_empty_path(env_isolated):
    """Regression: ``--tasks PATH`` MUST export ``SCITEX_CARDS_DB``.

    Pre-2026-06-05 the helper did not exist and ``handle()`` only embedded
    the path into the browser URL — so the in-process Django server fell
    through the project-store -> user-store -> bundled fallback chain and
    silently ignored ``--tasks``. This test pins the export.
    """
    # Arrange (env_isolated fixture cleared SCITEX_CARDS_DB)
    target = "/scitex-fleet/tasks.yaml"
    # Act
    _apply_tasks_env(target)
    # Assert
    assert os.environ.get(_ENV_KEY) == target


def test_apply_tasks_env_empty_string_is_noop(env_isolated):
    """Default (no ``--tasks``) must NOT clobber an inherited env var.

    argparse's default for the option is ``""``. Treating that as "unset
    the env" would break the host systemd unit recipe which exports
    ``SCITEX_CARDS_DB=...`` upstream of the CLI invocation.
    """
    # Arrange
    os.environ[_ENV_KEY] = "/inherited/from/upstream.yaml"
    # Act
    _apply_tasks_env("")
    # Assert
    assert os.environ.get(_ENV_KEY) == "/inherited/from/upstream.yaml"


def test_apply_tasks_env_overrides_inherited_value(env_isolated):
    """Explicit ``--tasks`` MUST win over an already-set env var.

    Matches the resolver precedence documented in ``scitex-todo --help``:
    "an explicit --tasks path, then $SCITEX_CARDS_DB, then the project
    store, ...". So a non-empty CLI value uses ``os.environ[...]`` (NOT
    ``setdefault``) and clobbers the inherited value.
    """
    # Arrange
    os.environ[_ENV_KEY] = "/stale/inherited.yaml"
    target = "/explicit/cli.yaml"
    # Act
    _apply_tasks_env(target)
    # Assert
    assert os.environ.get(_ENV_KEY) == target


# EOF
