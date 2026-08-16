#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The reconciler stamps ITSELF when nothing else names an author.

An unattended cron run has no ``$SCITEX_CARDS_AGENT_ID``, so every close raised
"creator unresolved" and the job closed nothing — measured on the live */15
entry 2026-08-01, once the store-target failure that had been masking it was
fixed.

The precedence is widened only at the end: explicit ``by`` wins, then the
ambient environment, and only when NEITHER exists is the reconciler named.
Nothing that already worked changes, and ``_resolve_creator_or_raise`` still
refuses to invent an author for an ordinary caller.
"""

from __future__ import annotations

import os

import pytest

from scitex_cards._reconcile_prs import SYSTEM_ACTOR, reconcile_merged_prs
from scitex_cards._store import ENV_AGENT
from scitex_cards._store_mutate import add_task

_MANAGED = (ENV_AGENT, "SCITEX_CARDS_DB", "HOME", "SCITEX_DIR")


@pytest.fixture
def unattended_store(tmp_path):
    """A real store with one merged-PR card, and NO ambient identity."""
    saved_env = {name: os.environ.get(name) for name in _MANAGED}
    saved_cwd = os.getcwd()

    for name in (ENV_AGENT, "SCITEX_DIR"):
        os.environ.pop(name, None)
    os.environ["HOME"] = str(tmp_path)
    (tmp_path / ".scitex" / "cards").mkdir(parents=True)
    store = tmp_path / "cards.db"
    os.environ["SCITEX_CARDS_DB"] = str(store)
    os.chdir(tmp_path)
    # The canonical read REFUSES a missing database rather than bootstrapping
    # one, so create it the way normal operation does.
    from scitex_cards._db import open_db

    open_db(str(store)).close()

    add_task(
        None,
        id="probe-card",
        title="[probe] a card whose PR merged",
        status="in_progress",
        pr_url="https://github.com/example/repo/pull/1",
        created_by="someone-else",
        assignee="someone-else",
    )

    yield store

    os.chdir(saved_cwd)
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _merged(_pr_url):
    return "merged"


class TestUnattendedRunCanClose:
    def test_close_succeeds_without_any_ambient_identity(self, unattended_store):
        # Arrange
        os.environ.pop(ENV_AGENT, None)

        # Act
        result = reconcile_merged_prs(None, apply=True, merge_state_fn=_merged)

        # Assert
        assert [entry["id"] for entry in result.closed] == ["probe-card"]

    def test_the_reconciler_names_itself_as_the_author(self, unattended_store):
        # Arrange
        os.environ.pop(ENV_AGENT, None)
        reconcile_merged_prs(None, apply=True, merge_state_fn=_merged)

        # Act
        from scitex_cards._store import get_task

        completed_by = (get_task(None, "probe-card").get("_log_meta") or {}).get(
            "completed_by"
        )

        # Assert
        assert completed_by == SYSTEM_ACTOR


class TestExistingPrecedenceIsUnchanged:
    def test_ambient_identity_still_wins_over_the_system_actor(self, unattended_store):
        # Arrange
        os.environ[ENV_AGENT] = "a-real-agent"
        reconcile_merged_prs(None, apply=True, merge_state_fn=_merged)

        # Act
        from scitex_cards._store import get_task

        completed_by = (get_task(None, "probe-card").get("_log_meta") or {}).get(
            "completed_by"
        )

        # Assert
        assert completed_by == "a-real-agent"

    def test_explicit_by_still_wins_over_everything(self, unattended_store):
        # Arrange
        os.environ[ENV_AGENT] = "a-real-agent"
        reconcile_merged_prs(
            None, apply=True, merge_state_fn=_merged, by="explicit-actor"
        )

        # Act
        from scitex_cards._store import get_task

        completed_by = (get_task(None, "probe-card").get("_log_meta") or {}).get(
            "completed_by"
        )

        # Assert
        assert completed_by == "explicit-actor"
