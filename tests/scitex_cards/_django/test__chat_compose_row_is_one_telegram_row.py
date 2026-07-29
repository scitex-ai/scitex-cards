#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The composer must be ONE tight row, the way Telegram's is.

OPERATOR, 2026-07-28, verbatim: 「このスペースの使い方が下手」 (this space is used
badly) and, separately, 「telegram を真似てください」 (copy Telegram).

WHAT THEY WERE LOOKING AT, measured from their own element-inspector dump of
``form#compose``: ``display: flex; gap: 8px; justify-content: CENTER;
padding: 10px 12px``, ``height: 95.99px``, ``width: 1496px``. The textarea was
capped at 760px, so on that window the centring parked the field in the middle
and left the attach and emoji buttons floating in the gutter beside it with
Send far off to the right — three controls arranged around a hole, inside a
96px band that stayed 96px tall whether or not anything was typed.

TELEGRAM'S SHAPE, which is what these tests pin: attach at the far left, the
field taking every remaining pixel, emoji at the RIGHT EDGE OF THE FIELD, send
at the far right; nothing centred WITHIN the row; the row one control tall at
rest. None of that is checkable by eye in CI — this repo has no browser — so it
is pinned as structure and as declarations on the SERVED page, which is what
the operator's phone actually receives. A test that silently could not observe
the property would be worse than none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402

_CHAT_DIR = Path(views.__file__).parent / "static" / "scitex_cards" / "chat"


def _strip_css_comments(text: str) -> str:
    """Drop ``/* … */`` blocks.

    The compose block deliberately QUOTES the layout it replaced, including the
    old ``justify-content`` line. Prose describing a defect must not read as
    the defect — that is the vacuous-guard shape, a check that reads what we
    wrote ABOUT the code instead of the code.
    """
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


@pytest.fixture
def page() -> str:
    """The chat page exactly as a browser at the root mount receives it."""
    return views.chat_page(RequestFactory().get("/chat")).content.decode("utf-8")


@pytest.fixture
def css(page: str) -> str:
    """The page's own declarations, comments removed."""
    return _strip_css_comments(page)


def _rule(css_text: str, selector: str) -> str:
    """The declaration block for one selector, or "" when it is absent.

    Anchored on ``selector`` followed by optional space and ``{``, so
    ``#compose`` does not match ``#compose-row`` / ``#compose-field`` /
    ``#compose-longtext`` — the four are separate rules and a test that
    conflated them would pass on the wrong one.
    """
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css_text)
    return found.group(1) if found else ""


def test_nothing_in_the_composer_is_centred_on_its_own_axis(css: str):
    """THE defect, named at its cause.

    ``justify-content: center`` on the composer is what stranded the buttons:
    the row's contents were centred as a group, so the free space went to the
    OUTSIDE of them instead of into the field. The row is centred as a WHOLE
    now (``margin-inline: auto`` against ``--column-max``), which is a
    different thing and leaves no gutter inside it.
    """
    # Arrange
    blocks = {name: _rule(css, name) for name in ("#compose", "#compose-row")}

    # Act
    offenders = [name for name, body in blocks.items() if "justify-content" in body]

    # Assert
    assert offenders == []


def test_the_field_stretches_across_the_row(css: str):
    """A field that does not grow is what leaves space for a hole beside it."""
    # Arrange
    block = _rule(css, "#compose-field")

    # Act
    grows = "flex: 1 1 auto" in block

    # Assert
    assert grows


def test_the_field_can_shrink_below_its_content(css: str):
    """``min-width: 0`` is what keeps a narrow phone honest.

    A flex item's automatic minimum size is its content, so without this the
    field refuses to shrink and pushes Send off the right of the screen — the
    row would look correct at desktop width and break on the one device this
    is for.
    """
    # Arrange
    block = _rule(css, "#compose-field")

    # Act
    shrinks = "min-width: 0" in block

    # Assert
    assert shrinks


def test_attach_is_the_first_control_in_the_row(page: str):
    """Telegram's order, and the order a screen reader meets them in."""
    # Arrange
    attach = page.index('id="compose-attach"')

    # Act
    field = page.index('id="compose-body"')

    # Assert
    assert attach < field


def test_send_is_the_last_control_in_the_row(page: str):
    # Arrange
    send = page.index('id="compose-send"')

    # Act
    field = page.index('id="compose-body"')

    # Assert
    assert send > field


def test_the_emoji_toggle_sits_inside_the_field(page: str):
    """Not a fourth control beside the field — that is the orphan button the
    operator objected to. Inside the field is where Telegram puts it."""
    # Arrange
    opened = page.index('<div id="compose-field">')

    # Act
    field_markup = page[opened : page.index("</div>", opened)]

    # Assert
    assert 'data-stx-emoji-picker-for="compose-body"' in field_markup


