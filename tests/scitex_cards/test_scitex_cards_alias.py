#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The `scitex-cards` console script must stay wired to the same CLI.

The operator is moving the package name to `scitex-cards` (matching scitex-hub's
"Cards" surface) and has already put it in his GUI startup list. This entry point
ships the BINARY half of the bridge ahead of the full rename: same code, second
name.

It is deliberately NOT sufficient on its own — his loop also requires
`~/proj/scitex-cards/` to exist as a directory — but the binary half belongs in
the package, and if it silently disappears in the rename his startup breaks with
"scitex-cards: not found" and no explanation.
"""

from pathlib import Path

import tomllib


def _repo_root() -> Path:
    """The repo root, found by MARKER rather than by counting directories.

    `parents[1]` was correct while this file sat at `tests/`, and silently wrong
    the moment it moved to `tests/scitex_cards/` — it resolved to `tests/` and
    the open() failed with FileNotFoundError. Counting parents encodes the
    file's own location into a fact about the repository, so any future move
    re-arms the same trap one directory over. Searching upward for the marker
    does not care where the caller lives.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        f"no pyproject.toml above {__file__} — cannot locate the repo root"
    )


def _scripts() -> dict:
    root = _repo_root()
    with (root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["scripts"]


def test_scitex_cards_console_script_exists():
    # Arrange
    name = "scitex-cards"
    # Act
    scripts = _scripts()
    # Assert
    assert name in scripts


def test_scitex_cards_points_at_the_same_cli():
    """Second name, same entry point — not a fork."""
    # Arrange
    new_name, old_name = "scitex-cards", "scitex-todo"
    # Act
    scripts = _scripts()
    # Assert
    assert scripts[new_name] == scripts[old_name]


def test_scitex_todo_console_script_still_exists():
    """The old name must keep working — the whole fleet still calls it."""
    # Arrange
    name = "scitex-todo"
    # Act
    scripts = _scripts()
    # Assert
    assert name in scripts
