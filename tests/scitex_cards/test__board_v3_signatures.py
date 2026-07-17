#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression pins for ``board_v3.html`` features.

The board template is a 3463-line monolith edited by every UI PR;
squash-merge conflicts on parallel branches have silently regressed
already-shipped features twice during the 2026-06-12 wave (P1 search-
as-launcher + P7 self-named-card filter both got clobbered between
their merge and the P10/P11 wave landing).

This test pins the SIGNATURE STRINGS of each shipped feature so a
future squash that would silently drop them fails CI instead.

When a feature deliberately changes shape, update the signature in
the matching ``test_<feature>_signatures_present`` test — the pin
documents the intent.

Refactor-friendly: tests look for substrings (not byte-for-byte exact
blocks) so non-behavioural cosmetics + indentation edits stay free.

WHICH CORPUS DOES A PIN READ?
-----------------------------
The CSS half of board_v3 was extracted from the inline ``<style>`` block
on 2026-06-12, and its pins were re-pointed at the concatenated static
files (``css_text``). The JS half is now being extracted the same way:
the inline ``<script>`` becomes node-testable modules under
``static/scitex_cards/board_v3/*.js``. Pins that grep the template text
would trip on every such extraction, so the rule is:

* a pin about a JS SYMBOL or JS source string reads ``board_js`` — the
  template AND every extracted module, because the symbol may legally
  live in either place before/after an extraction;
* a pin about HTML MARKUP or a DOM id reads ``board_text`` — markup
  stays in the template;
* a NEGATIVE pin that a JS function was REMOVED reads ``board_js``:
  "removed" must mean removed from the whole GUI, not merely moved out
  of the template.

Note a DOM id is not automatically markup: ids emitted from a JS
template literal (e.g. the Stale toolbar's ``id="stale-days"``) are JS
source strings and travel with their renderer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scitex_cards

BOARD_TEMPLATE = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)

# CSS half of board_v3 was extracted from the inline <style> block on
# 2026-06-12 (squash-regression root-cause fix; see GITIGNORED/
# REFACTORING.md). CSS-specific signature pins now read from the
# concatenated static files instead of from board_v3.html. The JS half
# is being extracted into *.js modules in this SAME directory, so
# JS-symbol pins read `board_js` (template + modules) while HTML pins
# keep reading the template directly. (hook-bypass: line-limit — this
# file is pre-existing over-cap; not refactored here.)
BOARD_CSS_DIR = (
    Path(scitex_cards.__file__).parent
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
)


@pytest.fixture(scope="module")
def board_text() -> str:
    """Read the board template once per test module."""
    return BOARD_TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_text() -> str:
    """Concatenate every extracted board_v3 CSS file.

    Returns one big string so signature pins can ``assert x in css_text``
    without caring which of the 5 (currently 6) files the rule landed in.
    """
    return "\n".join(
        css_path.read_text(encoding="utf-8")
        for css_path in sorted(BOARD_CSS_DIR.glob("*.css"))
    )


@pytest.fixture(scope="module")
def js_text() -> str:
    """Concatenate every extracted board_v3 JS module.

    Returns one big string so signature pins can ``assert x in js_text``
    without caring which module the symbol landed in. JS and CSS share
    the one static dir, hence the same BOARD_CSS_DIR.
    """
    return "\n".join(
        js_path.read_text(encoding="utf-8")
        for js_path in sorted(BOARD_CSS_DIR.glob("*.js"))
    )


@pytest.fixture(scope="module")
def board_js(board_text, js_text) -> str:
    """The template AND every extracted module, concatenated.

    This is the corpus for pins about JS SYMBOLS, which may live in
    either place after an extraction: a symbol moved from the inline
    <script> into a *.js module is still present in the shipped GUI, so
    a pin on this corpus survives the move while still failing if the
    symbol is genuinely dropped. Negative "was removed" pins read it too
    — removal must mean gone from the whole GUI, not just the template.
    """
    return board_text + "\n" + js_text


# -----------------------------------------------------------------------------
# P1 — search-as-launcher (PR #86)
# -----------------------------------------------------------------------------


class TestP1SearchAsLauncher:
    """Pins for the PR #86 search-as-launcher feature."""

    def test_attach_search_keyboard_launcher_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function attachSearchKeyboardLauncher" in board_js

    def test_attach_search_keyboard_launcher_called(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "attachSearchKeyboardLauncher()" in board_js

    def test_kbd_hint_in_search_placeholder_board_text_contains(self, board_text):
        # Lead `032e41545fcf4ab4b98d864ec1770249` 2026-06-12: the
        # standalone `.filt-search-kbd` pill was operator-judged
        # noise and folded into the search input's placeholder per
        # operator "just write the kbd in the search box". The pin
        # now asserts the hint is INSIDE the placeholder text on
        # `#f-search`, so the affordance is still discoverable but
        # without the extra DOM chrome.
        # Arrange
        # Act
        # Assert
        assert "/ to focus" in board_text, (
            "search input must hint at the '/' keyboard shortcut in "
            "its placeholder (operator UX, lead a2a "
            "`032e41545fcf4ab4b98d864ec1770249`)"
        )

    def test_kbd_hint_in_search_placeholder_board_text_contains_2(self, board_text):
        # Lead `032e41545fcf4ab4b98d864ec1770249` 2026-06-12: the
        # standalone `.filt-search-kbd` pill was operator-judged
        # noise and folded into the search input's placeholder per
        # operator "just write the kbd in the search box". The pin
        # now asserts the hint is INSIDE the placeholder text on
        # `#f-search`, so the affordance is still discoverable but
        # without the extra DOM chrome.
        # Arrange
        # Act
        # Assert
        assert (
            "Esc to blur" in board_text
        ), "search input must hint at 'Esc to blur' in its placeholder"

    def test_search_input_min_width_filterbar_scale(self, css_text):
        # Operator 2026-07-10 (でかすぎ,
        # todo-board-search-box-oversized-20260710): the P1-era 320px /
        # 1rem / permanent-glow search input dwarfed the sibling .filt
        # controls; resized to the filterbar baseline (220px min-width,
        # 0.85rem, glow on :focus only). Still wider than the pre-P1
        # 180px narrow form so the primary-affordance intent survives.
        # (CSS pin — 02-card.css.)
        # Arrange
        # Act
        # Assert
        assert "min-width: 220px" in css_text


# -----------------------------------------------------------------------------
# Column + Table layouts REMOVED (operator TG, 2026-07-13:
# "Column, Table view がいらないです、削除してください")
# -----------------------------------------------------------------------------


class TestColumnAndTableLayoutsRemoved:
    """Pins for the Column (kanban) + Table (flat rows) removal.

    The operator asked for both layouts to be DELETED, not hidden. The
    surviving layouts are Timeline (default) | Wall | Graph.

    These pins are the mirror image of the ones they replace: the features
    that lived ONLY inside those two renderers — the per-card renderer, the
    column chrome (pin / drag-reorder / column ctx-menu / per-column nudge),
    the multi-select bulk toolbar, the Sort / Group / Group-by-time controls
    and the project-COLUMN hide — must stay GONE. Same convention used when
    the Stale layout was removed on 2026-07-10.
    """

    def test_layout_buttons_gone(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="f-layout-column"' not in board_text
        assert 'id="f-layout-table"' not in board_text
        assert "📋 Column" not in board_text
        assert "📑 Table" not in board_text

    def test_layouts_out_of_the_whitelist(self, board_js):
        # Arrange
        # The whitelist is a JS const, so it may live in the template or in
        # an extracted module. Assert the marker EXISTS before splitting on
        # it: a bare `.split(...)[1]` raises IndexError when the const is
        # renamed/moved, which reads as a crash rather than as this pin
        # telling you the whitelist could not be found.
        assert "VALID_LAYOUTS = " in board_js, (
            "VALID_LAYOUTS marker not found in the template or any extracted "
            "module — the layout whitelist was renamed or moved; re-point "
            "this pin at its new shape"
        )
        whitelist = board_js.split("VALID_LAYOUTS = ")[1].split("]")[0]
        # Act
        # Assert
        assert '"column"' not in whitelist
        assert '"table"' not in whitelist
        assert '"stale"' not in whitelist
        for live in ('"timeline"', '"wall"', '"graph"'):
            assert live in whitelist

    def test_default_layout_is_timeline(self, board_js):
        # Column used to be the DEFAULT. If the default is not re-pointed at
        # a layout that still renders, every first-time visitor gets a BLANK
        # board — the single most likely way to break this change.
        # Arrange
        # Act
        # Assert
        assert 'const DEFAULT_LAYOUT = "timeline";' in board_js

    def test_stale_persisted_layout_is_migrated(self, board_js):
        # A browser whose localStorage still says "column" / "table" must be
        # coerced to the default, not left on a dead layout.
        # Arrange
        # Act
        # Assert
        assert "function normalizeLayout" in board_js
        assert "normalizeLayout(stored)" in board_js
        assert "normalizeLayout(STATE.layout)" in board_js

    def test_renderers_gone(self, board_js):
        # Arrange
        # Act
        # Assert
        for fn in (
            "function _renderColumnView",
            "function _renderColumnHtml",
            "function _renderTableView",
            "function _renderTimeBucketedColumn",
            "function cardHtml",
        ):
            assert fn not in board_js, f"{fn} must be removed"

    def test_column_chrome_gone(self, board_js):
        # Pin / drag-reorder / column ctx-menu / per-column nudge / card drag
        # only ever acted on column DOM.
        # Arrange
        # Act
        # Assert
        for fn in (
            "function toggleColPin",
            "function openColCtx",
            "function onColDragStart",
            "function onCardDragStart",
            "function nudgePrimaryAgent",
            "function isSelfNamedProjectCard",
            "function bumpPriority",
        ):
            assert fn not in board_js, f"{fn} must be removed"

    def test_column_only_controls_gone(self, board_text, board_js):
        # Sort / Group / Group-by-time / bulk-select / project-column hide
        # all operated on column cards; a control that cannot act on anything
        # is a lie in the UI, so they went with the layouts.
        # Arrange
        # Act
        # Assert
        # Filterbar markup — stays in the template.
        for dom_id in (
            'id="f-sort"',
            'id="f-groupby"',
            'id="stx-toggle-group-by-time"',
            'id="board-toolbar"',
            'id="group-spans-all"',
            'id="proj-hide-wrap"',
        ):
            assert dom_id not in board_text, f"{dom_id} must be removed"
        # Their JS must be gone from the whole GUI, not just the template.
        for fn in (
            "function _sortComparator",
            "function renderGroupStrip",
            "function _applyGroupClustering",
            "function bulkSetStatus",
            "function toggleProjHidden",
        ):
            assert fn not in board_js, f"{fn} must be removed"

    def test_table_css_gone(self, css_text):
        # Arrange
        # Act
        # Assert
        assert '[data-layout="table"]' not in css_text
        assert ".tbl-wrap" not in css_text
        assert ".tbl-status-dot" not in css_text


# -----------------------------------------------------------------------------
# Graph layout — background render + fit-to-panel sizing
# (operator TG 2026-07-13: "Graph view は最適化してください。裏で描画を始めて置き、
#  サイズも調整して表示されるようにしてください")
# -----------------------------------------------------------------------------


class TestGraphBackgroundRender:
    """Pins for the async / pre-warmed mermaid render.

    Switching to Graph used to freeze the UI for seconds (3 MB bundle fetch
    + a synchronous ``mermaid.run()`` against the live canvas). The render is
    now done OFF-DOM on an idle callback and cached by source string.
    """

    def test_source_builder_is_pure(self, board_js):
        # The mermaid source build must be separable from the DOM write —
        # that is what lets it run on the idle callback.
        # Arrange
        # Act
        # Assert
        assert "function _graphSrc" in board_js

    def test_prewarm_runs_on_idle(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function _prewarmGraph" in board_js
        assert "requestIdleCallback" in board_js
        assert "_prewarmGraph();" in board_js

    def test_render_uses_the_promise_api_not_run(self, board_js):
        # `mermaid.run({querySelector})` lays out against the LIVE canvas and
        # blocks; `mermaid.render(id, src)` resolves an SVG string off-DOM.
        # Arrange
        # Act
        # Assert
        assert "mermaid.render(" in board_js
        assert "mermaid.run(" not in board_js

    def test_render_is_cached_by_source(self, board_js):
        # Re-rendering on every poll tick was the other half of the cost.
        # Arrange
        # Act
        # Assert
        assert "GRAPH_CACHE" in board_js
        assert "GRAPH_CACHE.key === built.src" in board_js

    def test_svg_is_sized_to_the_panel(self, board_js, css_text):
        # An SVG rendered off-DOM carries mermaid's natural px width; dropped
        # into the panel unscaled it overflows. The viewBox + the fit rule are
        # what make it show up correctly sized.
        # Arrange
        # Act
        # Assert
        assert "preserveAspectRatio" in board_js
        assert "viewBox" in board_js
        assert ".graph-wrap--fit .graph-canvas svg" in css_text
        assert ".graph-canvas" in css_text


# -----------------------------------------------------------------------------
# P8 — move-picker lists ALL projects + new-project flow (PR #88)
# -----------------------------------------------------------------------------


class TestP8MovePickerAllProjects:
    """Pins for the PR #88 move-picker (post-P11b Combobox fallback path)."""

    def test_no_twelve_item_slice_cap(self, board_js):
        # The pre-P8 cap was ``.slice(0, 12)`` — listing only the first
        # 12 projects out of ~30 in the store. P8 dropped it; PR #94's
        # Combobox layer also lists ALL. A reappearance of the literal
        # slice call means the regression came back.
        # Arrange
        # Act
        # Assert
        assert ".slice(0, 12)" not in board_js

    def test_prompt_move_to_new_project_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function promptMoveToNewProject" in board_js

    def test_new_project_button_text_present(self, board_js):
        # The legacy "+ New project…" fallback button (used when the
        # scitex-ui Combobox is unavailable). Either spelling acceptable.
        # The button is emitted from a JS template literal, not template
        # markup, so it travels with the move-picker's renderer.
        # Arrange
        # Act
        # Assert
        assert "New project" in board_js


# -----------------------------------------------------------------------------
# P2 + P9 — filter UX collapse + sort-by (PR #89)
# -----------------------------------------------------------------------------


class TestP2P9FilterAndSort:
    """Pins for the PR #89 filter-UX collapse + sort-by control."""

    def test_filter_popover_class_present(self, css_text):
        # CSS pin — extracted to 04-collapse-and-groups.css 2026-06-12.
        # Arrange
        # Act
        # Assert
        assert "filt-popover" in css_text

    def test_render_active_filter_chips_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function renderActiveFilterChips" in board_js

    def test_clear_one_filter_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function clearOneFilter" in board_js

    def test_sort_control_removed_with_the_column_layout(self, board_text, board_js):
        # P9's Sort dropdown only re-ordered cards WITHIN a column (and the
        # Table rows). Both layouts were removed 2026-07-13, so the control
        # and its comparator went too — see
        # TestColumnAndTableLayoutsRemoved.
        # Arrange
        # Act
        # Assert
        # `STATE.sort` deliberately stays on the TEMPLATE corpus. The string
        # occurs in extracted timeline.js — but only inside a PROSE COMMENT
        # ("mirror STATE.sort/layout stickiness"), never as live state. Pinned
        # against board_js this would fail on that comment: a false positive
        # about a symbol that is genuinely gone. The board_js corpus cannot
        # tell code from comments, so the narrower corpus is the honest one.
        assert "STATE.sort" not in board_text
        # The comparator is a real function — removal must hold GUI-wide.
        assert "function _sortComparator" not in board_js


# -----------------------------------------------------------------------------
# P10 — GROUPS (PR #91)
# -----------------------------------------------------------------------------


class TestP10GroupsRemoved:
    """PR #91's group clustering re-ordered COLUMNS and rendered a banner
    above the columns grid. Both died with the Column layout (2026-07-13).
    The `groups:` schema + the /graph payload field are untouched — only the
    board-side clustering UI is gone.
    """

    def test_group_clustering_ui_removed(self, board_text, board_js):
        # Arrange
        # Act
        # Assert
        # JS state + renderers — must be gone from the whole GUI.
        assert "STATE.groupBy" not in board_js
        assert "function renderGroupStrip" not in board_js
        assert "function _applyGroupClustering" not in board_js
        # The banner's DOM id is template markup.
        assert 'id="group-spans-all"' not in board_text


# -----------------------------------------------------------------------------
# P4 PR1 — deadline + scheduled FE consumption (PR #92)
# -----------------------------------------------------------------------------


class TestP4DeadlineFE:
    """Pins for the PR #92 deadline field prefer-over-title FE path."""

    def test_date_info_reads_deadline_field(self, board_js):
        # `dateInfo()` must check the schema deadline field BEFORE
        # falling back to the title parse. The substring matches the
        # actual line where the field read happens.
        # Arrange
        # Act
        # Assert
        assert "t.deadline" in board_js


class TestP4MultiRecurringFE:
    """Pins for the P4 PR3 multi/recurring FE consumer."""

    def test_date_info_reads_deadline_next(self, board_js):
        # The server expands recurring + multi to a single
        # `deadline_next` ISO; the FE must prefer it over `deadline`
        # when present.
        # Arrange
        # Act
        # Assert
        assert "t.deadline_next" in board_js

    def test_extract_repeater_suffix_helper_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function _extractRepeaterSuffix" in board_js

    def test_first_recurring_deadline_helper_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function _firstRecurringDeadline" in board_js


# -----------------------------------------------------------------------------
# P11b — Combobox consumer (PR #94)
# -----------------------------------------------------------------------------


class TestP11bComboboxConsumer:
    """Pins for the PR #94 Combobox layer + Combobox-driven move-picker."""

    def test_combobox_css_static_load(self, board_text):
        # A {% static %} <link> tag — template markup, stays put.
        # Arrange
        # Act
        # Assert
        assert "combobox.css" in board_text

    def test_combobox_js_static_load(self, board_text):
        # A {% static %} <script> tag — template markup, stays put.
        # Arrange
        # Act
        # Assert
        assert "combobox.js" in board_text

    def test_attach_combobox_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function attachCombobox" in board_js

    def test_open_move_to_combobox_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function _openMoveToCombobox" in board_js


# -----------------------------------------------------------------------------
# Search qualifier syntax (operator TG 12315/12316, lead a2a 7dde227a)
# -----------------------------------------------------------------------------


class TestSearchQualifierSyntax:
    """Pins for the GitHub-style ``project:foo`` / ``status:blocked`` /
    ``kind:compute`` search syntax. The operator was photographed typing
    ``project: paper-scitex-clew`` (TG 12315/12316) and expected the
    qualifier to filter — i.e. they assumed GitHub-style behaviour. This
    test class makes sure the wiring stays in.
    """

    def test_search_query_js_static_load(self, board_text):
        # The parser ships as static/scitex_cards/board_v3/searchQuery.js
        # — board_v3.html must pull it in via {% static %}.
        # Arrange
        # Act
        # Assert
        assert "board_v3/searchQuery.js" in board_text

    def test_render_qualifier_hints_defined(self, board_js):
        # The hint-pill renderer must exist in the page logic.
        # Arrange
        # Act
        # Assert
        assert "function renderQualifierHints" in board_js

    def test_hint_pill_container_present(self, board_text):
        # And the <div id="filt-qhints"> the renderer writes into.
        # Arrange
        # Act
        # Assert
        assert 'id="filt-qhints"' in board_text

    def test_search_input_advertises_qualifier_syntax_board_text_contains(
        self, board_text
    ):
        # Placeholder + title should mention the new qualifier syntax so
        # the operator's expectation (GitHub-style) is met without docs.
        # Arrange
        # Act
        # Assert
        assert "project:" in board_text

    def test_search_input_advertises_qualifier_syntax_board_text_contains_2(
        self, board_text
    ):
        # Placeholder + title should mention the new qualifier syntax so
        # the operator's expectation (GitHub-style) is met without docs.
        # Arrange
        # Act
        # Assert
        assert "status:" in board_text

    def test_fuzzy_match_delegates_to_parser(self, board_js):
        # Sanity pin: the fuzzy-match function must consult
        # window.STX.searchQuery so a future squash that strips the
        # delegation reverts the operator pain.
        # Arrange
        # Act
        # Assert
        assert "window.STX.searchQuery" in board_js

    def test_hint_pill_css_defined(self, css_text):
        # CSS pin — `.filt-qhint` lives in the extracted filterbar stylesheet.
        # Arrange
        # Act
        # Assert
        assert ".filt-qhint" in css_text


# -----------------------------------------------------------------------------
# PR(h) Stage 1 — multi-select + bulk status change
# (board card todo-multiselect-batch-ops, lead a2a 1ebc792c)
# -----------------------------------------------------------------------------


class TestMultiselectBatchOpsRemoved:
    """PR(h) Stage 1's multi-select + bulk status change is GONE (2026-07-13).

    Not a deliberate feature cut: the per-row checkbox was rendered by
    ``cardHtml`` and existed ONLY on Column / Table cards. With both layouts
    removed there is nothing to select, so the toolbar could not act on
    anything. Re-introducing bulk ops means putting a selection affordance on
    the Wall notes / Timeline markers first — a separate card.
    """

    def test_card_checkbox_gone(self, board_js):
        # The checkbox was emitted from ``cardHtml``'s template literal and
        # the flag is a JS global — both are JS source, so removal is pinned
        # against the whole GUI.
        # Arrange
        # Act
        # Assert
        assert 'class="card-select"' not in board_js
        assert "window.MULTISELECT" not in board_js

    def test_bulk_toolbar_gone(self, board_text):
        # Toolbar DOM ids are template markup.
        # Arrange
        # Act
        # Assert
        for dom_id in (
            'id="board-toolbar"',
            'id="board-toolbar-select-all"',
            'id="board-toolbar-count"',
            'id="board-toolbar-status"',
        ):
            assert dom_id not in board_text

    def test_bulk_helpers_gone(self, board_js):
        # Arrange
        # Act
        # Assert
        for fn in (
            "function toggleCardSelected",
            "function toggleSelectAll",
            "function clearMultiselect",
            "async function bulkSetStatus",
        ):
            assert fn not in board_js

    def test_single_card_update_path_survives(self, board_js):
        # The ctx-menu status change still posts to /update — the endpoint is
        # untouched, only the bulk loop is gone.
        # Arrange
        # Act
        # Assert
        assert '"/update"' in board_js
        assert "async function setCardStatus" in board_js


# -----------------------------------------------------------------------------
# Activity bucket badge — render side of working-status decay
# (board card `scitex-todo-working-status-decay-tg12739`, render half of PR #122)
# -----------------------------------------------------------------------------


class TestActivityBucketBadgeRemoved:
    """The per-card activity badge (and the age / date pills) rendered INSIDE
    ``cardHtml``, which only Column + Table used. They went with the card
    renderer on 2026-07-13.

    The RECENCY signal itself is not lost: the Timeline raster is built on
    exactly the same ``last_activity`` axis, and the backend
    ``_build_fleet`` decay derivation (PR #122) is untouched.
    """

    def test_card_pill_renderers_gone(self, board_js):
        # Arrange
        # Act
        # Assert
        for fn in (
            "function activityBadgeHtml",
            "function _activityBucket",
            "function _activityHoursSince",
            "function agePillHtml",
            "function datePillHtml",
        ):
            assert fn not in board_js, f"{fn} must be removed"

    def test_last_activity_axis_survives(self, board_js):
        # The field is still read (recent-count pill + the /timeline raster).
        # Arrange
        # Act
        # Assert
        assert "t.last_activity" in board_js


# -----------------------------------------------------------------------------
# Stale Review FE panel — operator-requested recurring stale-cards review
# (operator via lead a2a 2026-06-13; backend half is PR #153)
# -----------------------------------------------------------------------------


class TestStaleReviewPanel:
    """Pins for the Stale Review render path + Archive button.

    Backend half is PR #153 (/stale + /archive endpoints). The layout
    BUTTON was removed 2026-07-10 (operator, card
    todo-board-remove-stale-view-timeline-first-20260710 — "Stale view
    要らない"): the first two pins now assert the button stays GONE and
    the layout unreachable, while the fetch+render helpers, per-row
    Archive button (HTTP twin of CLI `close --reason` PR #151) and the
    days + include_no_timestamp toolbar knobs remain pinned as shared
    code.
    """

    def test_stale_layout_button_removed_from_filterbar(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="f-layout-stale"' not in board_text
        assert "🧹 Stale" not in board_text

    def test_stale_layout_unreachable_in_whitelist(self, board_js):
        # Arrange
        # Assert the marker exists before splitting on it, so a moved/renamed
        # whitelist fails this pin with a readable message instead of an
        # IndexError. See TestColumnAndTableLayoutsRemoved for the twin pin.
        assert "VALID_LAYOUTS = " in board_js, (
            "VALID_LAYOUTS marker not found in the template or any extracted "
            "module — the layout whitelist was renamed or moved; re-point "
            "this pin at its new shape"
        )
        # Act
        # Assert
        assert (
            '"stale"' not in board_js.split("VALID_LAYOUTS = ")[1].split("]")[0]
        ), "stale must stay out of the layout whitelist (operator removal)"

    def test_stale_render_helper_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "function _renderStaleView" in board_js

    def test_stale_render_dispatched_from_render(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "_renderStaleView(canvas)" in board_js

    def test_stale_fetch_target_endpoint(self, board_js):
        # Arrange
        # Act
        # Assert
        assert '"/scitex-todo/stale?"' in board_js

    def test_archive_helper_defined(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "async function archiveStaleCard" in board_js

    def test_archive_post_target_endpoint(self, board_js):
        # Arrange
        # Act
        # Assert
        assert '"/scitex-todo/archive"' in board_js

    def test_archive_requires_reason(self, board_js):
        # Arrange
        # Act
        # Assert
        assert "Archive requires a non-empty reason" in board_js

    def test_stale_toolbar_days_input_present(self, board_js):
        # JS corpus, not markup: the Stale toolbar is emitted from
        # `_renderStaleView`'s template literal, so this id travels with the
        # renderer when it is extracted.
        # Arrange
        # Act
        # Assert
        assert 'id="stale-days"' in board_js

    def test_stale_toolbar_include_no_timestamp_checkbox_present(self, board_js):
        # JS corpus — same rationale as the days input above.
        # Arrange
        # Act
        # Assert
        assert 'id="stale-incnotime"' in board_js

    def test_stale_wrap_css_class_defined(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".stale-wrap" in css_text

    def test_stale_archive_btn_css_class_defined(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".stale-archive-btn" in css_text


# EOF
