#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the ``SCITEX_CARDS_*`` -> ``SCITEX_TODO_*`` env mirror.

The module shipped with ZERO tests, which is a large part of why the incident
below reached the live fleet: there was nothing that could have gone red.

INCIDENT 2026-07-19. ``mirror_env`` overwrote ``SCITEX_TODO_<X>`` with
``SCITEX_CARDS_<X>`` unconditionally. On the live fleet the new-prefix values
were an unexpanded ``${SCITEX_CARDS_AGENT_ID}`` literal and a store path
pointing at an empty file, while the old-prefix values were correct. Both
overrides applied, so the task store silently FORKED and every card written
carried a literal placeholder as its author.

MUTATION-CHECKED. Every test marked `# MUTATION:` below was run against the
pre-fix module (a plain `environ[old] = environ[new]`) and observed to FAIL.
A test that stays green under the buggy code is not a regression test, and
adding one here would be worse than adding nothing — it would certify the
absence of a bug that is present.
"""

from __future__ import annotations

import logging

import pytest

from scitex_cards._env_compat import mirror_env

CANONICAL = "SCITEX_TODO_DB"
RENAMED = "SCITEX_CARDS_DB"


@pytest.fixture
def populated_store(tmp_path):
    """A store file that EXISTS and is non-empty — the thing worth protecting."""
    p = tmp_path / "todo" / "cards.db"
    p.parent.mkdir(parents=True)
    p.write_text("SQLite format 3\x00 (not really, just non-empty)\n")
    return p


# --------------------------------------------------------------------------- #
# REFUSAL 1 — an unexpanded placeholder is a failed expansion, not a value.   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "placeholder",
    [
        "${SCITEX_CARDS_AGENT_ID}",  # the exact literal seen on the live fleet
        "$SCITEX_CARDS_AGENT_ID",
        "{{ agent_id }}",
        "  ${SPACED}  ",
    ],
)
def test_an_unexpanded_placeholder_never_overrides_a_real_value(placeholder):
    """MUTATION: fails pre-fix — the literal was mirrored onto the good value.

    This is the half of the bug that corrupted `created_by` on every card an
    affected agent wrote.
    """
    # Arrange
    env = {"SCITEX_TODO_AGENT_ID": "scitex-cards", "SCITEX_CARDS_AGENT_ID": placeholder}

    # Act
    mirror_env(env)

    # Assert
    assert env["SCITEX_TODO_AGENT_ID"] == "scitex-cards"


def _mirror_a_refused_placeholder(caplog):
    """Mirror an env whose new-prefix AGENT_ID is an unexpanded placeholder."""
    env = {"SCITEX_TODO_AGENT_ID": "scitex-cards", "SCITEX_CARDS_AGENT_ID": "${NOPE}"}
    with caplog.at_level(logging.ERROR):
        mirror_env(env)
    return env


def test_a_refused_placeholder_is_reported_at_error_level(caplog):
    """A silent refusal would just relocate the mystery, not remove it."""
    # Arrange
    # Act
    _mirror_a_refused_placeholder(caplog)

    # Assert
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_a_refused_placeholder_is_named_as_unexpanded_in_the_log(caplog):
    """The log must say WHY, or the reader cannot fix the template."""
    # Arrange
    # Act
    _mirror_a_refused_placeholder(caplog)

    # Assert
    assert "UNEXPANDED" in caplog.text


def test_a_value_merely_containing_a_dollar_is_still_honoured(tmp_path):
    """Guards the OPPOSITE error: over-refusing legitimate values.

    Only a value that is ENTIRELY a placeholder is a failed expansion. A path
    that happens to contain `$` is unusual but legal, and refusing it would
    break a working configuration in the name of fixing one.
    """
    # Arrange
    env = {"SCITEX_TODO_AGENT_ID": "old", "SCITEX_CARDS_AGENT_ID": "agent$7"}

    # Act
    mirror_env(env)

    # Assert
    assert env["SCITEX_TODO_AGENT_ID"] == "agent$7"


# --------------------------------------------------------------------------- #
# REFUSAL 2 — a rename must not silently relocate a POPULATED data store.     #
# --------------------------------------------------------------------------- #


def test_relocating_a_populated_store_is_refused(populated_store, tmp_path):
    """MUTATION: fails pre-fix — this is the fork that nearly destroyed 2117 cards.

    Honouring the new name here means writes land in `cards/` while every
    existing record stays in `todo/`, with nothing reconciling them.
    """
    # Arrange
    env = {
        CANONICAL: str(populated_store),
        RENAMED: str(tmp_path / "cards" / "cards.db"),
    }

    # Act
    mirror_env(env)

    # Assert
    assert env[CANONICAL] == str(populated_store)


def _mirror_a_refused_relocation(populated_store, tmp_path, caplog):
    """Mirror an env whose new store path would fork away from a populated one."""
    other = tmp_path / "cards" / "cards.db"
    env = {CANONICAL: str(populated_store), RENAMED: str(other)}
    with caplog.at_level(logging.ERROR):
        mirror_env(env)
    return other


def test_the_relocation_refusal_names_the_populated_store(
    populated_store, tmp_path, caplog
):
    # Arrange
    # Act
    _mirror_a_refused_relocation(populated_store, tmp_path, caplog)

    # Assert — half of what makes the fork diagnosable.
    assert str(populated_store) in caplog.text


def test_the_relocation_refusal_names_the_rejected_path(
    populated_store, tmp_path, caplog
):
    # Arrange
    # Act
    other = _mirror_a_refused_relocation(populated_store, tmp_path, caplog)

    # Assert — the other half; both paths together diagnose the fork.
    assert str(other) in caplog.text


def test_a_fresh_install_may_still_adopt_the_new_store_path(tmp_path):
    """Guards over-refusal. When the old path does NOT exist there is no data
    to strand, so this is a real migration and must be allowed — otherwise no
    new deployment could ever adopt the new variable name.
    """
    # Arrange
    new = tmp_path / "cards" / "cards.db"
    env = {CANONICAL: str(tmp_path / "todo" / "cards.db"), RENAMED: str(new)}

    # Act
    mirror_env(env)

    # Assert
    assert env[CANONICAL] == str(new)


def test_an_empty_old_store_is_not_treated_as_populated(tmp_path):
    """A zero-byte file holds no records, so moving off it strands nothing."""
    # Arrange
    old = tmp_path / "todo" / "cards.db"
    old.parent.mkdir(parents=True)
    old.write_text("")
    new = tmp_path / "cards" / "cards.db"
    env = {CANONICAL: str(old), RENAMED: str(new)}

    # Act
    mirror_env(env)

    # Assert
    assert env[CANONICAL] == str(new)


def test_pointing_both_names_at_the_same_store_is_not_a_fork(populated_store):
    # Arrange
    env = {CANONICAL: str(populated_store), RENAMED: str(populated_store)}

    # Act
    mirror_env(env)

    # Assert
    assert env[CANONICAL] == str(populated_store)


def test_a_non_store_variable_is_never_refused_for_relocation(populated_store):
    """The relocation guard keys on DATA-STORE variables only. A behavioural
    setting that happens to look like a path must still be overridable.
    """
    # Arrange
    env = {
        "SCITEX_TODO_LOG_DIR": str(populated_store),
        "SCITEX_CARDS_LOG_DIR": "/tmp/x",
    }

    # Act
    mirror_env(env)

    # Assert
    assert env["SCITEX_TODO_LOG_DIR"] == "/tmp/x"


# --------------------------------------------------------------------------- #
# Unchanged behaviour — the transition window must keep working.              #
# --------------------------------------------------------------------------- #


def test_a_normal_override_still_wins():
    # Arrange
    env = {"SCITEX_TODO_SCOPE": "agent:old", "SCITEX_CARDS_SCOPE": "agent:new"}

    # Act
    mirror_env(env)

    # Assert
    assert env["SCITEX_TODO_SCOPE"] == "agent:new"


def test_a_new_name_with_no_old_twin_is_mirrored():
    # Arrange
    env = {"SCITEX_CARDS_SCOPE": "agent:new"}

    # Act
    mirror_env(env)

    # Assert
    assert env["SCITEX_TODO_SCOPE"] == "agent:new"


def test_old_only_names_still_emit_the_deprecation_warning(caplog):
    # Arrange
    env = {"SCITEX_TODO_AGENT_ID": "scitex-cards"}

    # Act
    with caplog.at_level(logging.WARNING):
        mirror_env(env)

    # Assert
    assert "deprecated SCITEX_TODO_*" in caplog.text


def test_mirror_env_survives_a_hostile_old_path_and_still_mirrors(tmp_path):
    """This module runs at IMPORT time. A raise here does not fail one call —
    it makes `import scitex_cards` fail for every consumer of the package.

    An unreadable old path is not evidence of a populated store, so the
    relocation guard must swallow the error and let the mirror proceed. Asserting
    the mirrored value proves BOTH halves: the call returned, and it returned
    having made the documented conservative decision.
    """
    # Arrange — a NUL byte makes every stat on this path raise.
    new = tmp_path / "x.yaml"
    env = {CANONICAL: "\0not/a/valid/path", RENAMED: str(new)}

    # Act — must not raise.
    mirror_env(env)

    # Assert
    assert env[CANONICAL] == str(new)


def test_mirror_env_is_idempotent(populated_store, tmp_path):
    # Arrange
    env = {
        CANONICAL: str(populated_store),
        RENAMED: str(tmp_path / "cards" / "cards.db"),
    }
    mirror_env(env)
    first = dict(env)

    # Act — a second pass over the already-mirrored env.
    mirror_env(env)

    # Assert
    assert env == first


# EOF
