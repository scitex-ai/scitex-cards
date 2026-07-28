#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The rebuilt message menu: a quick reaction ROW above the action LIST.

The operator sent Telegram's menu as the target and then corrected it three
times. Those corrections are the reason this file exists — every one of them is
a decision that a later "obvious cleanup" would undo, so each is pinned:

  * 「絵文字リアクションがすぐ打てると嬉しい」 — one-tap reactions, no submenu.
    So the ROW is served, ABOVE the list, and it is not the old React… item.
  * 「〇、×、？ がいい」 — maru / batsu / hatena.
  * 「親指の下向きのやつはあまり好きじゃない、下品」 — NO 👎, anywhere.
  * 「translate はいらない」 — no Translate item.
  * 「select はいる」 — Select must exist, and must be able to DO something.

Asserted against the RENDERED RESPONSE wherever the question is "does the
operator actually get this?", for the reason the sibling wiring test gives: a
feature can be entirely correct and still never reach the phone.

The two ABSENCES (Pin, Delete) are asserted too. Both were on the reference
screenshot and both were left out on purpose — see the template comment. An
absence that is not tested is indistinguishable from an omission, and the next
reader would "fix" it by adding a control with nothing behind it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._reactions import QUICK_REACTION_EMOJI, REACTION_EMOJI  # noqa: E402

_CHAT_JS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
)

#: U+1F44E THUMBS DOWN — refused by name, so it is named here once.
_THUMBS_DOWN = "\U0001f44e"


@pytest.fixture()
def page() -> str:
    """The rendered /chat page — what the browser is actually served."""
    return views.chat_page(RequestFactory().get("/chat")).content.decode("utf-8")


@pytest.fixture()
def select_js() -> str:
    return (_CHAT_JS_DIR / "chat_select.js").read_text(encoding="utf-8")


@pytest.fixture()
def menu_js() -> str:
    return (_CHAT_JS_DIR / "chat_menu.js").read_text(encoding="utf-8")


@pytest.fixture()
def actions_js() -> str:
    return (_CHAT_JS_DIR / "chat_actions.js").read_text(encoding="utf-8")


# === the layout ============================================================


def test_the_quick_reaction_row_is_served(page: str):
    # Arrange
    # Act
    present = 'id="react-quick"' in page
    # Assert
    assert present


def test_the_quick_row_sits_above_the_action_list(page: str):
    """The sketch's whole shape: reactions on top, actions beneath."""
    # Arrange
    # Act
    order = [page.index('id="react-quick"'), page.index('id="msg-menu"')]
    # Assert
    assert order == sorted(order)


def test_the_row_and_the_list_open_as_one_popover(page: str):
    """Two separately positioned panels could drift apart on screen."""
    # Arrange
    # Act
    present = 'id="msg-menu-wrap"' in page
    # Assert
    assert present


def test_the_stacked_panels_are_taken_out_of_fixed_positioning(page: str):
    """Base puts `position: fixed` on `.stx-app-context-menu` ITSELF.

    Both panels inside the wrap carry that class, so without this reset they
    leave the flex column and render on top of each other — the reaction row
    would cover the action list instead of sitting above it. This is the same
    failure family as the palette bug: a base component quietly deciding a
    layout the page thought it owned, with the damage visible in the COMPONENT
    rather than at the definition site. Hence a test and not a comment.
    """
    # Arrange
    reset = "#msg-menu-wrap > .stx-app-context-menu { position: static; }"
    # Act
    present = reset in page
    # Assert
    assert present


def test_the_row_ends_with_a_chevron_to_the_fuller_picker(page: str):
    """The reference's escape hatch — and the old React… item's new home."""
    # Arrange
    # Act
    present = 'id="mm-react"' in page
    # Assert
    assert present


def test_the_chevron_lives_inside_the_quick_row(page: str):
    # Arrange
    row = page[page.index('id="react-quick"') : page.index('id="msg-menu"')]
    # Act
    present = 'id="mm-react"' in row
    # Assert
    assert present


def test_the_chevron_is_not_an_icon_font(page: str):
    """A glyph that fails to resolve leaves an empty box where the control was."""
    # Arrange
    # Act
    present = "&#9662;" in page
    # Assert
    assert present


# === what the list offers ==================================================


@pytest.mark.parametrize("item", ["mm-reply", "mm-copy", "mm-forward", "mm-select"])
def test_the_action_list_offers(page: str, item: str):
    # Arrange
    # Act
    present = f'id="{item}"' in page
    # Assert
    assert present


def test_the_list_does_not_offer_translate(page: str):
    """Operator: 「translate はいらない」."""
    # Arrange
    # Act
    present = "Translate" in page
    # Assert
    assert not present


def test_the_list_does_not_offer_pin(page: str):
    """Nothing pins a DM in this codebase — no store, no endpoint, no render.

    It was on the reference screenshot and is deliberately absent: the item
    could only ever look like it worked.
    """
    # Arrange
    # Act
    present = 'id="mm-pin"' in page
    # Assert
    assert not present


