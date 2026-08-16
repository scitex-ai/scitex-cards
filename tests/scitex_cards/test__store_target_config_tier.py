#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The config tier of store-target resolution.

Guards the fix for the defect behind every store-target failure during the
2026-08-01 PostgreSQL cutover: the target could ONLY come from an environment
variable, so any caller that did not export one silently resolved to a private
SQLite file. Eight host-side writers did exactly that while the fleet was
believed migrated.

The tier sits BELOW the environment (so per-agent overrides still win and
nothing that worked before changes) and ABOVE the hardcoded default (so a host
states its store once instead of every caller remembering).

No fixture rewrites production internals here: these tests set real environment
variables, write real files, and restore both on teardown.
"""

from __future__ import annotations

import json
import os

import pytest

from scitex_cards._config import CONFIG_NAME
from scitex_cards._db import ENV_DB, resolve_db_path
from scitex_cards._store_target import (
    StoreTargetIsNotAPath,
    StoreTargetNotConfigured,
    resolve_store_target,
)

DSN = "postgresql://scitex_cards@127.0.0.1:5432/scitex_cards"
_MANAGED = (ENV_DB, "HOME", "SCITEX_DIR")


@pytest.fixture
def config_home(tmp_path):
    """A real HOME with an empty ``.scitex/cards/`` and no store env set."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    for name in (ENV_DB, "SCITEX_DIR"):
        os.environ.pop(name, None)
    os.environ["HOME"] = str(tmp_path)
    cards_dir = tmp_path / ".scitex" / "cards"
    cards_dir.mkdir(parents=True)
    # Outside any git repo, so the project-scope layer contributes nothing.
    os.chdir(tmp_path)

    yield cards_dir

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _write_config(cards_dir, payload):
    (cards_dir / CONFIG_NAME).write_text(json.dumps(payload), encoding="utf-8")


class TestConfigSuppliesTheTarget:
    def test_target_from_config_is_returned_verbatim(self, config_home):
        # Arrange
        _write_config(config_home, {"store": {"target": DSN}})

        # Act
        resolved = resolve_store_target(None)

        # Assert
        assert resolved == DSN

    def test_absent_config_falls_through_past_the_tier(self, config_home):
        """No config means this tier contributes nothing -- it does not ERROR.

        The assertion moved on 2026-08-13 and the SUBJECT did not. It used to
        be ``resolved.endswith("cards.db")``, because falling past this tier
        landed on the zero-config SQLite default. That tier is abolished, so
        falling past this one now lands on the refusal -- which is still the
        NEXT tier answering, not this one failing. What is being pinned either
        way is that an absent config is silent.
        """
        # Arrange
        for stale in config_home.glob(CONFIG_NAME):
            stale.unlink()

        # Act
        # Assert
        with pytest.raises(StoreTargetNotConfigured):
            resolve_store_target(None)


class TestEnvironmentStillWins:
    def test_env_beats_config(self, config_home):
        # Arrange
        _write_config(config_home, {"store": {"target": DSN}})
        os.environ[ENV_DB] = "/tmp/explicit-from-env.db"

        # Act
        resolved = resolve_store_target(None)

        # Assert
        assert resolved == "/tmp/explicit-from-env.db"

    def test_explicit_argument_beats_everything(self, config_home):
        # Arrange
        _write_config(config_home, {"store": {"target": DSN}})
        os.environ[ENV_DB] = "/tmp/from-env.db"

        # Act
        resolved = resolve_store_target("/tmp/explicit-arg.db")

        # Assert
        assert resolved == "/tmp/explicit-arg.db"


class TestFailSoft:
    """A bad config contributes nothing. It must never take the board down.

    THE DISTINCTION THESE TESTS DEFEND, restated after the 2026-08-13
    abolition: "unusable config" and "no config" must be INDISTINGUISHABLE
    downstream. A malformed JSON file must not produce a JSON error, a
    non-string target must not produce a TypeError -- each one falls THROUGH,
    and whatever the next tier says is what the caller hears.

    What the next tier says has changed, and that is the only change here. It
    used to invent ``~/.scitex/cards/cards.db``; it now refuses. So the
    expected outcome below is ``StoreTargetNotConfigured`` -- the SAME outcome
    an empty ``.scitex/cards/`` produces, which is exactly the equivalence
    being pinned. A config error surfacing as anything OTHER than this would
    still be the defect these tests were written for.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            {"store": "not-a-mapping"},
            {"store": {}},
            {"store": {"target": ""}},
            {"store": {"target": "   "}},
            {"store": {"target": 42}},
            {},
        ],
        ids=[
            "section-not-dict",
            "no-target",
            "empty",
            "whitespace",
            "not-str",
            "empty-file",
        ],
    )
    def test_unusable_config_falls_through(self, config_home, payload):
        # Arrange
        _write_config(config_home, payload)

        # Act
        # Assert — falls through to the NEXT tier's answer, whatever it
        # is, and never surfaces a config-shaped error of its own.
        with pytest.raises(StoreTargetNotConfigured):
            resolve_store_target(None)

    def test_malformed_json_falls_through(self, config_home):
        # Arrange
        (config_home / CONFIG_NAME).write_text("{not json", encoding="utf-8")

        # Act
        # Assert — a JSONDecodeError reaching the caller would be the
        # defect; the next tier's refusal is the correct pass-through.
        with pytest.raises(StoreTargetNotConfigured):
            resolve_store_target(None)


class TestPathOnlyCallerRefusesAConfiguredDsn:
    """``resolve_db_path`` is typed ``-> Path``, so a configured DSN must REFUSE.

    Coercing it would yield a relative path and silently create a second, empty
    store -- the exact failure the refusal exists to prevent. Adding a tier must
    not open a new way in.
    """

    def test_configured_dsn_raises_rather_than_coercing(self, config_home):
        # Arrange
        _write_config(config_home, {"store": {"target": DSN}})
        message = ""

        # Act
        try:
            resolve_db_path(None)
        except StoreTargetIsNotAPath as exc:
            message = str(exc)

        # Assert
        assert "not a file" in message

    def test_configured_path_is_still_returned(self, config_home):
        # Arrange
        _write_config(config_home, {"store": {"target": "/tmp/configured.db"}})

        # Act
        resolved = resolve_db_path(None)

        # Assert
        assert str(resolved) == "/tmp/configured.db"
