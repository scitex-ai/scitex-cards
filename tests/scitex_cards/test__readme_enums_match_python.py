#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the README schema-table pending-abolition sweep.

The repo-root ``README.md`` is the first doc a consuming agent reads, and
its Task-store schema tables taught the abolished ``pending`` status as
live AFTER ``pending`` was abolished 2026-07-10:

* Table 4 (``Task document fields``) listed the ``status`` enum as
  ``goal | pending | in_progress | blocked | done | deferred | failed`` —
  seven entries but the WRONG seven: abolished ``pending`` in,
  ``cancelled`` out.
* Table 4's ``kind`` row listed ``task`` (default) ``|`` ``compute`` only,
  while ``VALID_KINDS`` ships ``task/compute/decision/status`` (PR #146
  axis, live in ``add``/``update --kind`` and ``list-tasks --kind``) —
  same dead-instruction class as the skill-docs kind sweep
  (``test__skill_docs_kind_status``), on the README axis.
* Table 5 (``Board status colours``) styled a ``pending`` row grey
  ``#eceff1`` — the fill the abolished status formerly occupied and
  ``cancelled`` reuses (``STATUS_STYLE`` in ``_diagram/_mermaid.py``;
  board_v3 ``11-sticky-wall.css`` ``.sw-note--cancelled``).

Same dead-instruction class as the skill-docs / CLI-help / TS-enum
sweeps: a reader following the README writes a status the closed-enum
validator refuses (``--status`` is a ``click.Choice`` over
``VALID_STATUSES``) or misses rows cards really carry (``cancelled``
rendered with no documented colour at all).

These pins read the tracked ``README.md`` and import the single sources
(``_task.VALID_STATUSES`` / ``VALID_KINDS``, ``_mermaid.STATUS_STYLE``):
no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import scitex_cards
from scitex_cards._diagram._mermaid import STATUS_STYLE
from scitex_cards._task import ABOLISHED_STATUSES, VALID_KINDS, VALID_STATUSES

_README = (
    Path(scitex_cards.__file__).resolve().parent.parent.parent / "README.md"
)

_BACKTICK_RE = re.compile(r"`([^`]+)`")


def _readme() -> str:
    assert _README.is_file(), f"README moved? not found at {_README}"
    return _README.read_text(encoding="utf-8")


def _table_row(text: str, first_cell: str) -> str:
    """The markdown table row whose first cell is ``first_cell``.

    Fails LOUD when the row is absent so a table reshape moves the pin
    rather than silently vacating it.
    """
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == first_cell:
            return line
    raise AssertionError(f"row {first_cell!r} not found in README tables")


def test_readme_status_enum_mirrors_valid_statuses():
    text = _readme()
    row = _table_row(text, "`status`")
    tokens = set(_BACKTICK_RE.findall(row)) - {"status"}
    assert tokens == set(VALID_STATUSES), (
        f"README status enum drifted: {sorted(tokens)} != "
        f"{sorted(VALID_STATUSES)}"
    )
    # Vacuity guard: the mirror must cover the live 7-value enum.
    assert len(tokens) >= 7, f"status enum pin vacuous: {sorted(tokens)}"


def test_readme_schema_section_names_no_abolished_status():
    text = _readme()
    # Scope to the schema section so the legitimate ``pending-backlog``
    # noun elsewhere in the file (mermaid example, no backticks) cannot
    # satisfy or break this pin.
    section = text.split("## Task store schema")[1].split("## Part of SciTeX")[0]
    for abolished in ABOLISHED_STATUSES:
        assert f"`{abolished}`" not in section, (
            f"README schema section still teaches abolished `{abolished}`"
        )


def test_readme_kind_row_covers_valid_kinds():
    text = _readme()
    row = _table_row(text, "`kind`")
    tokens = set(_BACKTICK_RE.findall(row)) - {"kind"}
    assert set(VALID_KINDS) <= tokens, (
        f"README kind row omits live kinds: {sorted(set(VALID_KINDS) - tokens)}"
    )


def test_readme_color_table_covers_all_live_statuses():
    text = _readme()
    section = text.split("### status -> color")[1].split("## Part of SciTeX")[0]
    rows = [ln for ln in section.splitlines() if ln.strip().startswith("| `")]
    tokens = set()
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        tokens.update(_BACKTICK_RE.findall(cells[0]))
    assert tokens == set(VALID_STATUSES), (
        f"README color table drifted: {sorted(tokens)} != "
        f"{sorted(VALID_STATUSES)}"
    )
    cancelled = _table_row(text, "`cancelled`")
    assert STATUS_STYLE["cancelled"][0].lower() in cancelled.lower(), (
        f"cancelled colour must match STATUS_STYLE {STATUS_STYLE['cancelled']}: "
        f"{cancelled.strip()}"
    )


def test_detector_is_not_vacuous():
    fixture_status = (
        "| `status` | yes | `goal` \\| `pending` \\| `in_progress` "
        "\\| `blocked` \\| `done` \\| `deferred` \\| `failed` |"
    )
    tokens = set(_BACKTICK_RE.findall(fixture_status)) - {"status"}
    assert tokens != set(VALID_STATUSES)
    assert "`pending`" in fixture_status
    fixture_kind = "| `kind` | no | `task` (default) \\| `compute` — writer |"
    kind_tokens = set(_BACKTICK_RE.findall(fixture_kind)) - {"kind"}
    assert not (set(VALID_KINDS) <= kind_tokens)
