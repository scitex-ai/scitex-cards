#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered config.json — user base + project override, knob resolution.

REAL files in real locations, and no patching at all. `config_paths()` builds
its list from two things a test can genuinely control:

    the user layer     ``_user_root()`` honours ``$SCITEX_DIR``
    the project layer  ``_find_git_root(Path.cwd())`` walks up for a ``.git``

so the ``env`` fixture's ``set`` + ``chdir`` are sufficient. The previous
version replaced ``config_paths`` with a lambda, which is a mock (PA-306) and
also weaker: a bug INSIDE ``config_paths`` — the wrong filename, the layers in
the wrong order, the project entry omitted — could not have failed a single
test here, because the function under test was the one being replaced. Now it
runs.
"""

from __future__ import annotations

import json

from scitex_cards import _config
from scitex_cards._paths import PKG_SHORT


def _install(env, tmp_path, *, user=None, project=None):
    """Write REAL config files where ``config_paths()`` actually looks.

    Returns ``(user_dir, project_dir)``. A layer whose argument is ``None`` is
    simply not written, which is how "absent file" is expressed — the directory
    still exists, so the reader really does look and really does find nothing.
    """
    user_root = tmp_path / "userscope"
    user_dir = user_root / PKG_SHORT
    user_dir.mkdir(parents=True, exist_ok=True)
    env.set("SCITEX_DIR", str(user_root))
    if user is not None:
        (user_dir / "config.json").write_text(user, encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    project_dir = repo / ".scitex" / "cards"
    project_dir.mkdir(parents=True, exist_ok=True)
    if project is not None:
        (project_dir / "config.json").write_text(project, encoding="utf-8")
    env.chdir(repo)
    return user_dir, project_dir


def _layered_reminders(env, tmp_path):
    """User base + a project layer that overrides ONLY interval_minutes."""
    _install(
        env,
        tmp_path,
        user=json.dumps({"reminders": {"interval_minutes": 5, "escalate_after": 3}}),
        project=json.dumps({"reminders": {"interval_minutes": 1}}),
    )
    return _config.reminders_config()


# === layering: project overrides user, key-by-key ==========================


def test_absent_files_yield_empty_config(tmp_path, env):
    # Arrange
    _install(env, tmp_path)
    # Act
    cfg = _config.load_config()
    # Assert
    assert cfg == {}


def test_absent_files_yield_empty_reminders_config(tmp_path, env):
    # Arrange
    _install(env, tmp_path)
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {}


def test_project_layer_overrides_the_user_value(tmp_path, env):
    # Arrange
    # Act
    cfg = _layered_reminders(env, tmp_path)
    # Assert — project wins on the key it declares.
    assert cfg["interval_minutes"] == 1


def test_project_layer_inherits_untouched_user_keys(tmp_path, env):
    # Arrange
    # Act
    cfg = _layered_reminders(env, tmp_path)
    # Assert — escalate_after is inherited, not wiped by the partial override.
    assert cfg["escalate_after"] == 3


def test_malformed_file_is_ignored(tmp_path, env):
    # Arrange
    _install(env, tmp_path, user='{"reminders": [this is not valid json')
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {}


# === interval resolution: card > config > default =========================


def test_default_interval_when_nothing_set(tmp_path, env):
    # Arrange
    _install(env, tmp_path)
    # Act
    interval = _config.resolve_interval_minutes(None)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


def test_config_interval_used_when_no_card_override():
    # Arrange
    cfg = {"interval_minutes": 2}
    # Act
    interval = _config.resolve_interval_minutes({"id": "c1"}, cfg)
    # Assert
    assert interval == 2.0


def test_card_override_beats_config():
    # Arrange
    cfg = {"interval_minutes": 5}
    card = {"id": "c1", "reminder_interval_minutes": 1}
    # Act
    interval = _config.resolve_interval_minutes(card, cfg)
    # Assert
    assert interval == 1.0


def test_non_positive_values_fall_through():
    # Arrange
    cfg = {"interval_minutes": 0}  # invalid → ignored
    card = {"id": "c1", "reminder_interval_minutes": -3}  # invalid → ignored
    # Act
    interval = _config.resolve_interval_minutes(card, cfg)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


def test_bool_is_not_a_valid_interval_number():
    # Arrange — bool is an int subclass; it must NOT be accepted.
    cfg = {"interval_minutes": True}
    # Act
    interval = _config.resolve_interval_minutes(None, cfg)
    # Assert
    assert interval == _config.DEFAULT_INTERVAL_MINUTES


# === JSON format; there is no legacy-sidecar import path any more =========


def test_a_json_config_is_read(tmp_path, env):
    # Arrange
    _install(env, tmp_path, user=json.dumps({"reminders": {"interval_minutes": 7}}))
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {"interval_minutes": 7}


def test_json_config_is_read_while_a_sibling_pre_json_file_is_ignored(tmp_path, env):
    # Arrange — a stray pre-JSON sidecar sits next to the real config; only
    # the JSON file is ever consulted.
    user_dir, _ = _install(
        env, tmp_path, user=json.dumps({"reminders": {"interval_minutes": 7}})
    )
    (user_dir / "config.pre-json").write_text(
        "reminders:\n  interval_minutes: 99\n", encoding="utf-8"
    )
    # Act
    cfg = _config.reminders_config()
    # Assert
    assert cfg == {"interval_minutes": 7}


def test_a_lone_pre_json_sidecar_yields_no_config(tmp_path, env):
    # Arrange — no JSON config exists, only a pre-JSON sidecar. There is no
    # import path for it any more (the one-time sidecar migration was removed
    # once the fleet's stores moved off it) — the reader sees "no config".
    user_dir, _ = _install(env, tmp_path)
    (user_dir / "config.pre-json").write_text(
        "reminders:\n  interval_minutes: 3\n", encoding="utf-8"
    )
    # Act
    result = _config.reminders_config()
    # Assert
    assert result == {}


def test_a_lone_pre_json_sidecar_is_not_converted(tmp_path, env):
    # Arrange
    user_dir, _ = _install(env, tmp_path)
    (user_dir / "config.pre-json").write_text(
        "reminders:\n  interval_minutes: 3\n", encoding="utf-8"
    )
    # Act
    _config.reminders_config()
    # Assert — no JSON file is written on its behalf.
    assert not (user_dir / "config.json").exists()


def test_a_lone_pre_json_sidecar_is_left_untouched(tmp_path, env):
    # Arrange
    user_dir, _ = _install(env, tmp_path)
    sidecar = user_dir / "config.pre-json"
    sidecar.write_text("reminders:\n  interval_minutes: 3\n", encoding="utf-8")
    # Act
    _config.reminders_config()
    # Assert — the sidecar is still there, unread and unconsumed.
    assert sidecar.exists()


# EOF
