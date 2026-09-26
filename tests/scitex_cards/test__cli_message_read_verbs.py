#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI read verbs for messages: ``inbox list`` and ``dm list``.

Card ``cards-cli-has-no-read-verb-for-inbox-or-dm-20260905``: the scitex-cards
CLI could read CARDS but not MESSAGES — notifications were readable only via
``inbox ack`` (which does not return the payload) and DMs only via the MCP
server. When the MCP server vanished from a session (reported by scitex-app),
the agent had no read path for its own messages at all, because MCP was the
single one. This adds the standalone read verbs so a message is readable
WITHOUT any model harness or MCP server — the harness-independence this
package is required to keep.

Both verbs wrap the EXISTING Python API (``poll_notifications`` /
``dm_list``) as a read-only surface: ``inbox list`` never acks (a record you do
not ``inbox ack`` stays unseen and comes back next time), and ``dm list``
passes ``ack=False`` (reading does not mark messages read). No mocks (PA-306);
the store-backed round-trip uses a real seeded store via the ``new_store``
factory fixture.
"""

from __future__ import annotations

import inspect
import json

import pytest
from click.testing import CliRunner

from scitex_cards._cli._dm import dm_group
from scitex_cards._cli._inbox import inbox_group
from scitex_cards._threads import append_message


# --- the verbs exist and are wired into their noun groups -------------------


def test_inbox_group_has_a_list_read_verb():
    # Arrange
    commands = inbox_group.commands
    # Act
    # Assert — the gap was that inbox had ONLY ack; list must now be present.
    assert "list" in commands


def test_dm_group_has_a_list_read_verb():
    # Arrange
    commands = dm_group.commands
    # Act
    # Assert
    assert "list" in commands


# --- read-only contract: neither verb advances a cursor --------------------


def test_inbox_list_does_not_ack():
    # Arrange — the source must call poll_notifications and NOT ack_notifications.
    src = inspect.getsource(inbox_group.commands["list"].callback)
    # Act
    # Assert — reads pull; it must not confirm/advance the unseen cursor.
    assert "poll_notifications" in src and "ack_notifications" not in src


def test_dm_list_reads_with_ack_false():
    # Arrange — dm list must read the thread without marking it read.
    src = inspect.getsource(dm_group.commands["list"].callback)
    # Act
    # Assert
    assert "ack=False" in src


# --- a real round-trip: seeded DM is readable via the CLI, no harness -------


def test_dm_list_reads_a_seeded_thread(new_store):
    # Arrange — a real DM in a fresh throwaway store (no MCP, no harness).
    store = new_store()
    append_message("alice", "bob", "hello over the wire", store=store)
    # Act
    result = CliRunner().invoke(
        dm_group, ["list", "--peer", "bob", "--sender", "alice", "--store", store, "--json"]
    )
    # Assert — exit 0 AND the seeded message is in the payload, in one check.
    payload = json.loads(result.output) if result.exit_code == 0 else {}
    assert result.exit_code == 0 and any(
        m.get("body") == "hello over the wire" for m in payload.get("messages", [])
    ), result.output


# EOF
