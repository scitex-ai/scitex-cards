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
    assert _stale_markers(_doc41()) == []


def test_detector_is_not_vacuous():
    fixture = "\n".join(_STALE_MARKERS)
    assert sorted(_stale_markers(fixture)) == sorted(_STALE_MARKERS)


def test_add_section_names_every_shipped_flag():
    doc = _doc41()
    section = doc.split("### `scitex-cards add`")[1].split("### ")[0]
    has_today = next(
        line for line in section.splitlines() if line.startswith("Has today")
    )
    for flag in _ADD_SHIPPED_FLAGS:
        assert flag in has_today, flag
    assert "--tasks` never landed" in has_today
    assert "Shipped (was Missing" in section


def test_add_help_proves_shipped_and_tasks_absent():
    help_text = _help("add")
    for flag in _ADD_SHIPPED_FLAGS:
        assert flag in help_text, flag
    assert "--tasks" not in help_text


def test_list_section_names_every_shipped_filter():
    doc = _doc41()
    section = doc.split("### `scitex-cards list-tasks`")[1].split("### ")[0]
    has_today = next(
        line for line in section.splitlines() if line.startswith("Has today")
    )
    for flag in _LIST_SHIPPED_FLAGS:
        assert flag in has_today, flag
    assert "--status` (repeatable" in has_today
    assert "Shipped filters (was Missing" in section


def test_list_help_proves_filters_shipped():
    help_text = _help("list-tasks")
    for flag in _LIST_SHIPPED_FLAGS:
        assert flag in help_text, flag


def test_update_edges_shipped_with_replace():
    doc = _doc41()
    section = doc.split("### `scitex-cards update`")[1].split("### ")[0]
    assert "SHIPPED on `update` with REPLACE" in section
    assert "`+/-` delta semantics\nremain the follow-up" in section


def test_update_help_proves_edges_replace():
    help_text = _help("update")
    assert "--depends-on" in help_text
    assert "--blocks" in help_text
    assert "REPLACE" in help_text


def test_summary_stays_honest():
    doc = _doc41()
    section = doc.split("### `scitex-cards summary`")[1].split("## ")[0]
    assert "`--scope`, `--assignee`" in section
    assert "remains the follow-up now that `list-tasks` has landed" in section
    help_text = _help("summary")
    assert "--scope" in help_text
    assert "--assignee" in help_text
