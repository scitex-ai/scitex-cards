#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the PS-234 runtime-ignore repair (E1+E2 of the audit card).

``audit-project`` failed on develop HEAD with two PS-234 §4b errors: the
``.scitex/cards/runtime/`` directory had no catch-all ``.gitignore``
(``FileNotFoundError`` on read) and two seed entries (``.gitkeep``,
``README.md``) were tracked in the Git index. The repair (staged, not yet
merged) creates the on-disk policy file containing ``*``, removes the seed
entries from the index while keeping the local files, and drops the two
seed-file ``!`` exceptions from the root ``.gitignore`` — the policy file
itself stays untracked by design (its own ``*`` ignores it; the checker
reads the policy from disk and the entries from the index, so tracking it
is unnecessary).

These pins hold that end-state without touching a database (no Django
test client, no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard). They mirror the
checker in ``scitex_dev._runtime_gitignore_plugin`` (catch-all set,
no-``!``-after-catch-all, index scan) without importing its privates,
so the pins read as contract, not implementation coupling.

Card ``cards-audit-4-develop-inherited-errors-ps220-ps234-20261012``
owns the repair; E3/E4 (PS-220 stdout transport) are pinned separately
by ``test__output_content_contract.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import scitex_cards

# The checker's catch-all set, mirrored (not imported) so this file pins
# the observable contract: the policy must ignore everything.
_CATCH_ALL = frozenset({"*", "**", "/*", "/**", "**/*", "/**/*"})


def _repo_root() -> Path:
    # Arrange-helper: the src-layout checkout holding the package under test.
    return Path(scitex_cards.__file__).resolve().parents[2]


def _runtime_dir() -> Path:
    # Arrange-helper: the host-local state scope this repair governs.
    return _repo_root() / ".scitex" / "cards" / "runtime"


def _policy_covers_all(text: str) -> bool:
    # Mirror of the checker's policy scan: a catch-all line covers every
    # file, and any later `!` exception re-opens the door (fail).
    covers_all = False
    for line in text.splitlines():
        pattern = line.rstrip(" ")
        if pattern in _CATCH_ALL:
            covers_all = True
        elif pattern.startswith("!"):
            covers_all = False
    return covers_all


def _indexed_runtime_entries() -> list[str]:
    # Arrange-helper: paths the Git INDEX tracks under the cards runtime
    # scope (read-only `ls-files`; the worktree index is what the audit
    # reads, so the staged E2 deletions already count here).
    proc = subprocess.run(
        [
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "-C",
            str(_repo_root()),
            "ls-files",
            "-z",
            "--",
            ":(glob).scitex/cards/runtime",
            ":(glob).scitex/cards/runtime/**",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    return [name for name in proc.stdout.split("\0") if name]


def _path_is_ignored(relative: str) -> bool:
    # Arrange-helper: `git check-ignore` matches patterns even for paths
    # that do not exist on disk, so this holds on fresh clones too.
    proc = subprocess.run(
        ["git", "-C", str(_repo_root()), "check-ignore", "-q", "--", relative],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0


def test_runtime_policy_state_is_covered_or_has_nothing_to_govern():
    # E1: when the runtime dir exists it must carry a catch-all policy
    # with no later `!` exceptions. Absence is acceptable ONLY when there
    # is no runtime dir on disk and nothing indexed — the exact state in
    # which the checker itself stays silent (fresh clone).
    # Arrange
    # Arrange
    runtime = _runtime_dir()
    policy = runtime / ".gitignore"
    # Act
    covered = policy.is_file() and _policy_covers_all(
        policy.read_text(encoding="utf-8")
    )
    # Assert
    assert covered or (not runtime.exists() and not _indexed_runtime_entries())


def test_no_runtime_entries_in_the_git_index():
    # E2: `.gitignore` does not untrack existing entries — the seed files
    # had to leave the index (staged deletions). Red at HEAD (2 entries),
    # green in the worktree and post-merge.
    # Arrange
    # Arrange
    # Act
    indexed = _indexed_runtime_entries()
    # Assert
    assert indexed == []


def test_root_gitignore_ignores_runtime_contents():
    # The parent policy keeps seed files (and any future runtime payload)
    # out of `git status` even though they live on disk.
    # Arrange
    # Arrange
    # Act
    lines = (_repo_root() / ".gitignore").read_text(encoding="utf-8").splitlines()
    # Assert
    assert ".scitex/cards/runtime/*" in lines


def test_root_gitignore_has_no_seed_file_exceptions_case1():
    # The old per-seed `!` re-inclusions are gone (they were tracked only
    # because they predated the runtime move). Red at HEAD (both present).
    # Arrange
    # Arrange
    # Act
    text = (_repo_root() / ".gitignore").read_text(encoding="utf-8")
    # Assert
    assert "!.scitex/cards/runtime/.gitkeep" not in text


def test_root_gitignore_has_no_seed_file_exceptions_case2():
    # The old per-seed `!` re-inclusions are gone (they were tracked only
    # because they predated the runtime move). Red at HEAD (both present).
    # Arrange
    # Arrange
    # Act
    text = (_repo_root() / ".gitignore").read_text(encoding="utf-8")
    # Assert
    assert "!.scitex/cards/runtime/README.md" not in text


def test_seed_gitkeep_path_is_ignored():
    # Wherever the kept local seed file exists, Git must not see it.
    # Arrange + Act (pattern match; holds with or without the file on disk)
    # Arrange
    # Act
    ignored = _path_is_ignored(".scitex/cards/runtime/.gitkeep")
    # Assert
    assert ignored


def test_seed_readme_path_is_ignored():
    # Arrange + Act (pattern match; holds with or without the file on disk)
    # Arrange
    # Act
    ignored = _path_is_ignored(".scitex/cards/runtime/README.md")
    # Assert
    assert ignored


def test_policy_path_stays_untracked_by_design():
    # The policy file ignores ITSELF via its own `*`: plain `git add`
    # refuses it, so it lives on disk untracked and the checker reads it
    # there. If someone force-tracks it, the index pin above goes red.
    # Arrange + Act (pattern match; holds with or without the file on disk)
    # Arrange
    # Act
    ignored = _path_is_ignored(".scitex/cards/runtime/.gitignore")
    # Assert
    assert ignored


def test_catchall_detector_accepts_star():
    # Arrange
    # Arrange
    # Act
    text = "*\n"
    # Assert
    assert _policy_covers_all(text)


def test_catchall_detector_rejects_exception_after_catchall():
    # Arrange
    # Arrange
    # Act
    text = "*\n!.gitkeep\n"
    # Assert
    assert not _policy_covers_all(text)


def test_catchall_detector_rejects_empty_policy():
    # Arrange
    # Arrange
    # Act
    text = ""
    # Assert
    assert not _policy_covers_all(text)
