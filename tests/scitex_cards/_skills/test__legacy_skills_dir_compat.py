#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The legacy ``_skills/scitex-todo`` path must keep resolving after the rename.

WHY THIS EXISTS — it is a fleet-liveness pin, not a naming preference.

The fleet stages package skills through symlinks that name the directory
**by its on-disk path**:

    ~/.claude/to_claude/skills/scitex/scitex-todo
        -> <repo>/src/scitex_cards/_skills/scitex-todo

Those links are git-tracked dotfiles state (mode 120000 blobs), ~40 siblings
in the same shape, and they live OUTSIDE this repository. When the directory
was renamed, that link dangled and every ``sac agents start`` that stages the
skill died in ``shutil.copytree`` with ``[Errno 2]``. On 2026-07-16 that took
``scitex-todo``, ``scitex-dev``, ``scitex-hpc`` and ``claude-code-telegrammer``
down at once — the fleet could not be restored at all until the link was
re-pointed by hand.

WHY A SYMLINK IN THE REPO RATHER THAN CHOREOGRAPHY. The obvious fix — re-point
the external link first, then merge — is NOT EXECUTABLE: ``_skills/scitex-cards``
does not exist until this change merges and the checkout pulls, so there is
nothing to point the new link at when that step runs. Any hand-ordering leaves a
window between ``git pull`` and the re-point in which every agent start fails,
and the fleet has cron-driven restarts landing in that window. Shipping the
compat link inside the repo means no instant exists at which an external
consumer dangles. Removing the need to coordinate beats coordinating well.

REMOVING THIS IS A TWO-SIDED CHANGE. Delete it only together with the external
link, after a boot canary — one ``sac agents start`` that actually succeeds,
verified by the agent's process start time rather than the command's exit code
(a lifecycle verb returning 0 asserts that the verb returned, not that the world
changed). Deleting it alone re-opens the exact outage above, which is why the
assertion below is a contract and not a tautology.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from scitex_cards._cli._skills import _skills_root

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fleet staging path this pins is POSIX-only",
)

#: The pre-rename directory name. Hardcoded ON PURPOSE: it is the string
#: baked into out-of-repo symlinks, so deriving it from a constant would let
#: the two drift apart silently — which is the whole failure this pins.
LEGACY_DIR_NAME = "scitex-todo"


def _skills_parent() -> Path:
    return Path(_skills_root()).parent


class TestLegacyPathStillResolves:
    """The old directory name must remain a usable path into the skills."""

    def test_legacy_dir_name_resolves_to_the_renamed_directory(self):
        # Arrange
        legacy = _skills_parent() / LEGACY_DIR_NAME
        # Act
        resolved = legacy.resolve()
        # Assert
        assert legacy.exists(), (
            f"{legacy} does not resolve. Out-of-repo fleet symlinks name this "
            "exact path; without it `sac agents start` dies in copytree."
        )
        assert resolved == Path(_skills_root()).resolve(), (
            "the legacy path must resolve to the current skills directory, "
            f"not to a second copy — got {resolved}"
        )

    def test_legacy_path_carries_the_skill_entrypoint(self):
        # Arrange
        legacy = _skills_parent() / LEGACY_DIR_NAME
        # Act
        skill_md = legacy / "SKILL.md"
        # Assert
        assert skill_md.is_file(), (
            "a resolving directory that lacks SKILL.md would stage an empty "
            "skill — the link would look healthy and the skill would be gone"
        )


class TestFleetStagingOperationSucceeds:
    """Pin the ACTUAL operation that failed, not a proxy for it.

    The 2026-07-16 outage was a ``shutil.copytree`` raising ``FileNotFoundError``.
    Asserting that a path exists is a weaker claim than asserting that the call
    the fleet actually makes completes, so this runs the real call.
    """

    def test_copytree_through_the_legacy_path_stages_every_file(self, tmp_path):
        # Arrange
        legacy = _skills_parent() / LEGACY_DIR_NAME
        destination = tmp_path / "staged"
        expected = {p.name for p in Path(_skills_root()).iterdir()}
        # Act
        shutil.copytree(legacy, destination)
        # Assert
        staged = {p.name for p in destination.iterdir()}
        assert staged == expected, (
            "staging through the legacy path must produce the same tree as the "
            f"canonical directory; missing {expected - staged}"
        )


# EOF
