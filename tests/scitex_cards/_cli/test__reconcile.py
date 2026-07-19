#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo reconcile-merged-prs` CLI verb (CliRunner; no mocks).

The CLI is thin plumbing over ``_reconcile_prs.reconcile_merged_prs``; the
decision/seam logic is covered in ``tests/scitex_cards/test__reconcile_prs.py``.
Here we assert the verb registers, is DRY-RUN by default, and threads
``--tasks`` / ``--json`` through. We point the verb at a real temp store
where every PR is OPEN (so the default seam returns no candidates without a
network call) — exercising the wire end-to-end with no mocks.
"""

from __future__ import annotations

import json
import textwrap

from click.testing import CliRunner

from scitex_cards._cli import main


def _store(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            tasks:
              - id: no-pr-card
                title: no linked pr
                status: in_progress
            """
        )
    )
    return path


def test_verb_is_registered():
    # Arrange
    group = main
    # Act
    names = group.commands
    # Assert
    assert "reconcile-merged-prs" in names


def test_dry_run_by_default_exits_zero(tmp_path):
    # Arrange
    path = _store(tmp_path)
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["reconcile-merged-prs", "--tasks", str(path)])
    # Assert
    assert res.exit_code == 0


def test_dry_run_by_default_prints_dry_run_banner(tmp_path):
    # Arrange
    path = _store(tmp_path)
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["reconcile-merged-prs", "--tasks", str(path)])
    # Assert
    assert "DRY-RUN" in res.output


def test_json_output_is_machine_readable(tmp_path):
    # Arrange
    path = _store(tmp_path)
    runner = CliRunner()
    # Act
    res = runner.invoke(main, ["reconcile-merged-prs", "--tasks", str(path), "--json"])
    # Assert
    payload = json.loads(res.output)
    assert payload["applied"] is False


def test_apply_flag_sets_applied_true(tmp_path):
    # Arrange — no parseable pr_url, so nothing actually mutates.
    path = _store(tmp_path)
    runner = CliRunner()
    # Act
    res = runner.invoke(
        main,
        ["reconcile-merged-prs", "--tasks", str(path), "--apply", "--json"],
    )
    # Assert
    payload = json.loads(res.output)
    assert payload["applied"] is True


# EOF