def test_the_emoji_panel_opens_back_towards_the_screen(page: str):
    """A toggle at the right edge with a LEFT-anchored panel opens a ~20rem
    popover rightwards from a point already near the screen edge, so most of it
    lands outside a phone viewport. The component's own modifier flips it."""
    # Arrange
    marker = "stx-emoji-picker--right"

    # Act
    present = marker in page

    # Assert
    assert present


def test_the_composer_and_the_message_list_share_one_column(css: str):
    """Two literals would be two numbers free to drift, and a composer that
    does not line up with the messages above it IS the "space used badly"."""
    # Arrange
    token = "var(--column-max)"

    # Act
    sharers = [
        name for name in ("#messages", "#compose-row") if token in _rule(css, name)
    ]

    # Assert
    assert sharers == ["#messages", "#compose-row"]


def test_every_control_in_the_row_is_the_same_height(css: str):
    """One token, read by the buttons and by the field, so the row reads as one
    object. Independent heights is how the old row looked assembled."""
    # Arrange
    token = "min-height: var(--compose-control)"
    sized = ("#compose-row > button", "#compose-field textarea")

    # Act
    readers = [name for name in sized if token in _rule(css, name)]

    # Assert
    assert readers == list(sized)


def test_that_shared_height_is_a_real_tap_target(css: str):
    """44px is this page's standing floor for a phone (the reaction bar states
    the same number). The operator reads the board on a phone."""
    # Arrange
    declaration = re.search(r"--compose-control:\s*(\d+)px", css)

    # Act
    pixels = int(declaration.group(1)) if declaration else 0

    # Assert
    assert pixels >= 44


def test_the_field_reserves_no_fixed_band(css: str):
    """The old textarea was ``min-height: 64px`` inside 10px padding — a 96px
    band on every screen, typed in or not. It grows from one line now."""
    # Arrange
    block = _rule(css, "#compose-field textarea")

    # Act
    fixed = re.findall(r"min-height:\s*\d+px", block)

    # Assert
    assert fixed == []


def test_the_composer_starts_at_one_line(page: str):
    """``rows`` is the height before any CSS or script runs, so a page that
    loads slowly must not flash the old three-line box."""
    # Arrange
    field = re.search(r'<textarea id="compose-body"[^>]*>', page)

    # Act
    rows = re.search(r'rows="(\d+)"', field.group(0) if field else "")

    # Assert
    assert rows is not None and rows.group(1) == "1"


def test_the_page_loads_the_composer_module(page: str):
    """A textarea cannot size itself to its content in CSS, so a missing
    script tag leaves a composer that never grows past one line — with no
    error anywhere to say why."""
    # Arrange
    needle = "scitex_cards/chat/chat_compose.js"

    # Act
    present = needle in page

    # Assert
    assert present


def test_the_composer_never_grows_to_own_the_viewport():
    """Auto-grow with no ceiling reproduces the operator's complaint from the
    bottom of the screen instead of the top."""
    # Arrange
    source = _strip_css_comments(
        (_CHAT_DIR / "chat_compose.js").read_text(encoding="utf-8")
    )

    # Act
    capped = "MAX_VIEWPORT_SHARE" in source and "innerHeight" in source

    # Assert
    assert capped


def test_the_long_draft_offer_reuses_the_existing_upload_path():
    """ONE mechanism. The composer's ".txt instead" button must go through
    ChatAttach's upload, not a second POST of its own — a duplicate would
    drift from the storage layout the thread's renderer already reads."""
    # Arrange
    source = _strip_css_comments(
        (_CHAT_DIR / "chat_compose.js").read_text(encoding="utf-8")
    )

    # Act
    own_endpoints = re.findall(r"/dm/\w+", source)

    # Assert
    assert own_endpoints == []


def test_the_offer_uses_the_renderers_own_threshold():
    """The composer must not restate the number. If the two ever disagreed,
    the composer would offer a file for a body the thread shows in full, or
    stay silent for one it clamps — and both read as a broken feature."""
    # Arrange
    source = _strip_css_comments(
        (_CHAT_DIR / "chat_compose.js").read_text(encoding="utf-8")
    )

    # Act
    borrows = "ChatLongText" in source and "isLong" in source

    # Assert
    assert borrows


def test_the_attachment_module_exposes_the_single_file_seam():
    """``uploadOne`` is what makes the reuse above possible; without it the
    composer's only options are a duplicate fetch or nothing."""
    # Arrange
    source = (_CHAT_DIR / "chat_attach.js").read_text(encoding="utf-8")

    # Act
    exported = "uploadOne: uploadOne" in source

    # Assert
    assert exported


def test_chat_js_hands_the_composer_that_seam():
    """The two modules do not know about each other; the orchestrator wires
    them. A missing wire silently disables the offer."""
    # Arrange
    source = (_CHAT_DIR / "chat.js").read_text(encoding="utf-8")

    # Act
    wired = "attach.uploadOne" in source

    # Assert
    assert wired


# EOF
