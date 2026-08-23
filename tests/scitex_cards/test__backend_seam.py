#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backend seam (PR-1 of the remote-hub design) — LocalBackend parity.

Three guarantees, per docs/design/remote-hub-backend.md §2/§6:

1. COMPLETENESS — every name in ``BACKEND_VERBS`` is a callable on
   ``LocalBackend`` (a backend missing a verb would fail at call time on a
   remote host, long after review).
2. RESOLUTION — ``get_backend()`` returns the local passthrough when
   ``SCITEX_CARDS_HUB_URL`` is unset, and FAILS LOUD when it is set on a
   build with no HTTP client. A silent local fallback would write a store
   the hub never sees (the one-database ruling), so the error is pinned.
3. ROUND TRIP — every verb, called through the seam against a real tmp
   store, persists exactly what a direct read of that store shows
   (read-back through ``_store``, not through the object under test).
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

from scitex_cards import _store
from scitex_cards._backend import (
    BACKEND_VERBS,
    LocalBackend,
    get_backend,
)
from scitex_cards._currency import reset_currency_cache, warn_if_stale_once


@pytest.fixture
def store(env):
    env.set("SCITEX_CARDS_AGENT_ID", "seam-tester")
    env.delete("SCITEX_CARDS_HUB_URL")
    # SQLite is the store; the conftest pins + bootstraps an empty canonical
    # DB per test. Return the pinned STORE IDENTITY path (== resolve_tasks_path
    # (None)), NOT a tmp yaml: a write stamped with a tmp path would fail the
    # next read's stamp check (THE STORE-PATH RULE).
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _add(backend, store_path, cid, title, assignee):
    return backend.add_task(
        store_path,
        id=cid,
        title=title,
        status="deferred",
        assignee=assignee,
        created_by="seam-tester",
    )


def _seed_two_cards(backend, store_path):
    """Put ``seam-a`` and ``seam-b`` in the store through the seam."""
    _add(backend, store_path, "seam-a", "Seam A", "alice")
    _add(backend, store_path, "seam-b", "Seam B", "bob")


def _seed_and_delete(backend, store_path):
    """Add a card through the seam, delete it, and hand back the payload."""
    _add(backend, store_path, "seam-del", "To delete", "alice")
    return backend.delete_task(store_path, "seam-del")


# --------------------------------------------------------------------------- #
# 1. completeness + resolution                                                #
# --------------------------------------------------------------------------- #


def test_every_declared_verb_is_a_local_backend_callable():
    # Arrange
    backend = LocalBackend()
    # Act
    missing = [v for v in BACKEND_VERBS if not callable(getattr(backend, v, None))]
    # Assert
    assert missing == []


def test_resolution_is_local_when_hub_url_unset(env):
    # Arrange
    env.delete("SCITEX_CARDS_HUB_URL")
    # Act
    backend = get_backend()
    # Assert
    assert isinstance(backend, LocalBackend)


def test_resolution_returns_the_hub_client_when_url_set(env):
    """PR-3 replaced PR-1's resolve-time refusal with the real client.

    The fail-loud property MOVED, not vanished: it now fires at the first
    CALL when the hub is unusable (no token / unreachable) — pinned in
    test__hub_backend.py — because resolution must stay import-safe while
    a silent local fallback stays impossible.
    """
    # Arrange
    env.set("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:8765")
    from scitex_cards._backend_http import HubBackend

    # Act
    backend = get_backend()
    # Assert
    assert isinstance(backend, HubBackend)


def test_resolved_hub_client_carries_the_configured_url(env):
    # Arrange
    env.set("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:8765")
    # Act
    backend = get_backend()
    # Assert
    assert backend.url == "http://127.0.0.1:8765"


# --------------------------------------------------------------------------- #
# 2. task verbs — round trip through the seam                                 #
# --------------------------------------------------------------------------- #


def test_add_task_through_the_seam_returns_the_created_card(store):
    # Arrange
    backend = get_backend()
    # Act
    created = _add(backend, store, "seam-a", "Seam A", "alice")
    # Assert
    assert created["id"] == "seam-a"


def test_added_card_is_readable_through_the_store_engine(store):
    # Arrange
    backend = get_backend()
    # Act
    _seed_two_cards(backend, store)
    # Assert — read-back through the engine, never through the object
    # under test.
    assert _store.get_task(store, "seam-a")["title"] == "Seam A"


def test_added_card_is_readable_through_the_seam_get_task(store):
    # Arrange
    backend = get_backend()
    # Act
    _seed_two_cards(backend, store)
    # Assert
    assert backend.get_task(store, "seam-a")["title"] == "Seam A"


