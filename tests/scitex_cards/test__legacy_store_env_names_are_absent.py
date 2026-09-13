"""The retired Cards-specific store selectors cannot return unnoticed."""

from __future__ import annotations

from pathlib import Path


def test_shipped_surfaces_do_not_name_retired_store_environment_variables() -> None:
    # Arrange
    root = Path(__file__).resolve().parents[2]
    retired = (
        "SCITEX_CARDS_" + "DB",
        "SCITEX_CARDS_" + "INBOX_DSN",
    )
    candidates = [root / "README.md", root / "pyproject.toml"]
    for tree in (root / "src", root / "docs", root / "tests"):
        candidates.extend(path for path in tree.rglob("*") if path.is_file())

    # Act
    offenders: list[str] = []
    for path in candidates:
        if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name in retired:
            if name in text:
                offenders.append(f"{path.relative_to(root)}: {name}")

    # Assert
    assert offenders == [], "retired store selectors remain:\n" + "\n".join(offenders)
