#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the DM message actions' pure logic (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_actions.js``.

These ``require()`` the shipped module and run the REAL functions under node,
the same arrangement as ``test_chat_diff.py`` — there is deliberately no
hand-ported copy of the logic here.

What is pinned:

  1. ``forwardBody`` — the forwarded body, including the rule that forwarding
     an already-forwarded message does NOT stack a second banner.
  2. ``nextAction`` — the reaction toggle rule, which must agree with
     ``_reactions.next_action`` on the Python side.
  3. ``chipsOf`` — chip ordering and the viewer's own-reaction flag.
  4. ``reactionSignature`` — the change detector that makes a reaction
     arriving on its own actually repaint (see test_chat_diff_reactions.py).
"""

from __future__ import annotations

import json
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
    / "chat_actions.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_actions.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const ChatActions = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _call(expr: str) -> object:
    """Evaluate a ChatActions expression and JSON-decode the result."""
    return json.loads(_run(f"console.log(JSON.stringify({expr}));"))


_MSG = {
    "id": "m_001",
    "from": "agent-x",
    "body": "deploy is green",
    "ts": "2026-07-28T09:00:00Z",
}


# === forward ===============================================================


def test_forward_body_opens_with_the_banner():
    # Arrange
    message = json.dumps(_MSG)
    # Act
    body = _call(f"ChatActions.forwardBody({message})")
    # Assert
    assert body.startswith("[forwarded from agent-x, 2026-07-28T09:00:00Z]")


def test_forward_body_keeps_the_original_text():
    # Arrange
    message = json.dumps(_MSG)
    # Act
    body = _call(f"ChatActions.forwardBody({message})")
    # Assert
    assert body.endswith("deploy is green")


def test_forward_body_puts_the_banner_on_its_own_line():
    # Arrange
    message = json.dumps(_MSG)
    # Act
    body = _call(f"ChatActions.forwardBody({message})")
    # Assert
    assert body.count("\n") == 1


def test_forwarding_a_forward_does_not_stack_a_second_banner():
    # Arrange
    once = dict(_MSG)
    once["body"] = "[forwarded from agent-z, 2026-07-27T08:00:00Z]\noriginal text"
    # Act
    body = _call(f"ChatActions.forwardBody({json.dumps(once)})")
    # Assert
    # Telegram's rule: the ORIGINAL author stays at the top, not the relayer.
    assert body == once["body"]


def test_forward_body_carries_an_attachment_line_through():
    # Arrange
    with_file = dict(_MSG)
    with_file["body"] = "look\nattachments/ab/cd/shot.png"
    # Act
    body = _call(f"ChatActions.forwardBody({json.dumps(with_file)})")
    # Assert
    # attachments are lines in the body, so a forwarded image forwards for free.
    assert "attachments/ab/cd/shot.png" in body


def test_forward_banner_survives_a_missing_timestamp():
    # Arrange
    # Act
    banner = _call('ChatActions.forwardBanner("agent-x", "")')
    # Assert
    assert banner == "[forwarded from agent-x]"


def test_is_forwarded_is_false_for_an_ordinary_body():
    # Arrange
    # Act
    flagged = _call('ChatActions.isForwarded("just a message")')
    # Assert
    assert flagged is False


# === reaction toggle =======================================================


def test_next_action_adds_when_the_viewer_has_not_reacted():
    # Arrange
    # Act
    action = _call('ChatActions.nextAction(["agent-x"], "operator")')
    # Assert
    assert action == "add"


def test_next_action_removes_when_the_viewer_already_reacted():
    # Arrange
    # Act
    action = _call('ChatActions.nextAction(["agent-x", "operator"], "operator")')
    # Assert
    assert action == "remove"


def test_next_action_adds_on_an_empty_list():
    # Arrange
    # Act
    action = _call('ChatActions.nextAction(null, "operator")')
    # Assert
    assert action == "add"


# === chips =================================================================


def test_chips_of_counts_the_actors():
    # Arrange
    reactions = json.dumps({"\U0001f44d": ["operator", "agent-x"]})
    # Act
    chips = _call(f'ChatActions.chipsOf({reactions}, "operator")')
    # Assert
    assert chips[0]["count"] == 2


def test_chips_of_flags_the_viewers_own_reaction():
    # Arrange
    reactions = json.dumps({"\U0001f44d": ["operator"]})
    # Act
    chips = _call(f'ChatActions.chipsOf({reactions}, "operator")')
    # Assert
    assert chips[0]["mine"] is True


def test_chips_of_does_not_flag_someone_elses_reaction():
    # Arrange
    reactions = json.dumps({"\U0001f44d": ["agent-x"]})
    # Act
    chips = _call(f'ChatActions.chipsOf({reactions}, "operator")')
    # Assert
    assert chips[0]["mine"] is False


def test_chips_of_drops_an_emoji_with_no_actors():
    # Arrange
    reactions = json.dumps({"\U0001f44d": []})
    # Act
    chips = _call(f'ChatActions.chipsOf({reactions}, "operator")')
    # Assert
    assert chips == []


def test_chips_of_is_empty_for_a_message_with_no_reactions():
    # Arrange
    # Act
    chips = _call('ChatActions.chipsOf(null, "operator")')
    # Assert
    assert chips == []


# === signature =============================================================


def test_reaction_signature_is_empty_without_reactions():
    # Arrange
    # Act
    signature = _call("ChatActions.reactionSignature(null)")
    # Assert
    assert signature == ""


def test_reaction_signature_changes_when_an_actor_joins():
    # Arrange
    one = json.dumps({"\U0001f44d": ["operator"]})
    two = json.dumps({"\U0001f44d": ["operator", "agent-x"]})
    # Act
    signatures = _call(
        f"[ChatActions.reactionSignature({one}), ChatActions.reactionSignature({two})]"
    )
    # Assert
    assert signatures[0] != signatures[1]


def test_reaction_signature_ignores_actor_order():
    # Arrange
    forward = json.dumps({"\U0001f44d": ["operator", "agent-x"]})
    reverse = json.dumps({"\U0001f44d": ["agent-x", "operator"]})
    # Act
    signatures = _call(
        f"[ChatActions.reactionSignature({forward}), "
        f"ChatActions.reactionSignature({reverse})]"
    )
    # Assert
    # the same people reacted, so the pane must not repaint.
    assert signatures[0] == signatures[1]


# === the quick reaction row ================================================


def test_the_quick_row_offers_maru():
    """〇 — the operator asked for it by name (「〇、×、？ がいい」)."""
    # Arrange
    # Act
    quick = _call("ChatActions.QUICK_REACTION_EMOJI")
    # Assert
    assert "⭕" in quick


def test_the_quick_row_offers_batsu():
    """×."""
    # Arrange
    # Act
    quick = _call("ChatActions.QUICK_REACTION_EMOJI")
    # Assert
    assert "❌" in quick


def test_the_quick_row_offers_hatena():
    """？."""
    # Arrange
    # Act
    quick = _call("ChatActions.QUICK_REACTION_EMOJI")
    # Assert
    assert "❓" in quick


def test_the_quick_row_never_offers_thumbs_down():
    """Operator: 「親指の下向きのやつはあまり好きじゃない、下品」.

    Asserted rather than commented because 👎 is the obvious partner to 👍 —
    exactly the kind of "sensible default" a later edit re-adds without
    knowing it was refused. This makes that edit fail CI instead of arriving
    on their phone.
    """
    # Arrange
    # Act
    quick = _call("ChatActions.QUICK_REACTION_EMOJI")
    # Assert
    assert "\U0001f44e" not in quick


def test_the_full_palette_never_offers_thumbs_down():
    """The chevron's fuller picker is not a loophole for the same glyph."""
    # Arrange
    # Act
    full = _call("ChatActions.REACTION_EMOJI")
    # Assert
    assert "\U0001f44e" not in full


