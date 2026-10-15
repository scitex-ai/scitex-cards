#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the skill-doc ``kind: status`` mirror (PR #146 axis).

``VALID_KINDS`` gained ``"status"`` — a non-actionable status-tracking
card (the q-* quality-CI rows) — and CLI ``add`` / ``update --kind``
accept it, but the closed-enum table in bundled doc ``43`` still listed
only ``task · compute · decision``. A consuming agent reading that row
learns a 3-value enum the validator rejects as incomplete: the same
dead-instruction class as the pending-abolition sweep
(``test__skill_docs_pending_abolished``), only on the kind axis. Doc
``41`` (gap table + ``kind: status`` Addendum) and doc ``50``
(``--kind status`` filter) already named it — ``43`` was the one stale
row.

These pins read the bundled ``.md`` file and render ``--help``
through ``CliRunner`` only: no store, no PostgreSQL — they run in
containers where the store-backed suite refuses via the fail-LOUD
guard.
"""

from __future__ import annotations

import re

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]
from scitex_cards._task import VALID_KINDS

#: The stale 3-value row, byte-exact as it shipped before the fix.
_STALE_ROW = (
    "| `kind` | `task` (default if absent) · `compute` · `decision` |"
)

#: Backtick-quoted kind spellings on the enum row.
_KIND_TICK_RE = re.compile(r"`([a-z_]+)`")


def _doc_43() -> str:
    return (_skills_root() / "43_consuming-agent-schema-and-crud.md").read_text(
        encoding="utf-8"
    )


def _kind_row(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("| `kind` |"):
            return line
    raise AssertionError("no `kind` enum row found in doc 43")


def _add_help() -> str:
    out = CliRunner().invoke(main, ["add", "--help"])
    assert out.exit_code == 0, out.output
    return out.output


def test_kind_row_names_every_valid_kind():
    row = _kind_row(_doc_43())
    for kind in VALID_KINDS:
        assert f"`{kind}`" in row, f"{kind!r} missing from doc 43 kind row"


def test_kind_row_names_no_extra_kind():
    row = _kind_row(_doc_43())
    values_cell = row.split("|", 2)[2]  # drop the `kind` field-name cell
    assert set(_KIND_TICK_RE.findall(values_cell)) == set(VALID_KINDS), row


def test_stale_three_value_row_is_gone():
    assert _STALE_ROW not in _doc_43()


def test_cli_add_kind_accepts_status():
    assert "status" in VALID_KINDS
    text = _add_help()
    assert "--kind status" in text or "[task|compute|decision|status]" in text


def test_detector_is_not_vacuous():
    assert set(_KIND_TICK_RE.findall(_STALE_ROW)) != set(VALID_KINDS)
    assert "`status`" not in _STALE_ROW
