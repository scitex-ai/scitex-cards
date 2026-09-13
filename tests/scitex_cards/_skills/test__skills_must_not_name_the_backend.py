#!/usr/bin/env python3
"""The environment skill states the canonical shared-store contract."""

from __future__ import annotations

from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]


def test_environment_skill_names_the_postgresql_primitive() -> None:
    # Arrange
    path = _skills_root() / "20_env-vars.md"
    # Act
    text = path.read_text(encoding="utf-8")
    observed = all(
        phrase in text
        for phrase in (
            "SCITEX_STORE_DSN",
            "Shared PostgreSQL store DSN",
            "port 55432",
            "no SQLite fallback",
        )
    )
    # Assert
    assert observed


# EOF
