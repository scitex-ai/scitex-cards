#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The thread pane survives a refused read-receipt ack (no mocks).

Operator 2026-10-24: hub's mount reads the fleet store as a SELECT-only
role, so ``GET /dm/thread/<peer>?mark_read=1`` answered HTTP 403 and the
pane sat on "Loading..." though the messages read fine. The ack is best
effort now — the server serves 200 + messages with ``ack.ok == False``,
and the client renders them with a latched one-time notice instead of
the red error bar.

``chat.js`` touches the DOM and cannot be required under node, so the
client half is asserted against the source — the way
``test__dm_sidebar_project_slot.py`` asserts wiring. The server half is
pinned in ``handlers/test__dm_views.py`` against a real store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("django")

_CHAT_JS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat.js"
)


def _source() -> str:
    assert _CHAT_JS.is_file(), f"chat.js missing: {_CHAT_JS}"
    return _CHAT_JS.read_text(encoding="utf-8")


def test_pane_reads_the_ack_refusal_flag() -> None:
    """The render path must consult ``data.ack.ok``, not just messages."""
    # Arrange
    source = _source()
    # Act
    consults = "data.ack" in source and "!data.ack.ok" in source
    # Assert
    assert consults


def test_pane_notices_once_per_thread_not_once_per_poll() -> None:
    """A notice every 5s poll would flicker forever; the latch makes it one."""
    # Arrange
    source = _source()
    # Act
    latched = "state.ackWarnedFor !== peer" in source
    rearmed = "state.ackWarnedFor = null" in source
    # Assert
    assert latched and rearmed


def test_pane_uses_the_notice_bar_not_the_error_bar() -> None:
    """A paused ack is information, not a failure — red would be the lie."""
    # Arrange
    source = _source()
    # Act
    idx = source.find("state.ackWarnedFor = peer")
    assert idx != -1
    window = source[idx : idx + 400]
    # Assert
    assert "showNotice(" in window and "Read receipts paused" in window


# EOF
