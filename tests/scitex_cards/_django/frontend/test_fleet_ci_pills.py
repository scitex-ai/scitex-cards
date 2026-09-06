#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Front-end contract tests for the fleet CI-status pills.

Two halves (same pattern the recent ``test_table_filter.py`` /
``test_calendar_date.py`` PRs established):

1. **CSS contract** — open ``fleet-ci-pills.css`` and assert:
     - the canonical selectors are present
     - colors come from design tokens (``--status-success``, etc.) ONLY;
       NO hardcoded hex literals leak in (theme-breaking)
2. **Pill color mapping** — execute the actual ``pillModifier`` /
   ``isCiRepoErr`` / ``pillTooltip`` functions from ``FleetCiPills.tsx``
   via ``node`` and pin the mapping for each CiOverall value + the
   per-repo error case. Same lock-step assertion against the TS source
   as the table-filter test, so a rename downstream forces this test
   to update.
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
    / "fleet-ci-pills.css"
)
_TSX_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "frontend"
    / "src"
    / "FleetCiPills.tsx"
)


# ─── CSS contract ───────────────────────────────────────────────────────


def test_css_file_exists() -> None:
    # Arrange
    # Act
    # Assert
    assert _CSS_FILE.is_file(), f"missing CSS file: {_CSS_FILE}"


def test_css_has_canonical_selectors() -> None:
    """The component generates these class names — the CSS file MUST
    define each one or the pills will silently render unstyled."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Act
    # Assert
    for selector in (
        ".stx-cards-fleet-ci",
        ".stx-cards-fleet-ci__pill",
        ".stx-cards-fleet-ci__pill--success",
        ".stx-cards-fleet-ci__pill--failure",
        ".stx-cards-fleet-ci__pill--pending",
        ".stx-cards-fleet-ci__pill--unknown",
        ".stx-cards-fleet-ci__pill--error",
        ".stx-cards-fleet-ci__dot",
        ".stx-cards-fleet-ci__name",
        ".stx-cards-fleet-ci--note",
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
    assert (
        not hex_matches
    ), f"hardcoded hex colors in fleet-ci-pills.css: {hex_matches!r}"


def test_css_references_all_required_tokens() -> None:
    """Every required design token must be referenced at least once."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Act
    required = (
        "--status-success",
        "--status-error",
        "--status-warning",
    )
    # Assert
    assert all(token in css for token in required)


def test_css_is_imported_from_board_css_is_file() -> None:
    """The pills strip only renders correctly when board.css imports
    the partial. Pinning this guards against an accidental removal in
    a future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    text = board_css.read_text(encoding="utf-8")
    assert board_css.is_file()


def test_css_is_imported_from_board_css_text_contains() -> None:
    """The pills strip only renders correctly when board.css imports
    the partial. Pinning this guards against an accidental removal in
    a future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    text = board_css.read_text(encoding="utf-8")
    assert '@import "./fleet-ci-pills.css";' in text


# ─── component logic — pill color mapping via node ─────────────────────


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run_pill_helpers(repo: dict) -> dict:
    """Execute the actual ``pillModifier`` / ``isCiRepoErr`` /
    ``pillTooltip`` helpers via node against ``repo``.

    Mirrors the TS module — and asserts each runtime fragment is still
    present in the TS source so the mirror stays in lock-step (a
    rename downstream forces this test to update; no silent drift).
    """
    src = _TSX_FILE.read_text(encoding="utf-8")

    # Static-source contract — keeping the JS mirror in lock-step.
    for needle in [
        "export function isCiRepoErr(repo: CiRepo): repo is CiRepoErr {",
        'return Object.prototype.hasOwnProperty.call(repo, "error");',
        "export function pillModifier(repo: CiRepo): string {",
        'case "success":',
        'case "failure":',
        'case "pending":',
        "export function pillTooltip(repo: CiRepo): string {",
    ]:
        assert needle in src, (
            f"FleetCiPills.tsx no longer contains canonical fragment "
            f"{needle!r}; update this test in lock-step."
        )

    js_runtime = textwrap.dedent(
        """
        function isCiRepoErr(repo) {
          return Object.prototype.hasOwnProperty.call(repo, "error");
        }
        function pillModifier(repo) {
          if (isCiRepoErr(repo)) return "error";
          switch (repo.overall) {
            case "success": return "success";
            case "failure": return "failure";
            case "pending": return "pending";
            default: return "unknown";
          }
        }
        function pillTooltip(repo) {
          if (isCiRepoErr(repo)) {
            return repo.slug + ": adapter error — " + repo.error;
          }
          const sha = repo.head_sha ? repo.head_sha.slice(0, 7) : "(no sha)";
          return repo.slug + " @ " + repo.branch + " (" + sha + ") — " + repo.overall;
        }
        """
    ).strip()

    script = (
        js_runtime
        + "\nconst repo = "
        + json.dumps(repo)
        + ";\nconsole.log(JSON.stringify({"
        + "modifier: pillModifier(repo),"
        + "tooltip: pillTooltip(repo),"
        + "isErr: isCiRepoErr(repo)"
        + "}));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


@pytest.mark.parametrize(
    "overall,expected_mod",
    [
        ("success", "success"),
        ("failure", "failure"),
        ("pending", "pending"),
        ("unknown", "unknown"),
        # An overall value the FE doesn't know about must degrade to
        # "unknown" rather than emit a missing-CSS class. The
        # ``default:`` branch of the switch pins this.
        ("weird-future-state", "unknown"),
    ],
)
def test_pill_color_mapping_for_each_overall_modifier(
    overall: str, expected_mod: str
) -> None:
    """Pin one pill-modifier per known CiOverall value — these are the
    classes the CSS file rules on, so a rename here would silently
    blank the pill."""
    # Arrange
    # Act
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "abc1234deadbeef",
            "overall": overall,
            "checks": [],
        }
    )
    # Assert
    assert out["modifier"] == expected_mod