def test_list_tasks_through_the_seam_returns_every_card(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    listed = backend.list_tasks(store, scope="")
    # Assert
    assert {t["id"] for t in listed} == {"seam-a", "seam-b"}


def test_update_task_through_the_seam_persists_the_new_status(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.update_task(store, "seam-a", status="in_progress")
    # Assert
    assert _store.get_task(store, "seam-a")["status"] == "in_progress"


def test_update_task_through_the_seam_returns_the_updated_card(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    updated = backend.update_task(store, "seam-a", status="in_progress")
    # Assert
    assert updated["status"] == "in_progress"


def test_comment_task_through_the_seam_appends_the_comment(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.comment_task(store, "seam-a", "through the seam", by="alice")
    # Assert
    comments = _store.get_task(store, "seam-a")["comments"]
    assert comments[-1]["text"] == "through the seam"


def test_set_edge_through_the_seam_persists_the_depends_on_edge(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.set_edge(
        store, action="add", kind="depends_on", source="seam-b", target="seam-a"
    )
    # Assert
    assert _store.get_task(store, "seam-b")["depends_on"] == ["seam-a"]


def test_summarize_tasks_through_the_seam_returns_a_dict(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    summary = backend.summarize_tasks(store)
    # Assert
    assert isinstance(summary, dict) and summary


def test_complete_task_through_the_seam_marks_the_card_done(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.complete_task(store, "seam-a", by="alice")
    # Assert
    assert _store.get_task(store, "seam-a")["status"] == "done"


def test_reassign_task_through_the_seam_returns_the_new_owner(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    reassigned = backend.reassign_task(store, "seam-b", "carol", by="seam-tester")
    # Assert
    assert reassigned["task"]["assignee"] == "carol"


def test_reassign_task_through_the_seam_persists_the_new_owner(store):
    # Arrange
    backend = get_backend()
    _seed_two_cards(backend, store)
    # Act
    backend.reassign_task(store, "seam-b", "carol", by="seam-tester")
    # Assert
    assert _store.get_task(store, "seam-b")["assignee"] == "carol"


# --------------------------------------------------------------------------- #
# 3. delete / restore                                                         #
# --------------------------------------------------------------------------- #


def test_delete_task_through_the_seam_returns_the_removed_card(store):
    # Arrange
    backend = get_backend()
    # Act
    removed = _seed_and_delete(backend, store)
    # Assert
    assert removed["removed"]["id"] == "seam-del"


def test_deleted_card_is_gone_from_the_store(store):
    # Arrange
    backend = get_backend()
    # Act
    _seed_and_delete(backend, store)
    # Assert
    with pytest.raises(Exception):
        _store.get_task(store, "seam-del")


def test_restore_task_through_the_seam_brings_the_card_back(store):
    # Arrange
    backend = get_backend()
    removed = _seed_and_delete(backend, store)
    # Act
    backend.restore_task(store, task=removed["removed"], refs=removed.get("refs"))
    # Assert
    assert _store.get_task(store, "seam-del")["title"] == "To delete"


# --------------------------------------------------------------------------- #
# 4. help wait / clear                                                        #
# --------------------------------------------------------------------------- #


def test_help_wait_through_the_seam_returns_a_blocked_card(store):
    # Arrange
    backend = get_backend()
    # Act
    card = backend.help_wait(store, "seam-agent", question="which option?")
    # Assert
    assert card["status"] == "blocked"


def test_help_wait_through_the_seam_sets_the_operator_blocker(store):
    # Arrange
    backend = get_backend()
    # Act
    card = backend.help_wait(store, "seam-agent", question="which option?")
    # Assert
    assert _store.get_task(store, card["id"])["blocker"] == "operator-decision"


def test_help_clear_through_the_seam_reports_that_it_cleared(store):
    # Arrange
    backend = get_backend()
    backend.help_wait(store, "seam-agent", question="which option?")
    # Act
    cleared = backend.help_clear(store, "seam-agent")
    # Assert
    assert cleared["cleared"] is True


def test_help_clear_through_the_seam_marks_the_card_done(store):
    # Arrange
    backend = get_backend()
    card = backend.help_wait(store, "seam-agent", question="which option?")
    # Act
    backend.help_clear(store, "seam-agent")
    # Assert
    assert _store.get_task(store, card["id"])["status"] == "done"


# --------------------------------------------------------------------------- #
# 5. dm + inbox                                                               #
# --------------------------------------------------------------------------- #


def test_dm_send_through_the_seam_records_both_peers(store):
    # Arrange
    backend = get_backend()
    # Act
    record = backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Assert
    assert record["from"] == "seam-alice" and record["to"] == "seam-bob"


def test_dm_list_through_the_seam_returns_the_sent_body(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    thread = backend.dm_list("seam-bob", peer="seam-alice", ack=True, store=store)
    # Assert
    assert [m["body"] for m in thread["messages"]] == ["hello"]


def test_dm_list_through_the_seam_names_the_requested_peer(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    thread = backend.dm_list("seam-bob", peer="seam-alice", ack=True, store=store)
    # Assert
    assert thread["peer"] == "seam-alice"


def test_poll_notifications_through_the_seam_names_the_agent(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    inbox = backend.poll_notifications("seam-bob", store=store)
    # Assert
    assert inbox["agent"] == "seam-bob"


def test_poll_notifications_through_the_seam_returns_a_list(store):
    # Arrange
    backend = get_backend()
    backend.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Act
    inbox = backend.poll_notifications("seam-bob", store=store)
    # Assert
    assert isinstance(inbox["notifications"], list)


# --------------------------------------------------------------------------- #
# 6. CURRENCY VISIBILITY — the Python rail tells you the CLI rail is dead     #
#                                                                             #
# The incident (2026-07-29): `scitex-cards --version` answered while          #
# `list-tasks` REFUSED as stale, and the agent's DMs — which come through     #
# this seam, NOT through the gated CLI/MCP entry points — kept working. The   #
# card rail was dead for hours with nothing to signal it. The seam therefore  #
# calls the NON-RAISING `warn_if_stale_once()`: it must warn, name the        #
# sibling rail, and above all NOT take this rail down too.                    #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _fresh_currency_cache():
    """The warn-once state is REAL module state and survives a test.

    Cleared on the way in AND out, so one stale scenario cannot silence the
    next — the measurement is taken at most once per process by design.
    """
    reset_currency_cache()
    yield
    reset_currency_cache()


class _FakeStalenessError(RuntimeError):
    """What the injected scitex-dev publishes as its ``StalenessError``.

    ONE class shared by every arrangement below, and that is load-bearing: the
    verdict tells a staleness VERDICT from a malfunctioning scitex-dev by
    catching exactly this type, so a second, look-alike class defined
    elsewhere would be caught by nothing, degrade to ``unknown``, and silently
    emit no warning at all.
    """


def _seam_over(ensure_current):
    """A backend whose currency notice runs over the given ``ensure_current``.

    The notice stays the REAL :func:`warn_if_stale_once` — only the scitex-dev
    underneath it is supplied, through the loader seam the verdict obtains its
    checker from. That matters for the text assertions below: a hand-rolled
    warner would emit whatever the test wrote, which is no evidence at all
    about what an agent actually reads.
    """

    def _load():
        return ensure_current, _FakeStalenessError

    return LocalBackend(warn_currency=lambda: warn_if_stale_once(load_checker=_load))


def _stale_seam():
    """The world the 2026-07-29 incident happened in: scitex-dev REFUSES."""

    def _refuses(dist_name):
        raise _FakeStalenessError(f"{dist_name} 0.17.7 is behind latest 0.17.9")

    return _seam_over(_refuses)


def _exiting_seam():
    """A scitex-dev whose ``ensure_current`` calls ``sys.exit()``.

    The LIBRARY BUG the currency guard must absorb: ``SystemExit`` is a
    ``BaseException``, not an ``Exception``, so the pre-fix ``except
    Exception`` did not stop it.
    """

    def _exits(dist_name):
        sys.exit(f"scitex-dev exited while checking {dist_name}")

    return _seam_over(_exits)


def _currency_warning_texts(caplog):
    return [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "scitex_cards._currency" and rec.levelno == logging.WARNING
    ]


def test_dm_send_through_the_seam_warns_that_the_cli_rail_is_refusing(
    store, caplog
):
    # Arrange
    seam = _stale_seam()
    caplog.set_level(logging.WARNING, logger="scitex_cards._currency")
    # Act
    seam.dm_send("seam-alice", "seam-bob", "hello", store=store)
    # Assert
    assert "scitex-cards list-tasks" in "\n".join(_currency_warning_texts(caplog))


def test_dm_send_through_the_seam_still_delivers_when_the_install_is_stale(
    store
):
    """The warn path must never become the thing that takes the last working
    rail down — that is the failure this whole change refuses to ship."""
    # Arrange
    seam = _stale_seam()
    # Act
    seam.dm_send("seam-alice", "seam-bob", "hello", store=store)
    thread = seam.dm_list("seam-bob", peer="seam-alice", store=store)
    # Assert
    assert [m["body"] for m in thread["messages"]] == ["hello"]


def test_dm_list_through_the_seam_warns_that_the_cli_rail_is_refusing(
    store, caplog
):
    # Arrange
    seam = _stale_seam()
    caplog.set_level(logging.WARNING, logger="scitex_cards._currency")
    # Act
    seam.dm_list("seam-bob", peer="seam-alice", store=store)
    # Assert
    assert "scitex-cards list-tasks" in "\n".join(_currency_warning_texts(caplog))


def test_poll_notifications_through_the_seam_warns_that_the_cli_rail_is_refusing(
    store, caplog
):
    # Arrange
    seam = _stale_seam()
    caplog.set_level(logging.WARNING, logger="scitex_cards._currency")
    # Act
    seam.poll_notifications("seam-bob", store=store)
    # Assert
    assert "scitex-cards list-tasks" in "\n".join(_currency_warning_texts(caplog))


def test_dm_send_through_the_seam_still_delivers_when_the_currency_check_exits(
    store
):
    """THE REFUTED PROPERTY, measured where it broke. Before the fix the
    `SystemExit` propagated out of `dm_send`, the store was never touched, and
    the DM did not go out — the currency DIAGNOSTIC killed the last working
    rail. Reading the body back is the proof the write actually landed."""
    # Arrange
    seam = _exiting_seam()
    # Act
    seam.dm_send("seam-alice", "seam-bob", "hello", store=store)
    thread = seam.dm_list("seam-bob", peer="seam-alice", store=store)
    # Assert
    assert [m["body"] for m in thread["messages"]] == ["hello"]


# EOF
