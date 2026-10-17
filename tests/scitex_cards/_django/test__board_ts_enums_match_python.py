#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board SPA's TS enums must equal the Python ones they mirror.

``frontend/src/searchQuery.ts`` is the typed mirror of the vanilla-JS module
shipped at ``static/scitex_cards/board_v3/searchQuery.js`` — its own header
says the two are "kept in lock-step manually". The lock-step broke in BOTH
directions, and neither break was caught:

    VALID_STATUSES  TS carried `pending`, ABOLISHED in Python on 2026-07-10
                    (the board_v3 JS sibling was fixed; the TS was not).
    VALID_KINDS     TS was missing `status`, a kind cards really carry
                    (the board_v3 JS sibling gained it; the TS did not).

The shipped SPA bundle (``static/scitex_cards/assets/index.js``) is built
from this TS source, so the operator-visible React board offered
`status:pending` — a recognised pill for a value the store REFUSES on write,
hence zero matches — while `kind:status` could not be searched at all.

WHY THIS TEST IS IN PYTHON. Same reason as
``test__board_js_enums_match_python.py``: the node suite is executed by
nothing (card `gui-node-tests-not-run-by-ci-20260815`). A test that does not
run is decoration. This check parses the TS literal in the language CI
already runs and fails in the pytest matrix the moment either list moves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import scitex_cards
from scitex_cards._task import VALID_KINDS, VALID_STATUSES

_SEARCH_QUERY_TS = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "searchQuery.ts"
)

_BOARD_STORE_TS = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "store"
    / "useBoardStore.ts"
)

_NODE_DETAIL_PANEL_TSX = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "NodeDetailPanel.tsx"
)

_EXPORT_BOARD_TS = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "exportBoard.ts"
)

_CARDS_BOARD_TSX = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "CardsBoard.tsx"
)

_CALENDAR_VIEW_TSX = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "CalendarView.tsx"
)

_RECENT_VIEW_TSX = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "RecentView.tsx"
)

_LAYOUT_TS = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "frontend"
    / "src"
    / "layout.ts"
)


