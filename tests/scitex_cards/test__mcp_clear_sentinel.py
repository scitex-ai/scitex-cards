#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clearing a field over MCP: ``"__none"`` does what ``""`` cannot.

THE BUG (measured first-hand, 2026-07-18). The store documents ``field=""``
as the way to clear, and it works from Python and the CLI. Over MCP it is
UNREACHABLE: clients strip empty-string params before the server sees them,
so the call arrives malformed (`"blocker": }` -> InputValidationError) rather
than clearing anything. An agent following our own documentation got an error
whose text pointed at JSON syntax, not at the real cause.

It had been carded for a day and hit again while trying to un-block a card,
which is what finally made the cost concrete: the documented escape hatch did
not exist on the transport most agents actually use.

``"__none"`` is the same token ``list_tasks`` already spends on "no blocker",
so this adds a second VERB to one convention rather than a second spelling.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastmcp", reason="MCP tools require the [mcp] extra")

from scitex_cards._store import add_task  # noqa: E402


def _update(task_id, **kw):
    from scitex_cards._mcp_write import update_task

    return json.loads(asyncio.run(update_task(task_id=task_id, **kw)))


def test_it_clears_a_blocker():
    """The exact case that failed: un-blocking a card over MCP."""
    # Arrange
    add_task(
        id="t1",
        title="t1",
        agent="worker-x",
        status="blocked",
        blocker="operator-decision",
    )

    # Act
    merged = _update("t1", blocker="__none")

    # Assert — the key is GONE, not set to the string "__none".
    assert "blocker" not in merged or merged.get("blocker") in (None, "")


def test_it_clears_a_free_text_field():
    # Arrange
    add_task(id="t2", title="t2", agent="worker-x", note="some note")

    # Act
    merged = _update("t2", note="__none")

    # Assert
    assert not merged.get("note")


def test_it_un_parks_a_card():
    """`parked` is the field whose clear path had no reachable spelling."""
    # Arrange
    add_task(
        id="t3",
        title="t3",
        agent="worker-x",
        parked="north star; children hold the work",
    )

    # Act
    merged = _update("t3", parked="__none")

    # Assert — un-parked, so the backlog sweep sees it again.
    assert not merged.get("parked")


def test_a_normal_value_is_untouched():
    """The sentinel must not eat legitimate content."""
    # Arrange
    add_task(id="t4", title="t4", agent="worker-x")

    # Act
    merged = _update("t4", note="a real note")

    # Assert
    assert merged["note"] == "a real note"


def test_status_still_refuses_to_be_cleared():
    """A card must carry a decision — the sentinel is not a way around that."""
    # Arrange
    add_task(id="t5", title="t5", agent="worker-x", status="in_progress")

    # Act
    ctx = pytest.raises(Exception)

    # Assert — the sentinel is refused, so the card keeps its decision.
    with ctx:
        _update("t5", status="__none")


#: WHY the two tests below are split but share this rationale:
#: `None` = "leave this field alone" is the OTHER half of the sentinel
#: contract — adding a spelling that means "clear it" must not disturb the
#: spelling that means "do not touch it". One call exercises both halves at
#: once (an omitted `note` beside a supplied `title`), so each half is
#: asserted in its own test rather than behind a first-assert that can hide
#: the second.


def test_an_omitted_field_is_still_left_alone():
    # Arrange
    add_task(id="t6", title="t6", agent="worker-x", note="keep me")

    # Act
    merged = _update("t6", title="renamed")

    # Assert — `note` was not passed at all, so it must survive untouched.
    assert merged["note"] == "keep me"


def test_a_field_passed_alongside_an_omitted_one_still_updates():
    # Arrange
    add_task(id="t6", title="t6", agent="worker-x", note="keep me")

    # Act
    merged = _update("t6", title="renamed")

    # Assert — the half of the same call that WAS passed still took effect.
    assert merged["title"] == "renamed"


# EOF
