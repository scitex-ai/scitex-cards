#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The root --help must not advertise a storage backend or a default path.

Asserts the CONSTRAINT, not today's sentence: no default file path, and the
one honest answer to "which database?" is present. Any rewording that keeps
the constraint keeps passing.

The constant and the render are tested separately because neither ``main.help``
nor any positional slice of it is stable across environments:

    scitex-dev ABSENT   ``main.help`` = summary + config-resolution block,
    (fallback renderer)  and the block renders BEFORE the Options section

    scitex-dev PRESENT  ``main.help`` = the summary line ONLY; the block is
    (spec renderer)      composed at format time and renders AFTER Options

What IS stable: the constant is the single source of the text, and the text
reaches a reader in both. Hence the split -- the constraint tests read the
constant, and two more prove it actually reaches someone typing --help.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._main import _STORE_RESOLUTION


@pytest.fixture
def resolution_text() -> str:
    return "\n".join(_STORE_RESOLUTION)


@pytest.fixture
def rendered_help() -> str:
    return CliRunner().invoke(main, ["--help"]).output


def test_the_config_resolution_block_advertises_no_default_file(resolution_text):
    # Arrange
    a_default_path = re.compile(r"~/\.scitex/\S*\.db\b")
    # Act
    found = a_default_path.search(resolution_text)
    # Assert
    assert found is None, f"config resolution still advertises a file: {found}"


def test_the_config_resolution_block_points_at_resolve_store(resolution_text):
    # Arrange
    expected = "resolve-store"
    # Act
    present = expected in resolution_text
    # Assert
    assert present, "config resolution must name the verb reporting the target"


def test_the_resolution_text_reaches_a_user_invoking_help(rendered_help):
    # Arrange
    a_distinctive_phrase = "SOLE identity"
    # Act
    present = a_distinctive_phrase in rendered_help
    # Assert
    assert present, "the config-resolution block must reach --help, not just exist"


def test_the_summary_reaches_a_user_invoking_help(rendered_help):
    # Arrange
    expected = "Shared card database"
    # Act
    present = expected in rendered_help
    # Assert
    assert present, "the summary must reach --help"


# EOF
