#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LOSSLESS DELIVERY — handover is not confirmation.

The defect these tests pin (measured on the live store, 2026-07-29): reading
with ``ack=True`` marked notifications seen at HANDOVER, so a consumer that
read them and then failed to deliver had PERMANENTLY DESTROYED them — five
operator DMs were enqueued correctly, four were marked SEEN, and the agent saw
none of them. The operator asked twice, eleven minutes apart, because nothing
came back.

No mocks (STX-NM / PA-306): a real store, real ``_inbox`` records, the real
``LocalBackend`` verbs, and — for the load-bearing test — a REAL child process
that reads the inbox and is then killed by ``os._exit`` before it can confirm.
Nothing here simulates death with a patched function; the process genuinely
dies, which is the only honest way to prove the store survived it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_cards import _inbox
from scitex_cards._backend import LocalBackend

#: An UNREGISTERED recipient — the raw-name inbox key the dispatcher falls
#: back to, exercised end-to-end so no user-registration step can hide a bug.
AGENT = "lossless-consumer"

#: The exit code the child uses to die. Distinct from any pytest/python code
#: so "the child died the way we designed" is checkable, not assumed.
KILL_CODE = 9

#: A REAL consumer that reads its inbox and then DIES before confirming —
#: exactly the transient failure the old ack-on-read shape turned permanent.
_CONSUMER_THAT_DIES = """\
import os, sys
sys.path.insert(0, {src!r})
from scitex_cards._backend import LocalBackend

payload = LocalBackend().poll_notifications({agent!r}, store={store!r})
sys.stdout.write(",".join(n["id"] for n in payload["notifications"]))
sys.stdout.flush()
os._exit({code})  # dead between READ and CONFIRM. Nothing was delivered.
"""


def _seed(store, agent=AGENT, count=3):
    """Enqueue ``count`` distinct unseen notifications; return their ids."""
    return [
        _inbox.enqueue(
            agent,
            event_type="commented",
            card_id=f"card-{i}",
            body=f"operator DM {i}",
            actor="operator",
            ts=f"2026-07-29T07:0{i}:00Z",
            store=store,
        )["id"]
        for i in range(1, count + 1)
    ]


def _raw_records(store, agent=AGENT):
    """Read the inbox DIRECTLY — never through the verb under test."""
    return _inbox.poll_inbox(agent, unseen_only=False, mark_seen=False, store=store)


@pytest.fixture()
def inbox(env):
    """A real store holding three UNSEEN notifications for one raw-name agent."""
    env.set("SCITEX_TODO_AGENT_ID", "confirm-tester")
    env.delete("SCITEX_CARDS_HUB_URL")
    store = os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]
    return {"store": store, "ids": _seed(store)}


