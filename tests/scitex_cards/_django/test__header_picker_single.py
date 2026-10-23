"""Single project picker lives in the app header (operator 2026-10-23).

Hard rule, not a suggestion: exactly ONE project picker per page, and it
sits in the shared SDK AppHeader row — never a leaf-local selector in
Details. The DM page already follows this (sidebar slot + header
suppressed); this pins the board half:

1. the shared header still mounts the canonical SDK picker;
2. the Details Filters section owns NO project row (no f-project);
3. the projector plumbing follows the header: the boot STATE literal
   seeds filters.project from the server-rendered current project, and
   populateFilters no longer maintains a project <select>.

Mocks-free: read the tracked templates + inline script.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATES = _REPO_ROOT / "src" / "scitex_cards" / "_django" / "templates" / "scitex_cards"


def _read(name: str) -> str:
    path = _TEMPLATES / name
    assert path.is_file(), f"expected template missing: {path}"
    return path.read_text(encoding="utf-8")


def test_single_project_picker_owned_by_the_header() -> None:
    """One header picker, zero Details project rows, boot seeded from server."""
    # Arrange
    # Act
    header = _read("_page_header.html")
    pane = _read("_board_pane.html")
    foot = _read("_board_foot.html")
    # Assert
    assert (
        "_project_picker.html" in header
        and 'id="f-project"' not in pane
        and "for=\"f-project\"" not in pane
        and "project: \"{{ cards_current_project_id" in foot
        and 'set("f-project"' not in foot
    ), "board must carry exactly one project picker (header SDK slot); Details owns no project row"
