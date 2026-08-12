#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The root group's own help must not advertise a backend or a default path.

WHY NOT A STRING-EQUALITY TEST. Pinning today's exact sentence would re-encode
today's wording and fail on the next harmless rewrite. These assert the
CONSTRAINT — no banned backend name, no default file path, and the one honest
answer to "which database?" is present — so any rewording that keeps the
constraint keeps passing.

WHY ``main.help`` AND NOT THE FULL ``--help`` OUTPUT. The full output also
contains every subcommand's ``short_help``, and four of those still name
SQLite (``db``, ``inbox``, ``index``, ``init-store``). Asserting over the whole
render would make this file fail for text it does not own and cannot fix
without a judgement it has not made — ``index`` in particular describes a
genuinely separate SQLite artifact, the rebuildable derived index, where naming
the engine may well be correct. Those four are tracked on their own card.
``main.help`` is exactly the summary + config-resolution block this change
owns, and it is real rendered text rather than the module constants, because
the constants reaching the reader is the whole claim.

Both the spec-built renderer and ``_render_fallback_help`` compose these, so
these hold whether or not scitex-dev is installed.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main


@pytest.fixture
def group_help() -> str:
    return main.help or ""


def test_root_group_help_never_names_sqlite(group_help):
    # Arrange
    banned = re.compile(r"sqlite", re.IGNORECASE)
    # Act
    found = banned.search(group_help)
    # Assert
    assert found is None, f"the root group help still names sqlite:\n{group_help}"


def test_root_group_help_advertises_no_default_database_file(group_help):
    # Arrange
    a_default_path = re.compile(r"~/\.scitex/\S*\.db\b")
    # Act
    found = a_default_path.search(group_help)
    # Assert
    assert found is None, f"the root group help still advertises a file: {found}"


def test_root_group_help_points_at_resolve_store_for_the_real_target(group_help):
    # Arrange
    expected = "resolve-store"
    # Act
    present = expected in group_help
    # Assert
    assert present, "the root group help must name the verb reporting the target"


def test_the_summary_reaches_a_user_invoking_help():
    # Arrange
    runner = CliRunner()
    # Act
    rendered = runner.invoke(main, ["--help"]).output
    # Assert
    assert "Shared card database" in rendered


# EOF
