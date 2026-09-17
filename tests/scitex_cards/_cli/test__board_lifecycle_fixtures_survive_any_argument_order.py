#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board-lifecycle fixtures must survive the ARGUMENT ORDER CI gives pytest.

THE FAILURE THIS GUARDS (measured, not imagined). The py3.11 required-check leg
failed on 2026-09-15/16 with two setup errors in files nobody had touched:

    ERROR tests/scitex_cards/_cli/test__board_stop_process.py::TestSignalRefused::test_reports_signal_refused
    ERROR tests/scitex_cards/_cli/test__board_force_takeover.py::TestForceStopWithNothingRunning::test_returns_none_when_no_board_is_running
    E   fixture 'pidfile_path' not found
    >   available fixtures: ...env, monkeypatch, new_store, tmp_path... (none from _cli/conftest.py)

It was not flaky, and not a py3.11 problem: it reproduces on py3.12 with THREE
arguments and no xdist, deterministically. pytest 9 binds a conftest's fixtures
to the ``Directory`` NODE collected for its directory; explicit FILE arguments
make ``Session.collect()`` re-collect an argument's parent directory, minting a
FRESH ``tests/scitex_cards/_cli`` node, and resolution then matches by node
identity — so the items collected under the new node cannot see the fixtures
the first node owns. The org matrix orders its arguments by descending test
count, which interleaves ``tests/scitex_cards/test__*.py`` with
``tests/scitex_cards/_cli/test__*.py`` and triggers exactly this.

The fixtures are now registered as a plugin with Session scope
(``tests/_board_process_fixtures.py`` via ``tests/conftest.py``), which is
immune by construction: the Session node is in every item's parent chain.

WHY THE GUARD IS A SUBPROCESS RATHER THAN AN ASSERTION ON THE FIXTURES. The
defect is a property of the *collection*, not of the fixture definitions — an
in-process test asking for ``pidfile_path`` gets it (its own collection reached
the node that owns it). Only a separate pytest invocation with the offending
argument order can observe the difference, which is what this runs. The middle
argument matters and is not padding: a file directly in ``tests/scitex_cards/``
is what re-collects the parent directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: ``tests/scitex_cards/_cli/`` → the repository root pytest must run from.
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The reproduction, in order. The board files are the ones that failed; the
#: file between them lives directly in ``tests/scitex_cards/`` and is the
#: trigger (it re-collects that directory, and with it ``_cli``'s node).
_ARGUMENT_ORDER = (
    "tests/scitex_cards/_cli/test__board_force_takeover.py",
    "tests/scitex_cards/test__release_workflow_uses_xdist.py",
    "tests/scitex_cards/_cli/test__board_stop_process.py",
)


def test_the_board_fixtures_survive_a_ci_shaped_argument_order():
    """A three-argument order that used to hide every ``_cli`` conftest fixture."""
    # Arrange — the same interpreter, and the repo's own configured test run.
    argv = [
        sys.executable,
        "-m",
        "pytest",
        *_ARGUMENT_ORDER,
        "-p",
        "no:anyio",
        "--no-header",
        "-q",
    ]
    # Act
    proc = subprocess.run(
        argv,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    # Assert
    assert proc.returncode == 0, (
        f"the board-lifecycle fixtures went missing under this argument order "
        f"(exit {proc.returncode}):\n{proc.stdout[-4000:]}\n{proc.stderr[-1500:]}"
    )


# EOF