@pytest.fixture()
def dead_consumer(inbox):
    """Run a REAL consumer process that reads the inbox, then is killed.

    Fails the setup (not the test body) unless the child BOTH died the way we
    designed AND actually received every notification first — a child that
    read nothing would make the test that follows vacuous.
    """
    import scitex_cards

    src = str(Path(scitex_cards.__file__).resolve().parents[1])
    code = _CONSUMER_THAT_DIES.format(
        src=src, agent=AGENT, store=inbox["store"], code=KILL_CODE
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != KILL_CODE:
        pytest.fail(
            f"the child did not die as designed (rc={proc.returncode}): {proc.stderr}"
        )
    handed_over = [i for i in proc.stdout.strip().split(",") if i]
    if handed_over != inbox["ids"]:
        pytest.fail(
            f"the child did not receive the messages before dying "
            f"({handed_over!r} != {inbox['ids']!r}) — the test below would "
            f"be vacuous"
        )
    return inbox


# --------------------------------------------------------------------------- #
# Reading hands over; it does NOT confirm                                     #
# --------------------------------------------------------------------------- #
def test_reading_without_confirming_leaves_the_notification_unseen(inbox):
    # Arrange
    backend = LocalBackend()
    # Act
    backend.poll_notifications(AGENT, store=inbox["store"])
    # Assert
    assert [r["seen"] for r in _raw_records(inbox["store"])] == [False, False, False]


def test_a_second_poll_returns_the_same_unconfirmed_notification(inbox):
    """THE REDELIVERY GUARANTEE: unconfirmed means it comes back."""
    # Arrange
    backend = LocalBackend()
    first = backend.poll_notifications(AGENT, store=inbox["store"])
    # Act
    second = backend.poll_notifications(AGENT, store=inbox["store"])
    # Assert
    assert [n["id"] for n in second["notifications"]] == [
        n["id"] for n in first["notifications"]
    ]


def test_a_consumer_that_dies_between_read_and_confirm_loses_nothing(dead_consumer):
    """THE LOAD-BEARING ONE — the incident, reproduced and refused.

    A real process read every notification and was killed before confirming.
    All three must still be pending for the next consumer.
    """
    # Arrange
    backend = LocalBackend()
    # Act
    payload = backend.poll_notifications(AGENT, store=dead_consumer["store"])
    # Assert
    assert [n["id"] for n in payload["notifications"]] == dead_consumer["ids"]


# --------------------------------------------------------------------------- #
# Confirming is per-id and idempotent                                         #
# --------------------------------------------------------------------------- #
def test_confirming_by_id_advances_the_cursor_for_that_id_only(inbox):
    # Arrange
    backend = LocalBackend()
    first, target, last = inbox["ids"]
    backend.ack_notifications(AGENT, [target], store=inbox["store"])
    # Act
    pending = backend.poll_notifications(AGENT, store=inbox["store"])
    # Assert
    assert [n["id"] for n in pending["notifications"]] == [first, last]


def test_confirming_twice_is_a_no_op(inbox):
    # Arrange
    backend = LocalBackend()
    target = inbox["ids"][0]
    backend.ack_notifications(AGENT, [target], store=inbox["store"])
    # Act
    again = backend.ack_notifications(AGENT, [target], store=inbox["store"])
    # Assert — no raise, nothing newly flipped, and it says why.
    assert (again["confirmed"], again["already_confirmed"]) == ([], [target])


def test_confirming_an_unknown_id_is_not_an_error(inbox):
    # Arrange
    backend = LocalBackend()
    # Act
    result = backend.ack_notifications(AGENT, ["n_deadbeefdead"], store=inbox["store"])
    # Assert
    assert result["unknown"] == ["n_deadbeefdead"]


def test_poll_names_the_ids_awaiting_confirmation(inbox):
    """The safe loop must be the OBVIOUS one to write from the payload."""
    # Arrange
    backend = LocalBackend()
    # Act
    payload = backend.poll_notifications(AGENT, store=inbox["store"])
    # Assert
    assert payload["unconfirmed"] == inbox["ids"]


# --------------------------------------------------------------------------- #
# ack-on-read: DEPRECATED LOUDLY, behaviour deliberately UNCHANGED            #
# --------------------------------------------------------------------------- #
def test_ack_on_read_still_advances_the_cursor(inbox):
    """sac reads this path TODAY — a silent behaviour change is its own outage."""
    # Arrange
    backend = LocalBackend()
    backend.poll_notifications(AGENT, ack=True, store=inbox["store"])
    # Act
    pending = backend.poll_notifications(AGENT, store=inbox["store"])
    # Assert
    assert pending["notifications"] == []


def test_ack_on_read_warns_naming_the_safe_pattern(inbox):
    # Arrange
    backend = LocalBackend()
    # Act
    # Assert — the warning IS the behaviour; act and assert are one statement.
    with pytest.warns(DeprecationWarning, match="ack_notifications"):
        backend.poll_notifications(AGENT, ack=True, store=inbox["store"])


def test_ack_on_read_reports_the_deprecation_in_the_payload(inbox):
    """The consuming AGENT reads JSON, not our logs — tell it there."""
    # Arrange
    backend = LocalBackend()
    # Act
    payload = backend.poll_notifications(AGENT, ack=True, store=inbox["store"])
    # Assert
    assert "ack_notifications" in payload["ack_on_read_deprecated"]


# --------------------------------------------------------------------------- #
# The MCP tool surface (what sac actually calls)                              #
# --------------------------------------------------------------------------- #
try:
    import fastmcp as _fastmcp  # noqa: F401

    _HAS_FASTMCP = True
except ImportError:  # pragma: no cover — exercised only without the extra
    _HAS_FASTMCP = False

_skip_no_mcp = pytest.mark.skipif(
    not _HAS_FASTMCP,
    reason="fastmcp not installed. Install with scitex-cards[all].",
)


async def _call_tool(tool_callable, **kwargs):
    """Await a `@mcp.tool()` callable, peeling FastMCP 3.x's `.fn` wrapper."""
    fn = getattr(tool_callable, "fn", None) or tool_callable
    return await fn(**kwargs)


@_skip_no_mcp
def test_the_ack_notifications_tool_confirms_by_id(inbox):
    # Arrange
    from scitex_cards._mcp_skills import ack_notifications

    target = inbox["ids"][2]
    # Act
    payload = json.loads(
        asyncio.run(
            _call_tool(
                ack_notifications,
                agent=AGENT,
                ids=[target],
                tasks_path=inbox["store"],
            )
        )
    )
    # Assert
    assert payload["confirmed"] == [target]


@_skip_no_mcp
def test_the_poll_tool_redelivers_what_the_ack_tool_never_confirmed(inbox):
    # Arrange
    from scitex_cards._mcp_skills import poll_notifications

    asyncio.run(_call_tool(poll_notifications, agent=AGENT, tasks_path=inbox["store"]))
    # Act
    second = json.loads(
        asyncio.run(
            _call_tool(poll_notifications, agent=AGENT, tasks_path=inbox["store"])
        )
    )
    # Assert
    assert [n["id"] for n in second["notifications"]] == inbox["ids"]


# EOF
