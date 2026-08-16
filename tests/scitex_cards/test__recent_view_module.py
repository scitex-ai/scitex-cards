#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pins on the Recent-view's pure-helpers module shipped under
``static/scitex_cards/board_v3/recentSort.js`` + its TS mirror
``frontend/src/recentSort.ts`` + the React component
``frontend/src/RecentView.tsx``.

The JS module's own behaviour is covered by
``tests/scitex_cards/test__recent_view.js`` (``node --test``); this Python
test class is a static-content pin so a refactor that drops the public
API surface or the ViewToggle wiring trips CI on the Python side too.

Operator TG msg 513 (2026-06-12): "Make a Recent / 最近のToDo UI."
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scitex_cards

PKG_ROOT = Path(scitex_cards.__file__).parent

RECENT_SORT_JS = (
    PKG_ROOT / "_django" / "static" / "scitex_cards" / "board_v3" / "recentSort.js"
)
RECENT_SORT_TS = PKG_ROOT / "_django" / "frontend" / "src" / "recentSort.ts"
RECENT_VIEW_TSX = PKG_ROOT / "_django" / "frontend" / "src" / "RecentView.tsx"
TODO_BOARD_TSX = PKG_ROOT / "_django" / "frontend" / "src" / "TodoBoard.tsx"
STORE_TS = PKG_ROOT / "_django" / "frontend" / "src" / "store" / "useBoardStore.ts"


@pytest.fixture(scope="module")
def js_text() -> str:
    return RECENT_SORT_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ts_text() -> str:
    return RECENT_SORT_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tsx_text() -> str:
    return RECENT_VIEW_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def board_text() -> str:
    return TODO_BOARD_TSX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def store_text() -> str:
    return STORE_TS.read_text(encoding="utf-8")


class TestRecentSortModuleSurface:
    """Static pins on the JS pure-helper module's public API."""

    def test_js_module_exists(self):
        # Arrange
        # Act
        # Assert
        assert RECENT_SORT_JS.exists()

    def test_ts_mirror_exists(self):
        # Arrange
        # Act
        # Assert
        assert RECENT_SORT_TS.exists()

    def test_recent_view_component_exists(self):
        # Arrange
        # Act
        # Assert
        assert RECENT_VIEW_TSX.exists()

    @pytest.mark.parametrize(
        "fn_name",
        [
            "parseIso",
            "earliestCommentTs",
            "taskTimestamp",
            "classifyRecency",
            "sortByRecency",
            "countNewIn24h",
            "relativeTimestamp",
            "filterDefaultLookback",
        ],
    )
    def test_pure_helper_exported(self, js_text, fn_name):
        # Each helper appears in the module.exports literal at the bottom.
        # Arrange
        # Act
        # Assert
        assert fn_name in js_text

    @pytest.mark.parametrize(
        "fn_name",
        [
            "parseIso",
            "earliestCommentTs",
            "taskTimestamp",
            "classifyRecency",
            "sortByRecency",
            "countNewIn24h",
            "relativeTimestamp",
            "filterDefaultLookback",
        ],
    )
    def test_ts_mirror_exports_same_helper(self, ts_text, fn_name):
        # Each helper is re-exported by the TS mirror so the React side
        # can import from recentSort.ts with the same API.
        # Arrange
        # Act
        # Assert
        assert f"export function {fn_name}" in ts_text


class TestRecencyCutoffsLockedDown:
    """The NEW (<24h) + recent (24-72h) cutoffs are operator-visible
    UX — pin them so a refactor doesn't accidentally drift the
    thresholds (which would change WHICH rows wear the orange badge)."""

    def test_new_cutoff_is_24h(self, js_text):
        # 24 * HOUR_MS — pin the constant name + the magic number.
        # Arrange
        # Act
        # Assert
        assert "NEW_CUTOFF_MS = 24 * HOUR_MS" in js_text

    def test_recent_cutoff_is_72h(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "RECENT_CUTOFF_MS = 72 * HOUR_MS" in js_text

    def test_default_lookback_is_30_days(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "DEFAULT_LOOKBACK_DAYS = 30" in js_text


class TestViewToggleWiring:
    """The Recent view must be reachable via the segmented ViewToggle
    in TodoBoard.tsx. Pin the third button + the routing branch."""

    def test_view_enum_extended_in_store(self, store_text):
        # The PersistedView / store enum carries the 3-value type.
        # Arrange
        # Act
        # Assert
        assert '"graph" | "table" | "recent"' in store_text

    def test_view_toggle_has_recent_button_board_text_contains(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'setView("recent")' in board_text

    def test_view_toggle_has_recent_button_board_text_contains_2(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'view === "recent"' in board_text

    def test_recent_view_imported_into_todoboard(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "import { RecentView }" in board_text


class TestRecentViewLoadBearingUX:
    """The operator-visible affordances called out in the spec
    (title bar, NEW badge text, project chip, empty state copy)."""

    def test_title_bar_present(self, tsx_text):
        # Bilingual title from the spec.
        # Arrange
        # Act
        # Assert
        assert "Recent — 最近のToDo (新着が上)" in tsx_text

    def test_new_in_24h_count_label_present(self, tsx_text):
        # Arrange
        # Act
        # Assert
        assert "new in last 24h" in tsx_text

    def test_new_badge_text_present(self, tsx_text):
        # The bright orange badge — pin literal so the visual signal
        # doesn't drift to something subtle the operator can't scan.
        # Arrange
        # Act
        # Assert
        assert "NEW" in tsx_text

    def test_show_older_toggle_present(self, tsx_text):
        # Arrange
        # Act
        # Assert
        assert "Show older" in tsx_text

    def test_empty_state_mentions_created_at_tsx_text_contains(self, tsx_text):
        # The helpful empty-state message tells the operator HOW to
        # backfill a timestamp on an existing task.
        # Arrange
        # Act
        # Assert
        assert "created_at" in tsx_text

    def test_empty_state_mentions_created_at_tsx_text_contains_2(self, tsx_text):
        # The helpful empty-state message tells the operator HOW to
        # backfill a timestamp on an existing task.
        # Arrange
        # Act
        # Assert
        assert "scitex-cards update" in tsx_text


class TestQualifierSearchReused:
    """The Recent view must REUSE the qualifier-syntax search (PR #102)
    via the shared `taskMatchesFilter` helper, not re-implement it."""

    def test_recentview_imports_taskmatchesfilter(self, tsx_text):
        # Arrange
        # Act
        # Assert
        assert "taskMatchesFilter" in tsx_text
