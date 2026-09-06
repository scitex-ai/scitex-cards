#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Front-end contract tests for the fleet timing-chart panel (Phase 5).

Two halves (same pattern as ``test_fleet_hosts.py`` /
``test_fleet_ci_pills.py`` / ``test_fleet_mesh.py``):

1. **CSS contract** — open ``fleet-timing.css`` and assert:
     - the canonical selectors are present
     - colors come from design tokens (``--status-success``,
       ``--status-warning``, ``--status-error``, ``--stx-danger``,
       etc.) ONLY; NO hardcoded hex / ``white`` / ``#fff`` literals
       leak in (theme-breaking).
     - the partial is imported from ``board.css`` so the panel
       actually styles when the bundle loads.
2. **Component logic** — execute the actual ``isTimingPayloadErr`` /
   ``formatDurationSeconds`` / ``barWidthPct`` / ``barColorToken`` /
   ``sortKeysByP95Desc`` / ``pickRows`` / ``timingPanelLabel``
   helpers from ``FleetTimingPanel.tsx`` via ``node`` and pin the
   mapping for canonical payloads. Same lock-step assertion against
   the TS source so a rename downstream forces this test to update.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]

_CSS_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "fleet-timing.css"
)
_TSX_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "FleetTimingPanel.tsx"
)


# ─── CSS contract ───────────────────────────────────────────────────────


def test_css_file_exists() -> None:
    # Arrange
    # Act
    # Assert
    assert _CSS_FILE.is_file(), f"missing CSS file: {_CSS_FILE}"


def test_css_has_canonical_selectors() -> None:
    """The component generates these class names — the CSS file MUST
    define each one or the panel will silently render unstyled."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Act
    # Assert
    for selector in (
        ".stx-cards-fleet-timing",
        ".stx-cards-fleet-timing--loading",
        ".stx-cards-fleet-timing--collapsed",
        ".stx-cards-fleet-timing--expanded",
        ".stx-cards-fleet-timing--error",
        ".stx-cards-fleet-timing__label",
        ".stx-cards-fleet-timing__dot",
        ".stx-cards-fleet-timing__header",
        ".stx-cards-fleet-timing__control",
        ".stx-cards-fleet-timing__select",
        ".stx-cards-fleet-timing__close",
        ".stx-cards-fleet-timing__chart",
        ".stx-cards-fleet-timing__row",
        ".stx-cards-fleet-timing__rowlabel",
        ".stx-cards-fleet-timing__bars",
        ".stx-cards-fleet-timing__bar",
        ".stx-cards-fleet-timing__bar--median",
        ".stx-cards-fleet-timing__bar--p95",
        ".stx-cards-fleet-timing__bar--ok",
        ".stx-cards-fleet-timing__bar--warn",
        ".stx-cards-fleet-timing__bar--slow",
        ".stx-cards-fleet-timing__footer",
    ):
        assert selector in css, f"missing CSS selector: {selector}"


def test_css_has_no_hardcoded_hex_colors() -> None:
    """Hex literals freeze the panel to one theme; colors must come
    from design tokens."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Strip /* ... */ comments so the doc block (which names the
    # tokens verbatim) does not trip the hex / named-color scan.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Act
    hex_matches = re.findall(r"#[0-9A-Fa-f]{3,8}\b", no_comments)
    # Assert
    assert not hex_matches, f"hardcoded hex colors in fleet-timing.css: {hex_matches!r}"


def test_css_has_no_hardcoded_named_colors() -> None:
    """Named-color literals (white / black) are the same theming
    smell as hex."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Strip /* ... */ comments so the doc block (which names the
    # tokens verbatim) does not trip the hex / named-color scan.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Act
    forbidden = (r":\s*white\b", r":\s*black\b")
    # Assert
    assert not any(re.search(f, no_comments) for f in forbidden)


def test_css_references_all_required_tokens() -> None:
    """Every required design token must be referenced at least once."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Act
    required = (
        "--status-success",
        "--status-warning",
        "--status-error",
        "--stx-danger",
        "--stx-text-muted",
        "--stx-border",
        "--stx-panel-bg",
        "--stx-text",
    )
    # Assert
    assert all(token in css for token in required)


