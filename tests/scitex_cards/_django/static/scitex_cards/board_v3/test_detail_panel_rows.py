#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The detail panel's two re-implemented rows: local time, and who is subscribed.

Exercises the SHIPPED modules by ``require()``-ing them and running the REAL
functions under node — the ``chat/`` and ``16-card-helpers`` arrangement. No
hand-ported copy of the logic lives here: a mirror drifts, and then both
"pass" while disagreeing (measured this week on `searchQuery.js`, whose enum
had drifted from Python for two months under a comment claiming it mirrored
it).

WHAT THESE FIX. Both are the operator's own PR #330 (2026-07-18), which sat
unmergeable for seven weeks: it patched ``src/scitex_todo/...``, a path that
stopped existing at the package rename, and called ``bucket()``, deleted in
#484. Not a rebase — a re-implementation. The two defects were re-measured on
develop before this was written, not inferred from the PR being open:

  1. the panel printed ``t.last_activity`` RAW, i.e. the stored UTC ISO
     string, so a JST reader saw every stamp nine hours in the past;
  2. it rendered ``fmtList(t.subscribers)`` — explicit subscriptions only —
     while ``creator`` and ``assignee`` sat computed two lines above it and
     unused, contradicting the operator's 2026-07-06 rule that both are
     always subscribers.

Card ``board-v3-operator-pr330-stranded-20260815``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_STATIC = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
)
DATEINFO_JS = _STATIC / "15-dateinfo.js"
CARD_HELPERS_JS = _STATIC / "16-card-helpers.js"
TEMPLATE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)


