#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the ``runtime_dir`` PS-234 policy gap.

PS-234 §4b (scitex-dev 0.62.4) demands BOTH halves at once: every
existing ``runtime/`` directory must contain a ``.gitignore`` with
``*`` on disk, AND no runtime entry may be present in the Git index
(measured 2026-10-14: dir-without-policy -> 1 error exit 1;
policy-tracked -> 1 error exit 1; dir-absent or dir+untracked-policy
-> 0 errors). The tracked half can never hold on a fresh clone, so
the only robust posture is self-healing: whatever derives a runtime
dir seeds the 2-byte policy next to the mkdir.

:func:`scitex_cards._paths.runtime_dir` is that chokepoint — it
already creates the dir on demand (``create=True``). These pins prove
it also seeds ``<dir>/.gitignore`` with ``*`` (never overwriting a
present file, never touching the index, never creating anything when
``create=False``).

No store, no PostgreSQL: explicit tmp store paths only, so they hold
in containers where the store-backed suite refuses via the fail-LOUD
guard.
"""

from __future__ import annotations

from scitex_cards._paths import runtime_dir


def test_ensured_runtime_dir_carries_the_ignore_policy(tmp_path):
    # Arrange — a store path the package never saw before.
    store = tmp_path / "tasks.yaml"
    # Act — the same ensure every board/test/CLI run performs.
    d = runtime_dir(store, create=True)
    # Assert — the PS-234 policy rides next to the mkdir.
    policy = d / ".gitignore"
    assert policy.is_file()
    assert policy.read_text() == "*\n"


def test_present_policy_is_never_overwritten(tmp_path):
    # Arrange — a host-local policy (even a differing one) wins.
    d = runtime_dir(tmp_path / "tasks.yaml", create=True)
    (d / ".gitignore").write_text("*.log\n")
    # Act — a second ensure on the same dir.
    runtime_dir(tmp_path / "tasks.yaml", create=True)
    # Assert — present file untouched.
    assert (d / ".gitignore").read_text() == "*.log\n"


def test_create_false_touches_nothing(tmp_path):
    # Arrange — a store path with no dir yet.
    store = tmp_path / "tasks.yaml"
    # Act — the read-path spelling that must not touch the filesystem.
    d = runtime_dir(store, create=False)
    # Assert — neither dir nor policy minted.
    assert not d.exists()
    assert not (d / ".gitignore").exists()


def test_seeded_policy_has_no_negations():
    # Arrange — the rule forbids later negation exceptions.
    from scitex_cards import _paths
    # Act / Assert — the seeded constant is exactly star-newline.
    assert _paths._RUNTIME_GITIGNORE == "*\n"
    assert "!" not in _paths._RUNTIME_GITIGNORE


def test_runtime_dir_docstring_names_the_policy_contract():
    # Arrange — the contract readers must find.
    doc = runtime_dir.__doc__ or ""
    # Act / Assert — the docstring records the seed + the why.
    assert ".gitignore" in doc
    assert "PS-234" in doc


def test_seed_call_sits_after_mkdir_in_source():
    # Arrange — gate-before-lock style: seed must ride the ensure branch.
    import inspect
    # Act — source order inside runtime_dir.
    src = inspect.getsource(runtime_dir)
    # Assert — mkdir precedes the seed call (no seed on the read path).
    assert src.index("mkdir") < src.index("_seed_runtime_gitignore")
