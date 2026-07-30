#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered config.json — user base + project override, knob resolution.

Real files in ``tmp_path``, no mocks: we point ``config_paths`` at concrete
temp files and assert the merge + the interval-resolution precedence.
"""

from __future__ import annotations

from scitex_cards import _config


def _paths(monkeypatch, *paths):
    monkeypatch.setattr(_config, "config_paths", lambda: list(paths))


def _layered_reminders(monkeypatch, tmp_path):
    """User base + a project layer that overrides ONLY interval_minutes."""
    import json

    user = tmp_path / "user.json"
    project = tmp_path / "project.json"
    user.write_text(
        json.dumps({"reminders": {"interval_minutes": 5, "escalate_after": 3}}),
        encoding="utf-8",
    )
    project.write_text(
        json.dumps({"reminders": {"interval_minutes": 1}}), encoding="utf-8"
    )
    _paths(monkeypatch, user, project)
    return _config.reminders_config()


# === layering: project overrides user, key-by-key ==========================


def test_absent_files_yield_empty_config(tmp_path, monkeypatch):
    # Arrange
    _paths(monkeypatch, tmp_path / "missing.json")
    # Act
    cfg = _config.load_config()
    # Assert
    assert cfg == {}


def test_absent_files_yield_empty_reminders_config(tmp_path, monkeypatch):
    # Arrange
    _paths(monkeypatch, tmp_path / "missing.json")
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
    bad = tmp_path / "bad.json"
    bad.write_text('{"reminders": [this is not valid json', encoding="utf-8")
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


# === JSON format; there is no legacy-sidecar import path any more =========


def test_a_json_config_is_read(tmp_path, monkeypatch):
    # Arrange
    import json

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"reminders": {"interval_minutes": 7}}), encoding="utf-8"
    )
    _paths(monkeypatch, cfg_path)
    # Act / Assert
    assert _config.reminders_config() == {"interval_minutes": 7}


def test_json_config_is_read_while_a_sibling_pre_json_file_is_ignored(
    tmp_path, monkeypatch
):
    # Arrange — a stray pre-JSON sidecar sits next to the real config; only
    # the JSON file is ever consulted.
    import json

    (tmp_path / "config.pre-json").write_text(
        "reminders:\n  interval_minutes: 99\n", encoding="utf-8"
    )
    (tmp_path / "config.json").write_text(
        json.dumps({"reminders": {"interval_minutes": 7}}), encoding="utf-8"
    )
    _paths(monkeypatch, tmp_path / "config.json")
    # Act / Assert
    assert _config.reminders_config() == {"interval_minutes": 7}


def test_a_lone_pre_json_sidecar_is_not_read(tmp_path, monkeypatch):
    # Arrange — no JSON config exists, only a pre-JSON sidecar. There is no
    # import path for it any more (the one-time sidecar migration was removed
    # once the fleet's stores moved off it) — the reader sees "no config".
    (tmp_path / "config.pre-json").write_text(
        "reminders:\n  interval_minutes: 3\n", encoding="utf-8"
    )
    _paths(monkeypatch, tmp_path / "config.json")
    # Act
    result = _config.reminders_config()
    # Assert — nothing read, nothing converted, the sidecar left untouched.
    assert result == {}
    assert not (tmp_path / "config.json").exists()
    assert (tmp_path / "config.pre-json").exists()


# EOF