def _node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(module: Path, js: str, *, tz: str | None = None) -> str:
    assert module.is_file(), f"module under test missing: {module}"
    script = f"const M = require({json.dumps(str(module))});\n" + js
    env = None
    if tz is not None:
        import os

        env = {**os.environ, "TZ": tz}
    proc = subprocess.run(
        [_node(), "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip()


# --------------------------------------------------------------------------
# fmtLocalTime — the nine-hour bug
# --------------------------------------------------------------------------


def test_a_utc_stamp_renders_in_the_viewers_zone_not_utc():
    # The whole complaint: 03:00 UTC is 12:00 in Tokyo, and the panel used to
    # show the operator 03:00.
    # Arrange
    js = 'process.stdout.write(M.fmtLocalTime("2026-09-06T03:00:00Z"));'
    # Act
    out = _run(DATEINFO_JS, js, tz="Asia/Tokyo")
    # Assert
    assert "12:00" in out, out


def test_the_same_stamp_renders_differently_in_a_different_zone():
    # Pins that the zone is actually consulted. Without this, a formatter that
    # hard-coded one offset would pass the test above.
    # Arrange
    js = 'process.stdout.write(M.fmtLocalTime("2026-09-06T03:00:00Z"));'
    # Act
    tokyo = _run(DATEINFO_JS, js, tz="Asia/Tokyo")
    utc = _run(DATEINFO_JS, js, tz="UTC")
    # Assert
    assert tokyo != utc, f"zone ignored: {tokyo!r} == {utc!r}"


def test_midnight_renders_as_zero_thirty():
    # `hour12: false` renders midnight as "24:00" in some engines; the
    # operator's patch used hourCycle "h23" for exactly this, and losing that
    # detail in the re-implementation would reintroduce a bug he had solved.
    # Arrange
    js = 'process.stdout.write(M.fmtLocalTime("2026-09-06T15:30:00Z"));'
    # Act — 15:30Z is 00:30 the next day in Tokyo
    out = _run(DATEINFO_JS, js, tz="Asia/Tokyo")
    # Assert
    assert "00:30" in out, out


def test_midnight_never_renders_as_hour_twenty_four():
    # Arrange
    js = 'process.stdout.write(M.fmtLocalTime("2026-09-06T15:30:00Z"));'
    # Act
    out = _run(DATEINFO_JS, js, tz="Asia/Tokyo")
    # Assert
    assert "24:" not in out, out


def test_an_unparseable_stamp_is_returned_as_given_not_invalid_date():
    # A raw value a reader can copy beats a string saying only that the
    # formatter gave up. Also his behaviour, kept.
    # Arrange
    js = 'process.stdout.write(M.fmtLocalTime("not-a-date"));'
    # Act
    out = _run(DATEINFO_JS, js)
    # Assert
    assert out == "not-a-date", out


def test_an_absent_stamp_renders_the_em_dash_the_panel_uses():
    # Arrange
    js = 'process.stdout.write(M.fmtLocalTime(""));'
    # Act
    out = _run(DATEINFO_JS, js)
    # Assert
    assert out == "—", out


# --------------------------------------------------------------------------
# subscriberUnion — the under-reported row
# --------------------------------------------------------------------------


def test_the_creator_and_assignee_are_subscribers_without_being_listed():
    # The operator's 2026-07-06 rule, which the panel did not implement.
    # Arrange
    js = (
        'process.stdout.write(JSON.stringify('
        'M.subscriberUnion([], "alice", "bob")));'
    )
    # Act
    out = _run(CARD_HELPERS_JS, js)
    # Assert
    assert json.loads(out) == ["alice", "bob"]


def test_the_explicit_list_keeps_its_own_order_and_comes_first():
    # Arrange
    js = (
        'process.stdout.write(JSON.stringify('
        'M.subscriberUnion(["zoe", "amy"], "carol", "dave")));'
    )
    # Act
    out = _run(CARD_HELPERS_JS, js)
    # Assert — stored order preserved, implied members appended visibly
    assert json.loads(out) == ["zoe", "amy", "carol", "dave"]


def test_a_creator_who_is_also_listed_appears_once():
    # Arrange
    js = (
        'process.stdout.write(JSON.stringify('
        'M.subscriberUnion(["alice"], "alice", "alice")));'
    )
    # Act
    out = _run(CARD_HELPERS_JS, js)
    # Assert
    assert json.loads(out) == ["alice"]


def test_the_em_dash_placeholder_is_not_a_person():
    # `creator` is "—" on legacy cards that predate created_by. Rendering that
    # as a subscriber would be a fake name in an operator-facing list.
    # Arrange
    js = (
        'process.stdout.write(JSON.stringify('
        'M.subscriberUnion([], "\\u2014", "  ")));'
    )
    # Act
    out = _run(CARD_HELPERS_JS, js)
    # Assert
    assert json.loads(out) == []


# --------------------------------------------------------------------------
# the template actually calls them
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_the_panel_renders_last_activity_through_fmtLocalTime(template_text):
    # A pure function nothing calls is why #330's own halves went missing for
    # seven weeks. Pin the call site, not just the helper.
    # Arrange
    needle = "STX.dateInfo.fmtLocalTime(t.last_activity)"
    # Act
    present = needle in template_text
    # Assert
    assert present


def test_the_panel_renders_created_at_through_fmtLocalTime(template_text):
    # Arrange
    needle = "STX.dateInfo.fmtLocalTime(t.created_at)"
    # Act
    present = needle in template_text
    # Assert
    assert present


def test_the_panel_renders_subscribers_through_the_union(template_text):
    # Arrange
    needle = "STX.cardHelpers.subscriberUnion("
    # Act
    present = needle in template_text
    # Assert
    assert present


def test_the_panel_no_longer_prints_a_raw_last_activity(template_text):
    # The defect itself as an executable fact: the old row was
    # `["last_activity", t.last_activity || "—"]`.
    # Arrange
    old_row = '["last_activity", t.last_activity'
    # Act
    present = old_row in template_text
    # Assert
    assert not present


def test_the_panel_no_longer_prints_a_raw_created_at(template_text):
    # Arrange
    old_row = '["created_at", t.created_at'
    # Act
    present = old_row in template_text
    # Assert
    assert not present
