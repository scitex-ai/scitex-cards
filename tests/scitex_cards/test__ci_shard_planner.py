#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The shard planner's two claims, verified instead of asserted.

`scripts/ci_shard.py` decides which tests each CI shard runs, and the whole
point of the tool is that the answer is TRUSTWORTHY in two ways at once:

  1. **Anti-vacuity** — every collected node-id is selected by exactly one
     shard. A planner that quietly drops tests makes CI greener the more it
     fails to run them, which is the "gate that cannot fail" wearing a
     sharding costume.
  2. **Balance** — the estimated per-shard durations stay close to the mean,
     because wall time is the MAX shard, not the average. A shard set that is
     balanced only "on average" is the situation the tool was written to
     replace: one worker finishing at minute ~30 while the others idle.

Both are properties of a PURE function over (node-ids, profile, file flags), so
they are testable hermetically — no pytest collection, no DB, no network, no
xdist. That is why this file exists: the planner's docstring makes both claims,
and a claim nothing can falsify is a comment.

The script lives outside the package (it is CI machinery, not library code), so
it is loaded by path rather than imported by name.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci_shard.py"

_spec = importlib.util.spec_from_file_location("_ci_shard_under_test", _SCRIPT)
# Loud, not silent: a missing script would otherwise fail as an AttributeError
# three lines later, which reads like a broken test rather than a missing tool.
assert _spec is not None and _spec.loader is not None, f"missing CI machinery: {_SCRIPT}"
ci_shard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ci_shard
_spec.loader.exec_module(ci_shard)


def _ids(n: int, files: int = 7) -> list[str]:
    """`n` node-ids spread over `files` files — enough for a real bin-pack."""
    return [f"tests/scitex_cards/test__f{i % files}.py::test_case_{i}" for i in range(n)]


def test_every_node_id_is_selected_by_exactly_one_shard():
    """Anti-vacuity: nothing dropped, nothing run twice."""
    # Arrange
    ids = _ids(210)
    # Act
    plan = ci_shard.build_plan(ids, 4, {}, {})
    # Assert
    selected = [nid for shard in plan.shards for nid in shard]
    assert sorted(selected) == sorted(ids) and plan.anti_vacuity_ok


def test_a_skewed_profile_spreads_the_heavy_tests_over_the_shards():
    """The balance the tool exists for: heavy tests must not pile onto one shard."""
    # Arrange
    ids = _ids(40)
    heavy = ids[:4]
    profile = {nid: (1000.0 if nid in heavy else 10.0) for nid in ids}
    # Act
    plan = ci_shard.build_plan(ids, 4, profile, {})
    # Assert
    shards_holding_heavy = [i for i, shard in enumerate(plan.shards) if set(shard) & set(heavy)]
    assert len(shards_holding_heavy) == 4


def test_a_skewed_profile_stays_within_a_bounded_deviation():
    """A plan that is 'balanced' only on average is the defect, not the fix."""
    # Arrange
    ids = _ids(120)
    profile = {nid: (500.0 if nid.endswith(("0", "1")) else 5.0) for nid in ids}
    # Act
    plan = ci_shard.build_plan(ids, 4, profile, {})
    # Assert
    assert plan.max_deviation < 0.2


def test_the_planner_refuses_an_empty_collection():
    """A vacuous plan is refused at the source, not discovered in CI."""
    # Arrange
    empty: list[str] = []
    # Act
    build = ci_shard.build_plan
    # Assert
    with pytest.raises(ValueError):
        build(empty, 4, {}, {})


def test_the_verifier_rejects_a_shard_set_that_loses_a_test():
    """The verifier must be able to FAIL, or it is not a check."""
    # Arrange
    ids = _ids(12)
    # Act
    verdict = ci_shard.verify_anti_vacuity(ids, [ids[:-1], []])
    # Assert
    assert verdict is False


def test_the_verifier_rejects_a_duplicated_test():
    """Running one test in two shards wastes a runner and hides the double."""
    # Arrange
    ids = _ids(12)
    # Act
    verdict = ci_shard.verify_anti_vacuity(ids, [ids, ids[:1]])
    # Assert
    assert verdict is False


def test_the_profile_free_fallback_spreads_the_db_files(tmp_path):
    """Without a duration profile, the long tail (DB files) still gets spread."""
    # Arrange
    db_files = [f"tests/scitex_cards/test__db{i}.py" for i in range(4)]
    plain_files = [f"tests/scitex_cards/test__plain{i}.py" for i in range(24)]
    ids = [f"{f}::test_case_{k}" for f in db_files + plain_files for k in range(5)]
    file_db = {f: True for f in db_files}
    file_db.update({f: False for f in plain_files})
    # Act
    plan = ci_shard.build_plan(ids, 4, {}, file_db)
    # Assert
    shards_with_db = [
        i for i, shard in enumerate(plan.shards) if any(nid.split("::")[0] in db_files for nid in shard)
    ]
    assert len(shards_with_db) == 4


def test_the_same_inputs_always_produce_the_same_plan(tmp_path):
    """Determinism: a rebuild must not reshuffle what a green run measured."""
    # Arrange
    ids = _ids(90)
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    # Act
    first = ci_shard.build_plan(ids, 3, {}, {})
    second = ci_shard.build_plan(ids, 3, {}, {})
    first.write(str(first_dir))
    second.write(str(second_dir))
    # Assert
    assert (first_dir / "manifest.json").read_bytes() == (second_dir / "manifest.json").read_bytes()