def test_the_list_does_not_offer_delete(page: str):
    """The DM store is APPEND-ONLY by operator ruling 「一度書いたものは消えない」.

    `_threads` has no delete and `_reactions._save_events_unlocked` refuses a
    write that shrinks the log. A red Delete that leaves the message on screen
    after the next 5s poll is worse than no Delete.
    """
    # Arrange
    # Act
    present = 'id="mm-delete"' in page
    # Assert
    assert not present


# === selection has somewhere to go =========================================


def test_the_selection_bar_is_served(page: str):
    """Select without a destination would be a mode with no purpose."""
    # Arrange
    # Act
    present = 'id="select-bar"' in page
    # Assert
    assert present


@pytest.mark.parametrize("control", ["sb-count", "sb-copy", "sb-forward", "sb-cancel"])
def test_the_selection_bar_offers(page: str, control: str):
    # Arrange
    # Act
    present = f'id="{control}"' in page
    # Assert
    assert present


def test_the_selection_module_is_linked(page: str):
    # Arrange
    # Act
    present = "chat_select.js" in page
    # Assert
    assert present


def test_the_selection_module_loads_before_the_menu_that_mounts_it(page: str):
    # Arrange
    order = ["chat_actions.js", "chat_select.js", "chat_menu.js"]
    # Act
    positions = [page.index(name) for name in order]
    # Assert
    # `defer` executes in document order, so this list IS the load order.
    assert positions == sorted(positions)


def test_the_selection_module_reuses_the_one_forward_post(select_js: str):
    """A second POST to the same endpoint is a second place to drift."""
    # Arrange
    # Act
    own_fetch = re.findall(r"\bfetch\(", select_js)
    # Assert
    assert own_fetch == []


def test_the_selection_module_forwards_through_the_injected_seam(select_js: str):
    # Arrange
    # `host` and `.sendForwardBody` land on separate lines once the formatter
    # has broken the promise chain, so this cannot be a substring match.
    seam = re.compile(r"host\s*\.\s*sendForwardBody\s*\(")
    # Act
    present = bool(seam.search(select_js))
    # Assert
    assert present


def test_the_selection_module_fetches_no_external_asset(select_js: str):
    # Arrange
    # Act
    external = re.findall(r"https?://", select_js)
    # Assert
    # served over a tunnel to a phone: anything external simply does not load.
    assert external == []


def test_the_bulk_forward_is_sequential_not_parallel(select_js: str):
    """Parallel POSTs land the conversation out of order and convoy the flock."""
    # Arrange
    # Act
    parallel = "Promise.all" in select_js
    # Assert
    assert not parallel


def test_a_repaint_does_not_silently_drop_the_selection(menu_js: str):
    """The pane rebuilds every ~5s; the highlight has to be re-applied.

    Without this the selection would LOOK cleared while still being held, and
    the operator's next tap would remove a message they thought they were
    adding.
    """
    # Arrange
    # Act
    present = "select.decorate(" in menu_js
    # Assert
    assert present


# === one palette, two languages ============================================


def test_the_template_hardcodes_none_of_the_quick_emoji(page: str):
    """The row is built from the JS palette; a copy here could drift."""
    # Arrange
    # Act
    leaked = [emoji for emoji in QUICK_REACTION_EMOJI if emoji in page]
    # Assert
    assert leaked == []


def test_the_js_and_python_quick_rows_are_the_same_set(actions_js: str):
    # Arrange
    match = re.search(r"\bQUICK_REACTION_EMOJI = \[(.*?)\];", actions_js, re.S)
    # Act
    js_emoji = re.findall(r'"([^"]+)"', match.group(1))
    # Assert
    assert js_emoji == list(QUICK_REACTION_EMOJI)


def test_the_python_quick_row_is_a_subset_of_the_python_palette():
    """Row and picker are two views of ONE palette on this side too."""
    # Arrange
    # Act
    contained = set(QUICK_REACTION_EMOJI) <= set(REACTION_EMOJI)
    # Assert
    assert contained


def test_the_python_quick_row_never_offers_thumbs_down():
    """Operator: 「親指の下向きのやつはあまり好きじゃない、下品」."""
    # Arrange
    # Act
    present = _THUMBS_DOWN in QUICK_REACTION_EMOJI
    # Assert
    assert not present


def test_the_python_palette_never_offers_thumbs_down():
    """The fuller picker is not a loophole for the same glyph."""
    # Arrange
    # Act
    present = _THUMBS_DOWN in REACTION_EMOJI
    # Assert
    assert not present


def test_the_rendered_page_never_shows_thumbs_down(page: str):
    """The end-to-end statement: it does not reach their phone by ANY route."""
    # Arrange
    # Act
    present = _THUMBS_DOWN in page
    # Assert
    assert not present


# EOF
