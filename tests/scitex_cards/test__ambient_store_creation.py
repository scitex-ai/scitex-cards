#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A write must never MANUFACTURE a board at a path nobody named.

The 2026-07-20 chain, measured end to end: sac's `_card_exists` read
`FileNotFoundError` as "no such card yet" rather than "wrong path", called
`add_task`, and scitex-cards obligingly CREATED a store containing that one
card. Three cron jobs doing that grew a five-card document at an ambiently
resolved path; the hourly `db snapshot --refresh` imported it as canonical and
reconcile deleted the 2160 cards absent from it.

The conflation was sac's. The manufacturing was ours. These tests pin our half.
"""

from __future__ import annotations

import pytest

from scitex_cards._db import ENV_STORE_DSN
from scitex_cards._paths import refuse_ambient_store_creation


def _refuses(path, **kwargs) -> bool:
    """Did the guard refuse this path? Returns the VERDICT, never raises.

    The permissive cases below used to assert nothing at all — they called the
    guard and relied on "it did not raise" (STX-TQ001: running it only proves
    the import works). Turning the outcome into a boolean gives each of them a
    real assertion, and makes the allow-cases read as the exact mirror of the
    refuse-case rather than as an absence.
    """
    try:
        refuse_ambient_store_creation(path, **kwargs)
    except RuntimeError:
        return True
    return False


def _refusal_message(path, **kwargs) -> str:
    with pytest.raises(RuntimeError) as excinfo:
        refuse_ambient_store_creation(path, **kwargs)
    return str(excinfo.value)


def test_a_write_to_a_nonexistent_ambient_store_is_refused(tmp_path, env):
    # Arrange — nothing names the store: no explicit arg, no env var.
    env.delete(ENV_STORE_DSN)
    absent = tmp_path / "never-created" / "cards.db"
    # Act
    refused = _refuses(absent)
    # Assert — refusing is the whole point.
    assert refused is True


def test_the_refusal_says_what_it_is_refusing_to_do(tmp_path, env):
    # Arrange
    env.delete(ENV_STORE_DSN)
    absent = tmp_path / "never-created" / "cards.db"
    # Act
    message = _refusal_message(absent)
    # Assert
    assert "REFUSING to create a task store" in message


def test_the_refusal_names_the_path_it_would_have_created(tmp_path, env):
    # Arrange
    env.delete(ENV_STORE_DSN)
    absent = tmp_path / "never-created" / "cards.db"
    # Act
    message = _refusal_message(absent)
    # Assert — an unnamed path leaves the reader unable to tell WHICH ambient
    # resolution went wrong, which is the entire diagnostic value here.
    assert str(absent) in message


def test_the_refusal_names_the_variable_that_would_authorise_it(
    tmp_path, env
):
    # Arrange
    env.delete(ENV_STORE_DSN)
    absent = tmp_path / "never-created" / "cards.db"
    # Act
    message = _refusal_message(absent)
    # Assert — constitution section 2: say what to DO, not only what broke.
    assert ENV_STORE_DSN in message


def test_a_write_to_an_explicitly_named_nonexistent_store_is_allowed(
    tmp_path, env
):
    # Arrange — the caller NAMED the destination; naming it is the opt-in.
    env.delete(ENV_STORE_DSN)
    absent = tmp_path / "deliberate" / "cards.db"
    # Act
    refused = _refuses(absent, explicit=absent)
    # Assert — bootstraps and tests depend on this staying permitted.
    assert refused is False


def test_an_env_named_nonexistent_store_is_allowed(tmp_path, env):
    # Arrange — an operator who exported the store variable has stated intent
    # just as clearly as one who passed the path.
    absent = tmp_path / "configured" / "cards.db"
    env.set(ENV_STORE_DSN, str(absent))
    # Act
    refused = _refuses(absent)
    # Assert
    assert refused is False


def test_an_existing_ambient_store_is_untouched_by_the_guard(tmp_path, env):
    # Arrange — the ordinary healthy case: the board already exists.
    env.delete(ENV_STORE_DSN)
    present = tmp_path / "cards.db"
    present.write_text("", encoding="utf-8")
    # Act
    refused = _refuses(present)
    # Assert — the guard is about CREATION, never about writing.
    assert refused is False


def test_add_task_succeeds_against_an_existing_store(new_store, env):
    """The board already exists and the write must WORK. The pin that keeps
    CREATE agreeing with read/update.

    Reproduced by scitex-ui on 0.17.7: every `add` failed for any agent whose
    env lacked ``$SCITEX_STORE_DSN``, while every read/update on the same store
    succeeded. The cause was that CREATE guarded a SYNTHETIC display label
    (``<db_dir>/tasks.yaml``) instead of the resolved store. The YAML tier was
    deleted (#512), so that label can never exist and the guard refused
    unconditionally — including, absurdly, right after `init-store` created the
    very database the error told you to create.

    The guard exists to stop a write MANUFACTURING a board. When the board is
    already there, there is nothing to manufacture, so there is nothing to
    refuse. An agent that cannot create a card cannot record work, hand off, or
    escalate.
    """
    # Arrange — a REAL store that already exists, NAMED through the environment.
    import scitex_cards

    env.set(ENV_STORE_DSN, new_store())

    # Act
    scitex_cards.add_task(
        id="ambient-card",
        title="created against an existing store",
        assignee="scitex-cards",
        agent="scitex-cards",
    )

    # Assert — on the artefact: the card is readable back from the canonical store.
    assert scitex_cards.get_task(task_id="ambient-card")["id"] == "ambient-card"

