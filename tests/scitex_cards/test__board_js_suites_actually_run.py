#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the board's ``node:test`` suites from pytest, so they actually run.

Twelve files under ``tests/scitex_cards/*.js`` hold ~200 assertions covering
the search parser, the matrix drop arithmetic, the timeline packer and the
chat window. Until now NO runner executed them. Worse, Python docstrings cite
them as where a behaviour is covered --

    test__board_v3_signatures.py:1003
      "The drop arithmetic (`dropAxes`) is unit-tested in test__matrix.js"

-- so the Python suite deferred coverage to a suite that never ran. That is
worse than an untested module: an untested module invites a test, while one
documented as tested elsewhere repels one. When they were finally run
(2026-09-06) one had been RED for two months on a real defect.

WHY A PYTEST DRIVER AND NOT A CI JOB. scitex-cards ruled this on 2026-08-15
(card ``gui-node-tests-not-run-by-ci-20260815``) and the reasoning is the
part that generalises: a separate node job would be A SECOND GATE, and a
second gate is exactly how these twelve became orphans in the first place.
One runner, one report, and -- the property that decides it -- A SKIP IS
VISIBLE IN THE PYTEST SUMMARY. A machine without node reports twelve skips
here; a CI job that silently matched zero files would report green.

Follows the ``chat/`` precedent: locate node, skip cleanly when it is absent,
and drive the REAL files rather than hand-porting their assertions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

#: The JS suites, discovered rather than listed: a new sibling is picked up
#: without editing this file, which is what keeps the next one from becoming
#: an orphan the same way.
_JS_DIR = Path(__file__).resolve().parent
_JS_SUITES = sorted(_JS_DIR.glob("*.js"))


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node is not installed")
    return exe


def test_there_is_at_least_one_js_suite_to_run():
    """A driver that matches nothing passes, which is the original defect.

    This file exists because ~200 assertions were silently not running. A
    parametrized test over an EMPTY list collects zero cases and reports
    green, so the discovery itself needs an assertion or this file could
    reproduce, one level up, exactly what it was written to end.
    """
    # Arrange
    # Act
    found = len(_JS_SUITES)
    # Assert
    assert found > 0, f"no *.js suites discovered in {_JS_DIR}"


@pytest.mark.parametrize("js_suite", _JS_SUITES, ids=lambda p: p.name)
def test_the_js_suite_passes_under_node(js_suite: Path):
    # Arrange
    node = _node()
    # Act
    proc = subprocess.run(
        [node, "--test", str(js_suite)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Assert
    assert proc.returncode == 0, (
        f"{js_suite.name} failed under node --test:\n"
        f"{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}"
    )