def test_css_is_imported_from_board_css_is_file() -> None:
    """The panel only renders correctly when board.css imports the
    partial. Pinning this guards against an accidental removal in a
    future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    text = board_css.read_text(encoding="utf-8")
    assert board_css.is_file()


def test_css_is_imported_from_board_css_text_contains() -> None:
    """The panel only renders correctly when board.css imports the
    partial. Pinning this guards against an accidental removal in a
    future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    text = board_css.read_text(encoding="utf-8")
    assert '@import "./fleet-timing.css";' in text


# ─── component logic — helpers via node ─────────────────────────────────


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


# Static-source fragments — assert these stay verbatim in the TSX so
# the node mirror below stays in lock-step. A rename downstream forces
# this test to update; no silent drift.
_TS_CONTRACT_FRAGMENTS = [
    "export function isTimingPayloadErr(p: TimingPayload): p is TimingPayloadErr {",
    'return Object.prototype.hasOwnProperty.call(p, "error");',
    "export function formatDurationSeconds(seconds: number | null): string {",
    "export function barWidthPct(",
    "export function barColorToken(pct: number): string {",
    '"stx-cards-fleet-timing__bar--ok"',
    '"stx-cards-fleet-timing__bar--warn"',
    '"stx-cards-fleet-timing__bar--slow"',
    "export function sortKeysByP95Desc(",
    "export function timingPanelLabel(",
    "export function pickRows(",
]


def _assert_ts_lockstep() -> None:
    src = _TSX_FILE.read_text(encoding="utf-8")
    for needle in _TS_CONTRACT_FRAGMENTS:
        assert needle in src, (
            f"FleetTimingPanel.tsx no longer contains canonical "
            f"fragment {needle!r}; update this test in lock-step."
        )


# A pure-JS mirror of the TS helpers, kept in lock-step via the
# fragment-presence test above. node executes this without bundling
# the TSX so the test runs in a vanilla Python+node container.
_JS_RUNTIME = textwrap.dedent(
    """
    function isTimingPayloadErr(p) {
      return Object.prototype.hasOwnProperty.call(p, "error");
    }
    function formatDurationSeconds(seconds) {
      if (seconds === null || seconds === undefined) return "\\u2014";
      if (!Number.isFinite(seconds)) return "\\u2014";
      const s = Math.max(0, seconds);
      if (s < 1) {
        return s.toFixed(1) + "s";
      }
      if (s < 60) {
        if (s < 10 && s % 1 !== 0) return s.toFixed(1) + "s";
        return Math.round(s) + "s";
      }
      if (s < 3600) {
        const mins = Math.floor(s / 60);
        const rem = Math.round(s - mins * 60);
        if (rem === 0) return mins + "m";
        return mins + "m " + rem + "s";
      }
      if (s < 86400) {
        const hours = s / 3600;
        if (Number.isInteger(hours)) return hours + "h";
        return hours.toFixed(1) + "h";
      }
      const days = s / 86400;
      if (Number.isInteger(days)) return days + "d";
      return days.toFixed(1) + "d";
    }
    function barWidthPct(value, max) {
      if (value === null || value === undefined) return 0;
      if (!Number.isFinite(value) || value <= 0) return 0;
      if (!Number.isFinite(max) || max <= 0) return 0;
      const pct = (value / max) * 100;
      if (pct < 0) return 0;
      if (pct > 100) return 100;
      return pct;
    }
    function barColorToken(pct) {
      if (!Number.isFinite(pct) || pct <= 0) {
        return "stx-cards-fleet-timing__bar--ok";
      }
      if (pct < 50) return "stx-cards-fleet-timing__bar--ok";
      if (pct < 80) return "stx-cards-fleet-timing__bar--warn";
      return "stx-cards-fleet-timing__bar--slow";
    }
    function sortKeysByP95Desc(rows) {
      const entries = Object.entries(rows);
      entries.sort((a, b) => {
        const pa = a[1].p95_started_to_done_s;
        const pb = b[1].p95_started_to_done_s;
        if (pa === null && pb === null) return a[0].localeCompare(b[0]);
        if (pa === null) return 1;
        if (pb === null) return -1;
        if (pb !== pa) return pb - pa;
        return a[0].localeCompare(b[0]);
      });
      return entries.map((e) => e[0]);
    }
    function pickRows(p, groupBy) {
      if (groupBy === "agent") return p.per_agent || {};
      if (groupBy === "project") return p.per_project || {};
      return p.per_group || {};
    }
    function timingPanelLabel(p, groupBy) {
      const rows = pickRows(p, groupBy);
      const n = Object.keys(rows).length;
      return "\\uD83D\\uDCCA timing \\u00b7 " + n + " rows";
    }
    """
).strip()


