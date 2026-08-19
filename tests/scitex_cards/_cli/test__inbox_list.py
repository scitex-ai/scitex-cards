#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards inbox list`` — the read half of ``inbox ack``.

REPORTED BY scitex-agent-container 2026-08-18, from inside a live outage.
Their scitex-cards MCP server dropped mid-session while the STORE stayed
perfectly healthy — every card verb kept working — so this was a SURFACE
outage with an asymmetric hole in it:

    survives without MCP:  scitex-cards inbox ack <ids>
    does NOT:              poll_notifications  (MCP-only, no CLI equivalent)

``ack`` is the only cursor-advancing verb reachable without MCP, and it takes
IDS — which only the MCP verb could enumerate. So the inbox became unclearable
from the surface that still worked, and the reporter could ack only the seven
ids they happened to have scraped from channel banners.

*** THE FAILURE THAT MATTERS IS THE ONE IT TEMPTS. *** Their stop hook blocks
on "N unread notification(s) — poll_notifications and act on them", which is
unfollowable with MCP down, leaving an agent choosing between never clearing
the hook and ACKING IDS IT HAS NOT READ. The second is easier, and it converts
a gate that exists to ensure things get read into a counter that can be
satisfied without reading — a gate configured so it cannot fail, arrived at by
an agent behaving reasonably under an outage.

A cursor-advancing verb whose argument is only obtainable on a DIFFERENT
surface is not usable standalone. This verb closes that.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._inbox import enqueue
from scitex_cards._inbox_confirm import confirm_notifications


@pytest.fixture()
def two_notifications(env):
    """Two real records in this agent's inbox, one of them confirmed."""
    env.set("SCITEX_CARDS_AGENT_ID", "list-tester")
    ids = []
    for i in (1, 2):
        enqueue(
            "list-tester",
            event_type="commented",
            card_id=f"c{i}",
            body=f"body {i}",
            actor="someone",
            ts=f"2026-08-18T0{i}:00:00Z",
        )
    from scitex_cards._inbox import poll_inbox

    ids = [r["id"] for r in poll_inbox("list-tester", unseen_only=False, mark_seen=False)]
    confirm_notifications("list-tester", [ids[0]])
    return {"all": ids, "confirmed": ids[0], "open": ids[1]}


def _run(*args):
    return CliRunner().invoke(main, ["inbox", "list", *args])


def test_list_enumerates_every_record(two_notifications):
    # Arrange
    # Act
    result = _run("--json")

    # Assert
    payload = json.loads(result.output)
    assert payload["count"] == len(two_notifications["all"])


def test_list_names_the_id_that_ack_would_need(two_notifications):
    # Arrange
    # Act
    result = _run()

    # Assert — the id is the whole point: it is `ack`'s argument.
    assert two_notifications["open"] in result.output


def test_unconfirmed_excludes_what_was_already_confirmed(two_notifications):
    # Arrange
    # Act
    result = _run("--unconfirmed", "--json")

    # Assert
    listed = {r["id"] for r in json.loads(result.output)["notifications"]}
    assert two_notifications["confirmed"] not in listed


def test_unconfirmed_keeps_what_still_needs_acking(two_notifications):
    # Arrange
    # Act
    result = _run("--unconfirmed", "--json")

    # Assert
    listed = {r["id"] for r in json.loads(result.output)["notifications"]}
    assert two_notifications["open"] in listed


def test_listing_does_not_confirm_anything(two_notifications):
    """READ-ONLY, and it has to be provable rather than asserted in a docstring.

    An enumerate verb that quietly advanced the cursor would recreate the exact
    defect this package already has on the push rail: handover recorded as
    delivery. Listing twice must leave the second listing unchanged.
    """
    # Arrange
    before = json.loads(_run("--unconfirmed", "--json").output)["count"]

    # Act
    _run("--unconfirmed")

    # Assert
    assert json.loads(_run("--unconfirmed", "--json").output)["count"] == before


def test_it_tells_the_reader_how_to_confirm(two_notifications):
    # Arrange
    # Act
    result = _run("--unconfirmed")

    # Assert — the remedy names the sibling verb and this agent, so the ids
    # just printed can be acted on without looking anything else up.
    assert "scitex-cards inbox ack --agent list-tester" in result.output


# EOF