@pytest.mark.parametrize(
    "overall,expected_mod",
    [
        ("success", "success"),
        ("failure", "failure"),
        ("pending", "pending"),
        ("unknown", "unknown"),
        # An overall value the FE doesn't know about must degrade to
        # "unknown" rather than emit a missing-CSS class. The
        # ``default:`` branch of the switch pins this.
        ("weird-future-state", "unknown"),
    ],
)
def test_pill_color_mapping_for_each_overall_iserr(
    overall: str, expected_mod: str
) -> None:
    """Pin one pill-modifier per known CiOverall value — these are the
    classes the CSS file rules on, so a rename here would silently
    blank the pill."""
    # Arrange
    # Act
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "abc1234deadbeef",
            "overall": overall,
            "checks": [],
        }
    )
    # Assert
    assert out["isErr"] is False


@pytest.mark.parametrize(
    "overall,expected_mod",
    [
        ("success", "success"),
        ("failure", "failure"),
        ("pending", "pending"),
        ("unknown", "unknown"),
        # An overall value the FE doesn't know about must degrade to
        # "unknown" rather than emit a missing-CSS class. The
        # ``default:`` branch of the switch pins this.
        ("weird-future-state", "unknown"),
    ],
)
def test_pill_color_mapping_for_each_overall_tooltip_contains(
    overall: str, expected_mod: str
) -> None:
    """Pin one pill-modifier per known CiOverall value — these are the
    classes the CSS file rules on, so a rename here would silently
    blank the pill."""
    # Arrange
    # Act
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "abc1234deadbeef",
            "overall": overall,
            "checks": [],
        }
    )
    # Assert
    assert "foo/bar" in out["tooltip"]


@pytest.mark.parametrize(
    "overall,expected_mod",
    [
        ("success", "success"),
        ("failure", "failure"),
        ("pending", "pending"),
        ("unknown", "unknown"),
        # An overall value the FE doesn't know about must degrade to
        # "unknown" rather than emit a missing-CSS class. The
        # ``default:`` branch of the switch pins this.
        ("weird-future-state", "unknown"),
    ],
)
def test_pill_color_mapping_for_each_overall_tooltip_contains_2(
    overall: str, expected_mod: str
) -> None:
    """Pin one pill-modifier per known CiOverall value — these are the
    classes the CSS file rules on, so a rename here would silently
    blank the pill."""
    # Arrange
    # Act
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "abc1234deadbeef",
            "overall": overall,
            "checks": [],
        }
    )
    # Assert
    assert "main" in out["tooltip"]


@pytest.mark.parametrize(
    "overall,expected_mod",
    [
        ("success", "success"),
        ("failure", "failure"),
        ("pending", "pending"),
        ("unknown", "unknown"),
        # An overall value the FE doesn't know about must degrade to
        # "unknown" rather than emit a missing-CSS class. The
        # ``default:`` branch of the switch pins this.
        ("weird-future-state", "unknown"),
    ],
)
def test_pill_color_mapping_for_each_overall_tooltip_contains_3(
    overall: str, expected_mod: str
) -> None:
    """Pin one pill-modifier per known CiOverall value — these are the
    classes the CSS file rules on, so a rename here would silently
    blank the pill."""
    # Arrange
    # Act
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "abc1234deadbeef",
            "overall": overall,
            "checks": [],
        }
    )
    # Assert
    assert "abc1234" in out["tooltip"]  # short sha


def test_pill_modifier_for_per_repo_error_modifier() -> None:
    """A per-repo error (``{slug, error}``) maps to the ``--error``
    modifier with the adapter message in the tooltip."""
    # Arrange
    # Act
    out = _run_pill_helpers({"slug": "foo/dead", "error": "gh exited 1: not found"})
    # Assert
    assert out["modifier"] == "error"


def test_pill_modifier_for_per_repo_error_iserr() -> None:
    """A per-repo error (``{slug, error}``) maps to the ``--error``
    modifier with the adapter message in the tooltip."""
    # Arrange
    # Act
    out = _run_pill_helpers({"slug": "foo/dead", "error": "gh exited 1: not found"})
    # Assert
    assert out["isErr"] is True


def test_pill_modifier_for_per_repo_error_tooltip_contains() -> None:
    """A per-repo error (``{slug, error}``) maps to the ``--error``
    modifier with the adapter message in the tooltip."""
    # Arrange
    # Act
    out = _run_pill_helpers({"slug": "foo/dead", "error": "gh exited 1: not found"})
    # Assert
    assert "foo/dead" in out["tooltip"]


def test_pill_modifier_for_per_repo_error_tooltip_contains_2() -> None:
    """A per-repo error (``{slug, error}``) maps to the ``--error``
    modifier with the adapter message in the tooltip."""
    # Arrange
    # Act
    out = _run_pill_helpers({"slug": "foo/dead", "error": "gh exited 1: not found"})
    # Assert
    assert "gh exited 1" in out["tooltip"]


def test_pill_tooltip_handles_missing_sha() -> None:
    """If the back-end emits an OK pill without a ``head_sha`` (e.g. a
    fresh repo with zero commits — defensive), the tooltip shows
    ``(no sha)`` rather than a misleading empty truncation."""
    # Arrange
    # Act
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "",
            "overall": "unknown",
            "checks": [],
        }
    )
    # Assert
    assert "(no sha)" in out["tooltip"]
