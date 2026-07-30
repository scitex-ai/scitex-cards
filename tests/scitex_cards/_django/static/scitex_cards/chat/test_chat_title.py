#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The unread count in the BROWSER TAB TITLE (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_title.js``.

Operator, 2026-07-29 (TG): 「新着がある場合、ページタイトルに新着メッセージ数
（未読メッセージ数）を出してください。多少点滅などエフェクトがあっても良いかも
です。」 They are migrating off Telegram onto this page, and a background tab that
looks identical whether or not an agent has written is the thing Telegram did
for them that this page did not.

These tests ``require()`` the shipped module and run the REAL functions under
node — the same contract ``test_chat_diff.py`` follows, and for the same reason:
a hand-ported copy of the logic would pass while the file the browser loads was
broken. The DECISIONS in chat_title.js are pure precisely so this is possible;
``mount`` is the only part that needs a document, and its wiring is asserted
against the source in ``tests/.../test__dm_unread_in_page_title.py``.

What is pinned:

  1. ``totalUnread(agents)`` — the count comes from the SAME per-peer
     ``unread`` field the drawer badges render. One fact, two renderings.
  2. ``titleFor(base, count)`` — "(3) DM — …" when unread, the bare title at
     zero (no "(0)", which would make "nothing new" look like a state).
  3. ``flashPlan(prev, next, reducedMotion)`` — the two rules that keep the
     blink from being hostile: it fires only on an INCREASE, and it is
     SILENT under prefers-reduced-motion.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo-relative path to the JS module under test. Resolved off this file's
# location so the test runs from any cwd.
JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_title.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_title.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const ChatTitle = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _total_unread(agents: list[dict]) -> int:
    """The count the tab would show for this /dm/threads payload."""
    out = _run(
        f"const agents = {json.dumps(agents)};\n"
        "console.log(JSON.stringify(ChatTitle.totalUnread(agents)));"
    )
    return json.loads(out)


def _title_for(base: str, count: int) -> str:
    """The document.title for `count` unread on top of `base`."""
    out = _run(
        f"const base = {json.dumps(base)};\n"
        f"const count = {json.dumps(count)};\n"
        "console.log(JSON.stringify(ChatTitle.titleFor(base, count)));"
    )
    return json.loads(out)


def _flash_plan(previous: int, nxt: int, reduced_motion: bool) -> dict:
    """The alternation plan for a count change."""
    out = _run(
        f"const plan = ChatTitle.flashPlan({previous}, {nxt}, "
        f"{json.dumps(reduced_motion)});\n"
        "console.log(JSON.stringify(plan));"
    )
    return json.loads(out)


def _constant(name: str) -> object:
    """Read an exported module constant."""
    out = _run(f"console.log(JSON.stringify(ChatTitle[{json.dumps(name)}]));")
    return json.loads(out)


# The operator's own screenshot: four peers carrying 17 / 9 / 3 / 2 unread.
_PEERS = [
    {"name": "scitex-dev", "unread": 17},
    {"name": "scitex-cards", "unread": 9},
    {"name": "scitex-ui", "unread": 3},
    {"name": "worker-telegrammer", "unread": 2},
]

_BASE = "DM — SciTeX Cards v0.17.10"


# === the count comes from the existing per-peer unread field ===============


def test_total_unread_sums_the_per_peer_badges() -> None:
    """THE source-of-truth assertion. The tab count is the sum of the numbers
    already beside each peer — not a second count of anything."""
    # Arrange
    peers = _PEERS
    # Act
    total = _total_unread(peers)
    # Assert
    assert total == 31


def test_total_unread_of_an_empty_peer_list_is_zero() -> None:
    """First paint, before the first /dm/threads response lands."""
    # Arrange
    peers: list[dict] = []
    # Act
    total = _total_unread(peers)
    # Assert
    assert total == 0


def test_total_unread_ignores_a_peer_with_no_unread_field() -> None:
    """A registry agent who has never written has no thread summary merged in,
    so the row carries no count. It must contribute 0, not NaN — a title
    reading "(NaN)" is a badge lying about being broken."""
    # Arrange
    peers = [{"name": "quiet-agent"}, {"name": "loud-agent", "unread": 4}]
    # Act
    total = _total_unread(peers)
    # Assert
    assert total == 4


def test_total_unread_ignores_a_negative_count() -> None:
    """Server data. A negative would silently cancel a real unread out of the
    tab, which is the one failure mode nobody would ever notice."""
    # Arrange
    peers = [{"name": "a", "unread": -5}, {"name": "b", "unread": 2}]
    # Act
    total = _total_unread(peers)
    # Assert
    assert total == 2


