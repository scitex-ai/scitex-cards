#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`set_edge(action="remove")` must scrub an edge whose TARGET NO LONGER EXISTS.

Reported independently on 2026-08-09 by scitex-dev (who hit it) and scitex-db
(who was blocked by it), with the same observed error::

    set_edge: unknown target id 'figrecipe-fixes-tmpdir-in-own-repo-first'

THE DEFECT IN ONE LINE: removal validated that the target exists, so the ONE
verb whose job is to scrub a reference refused in exactly the case it exists
for. A repair tool that declines to operate on damage.

WHY THE SAME CHECK IS RIGHT ON `add` AND WRONG ON `remove`. It was one guard
written once for two verbs with OPPOSITE preconditions. On `add`, refusing an
unknown target stops a TYPO minting a dangling edge — a caller error, and the
defect this verb would otherwise CREATE. On `remove`, a dangling edge is not a
reason to refuse; it is the reason the call was made.

THE COST WAS NOT THE ERROR MESSAGE. scitex-db's tenant migration was blocked on
ONE orphaned edge, and the documented remedy could not be run against the damage
it names. The available workaround is ``update_task(depends_on=[...])``, which
REWRITES THE WHOLE LIST — so the refusal pushed callers onto a path that is
LOSSY UNDER CONCURRENCY where a targeted removal is not, and they took it,
because they had work to do. scitex-db put it best: validation and repair ended
up on opposite sides of the same wall.

A FORWARD REFERENCE IS NOT DAMAGE, and that is why the fix is to relax `remove`
rather than to tighten the bulk writers. Unknown ``depends_on``/``blocks`` ids
are DELIBERATELY tolerated — ``_validate.py`` says they are "DROPPED RATHER THAN
REJECTED", and ``_diagram/_mermaid.py`` skips and warns rather than failing.
Naming a card that does not exist yet is a real, supported pattern. Leniency is
policy; removal simply joins it.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import pytest

from scitex_cards import _store
from scitex_cards._store import TaskNotFoundError


@pytest.fixture()
def store_with_dangling_edge(tmp_path: Path) -> Path:
    """A card carrying a ``depends_on`` that points at nothing.

    Built the way the fleet builds one — by naming a target that was never
    created — rather than by hand-editing the document, so the fixture exercises
    the same shape production produced.
    """
    path = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    _store.add_task(
        path,
        id="waiter",
        title="waits on a card that does not exist",
        status="blocked",
        agent="agent-a",
        blocker="dependency",
        depends_on=["never-created"],
    )
    return path


def _edges(path: Path, task_id: str) -> list[str]:
    return list(_store.get_task(path, task_id).get("depends_on") or [])


def test_the_fixture_really_holds_a_dangling_edge(store_with_dangling_edge):
    """Positive control: the damage exists before we try to repair it.

    Without this, a `remove` that silently did nothing would pass the test below
    for the wrong reason — the edge would be absent because it was never there.
    """
    # Arrange
    path = store_with_dangling_edge

    # Act
    edges = _edges(path, "waiter")

    # Assert
    assert edges == ["never-created"]


def test_remove_scrubs_an_edge_whose_target_does_not_exist(store_with_dangling_edge):
    """The reported case: the repair verb must operate on the damage."""
    # Arrange
    path = store_with_dangling_edge

    # Act
    _store.set_edge(
        path,
        action="remove",
        kind="depends_on",
        source="waiter",
        target="never-created",
    )

    # Assert
    assert _edges(path, "waiter") == []


def test_remove_of_an_edge_that_was_never_there_is_a_no_op(store_with_dangling_edge):
    """Removal is IDEMPOTENT — a retrying caller must not be punished.

    The same rule ``ack_notifications`` already follows in this package
    ("confirming the same id twice is a no-op, never an error"). The convention
    existed; this verb simply did not follow it.
    """
    # Arrange
    path = store_with_dangling_edge

    # Act
    result = _store.set_edge(
        path,
        action="remove",
        kind="depends_on",
        source="waiter",
        target="some-id-nobody-ever-mentioned",
    )

    # Assert
    assert result["action"] == "remove"


def test_add_still_refuses_an_unknown_target(store_with_dangling_edge):
    """POSITIVE CONTROL, and the reason this file has four tests instead of two.

    The fix relaxes ONE of two branches that shared a guard. Without this, the
    obvious over-correction — deleting the check outright — passes every other
    test here while removing the typo protection that stops `add` MINTING the
    very damage `remove` now cleans up. A fix that quietly enables the defect it
    repairs is not a fix.
    """
    # Arrange
    path = store_with_dangling_edge

    # Act — `add` keeps its guard, and for its own distinct reason.
    minting_a_typo = functools.partial(
        _store.set_edge,
        path,
        action="add",
        kind="depends_on",
        source="waiter",
        target="typo-in-this-id",
    )

    # Assert
    with pytest.raises(TaskNotFoundError):
        minting_a_typo()

# EOF
