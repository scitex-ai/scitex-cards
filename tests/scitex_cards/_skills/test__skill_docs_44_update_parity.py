#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the doc-44 update-parity sweep.

Doc 44 (``44_consuming-agent-coordination.md``) Pattern A told consuming
agents ``--depends-on`` / ``--blocker`` were still "pending CLI parity"
("Once --depends-on lands as a CLI flag on update; today via Python"),
long after both shipped on ``scitex-cards update`` (``--blocker`` closed
enum incl. ``dependency``; ``--depends-on`` / ``--blocks`` with REPLACE
semantics — pinned by the doc-41 gap sweep). A consuming agent following
Pattern A would hand-roll Python for flags that already exist — the same
dead-instruction class as the doc-41 gap fix. Pattern A now shows the
shipped CLI recipe; Python stays as the multi-field alternative.

These pins read the bundled doc and render ``--help`` through
``CliRunner`` only: no store, no PostgreSQL — they run in containers
where the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

_DOC = "44_consuming-agent-coordination.md"

#: Stale parity-gap spellings that must not appear in doc 44. Each claims a
#: shipped ``update`` flag is not yet on the CLI.
_STALE_MARKERS = (
    "Once --depends-on lands",
    "today via Python",
    "pending CLI parity",
    "pending --blocker on CLI",
    "pending --depends-on on update",
)


def _doc44() -> str:
    return (_skills_root() / _DOC).read_text(encoding="utf-8")


def _pattern_a(text: str) -> str:
    assert "### A. Hard dep" in text
    return text.split("### A. Hard dep")[1].split("### ")[0]


def _help(*argv: str) -> str:
    out = CliRunner().invoke(main, [*argv, "--help"])
    assert out.exit_code == 0, out.output
    return out.output


def _stale_markers(text: str) -> list[str]:
    return [m for m in _STALE_MARKERS if m in text]


def test_no_stale_parity_markers_in_doc44():
    # Arrange
    # Act
    # Assert
    assert _stale_markers(_doc44()) == []


def test_detector_is_not_vacuous():
    # Arrange
    # Act
    fixture = "\n".join(_STALE_MARKERS)
    # Assert
    assert sorted(_stale_markers(fixture)) == sorted(_STALE_MARKERS)


def test_pattern_a_section_exists_case1():
    # Arrange
    # Act
    doc = _doc44()
    # Assert
    section = _pattern_a(doc)
    assert doc.startswith("---\n")


def test_pattern_a_section_exists_case2():
    # Arrange
    doc = _doc44()
    # Act
    section = _pattern_a(doc)
    # Assert
    assert "scitex-cards update" in section


def test_pattern_a_shows_shipped_cli_recipe_case1():
    # Arrange
    # Act
    section = _pattern_a(_doc44())
    # Assert
    assert "--blocker dependency" in section


def test_pattern_a_shows_shipped_cli_recipe_case2():
    # Arrange
    # Act
    section = _pattern_a(_doc44())
    # Assert
    assert "--depends-on" in section


def test_pattern_a_shows_shipped_cli_recipe_case3():
    # Arrange
    # Act
    section = _pattern_a(_doc44())
    # Assert
    assert "REPLACE" in section


def test_update_help_proves_flags_shipped_case1():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "--blocker" in help_text


def test_update_help_proves_flags_shipped_case2():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "--depends-on" in help_text


def test_update_help_proves_flags_shipped_case3():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "--blocks" in help_text


def test_update_help_proves_flags_shipped_case4():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "dependency" in help_text


def test_update_help_proves_flags_shipped_case5():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "REPLACE" in help_text


def test_update_help_proves_flags_shipped_case6():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "--status [goal|in_progress|blocked|done|deferred|failed|cancelled]" in help_text