# === the title itself ======================================================


def test_title_shows_the_count_when_unread_is_positive() -> None:
    """The whole request: 「ページタイトルに新着メッセージ数を出してください」."""
    # Arrange
    base = _BASE
    # Act
    title = _title_for(base, 3)
    # Assert
    assert title == "(3) DM — SciTeX Cards v0.17.10"


def test_title_has_no_count_at_zero_unread() -> None:
    """At zero the tab is the plain title — no "(0)", which would make
    "nothing new" look like a state that wants attention."""
    # Arrange
    base = _BASE
    # Act
    title = _title_for(base, 0)
    # Assert
    assert title == _BASE


def test_title_puts_the_count_in_front_of_the_base() -> None:
    """A tab strip truncates from the RIGHT, so a suffix is the first thing to
    disappear on the narrow tab this page will actually be."""
    # Arrange
    base = _BASE
    # Act
    title = _title_for(base, 7)
    # Assert
    assert title.startswith("(7) ")


def test_title_caps_a_very_large_count() -> None:
    """Beyond ~99 the exact number stops informing and starts eating the tab."""
    # Arrange
    base = _BASE
    # Act
    title = _title_for(base, 1234)
    # Assert
    assert title.startswith("(99+) ")


def test_title_keeps_the_version_string_from_the_template() -> None:
    """The module never spells the version or the word DM — it prefixes
    whatever chat.html rendered, so those live in exactly one place."""
    # Arrange
    base = _BASE
    # Act
    title = _title_for(base, 2)
    # Assert
    assert title.endswith(_BASE)


# === the flash: bounded, and silent under reduced motion ===================


def test_flash_is_silent_under_prefers_reduced_motion() -> None:
    """ACCESSIBILITY, not a nicety. `prefers-reduced-motion: reduce` means NO
    alternation — vestibular triggers do not care that the motion is "only" in
    the tab strip."""
    # Arrange
    reduced_motion = True
    # Act
    plan = _flash_plan(0, 3, reduced_motion)
    # Assert
    assert plan["alternations"] == 0


def test_count_still_appears_under_reduced_motion() -> None:
    """Suppressing the MOTION must not suppress the INFORMATION: the count is
    what was asked for, the blink is the garnish."""
    # Arrange
    base = _BASE
    # Act
    title = _title_for(base, 3)
    # Assert
    assert title == "(3) DM — SciTeX Cards v0.17.10"


def test_flash_runs_when_the_unread_count_rises() -> None:
    """A new message arriving is the one event worth announcing."""
    # Arrange
    reduced_motion = False
    # Act
    plan = _flash_plan(0, 1, reduced_motion)
    # Assert
    assert plan["alternations"] > 0


def test_flash_does_not_repeat_for_a_standing_unread() -> None:
    """THE forever-blink guard. /dm/threads is polled every 10s and keeps
    returning the same unread until the operator opens the thread; re-flashing
    each poll is a tab that never stops asking."""
    # Arrange
    reduced_motion = False
    # Act
    plan = _flash_plan(3, 3, reduced_motion)
    # Assert
    assert plan["alternations"] == 0


def test_flash_does_not_run_when_the_count_falls() -> None:
    """Reading a thread clears its unread. Nothing arrived — do not announce."""
    # Arrange
    reduced_motion = False
    # Act
    plan = _flash_plan(9, 0, reduced_motion)
    # Assert
    assert plan["alternations"] == 0


def test_flash_alternation_count_is_finite() -> None:
    """ "多少点滅" — SOME blinking. A title that flashes forever is hostile and
    unreadable in the tab strip, so the alternation is a fixed number of steps
    that then SETTLES on the count."""
    # Arrange
    # Act
    alternations = _constant("FLASH_ALTERNATIONS")
    # Assert
    assert 0 < alternations <= 8


def test_flash_interval_is_slow_enough_to_read() -> None:
    """Under ~500ms the tab strobes rather than notifies — which is precisely
    what the reduced-motion preference exists to suppress."""
    # Arrange
    # Act
    interval = _constant("FLASH_INTERVAL_MS")
    # Assert
    assert interval >= 500


def test_flash_is_bounded_in_wall_clock_time() -> None:
    """The two constants together: the announcement is over in a few seconds,
    not "until you look"."""
    # Arrange
    steps = _constant("FLASH_ALTERNATIONS")
    # Act
    duration_ms = steps * _constant("FLASH_INTERVAL_MS")
    # Assert
    assert duration_ms <= 6000


# EOF
