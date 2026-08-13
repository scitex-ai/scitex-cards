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

from scitex_cards._db import ENV_DB
from scitex_cards._paths import refuse_ambient_store_creation


def test_a_write_to_a_nonexistent_ambient_store_is_refused(tmp_path, monkeypatch):
    # ARRANGE — nothing names the store: no explicit arg, no env var.
    monkeypatch.delenv(ENV_DB, raising=False)
    absent = tmp_path / "never-created" / "cards.db"

    # ACT / ASSERT — refusing is the whole point.
    with pytest.raises(RuntimeError) as excinfo:
        refuse_ambient_store_creation(absent)

    message = str(excinfo.value)
    assert "REFUSING to create a task store" in message
    # The error must be ACTIONABLE: name the path, and say what to do instead.
    assert str(absent) in message
    assert ENV_DB in message


def test_a_write_to_an_explicitly_named_nonexistent_store_is_allowed(
    tmp_path, monkeypatch
):
    # ARRANGE — the caller NAMED the destination; naming it is the opt-in.
    monkeypatch.delenv(ENV_DB, raising=False)
    absent = tmp_path / "deliberate" / "cards.db"

    # ACT / ASSERT — must not raise; bootstraps and tests depend on this.
    refuse_ambient_store_creation(absent, explicit=absent)


def test_an_env_named_nonexistent_store_is_allowed(tmp_path, monkeypatch):
    # ARRANGE — an operator who exported the store variable has stated intent
    # just as clearly as one who passed the path.
    absent = tmp_path / "configured" / "cards.db"
    monkeypatch.setenv(ENV_DB, str(absent))

    # ACT / ASSERT
    refuse_ambient_store_creation(absent)


def test_an_existing_ambient_store_is_untouched_by_the_guard(tmp_path, monkeypatch):
    # ARRANGE — the ordinary healthy case: the board already exists.
    monkeypatch.delenv(ENV_DB, raising=False)
    present = tmp_path / "cards.db"
    present.write_text("", encoding="utf-8")

    # ACT / ASSERT — the guard is about CREATION, never about writing.
    refuse_ambient_store_creation(present)


def test_add_task_succeeds_against_an_existing_store(tmp_path, monkeypatch):
    """The board already exists and the write must WORK. The pin that keeps
    CREATE agreeing with read/update.

    Reproduced by scitex-ui on 0.17.7: every `add` failed for any agent whose
    env lacked ``$SCITEX_CARDS_DB``, while every read/update on the same store
    succeeded. The cause was that CREATE guarded a SYNTHETIC display label
    (``<db_dir>/tasks.yaml``) instead of the resolved store. The YAML tier was
    deleted (#512), so that label can never exist and the guard refused
    unconditionally — including, absurdly, right after `init-store` created the
    very database the error told you to create.

    The guard exists to stop a write MANUFACTURING a board. When the board is
    already there, there is nothing to manufacture, so there is nothing to
    refuse. An agent that cannot create a card cannot record work, hand off, or
    escalate.

    THE STORE IS NOW NAMED, AND THE TEST'S SUBJECT IS UNCHANGED. This used to
    arrange "a REAL store at the AMBIENT default, named by nothing" — the
    configuration it said every fleet agent ran in. That configuration is what
    the operator abolished on 2026-08-13, precisely because "named by nothing"
    and "named by a variable that got lost" are the same state from inside the
    process. The write path being pinned here is identical either way; only the
    arrangement moved from ambient to named, and the sibling test below still
    covers the ambient-write refusal.
    """
    # ARRANGE — a REAL store, NAMED through the environment.
    import scitex_cards
    from scitex_cards._db import connect, init_schema, resolve_db_path

    monkeypatch.delenv("SCITEX_TODO_DB", raising=False)
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path / "scitex"))
    monkeypatch.setenv(ENV_DB, str(tmp_path / "scitex" / "cards" / "cards.db"))

    db = resolve_db_path(None)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    try:
        init_schema(conn)
        conn.commit()
    finally:
        conn.close()
    assert db.exists(), "arrange failed: no store to write against"

    # ACT
    scitex_cards.add_task(
        id="ambient-card",
        title="created against an existing ambient store",
        assignee="scitex-cards",
        agent="scitex-cards",
    )

    # ASSERT — on the artefact: the card is readable back from the canonical store.
    assert scitex_cards.get_task(task_id="ambient-card")["id"] == "ambient-card"


def test_add_task_does_not_manufacture_a_board_at_an_ambient_path(
    tmp_path, monkeypatch
):
    """The end-to-end shape that actually happened, as a regression pin.

    Asserts on the FILESYSTEM, not on "nothing was raised" — a probe that
    concludes from an absent exception reports success when it never ran.
    """
    # ARRANGE — point the ambient user root at an empty dir, name nothing.
    import scitex_cards

    monkeypatch.delenv(ENV_DB, raising=False)
    monkeypatch.delenv("SCITEX_TODO_DB", raising=False)
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path / "scitex"))
    would_be = tmp_path / "scitex" / "cards" / "cards.db"

    # ACT
    with pytest.raises(RuntimeError):
        scitex_cards.add_task(
            id="decoy-card",
            title="written to a store that did not exist",
            assignee="scitex-cards",
            agent="scitex-cards",
        )

    # ASSERT — the artefact, not the exception: no board was invented.
    assert not would_be.exists()
