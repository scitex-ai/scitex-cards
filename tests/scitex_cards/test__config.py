#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered config.json — user base + project override, knob resolution.

REAL FILES AT THE PATHS DISCOVERY ACTUALLY READS. This file used to say "no
mocks" while stubbing ``config_paths`` — the files were real, the DISCOVERY
was not, so the suite could not have noticed if ``_user_root()``,
``CONFIG_NAME``, or the git-root walk changed. The layering is now arranged on
disk:

    user     $SCITEX_DIR/cards/config.json
    project  <a real git root>/.scitex/cards/config.json

so the merge, the precedence AND the discovery of both layers are under test
together.
"""

from __future__ import annotations

import pytest

from scitex_cards import _config


@pytest.fixture
def config_layers(env, tmp_path):
    """Arrange a real user root and a real repo; return both config paths.

    The project layer requires a git root, because ``config_paths`` only
    appends it when ``_find_git_root`` finds one — so the fixture creates a
    real ``.git`` directory and moves cwd into it. That walk is part of the
    behaviour under test and is exactly what a stub used to skip.
    """
    user_root = tmp_path / "scitex-dir"
    (user_root / "cards").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".scitex" / "cards").mkdir(parents=True)
    env.set("SCITEX_DIR", str(user_root))
    env.chdir(repo)
    return user_root / "cards" / "config.json", repo / ".scitex" / "cards" / "config.json"


def _layered_reminders(config_layers):
    """User base + a project layer that overrides ONLY interval_minutes."""
    import json

    user, project = config_layers
    user.write_text(
        json.dumps({"reminders": {"interval_minutes": 5, "escalate_after": 3}}),
        encoding="utf-8",
    )
    project.write_text(
        json.dumps({"reminders": {"interval_minutes": 1}}), encoding="utf-8"
    )
    return _config.reminders_config()


# === layering: project overrides user, key-by-key ==========================


def test_absent_files_yield_empty_config(config_layers):
    # Arrange — both layers are real discoverable paths holding no file
    # Act
    cfg = _config.load_config()
    # Assert
    assert cfg == {}


def test_absent_files_yield_empty_reminders_config(config_layers):
    # Arrange — both layers are real discoverable paths holding no file
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {}


def test_project_layer_overrides_the_user_value(config_layers):
    # Arrange
    # Act
    cfg = _layered_reminders(config_layers)
    # Assert — project wins on the key it declares.
    assert cfg["interval_minutes"] == 1


def test_project_layer_inherits_untouched_user_keys(config_layers):
    # Arrange
    # Act
    cfg = _layered_reminders(config_layers)
    # Assert — escalate_after is inherited, not wiped by the partial override.
    assert cfg["escalate_after"] == 3


def test_malformed_file_is_ignored(config_layers):
    # Arrange
    config_layers[0].write_text(
        '{"reminders": [this is not valid json', encoding="utf-8"
    )
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {}


# === interval resolution: card > config > default =========================


def test_default_interval_when_nothing_set(config_layers):
    # Arrange — no config file exists at either discoverable layer
    # Act
    interval = _config.resolve_interval_minutes(None)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


def test_config_interval_used_when_no_card_override(tmp_path, env):
    # Arrange
    cfg = {"interval_minutes": 2}
    # Act
    interval = _config.resolve_interval_minutes({"id": "c1"}, cfg)
    # Assert
    assert interval == 2.0


def test_card_override_beats_config(tmp_path, env):
    # Arrange
    cfg = {"interval_minutes": 5}
    card = {"id": "c1", "reminder_interval_minutes": 1}
    # Act
    interval = _config.resolve_interval_minutes(card, cfg)
    # Assert
    assert interval == 1.0


def test_non_positive_values_fall_through(tmp_path, env):
    # Arrange
    cfg = {"interval_minutes": 0}  # invalid → ignored
    card = {"id": "c1", "reminder_interval_minutes": -3}  # invalid → ignored
    # Act
    interval = _config.resolve_interval_minutes(card, cfg)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


def test_bool_is_not_a_valid_interval_number(tmp_path, env):
    # Arrange — bool is an int subclass; it must NOT be accepted.
    cfg = {"interval_minutes": True}
    # Act
    interval = _config.resolve_interval_minutes(None, cfg)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


# === JSON format; there is no legacy-sidecar import path any more =========


def test_a_json_config_is_read(config_layers):
    # Arrange
    import json

    config_layers[0].write_text(
        json.dumps({"reminders": {"interval_minutes": 7}}), encoding="utf-8"
    )
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {"interval_minutes": 7}


def test_json_config_is_read_while_a_sibling_pre_json_file_is_ignored(
    config_layers,
):
    # Arrange — a stray pre-JSON sidecar sits next to the real config; only
    # the JSON file is ever consulted.
    import json

    user = config_layers[0]
    user.with_name("config.pre-json").write_text(
        "reminders:\n  interval_minutes: 99\n", encoding="utf-8"
    )
    user.write_text(
        json.dumps({"reminders": {"interval_minutes": 7}}), encoding="utf-8"
    )
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {"interval_minutes": 7}


def _read_with_only_a_pre_json_sidecar(config_layers):
    """No JSON config, only a pre-JSON sidecar beside it.

    There is no import path for the sidecar any more — the one-time migration
    was removed once the fleet's stores moved off it — so the reader must see
    "no config" and must not convert anything.
    """
    user = config_layers[0]
    user.with_name("config.pre-json").write_text(
        "reminders:\n  interval_minutes: 3\n", encoding="utf-8"
    )
    return _config.reminders_config(), user


def test_a_lone_pre_json_sidecar_is_not_read(config_layers):
    # Arrange
    # Act
    result, _user = _read_with_only_a_pre_json_sidecar(config_layers)
    # Assert — nothing read from the sidecar.
    assert result == {}


def test_a_lone_pre_json_sidecar_is_not_converted_into_a_json_config(
    config_layers,
):
    # Arrange
    # Act
    _result, user = _read_with_only_a_pre_json_sidecar(config_layers)
    # Assert — the reader did not write a config.json as a side effect.
    assert not user.exists()


def test_a_lone_pre_json_sidecar_is_left_on_disk_untouched(config_layers):
    # Arrange
    # Act
    _result, user = _read_with_only_a_pre_json_sidecar(config_layers)
    # Assert — the sidecar is neither consumed nor deleted.
    assert user.with_name("config.pre-json").exists()


# EOF
