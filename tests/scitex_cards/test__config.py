#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered config.yaml — user base + project override, knob resolution.

Real files in ``tmp_path``, no mocks: we point ``config_paths`` at concrete
temp files and assert the merge + the interval-resolution precedence.
"""

from __future__ import annotations

from scitex_cards import _config


def _paths(monkeypatch, *paths):
    monkeypatch.setattr(_config, "config_paths", lambda: list(paths))


def _layered_reminders(monkeypatch, tmp_path):
    """User base + a project layer that overrides ONLY interval_minutes."""
    user = tmp_path / "user.yaml"
    project = tmp_path / "project.yaml"
    user.write_text(
        "reminders:\n  interval_minutes: 5\n  escalate_after: 3\n",
        encoding="utf-8",
    )
    project.write_text("reminders:\n  interval_minutes: 1\n", encoding="utf-8")
    _paths(monkeypatch, user, project)
    return _config.reminders_config()


# === layering: project overrides user, key-by-key ==========================


def test_absent_files_yield_empty_config(tmp_path, monkeypatch):
    # Arrange
    _paths(monkeypatch, tmp_path / "missing.yaml")
    # Act
    cfg = _config.load_config()
    # Assert
    assert cfg == {}


def test_absent_files_yield_empty_reminders_config(tmp_path, monkeypatch):
    # Arrange
    _paths(monkeypatch, tmp_path / "missing.yaml")
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {}


def test_project_layer_overrides_the_user_value(tmp_path, monkeypatch):
    # Arrange
    # Act
    cfg = _layered_reminders(monkeypatch, tmp_path)
    # Assert — project wins on the key it declares.
    assert cfg["interval_minutes"] == 1


def test_project_layer_inherits_untouched_user_keys(tmp_path, monkeypatch):
    # Arrange
    # Act
    cfg = _layered_reminders(monkeypatch, tmp_path)
    # Assert — escalate_after is inherited, not wiped by the partial override.
    assert cfg["escalate_after"] == 3


def test_malformed_file_is_ignored(tmp_path, monkeypatch):
    # Arrange
    bad = tmp_path / "bad.yaml"
    bad.write_text("reminders: [this is not a mapping\n", encoding="utf-8")
    _paths(monkeypatch, bad)
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {}


# === interval resolution: card > config > default =========================


def test_default_interval_when_nothing_set(tmp_path, monkeypatch):
    # Arrange
    _paths(monkeypatch)
    # Act
    interval = _config.resolve_interval_minutes(None)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


def test_config_interval_used_when_no_card_override(tmp_path, monkeypatch):
    # Arrange
    cfg = {"interval_minutes": 2}
    # Act
    interval = _config.resolve_interval_minutes({"id": "c1"}, cfg)
    # Assert
    assert interval == 2.0


def test_card_override_beats_config(tmp_path, monkeypatch):
    # Arrange
    cfg = {"interval_minutes": 5}
    card = {"id": "c1", "reminder_interval_minutes": 1}
    # Act
    interval = _config.resolve_interval_minutes(card, cfg)
    # Assert
    assert interval == 1.0


def test_non_positive_values_fall_through(tmp_path, monkeypatch):
    # Arrange
    cfg = {"interval_minutes": 0}  # invalid → ignored
    card = {"id": "c1", "reminder_interval_minutes": -3}  # invalid → ignored
    # Act
    interval = _config.resolve_interval_minutes(card, cfg)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


def test_bool_is_not_a_valid_interval_number(tmp_path, monkeypatch):
    # Arrange — bool is an int subclass; it must NOT be accepted.
    cfg = {"interval_minutes": True}
    # Act
    interval = _config.resolve_interval_minutes(None, cfg)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


# EOF