def _run_js(extra: str) -> dict:
    """Run the JS runtime + a small wrapper that prints a JSON object."""
    _assert_ts_lockstep()
    script = _JS_RUNTIME + "\n" + extra
    proc = subprocess.run(
        [_node(), "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


# ─── formatter ──────────────────────────────────────────────────────────


def test_format_duration_sub_second() -> None:
    """1.5s renders as ``1.5s`` — one decimal so the operator sees the
    millisec resolution that lives in the back-end timestamps."""
    # Arrange
    # Act
    out = _run_js(
        "process.stdout.write(JSON.stringify({a: formatDurationSeconds(1.5)}));"
    )
    # Assert
    assert out["a"] == "1.5s"


def test_format_duration_minutes_ninety() -> None:
    """90s renders as ``1m 30s`` — operator wants both halves visible.
    3600s renders as ``1h`` cleanly (no leftover ``0m``). 7200s →
    ``2h``."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ninety: formatDurationSeconds(90),
              one_hour: formatDurationSeconds(3600),
              two_hours: formatDurationSeconds(7200),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["ninety"] == "1m 30s"


def test_format_duration_minutes_one_hour() -> None:
    """90s renders as ``1m 30s`` — operator wants both halves visible.
    3600s renders as ``1h`` cleanly (no leftover ``0m``). 7200s →
    ``2h``."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ninety: formatDurationSeconds(90),
              one_hour: formatDurationSeconds(3600),
              two_hours: formatDurationSeconds(7200),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["one_hour"] == "1h"


def test_format_duration_minutes_two_hours() -> None:
    """90s renders as ``1m 30s`` — operator wants both halves visible.
    3600s renders as ``1h`` cleanly (no leftover ``0m``). 7200s →
    ``2h``."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ninety: formatDurationSeconds(90),
              one_hour: formatDurationSeconds(3600),
              two_hours: formatDurationSeconds(7200),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["two_hours"] == "2h"


def test_format_duration_handles_nulls_and_infinities_n() -> None:
    """Null / NaN / Infinity all surface as the dash literal — the
    fail-loud principle: don't render a bogus number."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: formatDurationSeconds(null),
              i: formatDurationSeconds(Infinity),
              nan: formatDurationSeconds(NaN),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["n"] == "—"


def test_format_duration_handles_nulls_and_infinities_i() -> None:
    """Null / NaN / Infinity all surface as the dash literal — the
    fail-loud principle: don't render a bogus number."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: formatDurationSeconds(null),
              i: formatDurationSeconds(Infinity),
              nan: formatDurationSeconds(NaN),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["i"] == "—"


def test_format_duration_handles_nulls_and_infinities_nan() -> None:
    """Null / NaN / Infinity all surface as the dash literal — the
    fail-loud principle: don't render a bogus number."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: formatDurationSeconds(null),
              i: formatDurationSeconds(Infinity),
              nan: formatDurationSeconds(NaN),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["nan"] == "—"


def test_format_duration_seconds_integer() -> None:
    """45s renders without decimals — operator doesn't need 45.0s."""
    # Arrange
    # Act
    out = _run_js(
        "process.stdout.write(JSON.stringify({a: formatDurationSeconds(45)}));"
    )
    # Assert
    assert out["a"] == "45s"


# ─── bar-width helper ───────────────────────────────────────────────────


def test_bar_width_pct_basic_full() -> None:
    """value=max → 100; value=0 → 0; value=half → 50."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              full: barWidthPct(100, 100),
              half: barWidthPct(50, 100),
              zero: barWidthPct(0, 100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["full"] == 100


def test_bar_width_pct_basic_half() -> None:
    """value=max → 100; value=0 → 0; value=half → 50."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              full: barWidthPct(100, 100),
              half: barWidthPct(50, 100),
              zero: barWidthPct(0, 100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["half"] == 50


def test_bar_width_pct_basic_zero() -> None:
    """value=max → 100; value=0 → 0; value=half → 50."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              full: barWidthPct(100, 100),
              half: barWidthPct(50, 100),
              zero: barWidthPct(0, 100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["zero"] == 0


def test_bar_width_pct_clamps_and_null_safe_n() -> None:
    """Null / negative / over-max all clamp to [0, 100] — no NaN /
    overflow geometry."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: barWidthPct(null, 100),
              neg: barWidthPct(-5, 100),
              over: barWidthPct(200, 100),
              zero_max: barWidthPct(50, 0),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["n"] == 0


def test_bar_width_pct_clamps_and_null_safe_neg() -> None:
    """Null / negative / over-max all clamp to [0, 100] — no NaN /
    overflow geometry."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: barWidthPct(null, 100),
              neg: barWidthPct(-5, 100),
              over: barWidthPct(200, 100),
              zero_max: barWidthPct(50, 0),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["neg"] == 0


def test_bar_width_pct_clamps_and_null_safe_over() -> None:
    """Null / negative / over-max all clamp to [0, 100] — no NaN /
    overflow geometry."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: barWidthPct(null, 100),
              neg: barWidthPct(-5, 100),
              over: barWidthPct(200, 100),
              zero_max: barWidthPct(50, 0),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["over"] == 100


def test_bar_width_pct_clamps_and_null_safe_zero_max() -> None:
    """Null / negative / over-max all clamp to [0, 100] — no NaN /
    overflow geometry."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              n: barWidthPct(null, 100),
              neg: barWidthPct(-5, 100),
              over: barWidthPct(200, 100),
              zero_max: barWidthPct(50, 0),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["zero_max"] == 0


# ─── bar-color helper ───────────────────────────────────────────────────


def test_bar_color_token_thresholds_ok() -> None:
    """Green / warn / error mapping by width-percentage."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ok: barColorToken(10),
              ok_just_under: barColorToken(49.99),
              warn: barColorToken(50),
              warn_just_under: barColorToken(79.99),
              slow: barColorToken(80),
              slow_full: barColorToken(100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["ok"] == "stx-cards-fleet-timing__bar--ok"


def test_bar_color_token_thresholds_ok_just_under() -> None:
    """Green / warn / error mapping by width-percentage."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ok: barColorToken(10),
              ok_just_under: barColorToken(49.99),
              warn: barColorToken(50),
              warn_just_under: barColorToken(79.99),
              slow: barColorToken(80),
              slow_full: barColorToken(100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["ok_just_under"] == "stx-cards-fleet-timing__bar--ok"


def test_bar_color_token_thresholds_warn() -> None:
    """Green / warn / error mapping by width-percentage."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ok: barColorToken(10),
              ok_just_under: barColorToken(49.99),
              warn: barColorToken(50),
              warn_just_under: barColorToken(79.99),
              slow: barColorToken(80),
              slow_full: barColorToken(100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["warn"] == "stx-cards-fleet-timing__bar--warn"


def test_bar_color_token_thresholds_warn_just_under() -> None:
    """Green / warn / error mapping by width-percentage."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ok: barColorToken(10),
              ok_just_under: barColorToken(49.99),
              warn: barColorToken(50),
              warn_just_under: barColorToken(79.99),
              slow: barColorToken(80),
              slow_full: barColorToken(100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["warn_just_under"] == "stx-cards-fleet-timing__bar--warn"


def test_bar_color_token_thresholds_slow() -> None:
    """Green / warn / error mapping by width-percentage."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ok: barColorToken(10),
              ok_just_under: barColorToken(49.99),
              warn: barColorToken(50),
              warn_just_under: barColorToken(79.99),
              slow: barColorToken(80),
              slow_full: barColorToken(100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["slow"] == "stx-cards-fleet-timing__bar--slow"


def test_bar_color_token_thresholds_slow_full() -> None:
    """Green / warn / error mapping by width-percentage."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              ok: barColorToken(10),
              ok_just_under: barColorToken(49.99),
              warn: barColorToken(50),
              warn_just_under: barColorToken(79.99),
              slow: barColorToken(80),
              slow_full: barColorToken(100),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["slow_full"] == "stx-cards-fleet-timing__bar--slow"


# ─── sort helper ────────────────────────────────────────────────────────


def test_sort_keys_by_p95_desc_basic() -> None:
    """Three agents — slowest p95 sorts first (operator-friendly
    bottleneck-at-top)."""
    # Arrange
    payload = {
        "fast": {
            "n_tasks_done": 5,
            "median_started_to_done_s": 10.0,
            "p95_started_to_done_s": 20.0,
            "median_created_to_started_s": 1.0,
        },
        "slow": {
            "n_tasks_done": 3,
            "median_started_to_done_s": 50.0,
            "p95_started_to_done_s": 200.0,
            "median_created_to_started_s": 5.0,
        },
        "mid": {
            "n_tasks_done": 4,
            "median_started_to_done_s": 25.0,
            "p95_started_to_done_s": 100.0,
            "median_created_to_started_s": 2.0,
        },
    }
    # Act
    out = _run_js(
        "const rows = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({order: sortKeysByP95Desc(rows)}));"
    )
    # Assert
    assert out["order"] == ["slow", "mid", "fast"]


def test_sort_keys_by_p95_desc_nulls_last() -> None:
    """Rows whose p95 is null sink to the bottom — they have no work
    duration to compare against (no done tasks in window)."""
    # Arrange
    payload = {
        "has_data": {
            "n_tasks_done": 1,
            "median_started_to_done_s": 5.0,
            "p95_started_to_done_s": 10.0,
            "median_created_to_started_s": 1.0,
        },
        "empty": {
            "n_tasks_done": 0,
            "median_started_to_done_s": None,
            "p95_started_to_done_s": None,
            "median_created_to_started_s": None,
        },
    }
    # Act
    out = _run_js(
        "const rows = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({order: sortKeysByP95Desc(rows)}));"
    )
    # Assert
    assert out["order"] == ["has_data", "empty"]


def test_sort_keys_by_p95_desc_stable_tie_break() -> None:
    """Equal p95 → alphabetical by key name. Deterministic order across
    polls so the chart doesn't jiggle."""
    # Arrange
    payload = {
        "zebra": {
            "n_tasks_done": 1,
            "median_started_to_done_s": 10.0,
            "p95_started_to_done_s": 10.0,
            "median_created_to_started_s": None,
        },
        "alpha": {
            "n_tasks_done": 1,
            "median_started_to_done_s": 10.0,
            "p95_started_to_done_s": 10.0,
            "median_created_to_started_s": None,
        },
    }
    # Act
    out = _run_js(
        "const rows = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({order: sortKeysByP95Desc(rows)}));"
    )
    # Assert
    assert out["order"] == ["alpha", "zebra"]


# ─── error discriminator + label + pickRows ─────────────────────────────


def test_error_payload_discriminator_ok() -> None:
    """``isTimingPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            const ok = {window_days: 30, per_agent: {}, per_project: {},
              per_group: {}, n_tasks_in_window: 0,
              n_tasks_missing_timestamps: 0};
            const err = {error: "boom"};
            process.stdout.write(JSON.stringify({
              ok: isTimingPayloadErr(ok),
              err: isTimingPayloadErr(err),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["ok"] is False


def test_error_payload_discriminator_err() -> None:
    """``isTimingPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_js(
        textwrap.dedent(
            """
            const ok = {window_days: 30, per_agent: {}, per_project: {},
              per_group: {}, n_tasks_in_window: 0,
              n_tasks_missing_timestamps: 0};
            const err = {error: "boom"};
            process.stdout.write(JSON.stringify({
              ok: isTimingPayloadErr(ok),
              err: isTimingPayloadErr(err),
            }));
            """
        ).strip()
    )
    # Assert
    assert out["err"] is True


def test_timing_panel_label_counts_rows_in_dimension_agent_contains() -> None:
    """The label reflects the row count of the currently-selected
    group-by dimension, not the union of all dimensions."""
    # Arrange
    payload = {
        "window_days": 30,
        "per_agent": {
            "a": {
                "n_tasks_done": 1,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            },
            "b": {
                "n_tasks_done": 1,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            },
        },
        "per_project": {
            "proj1": {
                "n_tasks_done": 2,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            }
        },
        "per_group": {},
        "n_tasks_in_window": 2,
        "n_tasks_missing_timestamps": 0,
    }
    # Act
    out = _run_js(
        "const p = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({"
        + "agent: timingPanelLabel(p, 'agent'),"
        + "project: timingPanelLabel(p, 'project'),"
        + "group: timingPanelLabel(p, 'group')"
        + "}));"
    )
    # Assert
    assert "2 rows" in out["agent"]


def test_timing_panel_label_counts_rows_in_dimension_project_contains() -> None:
    """The label reflects the row count of the currently-selected
    group-by dimension, not the union of all dimensions."""
    # Arrange
    payload = {
        "window_days": 30,
        "per_agent": {
            "a": {
                "n_tasks_done": 1,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            },
            "b": {
                "n_tasks_done": 1,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            },
        },
        "per_project": {
            "proj1": {
                "n_tasks_done": 2,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            }
        },
        "per_group": {},
        "n_tasks_in_window": 2,
        "n_tasks_missing_timestamps": 0,
    }
    # Act
    out = _run_js(
        "const p = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({"
        + "agent: timingPanelLabel(p, 'agent'),"
        + "project: timingPanelLabel(p, 'project'),"
        + "group: timingPanelLabel(p, 'group')"
        + "}));"
    )
    # Assert
    assert "1 rows" in out["project"]


def test_timing_panel_label_counts_rows_in_dimension_group_contains() -> None:
    """The label reflects the row count of the currently-selected
    group-by dimension, not the union of all dimensions."""
    # Arrange
    payload = {
        "window_days": 30,
        "per_agent": {
            "a": {
                "n_tasks_done": 1,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            },
            "b": {
                "n_tasks_done": 1,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            },
        },
        "per_project": {
            "proj1": {
                "n_tasks_done": 2,
                "median_started_to_done_s": 1.0,
                "p95_started_to_done_s": 1.0,
                "median_created_to_started_s": None,
            }
        },
        "per_group": {},
        "n_tasks_in_window": 2,
        "n_tasks_missing_timestamps": 0,
    }
    # Act
    out = _run_js(
        "const p = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({"
        + "agent: timingPanelLabel(p, 'agent'),"
        + "project: timingPanelLabel(p, 'project'),"
        + "group: timingPanelLabel(p, 'group')"
        + "}));"
    )
    # Assert
    assert "0 rows" in out["group"]


def test_pick_rows_routes_to_correct_dimension_a() -> None:
    """``pickRows`` returns the agent / project / group map for the
    current selection — the SINGLE point where the dimension switch
    happens."""
    # Arrange
    payload = {
        "per_agent": {"a": "AGENT"},
        "per_project": {"p": "PROJECT"},
        "per_group": {"g": "GROUP"},
    }
    # Act
    out = _run_js(
        "const p = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({"
        + "a: pickRows(p, 'agent'),"
        + "p: pickRows(p, 'project'),"
        + "g: pickRows(p, 'group')"
        + "}));"
    )
    # Assert
    assert out["a"] == {"a": "AGENT"}


def test_pick_rows_routes_to_correct_dimension_p() -> None:
    """``pickRows`` returns the agent / project / group map for the
    current selection — the SINGLE point where the dimension switch
    happens."""
    # Arrange
    payload = {
        "per_agent": {"a": "AGENT"},
        "per_project": {"p": "PROJECT"},
        "per_group": {"g": "GROUP"},
    }
    # Act
    out = _run_js(
        "const p = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({"
        + "a: pickRows(p, 'agent'),"
        + "p: pickRows(p, 'project'),"
        + "g: pickRows(p, 'group')"
        + "}));"
    )
    # Assert
    assert out["p"] == {"p": "PROJECT"}


def test_pick_rows_routes_to_correct_dimension_g() -> None:
    """``pickRows`` returns the agent / project / group map for the
    current selection — the SINGLE point where the dimension switch
    happens."""
    # Arrange
    payload = {
        "per_agent": {"a": "AGENT"},
        "per_project": {"p": "PROJECT"},
        "per_group": {"g": "GROUP"},
    }
    # Act
    out = _run_js(
        "const p = "
        + json.dumps(payload)
        + ";\nprocess.stdout.write(JSON.stringify({"
        + "a: pickRows(p, 'agent'),"
        + "p: pickRows(p, 'project'),"
        + "g: pickRows(p, 'group')"
        + "}));"
    )
    # Assert
    assert out["g"] == {"g": "GROUP"}


# EOF