def test_the_quick_row_is_a_subset_of_the_full_palette():
    """Row and picker are two views of ONE palette, not two palettes."""
    # Arrange
    # Act
    palettes = _call("[ChatActions.QUICK_REACTION_EMOJI, ChatActions.REACTION_EMOJI]")
    # Assert
    assert set(palettes[0]) <= set(palettes[1])


def test_the_quick_row_stays_short_enough_for_one_phone_row():
    """Six 44px targets plus the chevron is what fits a 375pt screen."""
    # Arrange
    # Act
    quick = _call("ChatActions.QUICK_REACTION_EMOJI")
    # Assert
    assert len(quick) <= 6


# === selection =============================================================


def test_toggle_selection_adds_an_unselected_id():
    # Arrange
    # Act
    ids = _call('ChatActions.toggleSelection(["m_1"], "m_2")')
    # Assert
    assert ids == ["m_1", "m_2"]


def test_toggle_selection_removes_an_already_selected_id():
    # Arrange
    # Act
    ids = _call('ChatActions.toggleSelection(["m_1", "m_2"], "m_1")')
    # Assert
    assert ids == ["m_2"]


def test_toggle_selection_does_not_mutate_the_input_list():
    """The caller holds the old list; a mutation would edit it behind them."""
    # Arrange
    # Act
    before = _call(
        '(function () { var held = ["m_1"]; '
        'ChatActions.toggleSelection(held, "m_2"); return held; })()',
    )
    # Assert
    assert before == ["m_1"]


def test_toggle_selection_starts_from_nothing():
    # Arrange
    # Act
    ids = _call('ChatActions.toggleSelection(null, "m_1")')
    # Assert
    assert ids == ["m_1"]


def test_selection_label_counts_the_selection():
    # Arrange
    # Act
    label = _call("ChatActions.selectionLabel(3)")
    # Assert
    assert label == "3 selected"


def test_selected_records_returns_thread_order_not_tap_order():
    """Tapping bottom-up must not forward the conversation backwards."""
    # Arrange
    messages = json.dumps(
        [{"id": "m_1"}, {"id": "m_2"}, {"id": "m_3"}],
    )
    # Act
    records = _call(f'ChatActions.selectedRecords({messages}, ["m_3", "m_1"])')
    # Assert
    assert [r["id"] for r in records] == ["m_1", "m_3"]


def test_selected_records_drops_an_id_no_longer_in_the_thread():
    # Arrange
    messages = json.dumps([{"id": "m_1"}])
    # Act
    records = _call(f'ChatActions.selectedRecords({messages}, ["m_1", "m_gone"])')
    # Assert
    assert len(records) == 1


def test_join_texts_separates_messages_with_a_blank_line():
    # Arrange
    # Act
    joined = _call('ChatActions.joinTexts(["one", "two"])')
    # Assert
    assert joined == "one\n\ntwo"


def test_join_texts_drops_an_attachment_only_message():
    """An empty bubble must not leave a hole in the middle of the paste."""
    # Arrange
    # Act
    joined = _call('ChatActions.joinTexts(["one", "   ", "two"])')
    # Assert
    assert joined == "one\n\ntwo"


# EOF