def _ts_string_array(source: str, name: str, filename: str) -> list[str]:
    """Return the string members of an `export const <name> = [ ... ]` literal.

    Accepts the trailing `as const` the TS sources use, and ignores `//`
    comments so a commented-out entry does not count as a member.
    """
    match = re.search(
        rf"export\s+const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*(?:as\s+const\s*)?;",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} literal not found in {filename}"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r"""["']([^"']+)["']""", body)


@pytest.fixture(scope="module")
def ts_source() -> str:
    return _SEARCH_QUERY_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def board_store_source() -> str:
    return _BOARD_STORE_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def node_detail_source() -> str:
    return _NODE_DETAIL_PANEL_TSX.read_text(encoding="utf-8")


def _ts_const_array(source: str, name: str, filename: str) -> list[str]:
    """Return the string members of a `const <name> = [ ... ]` literal.

    Same member extraction as `_ts_string_array`, but the `export`
    keyword is optional: display-order lists (`exportBoard.ts`
    STATUS_ORDER, the CardsBoard Progress `order`) are module-private,
    so requiring `export` would refuse to find them. Accepts the
    trailing `as const` the TS sources use, and ignores `//` comments.
    """
    match = re.search(
        rf"(?:export\s+)?const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*(?:as\s+const\s*)?;",
        source,
        re.DOTALL,
    )
    assert match is not None, f"{name} literal not found in {filename}"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r"""["']([^"']+)["']""", body)


@pytest.fixture(scope="module")
def export_board_source() -> str:
    return _EXPORT_BOARD_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cards_board_source() -> str:
    return _CARDS_BOARD_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def calendar_view_source() -> str:
    return _CALENDAR_VIEW_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def recent_view_source() -> str:
    return _RECENT_VIEW_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def layout_source() -> str:
    return _LAYOUT_TS.read_text(encoding="utf-8")


def _status_qualifier_values(source: str) -> list[str]:
    """Return every ``status:<value>`` qualifier literal in a TS comment/prose.

    Doc examples invite copy-paste into the toolbar: a ``status:pending``
    example filters to zero rows (the store refuses ``pending`` on write
    since its 2026-07-10 abolition), so each named value must be live.
    """
    return re.findall(r"status:([A-Za-z_]+)", source)


def test_the_spa_status_enum_equals_the_python_one(ts_source):
    # Arrange
    # Act
    ts_statuses = _ts_string_array(ts_source, "VALID_STATUSES", _SEARCH_QUERY_TS.name)
    # Assert
    assert sorted(ts_statuses) == sorted(VALID_STATUSES), (
        "searchQuery.ts VALID_STATUSES has drifted from scitex_cards._task. "
        "The SPA board would offer a qualifier the store does not accept, or "
        "omit one that cards really carry."
    )


def test_the_spa_kind_enum_equals_the_python_one(ts_source):
    # Arrange
    # Act
    ts_kinds = _ts_string_array(ts_source, "VALID_KINDS", _SEARCH_QUERY_TS.name)
    # Assert
    assert sorted(ts_kinds) == sorted(VALID_KINDS), (
        "searchQuery.ts VALID_KINDS has drifted from scitex_cards._task."
    )


def test_an_abolished_status_is_not_offered_by_the_spa(ts_source):
    # The specific defect this file was written for: `pending` was abolished
    # 2026-07-10 and survived in the SPA enum into the shipped bundle.
    # Pinned by name so a future re-introduction fails loudly rather than
    # only shifting a count.
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES

    # Act
    ts_statuses = set(
        _ts_string_array(ts_source, "VALID_STATUSES", _SEARCH_QUERY_TS.name)
    )
    # Assert
    assert ts_statuses.isdisjoint(ABOLISHED_STATUSES), (
        f"the SPA board offers abolished status(es) "
        f"{sorted(ts_statuses & set(ABOLISHED_STATUSES))} as search qualifiers"
    )


def test_the_spa_editor_status_enum_equals_the_python_one(board_store_source):
    # Arrange
    # Act
    editor_statuses = _ts_string_array(
        board_store_source, "STATUSES", _BOARD_STORE_TS.name
    )
    # Assert
    assert sorted(editor_statuses) == sorted(VALID_STATUSES), (
        "useBoardStore.ts STATUSES has drifted from scitex_cards._task. "
        "The SPA editor dropdown would offer a status the store refuses, or "
        "omit one that cards really carry."
    )


def test_an_abolished_status_is_not_offered_by_the_spa_editor(board_store_source):
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES

    # Act
    editor_statuses = set(
        _ts_string_array(board_store_source, "STATUSES", _BOARD_STORE_TS.name)
    )
    # Assert
    assert editor_statuses.isdisjoint(ABOLISHED_STATUSES), (
        f"the SPA editor offers abolished status(es) "
        f"{sorted(editor_statuses & set(ABOLISHED_STATUSES))} in its dropdown"
    )


def test_the_spa_new_task_default_is_a_live_status_case1(node_detail_source):
    # Arrange
    # Arrange
    # Act
    match = re.search(
        r"useState\(node\?\.status \?\? [\"']([^\"']+)[\"']\)", node_detail_source
    )
    # Assert
    default_status = match.group(1)
    assert match is not None, "new-task default status literal not found"


def test_the_spa_new_task_default_is_a_live_status_case2(node_detail_source):
    # Arrange
    # Arrange
    match = re.search(
        r"useState\(node\?\.status \?\? [\"']([^\"']+)[\"']\)", node_detail_source
    )
    # Act
    default_status = match.group(1)
    # Assert
    assert default_status in VALID_STATUSES, (
        f"NodeDetailPanel defaults new tasks to {default_status!r}, "
        "which the store does not accept"
    )


def test_the_spa_new_task_default_is_a_live_status_case3(node_detail_source):
    # Arrange
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES
    match = re.search(
        r"useState\(node\?\.status \?\? [\"']([^\"']+)[\"']\)", node_detail_source
    )
    # Act
    default_status = match.group(1)
    # Assert
    assert default_status not in ABOLISHED_STATUSES, (
        f"NodeDetailPanel defaults new tasks to abolished {default_status!r}"
    )


def test_the_spa_export_order_covers_the_python_statuses(export_board_source):
    # `exportBoard.ts` STATUS_ORDER is display order, not validation — but
    # as a SET it must equal VALID_STATUSES: a status missing here gets
    # dumped into the unsorted tail of the export, and an extra one there
    # is a qualifier the store refuses.
    # Arrange
    # Act
    ordered = _ts_const_array(
        export_board_source, "STATUS_ORDER", _EXPORT_BOARD_TS.name
    )
    # Assert
    assert sorted(ordered) == sorted(VALID_STATUSES), (
        "exportBoard.ts STATUS_ORDER has drifted from scitex_cards._task. "
        "The Markdown export would mis-group a status the store accepts, or "
        "carry one it refuses."
    )


def test_an_abolished_status_is_not_ordered_by_the_spa_export(
    export_board_source,
):
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES

    # Act
    ordered = set(
        _ts_const_array(
            export_board_source, "STATUS_ORDER", _EXPORT_BOARD_TS.name
        )
    )
    # Assert
    assert ordered.isdisjoint(ABOLISHED_STATUSES), (
        f"the SPA export orders abolished status(es) "
        f"{sorted(ordered & set(ABOLISHED_STATUSES))} as sections"
    )


def test_the_spa_progress_chips_cover_the_python_statuses(cards_board_source):
    # The CardsBoard Progress `order` is the chip row's display order — as a
    # SET it must equal VALID_STATUSES so every live status gets a chip and
    # no chip names a value the store refuses.
    # Arrange
    # Act
    ordered = _ts_const_array(cards_board_source, "order", _CARDS_BOARD_TSX.name)
    # Assert
    assert sorted(ordered) == sorted(VALID_STATUSES), (
        "CardsBoard.tsx Progress order has drifted from scitex_cards._task. "
        "The progress chips would miss a status the store accepts, or offer "
        "one it refuses."
    )


def test_an_abolished_status_has_no_progress_chip(cards_board_source):
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES

    # Act
    ordered = set(
        _ts_const_array(cards_board_source, "order", _CARDS_BOARD_TSX.name)
    )
    # Assert
    assert ordered.isdisjoint(ABOLISHED_STATUSES), (
        f"the SPA progress row chips abolished status(es) "
        f"{sorted(ordered & set(ABOLISHED_STATUSES))}"
    )


def test_the_spa_calendar_doc_example_names_blocked(
    calendar_view_source, recent_view_source
):
    # CalendarView + RecentView doc headers teach the qualifier syntax with a
    # `status:<value>` example. The repoint (pending -> blocked) is
    # comment-only, so without this pin a `status:pending` example returns
    # with every pin green and filters every view to zero rows.
    # Arrange
    # Act
    # Assert
    assert "status:blocked" in calendar_view_source, (
        "CalendarView doc example drifted away from its pinned successor"
    )


def test_the_spa_recent_doc_example_names_blocked(
    calendar_view_source, recent_view_source
):
    # CalendarView + RecentView doc headers teach the qualifier syntax with a
    # `status:<value>` example. The repoint (pending -> blocked) is
    # comment-only, so without this pin a `status:pending` example returns
    # with every pin green and filters every view to zero rows.
    # Arrange
    # Act
    # Assert
    assert "status:blocked" in recent_view_source, (
        "RecentView doc example drifted away from its pinned successor"
    )


def test_the_spa_view_doc_examples_name_live_statuses(
    calendar_view_source, recent_view_source
):
    # CalendarView + RecentView doc headers teach the qualifier syntax with a
    # `status:<value>` example. The repoint (pending -> blocked) is
    # comment-only, so without this pin a `status:pending` example returns
    # with every pin green and filters every view to zero rows.
    # Arrange
    # Act
    calendar_values = _status_qualifier_values(calendar_view_source)
    recent_values = _status_qualifier_values(recent_view_source)
    bad = [
        value
        for value in (*calendar_values, *recent_values)
        if value not in VALID_STATUSES
    ]
    # Assert
    assert not bad, (
        f"SPA view doc examples name {bad}, which the store refuses"
    )


def test_the_spa_view_doc_examples_name_no_abolished_status(
    calendar_view_source, recent_view_source
):
    # CalendarView + RecentView doc headers teach the qualifier syntax with a
    # `status:<value>` example. The repoint (pending -> blocked) is
    # comment-only, so without this pin a `status:pending` example returns
    # with every pin green and filters every view to zero rows.
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES

    # Act
    calendar_values = _status_qualifier_values(calendar_view_source)
    recent_values = _status_qualifier_values(recent_view_source)
    bad = [
        value
        for value in (*calendar_values, *recent_values)
        if value in ABOLISHED_STATUSES
    ]
    # Assert
    assert not bad, f"SPA view doc examples name abolished {bad}"


def test_the_spa_layout_lifecycle_signal_names_live_statuses_case1(layout_source):
    # `layout.ts` documents which lifecycle signal the parent depth cue
    # preserves. The repoint (pending -> deferred) is comment-only, so
    # without this pin `pending` returns here with every pin green.
    # Arrange
    # Arrange
    # Act
    match = re.search(r"lifecycle signal \((.*?)\)", layout_source)
    # Assert
    named = {
        token.strip().strip("…").strip()
        for token in match.group(1).split("/")
    } - {"", "…"}
    assert match is not None, "layout.ts lifecycle-signal prose not found"


def test_the_spa_layout_lifecycle_signal_names_live_statuses_case2(layout_source):
    # `layout.ts` documents which lifecycle signal the parent depth cue
    # preserves. The repoint (pending -> deferred) is comment-only, so
    # without this pin `pending` returns here with every pin green.
    # Arrange
    # Arrange
    # Act
    # Assert
    match = re.search(r"lifecycle signal \((.*?)\)", layout_source)
    named = {
        token.strip().strip("…").strip()
        for token in match.group(1).split("/")
    } - {"", "…"}
    assert "deferred / done / blocked / goal" in layout_source, (
        "layout.ts lifecycle signal drifted away from its pinned repoint"
    )


def test_the_spa_layout_lifecycle_signal_names_live_statuses_case3(layout_source):
    # `layout.ts` documents which lifecycle signal the parent depth cue
    # preserves. The repoint (pending -> deferred) is comment-only, so
    # without this pin `pending` returns here with every pin green.
    # Arrange
    # Arrange
    match = re.search(r"lifecycle signal \((.*?)\)", layout_source)
    # Act
    named = {
        token.strip().strip("…").strip()
        for token in match.group(1).split("/")
    } - {"", "…"}
    # Assert
    assert named.issubset(set(VALID_STATUSES)), (
        f"layout.ts lifecycle signal names {sorted(named - set(VALID_STATUSES))}, "
        "which the store does not accept"
    )


def test_the_spa_layout_lifecycle_signal_names_live_statuses_case4(layout_source):
    # `layout.ts` documents which lifecycle signal the parent depth cue
    # preserves. The repoint (pending -> deferred) is comment-only, so
    # without this pin `pending` returns here with every pin green.
    # Arrange
    # Arrange
    from scitex_cards._task import ABOLISHED_STATUSES
    match = re.search(r"lifecycle signal \((.*?)\)", layout_source)
    # Act
    named = {
        token.strip().strip("…").strip()
        for token in match.group(1).split("/")
    } - {"", "…"}
    # Assert
    assert named.isdisjoint(ABOLISHED_STATUSES), (
        f"layout.ts lifecycle signal names abolished "
        f"{sorted(named & set(ABOLISHED_STATUSES))}"
    )
