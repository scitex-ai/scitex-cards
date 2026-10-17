#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the doc-41 gap-shipped sweep.

Doc 41 (``41_cli-mcp-gap-analysis.md``) §B listed Task-dataclass flags
as Missing long after they shipped in the CLI: every ``add`` row
(``--task`` … ``--finished-at``), every ``list-tasks`` filter
(``--project`` … ``--blocking-me``), and ``update --depends-on`` /
``--blocks`` (claimed missing entirely, actually SHIPPED with REPLACE
semantics). A consuming agent following the gap list would hand-roll
bridges for flags that already exist — the same dead-instruction class
as the CLI-help / hook-script / skill-docs pending-abolition sweep.
``--tasks`` is the one honest non-shipment (no such flag in
``add --help``) and is now said so explicitly.

These pins read the bundled doc and render ``--help`` through
``CliRunner`` only: no store, no PostgreSQL — they run in containers
where the store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

_DOC = "41_cli-mcp-gap-analysis.md"

#: Stale gap-list spellings that must not appear in doc 41. Each names a
#: flag/follow-up as Missing / not-yet-landed AFTER it shipped.
_STALE_MARKERS = (
    "Missing (Task dataclass fields",
    "are missing from `update`",
    "currently only on `add`",
    "Missing filters:",
    "`--status pending --status in_progress`",
    "once the dataclass migration completes",
    "once `list-tasks` lands",
    "--depends-on +X",
)

#: Every ``add`` table row: all shipped (verified against ``add --help``).
_ADD_SHIPPED_FLAGS = (
    "--task",
    "--project",
    "--host",
    "--agent",
    "--goal",
    "--last-activity",
    "--blocker",
    "--pr-url",
    "--issue-url",
    "--kind",
    "--job-id",
    "--command",
    "--started-at",
    "--finished-at",
)

#: Every ``list-tasks`` filter-table row: all shipped.
_LIST_SHIPPED_FLAGS = (
    "--project",
    "--host",
    "--blocker",
    "--kind",
    "--blocking-me",
    "--id-prefix",
    "--agent",
)


def _doc41() -> str:
    return (_skills_root() / _DOC).read_text(encoding="utf-8")


def _help(*argv: str) -> str:
    out = CliRunner().invoke(main, [*argv, "--help"])
    assert out.exit_code == 0, out.output
    return out.output


def _stale_markers(text: str) -> list[str]:
    return [m for m in _STALE_MARKERS if m in text]


def test_no_stale_gap_markers_in_doc41():
    # Arrange
    # Act
    # Assert
    assert _stale_markers(_doc41()) == []


def test_detector_is_not_vacuous():
    # Arrange
    # Act
    fixture = "\n".join(_STALE_MARKERS)
    # Assert
    assert sorted(_stale_markers(fixture)) == sorted(_STALE_MARKERS)


def test_add_section_names_every_shipped_flag():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards add`")[1].split("### ")[0]
    has_today = next(
        line for line in section.splitlines() if line.startswith("Has today")
    )
    missing = [flag for flag in _ADD_SHIPPED_FLAGS if flag not in has_today]
    # Assert
    assert not missing, missing


def test_add_section_notes_unlanded_tasks_flag():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards add`")[1].split("### ")[0]
    has_today = next(
        line for line in section.splitlines() if line.startswith("Has today")
    )
    # Assert
    assert "--tasks` never landed" in has_today


def test_add_section_notes_shipped_was_missing():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards add`")[1].split("### ")[0]
    # Assert
    assert "Shipped (was Missing" in section


def test_add_help_names_every_shipped_flag():
    # Arrange
    # Act
    help_text = _help("add")
    missing = [flag for flag in _ADD_SHIPPED_FLAGS if flag not in help_text]
    # Assert
    assert not missing, missing


def test_add_help_proves_tasks_absent():
    # Arrange
    # Act
    help_text = _help("add")
    # Assert
    assert "--tasks" not in help_text


def test_list_section_names_every_shipped_filter():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards list-tasks`")[1].split("### ")[0]
    has_today = next(
        line for line in section.splitlines() if line.startswith("Has today")
    )
    missing = [flag for flag in _LIST_SHIPPED_FLAGS if flag not in has_today]
    # Assert
    assert not missing, missing


def test_list_section_notes_repeatable_status_filter():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards list-tasks`")[1].split("### ")[0]
    has_today = next(
        line for line in section.splitlines() if line.startswith("Has today")
    )
    # Assert
    assert "--status` (repeatable" in has_today


def test_list_section_notes_shipped_filters_were_missing():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards list-tasks`")[1].split("### ")[0]
    # Assert
    assert "Shipped filters (was Missing" in section


def test_list_help_names_every_shipped_filter():
    # Arrange
    # Act
    help_text = _help("list-tasks")
    missing = [flag for flag in _LIST_SHIPPED_FLAGS if flag not in help_text]
    # Assert
    assert not missing, missing


def test_update_edges_shipped_with_replace_case1():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards update`")[1].split("### ")[0]
    # Assert
    assert "SHIPPED on `update` with REPLACE" in section


def test_update_edges_shipped_with_replace_case2():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards update`")[1].split("### ")[0]
    # Assert
    assert "`+/-` delta semantics\nremain the follow-up" in section


def test_update_help_proves_edges_replace_case1():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "--depends-on" in help_text


def test_update_help_proves_edges_replace_case2():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "--blocks" in help_text


def test_update_help_proves_edges_replace_case3():
    # Arrange
    # Act
    help_text = _help("update")
    # Assert
    assert "REPLACE" in help_text


def test_summary_stays_honest_case1():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards summary`")[1].split("## ")[0]
    # Assert
    help_text = _help("summary")
    assert "`--scope`, `--assignee`" in section


def test_summary_stays_honest_case2():
    # Arrange
    doc = _doc41()
    # Act
    section = doc.split("### `scitex-cards summary`")[1].split("## ")[0]
    # Assert
    help_text = _help("summary")
    assert "remains the follow-up now that `list-tasks` has landed" in section


def test_summary_stays_honest_case3():
    # Arrange
    # Act
    doc = _doc41()
    section = doc.split("### `scitex-cards summary`")[1].split("## ")[0]
    help_text = _help("summary")
    # Assert
    assert "--scope" in help_text


def test_summary_stays_honest_case4():
    # Arrange
    # Act
    doc = _doc41()
    section = doc.split("### `scitex-cards summary`")[1].split("## ")[0]
    help_text = _help("summary")
    # Assert
    assert "--assignee" in help_text
