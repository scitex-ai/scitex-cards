#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``shortTs`` must render a stored UTC stamp on the VIEWER'S clock.

Regression cover for the operator's 2026-07-24 report. The old implementation
string-sliced the ISO stamp (``"…T20:39" -> "20:39"``) and printed UTC digits
under a local label, so a message sent at ``2026-07-23T20:39:25Z`` displayed as
"07-23 20:39" to an operator in Japan whose clock read 05:39 the next morning.
They asked whether the board was on US time. Slicing a timestamp is not
formatting one.

Runs the REAL ``chat_diff.js`` under node with ``TZ`` pinned, so "the viewer's
clock" is an actual controlled variable rather than whatever the CI box is set
to. No mocks and no hand-ported copy of the logic (mirrors test_chat_diff.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_diff.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _short_ts(value, tz: str) -> str:
    """Call the REAL shortTs under node with ``TZ`` pinned to ``tz``."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = (
        f"const ChatDiff = require({json.dumps(str(JS_FILE))});\n"
        f"console.log(JSON.stringify(ChatDiff.shortTs({json.dumps(value)})));"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "TZ": tz},
    )
    return json.loads(proc.stdout.strip())


class TestShortTsRendersOnTheViewersClock:
    """The stamp follows the reader, not the server."""

    def test_the_operators_stamp_reads_as_their_morning_in_tokyo(self):
        # Arrange — the exact message that prompted the report.
        # Act
        result = _short_ts("2026-07-23T20:39:25Z", tz="Asia/Tokyo")
        # Assert — 20:39Z is 05:39 the NEXT day in JST; the date rolls too.
        assert result == "07-24 05:39"

    def test_the_same_instant_reads_as_utc_for_a_utc_viewer(self):
        # Arrange — proves the fix is viewer-relative, not hardcoded to Japan.
        # Act
        result = _short_ts("2026-07-23T20:39:25Z", tz="UTC")
        # Assert
        assert result == "07-23 20:39"

    def test_a_western_viewer_sees_their_own_afternoon(self):
        # Arrange — Denver in July is MDT, i.e. UTC-6 (UTC-7 is the WINTER
        # offset; this expectation was wrong on the first run and the code was
        # right). 20:39Z is therefore 14:39 the same day. Keeping a DST-active
        # zone here is deliberate: a fixed-offset zone would not catch a
        # formatter that ignores daylight saving.
        # Act
        result = _short_ts("2026-07-23T20:39:25Z", tz="America/Denver")
        # Assert
        assert result == "07-23 14:39"


class TestShortTsInputHandling:
    """The store writes UTC; anything else must fail visibly, not quietly."""

    def test_a_stamp_without_a_zone_is_read_as_utc_not_local(self):
        # Arrange — JS parses a bare "…THH:MM:SS" as LOCAL, which would shift
        # exactly these stamps by the viewer's offset. The store writes UTC.
        # Act
        result = _short_ts("2026-07-23T20:39:25", tz="Asia/Tokyo")
        # Assert — same answer as the Z-suffixed form.
        assert result == "07-24 05:39"

    def test_an_explicit_offset_is_honoured(self):
        # Arrange — 09:00+09:00 is 00:00Z, i.e. 09:00 in Tokyo.
        # Act
        result = _short_ts("2026-07-24T09:00:00+09:00", tz="Asia/Tokyo")
        # Assert
        assert result == "07-24 09:00"

    def test_an_unparseable_value_is_returned_verbatim(self):
        # Arrange — showing the raw string is honest; showing a confidently
        # wrong time is not.
        # Act
        result = _short_ts("not-a-timestamp", tz="Asia/Tokyo")
        # Assert
        assert result == "not-a-timestamp"

    def test_an_empty_value_renders_as_empty(self):
        # Arrange
        # Act
        result = _short_ts("", tz="Asia/Tokyo")
        # Assert
        assert result == ""
