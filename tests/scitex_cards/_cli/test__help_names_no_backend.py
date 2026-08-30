#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The root --help must not advertise a backend or a default path.

WHY NOT A STRING-EQUALITY TEST. Pinning today's exact sentence would re-encode
today's wording and fail on the next harmless rewrite. These assert the
CONSTRAINT — no banned backend name, no default file path, and the one honest
answer to "which database?" is present — so any rewording that keeps the
constraint keeps passing.

WHY THE CONSTANT PLUS A REACHES-THE-READER TEST, RATHER THAN THE RENDER ALONE.
The first version of this file asserted over ``main.help`` and claimed in this
docstring that "both the spec-built renderer and _render_fallback_help compose
these". THAT CLAIM WAS FALSE AND CI CAUGHT IT. Measured in both interpreters:

    scitex-dev ABSENT   ``main.help`` = summary + config-resolution block,
    (fallback renderer)  and the block renders BEFORE the Options section

    scitex-dev PRESENT  ``main.help`` = the summary line ONLY; the block is
    (spec renderer)      composed at format time and renders AFTER Options

So neither ``main.help`` nor any positional slice of the render is stable
across environments. What IS stable: the constant is the single source of the
text, and the text reaches a reader in both. Hence the split — the constraint
tests read the constant this change owns, and two separate tests prove the
constant actually reaches someone typing --help in whatever environment they
are in.

The full ``--help`` render is deliberately NOT asserted over, because it also
contains every subcommand's short_help, four of which still name the retired engine (db,
inbox, index, init-store). Those need four separate judgements — ``index`` may
be RIGHT to name it, the derived index being a genuinely separate rebuildable
artifact — and are tracked on their own card. Widening these assertions is
correct once that lands.
"""

from __future__ import annotations

from _banned import DRIVER, ENGINE  # noqa: F401

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


def test_the_config_resolution_block_never_names_the_retired_engine(resolution_text):
    # Arrange
    banned = re.compile(rENGINE, re.IGNORECASE)
    # Act
    found = banned.search(resolution_text)
    # Assert
    assert found is None, f"config resolution still names the retired engine:\n{resolution_text}"


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
