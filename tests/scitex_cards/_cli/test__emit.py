#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `emit-event` + `find-card` producer CLI verbs (no mocks).

The generic no-import shell-out seam fleet producers (scitex-dev's C7
``released`` / C8 ``pulled`` + future) use to emit canonical card-events
WITHOUT importing :mod:`scitex_cards`. Real ``tmp_path`` store, real users
via ``register_user``, real cards via ``add_task``, CLI driven through
``CliRunner.invoke(main, [...])`` exactly like the sibling CLI tests — no
mocks (STX-NM / PA-306). Covers:

* ``emit-event --type pulled --repo X`` (no card_id) → exits 0; the C4
  consumer no-ops (nothing to resolve), asserted via the JSON summary.
* ``emit-event --type released --card-id C --repo R --version V --actor A``
  on a card with a subscriber → the event reaches the standalone INBOX of
  that subscriber (end-to-end: emit → bus → C4 → inbox; asserted via
  ``poll_inbox`` AND the printed ``notify.enqueued``).
* ``emit-event --type bogus`` → fails loud (non-zero, names valid types).
* ``find-card --repo R`` → prints the ids of cards with repo=R; empty
  output when none; honors ``--kind`` / ``--status`` filters.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._inbox import poll_inbox
from scitex_cards._store import add_task
from scitex_cards._users import register_user


def _store_path(tmp_path) -> str:
    """Path string to a fresh tasks.yaml under tmp_path."""
    return str(tmp_path / "tasks.yaml")


def _summary(output: str) -> dict:
    """Parse the emit-event JSON summary from CLI ``output``.

    The verb prints the JSON summary as its FINAL line. When the optional
    push rail runs under ``SCITEX_TODO_PUSH_DRY_RUN=1`` it also writes a
    dev/test banner to stdout BEFORE the JSON (a production producer never
    sets dry-run, so the JSON is the only stdout there). Parse the last
    non-empty line so the test is robust to that dev banner.
    """
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# emit-event — repo-level (no card_id) → quiet no-op                          #
# --------------------------------------------------------------------------- #
def _emit_pulled_no_card(tmp_path):
    """A repo-level `pulled` with --repo and NO --card-id."""
    runner = CliRunner()
    store = _store_path(tmp_path)
    return runner.invoke(
        main,
        ["emit-event", "--type", "pulled", "--repo", "owner/repo", "--tasks", store],
    )


def test_emit_event_pulled_no_card_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result = _emit_pulled_no_card(tmp_path)
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_pulled_no_card_emits_a_card_event(tmp_path, env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card(tmp_path).output)
    # Assert
    assert summary["kind"] == "card-event"


def test_emit_event_pulled_no_card_resolves_no_card_id(tmp_path, env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card(tmp_path).output)
    # Assert — the C4 consumer ran but had no card to resolve against.
    assert summary["notify"]["card_id"] is None


def test_emit_event_pulled_no_card_enqueues_nothing(tmp_path, env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card(tmp_path).output)
    # Assert — the intended quiet no-op.
    assert summary["notify"]["enqueued"] == []


def test_emit_event_pulled_no_card_delivers_nothing(tmp_path, env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card(tmp_path).output)
    # Assert — the intended quiet no-op.
    assert summary["notify"]["delivered"] == []


def test_emit_event_pulled_no_card_records_the_skip_reason(tmp_path, env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card(tmp_path).output)
    # Assert — the no-op is EXPLAINED, not silent.
    assert "no-card-id" in summary["notify"]["skipped"]


# --------------------------------------------------------------------------- #
# emit-event — end-to-end: emit → bus → C4 → standalone inbox                  #
# --------------------------------------------------------------------------- #
def _release_to_subscriber(tmp_path, env):
    """Emit `released` on a card whose SUBSCRIBER is the intended recipient.

    The C3 default rule for `released` is `[subscribers]` (a release
    announcement), so the subscriber `eve` is the recipient; the owner
    `alice` is NOT notified for a release by default. Push rail dry-run so
    the optional accelerator never touches the network (the standalone inbox
    rail is the one under test). Returns ``(result, alice, eve, store)``.
    """
    runner = CliRunner()
    store = _store_path(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps alice's inbox empty
    # so the `released`-to-subscriber assertion is not polluted.
    add_task(
        store=store,
        id="card-rel",
        title="x",
        agent="alice",
        subscribers=["eve"],
        created_by="alice",
    )
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    # A `released` card-event for the card, caused by `ci` (the actor is never
    # self-notified; here the actor is neither alice nor eve).
    result = runner.invoke(
        main,
        [
            "emit-event",
            "--type",
            "released",
            "--card-id",
            "card-rel",
            "--repo",
            "owner/repo",
            "--version",
            "v1.2.3",
            "--actor",
            "ci",
            "--tasks",
            store,
        ],
    )
    return result, alice, eve, store


def test_emit_event_released_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result, _alice, _eve, _store = _release_to_subscriber(tmp_path, env)
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_released_summary_names_the_event_type(tmp_path, env):
    # Arrange
    # Act
    result, _alice, _eve, _store = _release_to_subscriber(tmp_path, env)
    summary = _summary(result.output)
    # Assert
    assert summary["notify"]["event_type"] == "released"


def test_emit_event_released_summary_enqueues_the_subscriber(tmp_path, env):
    # Arrange
    # Act
    result, _alice, eve, _store = _release_to_subscriber(tmp_path, env)
    summary = _summary(result.output)
    # Assert — the printed summary reports the enqueue to the subscriber only.
    assert summary["notify"]["enqueued"] == [eve.id]


def test_emit_event_released_reaches_subscriber_inbox(tmp_path, env):
    # Arrange
    # Act
    _result, _alice, eve, store = _release_to_subscriber(tmp_path, env)
    eve_inbox = poll_inbox(eve.id, store=store)
    # Assert — end-to-end, the subscriber's standalone INBOX received it.
    assert [r["event_type"] for r in eve_inbox] == ["released"]


def test_emit_event_released_inbox_row_names_the_card(tmp_path, env):
    # Arrange
    # Act
    _result, _alice, eve, store = _release_to_subscriber(tmp_path, env)
    eve_inbox = poll_inbox(eve.id, store=store)
    # Assert
    assert [r["card_id"] for r in eve_inbox] == ["card-rel"]


def test_emit_event_released_leaves_the_owner_inbox_empty(tmp_path, env):
    # Arrange
    # Act
    _result, alice, _eve, store = _release_to_subscriber(tmp_path, env)
    # Assert — the owner is not a `released` recipient.
    assert poll_inbox(alice.id, store=store) == []


def _release_caused_by_the_subscriber(tmp_path, env):
    """Emit `released` where the SUBSCRIBER is also the actor.

    alice is a SUBSCRIBER (so she WOULD be a `released` recipient) AND the
    actor. The actor (the cause of the event) must never be self-notified, so
    her inbox must stay empty even though her role matches. Returns
    ``(result, alice, store)``.
    """
    runner = CliRunner()
    store = _store_path(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="card-rel", title="x", agent="bob", subscribers=["alice"])
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    result = runner.invoke(
        main,
        [
            "emit-event",
            "--type",
            "released",
            "--card-id",
            "card-rel",
            "--actor",
            "alice",
            "--tasks",
            store,
        ],
    )
    return result, alice, store


def test_emit_event_released_by_the_actor_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result, _alice, _store = _release_caused_by_the_subscriber(tmp_path, env)
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_released_summary_drops_the_actor(tmp_path, env):
    # Arrange
    # Act
    result, _alice, _store = _release_caused_by_the_subscriber(tmp_path, env)
    summary = _summary(result.output)
    # Assert — alice caused it, so nothing is enqueued for her.
    assert summary["notify"]["enqueued"] == []


def test_emit_event_released_actor_is_not_notified(tmp_path, env):
    # Arrange
    # Act
    _result, alice, store = _release_caused_by_the_subscriber(tmp_path, env)
    # Assert — alice caused it, so her inbox stays empty (actor dropped).
    assert poll_inbox(alice.id, store=store) == []


def test_emit_event_extra_payload_parsed(tmp_path, env):
    # Arrange — --extra KEY=VALUE flows into the event envelope; with no
    # recipient it stays a quiet no-op but must still exit 0.
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "emit-event",
            "--type",
            "deployed",
            "--repo",
            "owner/repo",
            "--extra",
            "service=api",
            "--extra",
            "region=us-east-1",
            "--tasks",
            store,
        ],
    )
    # Assert
    assert result.exit_code == 0, result.output


def _emit_malformed_extra(tmp_path):
    """`--extra` missing '=' must fail loud (no silent drop)."""
    runner = CliRunner()
    store = _store_path(tmp_path)
    return runner.invoke(
        main,
        [
            "emit-event",
            "--type",
            "pulled",
            "--repo",
            "r",
            "--extra",
            "noequals",
            "--tasks",
            store,
        ],
    )


def test_emit_event_malformed_extra_nonzero(tmp_path, env):
    # Arrange
    # Act
    result = _emit_malformed_extra(tmp_path)
    # Assert
    assert result.exit_code != 0


def test_emit_event_malformed_extra_shows_no_traceback(tmp_path, env):
    # Arrange
    # Act
    result = _emit_malformed_extra(tmp_path)
    # Assert — a usage error, not a crash.
    assert "Traceback" not in result.output


# --------------------------------------------------------------------------- #
# emit-event — fail-loud on an unknown type                                   #
# --------------------------------------------------------------------------- #
def _emit_bogus_type(tmp_path):
    """An unknown `--type` must be refused."""
    runner = CliRunner()
    store = _store_path(tmp_path)
    return runner.invoke(
        main, ["emit-event", "--type", "bogus", "--repo", "r", "--tasks", store]
    )


def test_emit_event_bogus_type_fails_loud(tmp_path, env):
    # Arrange
    # Act
    result = _emit_bogus_type(tmp_path)
    # Assert
    assert result.exit_code != 0


def test_emit_event_bogus_type_shows_no_traceback(tmp_path, env):
    # Arrange
    # Act
    result = _emit_bogus_type(tmp_path)
    # Assert — a usage error, not a crash.
    assert "Traceback" not in result.output


def test_emit_event_bogus_type_echoes_the_bad_value(tmp_path, env):
    # Arrange
    # Act
    result = _emit_bogus_type(tmp_path)
    # Assert
    assert "bogus" in result.output


def test_emit_event_bogus_type_lists_a_valid_type(tmp_path, env):
    # Arrange
    # Act
    result = _emit_bogus_type(tmp_path)
    # Assert — a representative valid type is listed so the producer can
    # self-correct.
    assert "released" in result.output


# --------------------------------------------------------------------------- #
# find-card — repo → card id(s)                                            #
# --------------------------------------------------------------------------- #
def _find_card_across_two_repos(tmp_path):
    """Two cards on the queried repo, one on another; run `find-card`."""
    runner = CliRunner()
    store = _store_path(tmp_path)
    add_task(store=store, id="c-a", title="A", assignee="agent:t", repo="owner/repo")
    add_task(store=store, id="c-b", title="B", assignee="agent:t", repo="owner/repo")
    add_task(store=store, id="c-c", title="C", assignee="agent:t", repo="owner/other")
    return runner.invoke(main, ["find-card", "--repo", "owner/repo", "--tasks", store])


def _find_card_with_no_match(tmp_path):
    """No card carries the queried repo; run `find-card`."""
    runner = CliRunner()
    store = _store_path(tmp_path)
    add_task(store=store, id="c-a", title="A", assignee="agent:t", repo="owner/other")
    return runner.invoke(main, ["find-card", "--repo", "owner/repo", "--tasks", store])


def _find_card_filtered_by_status(tmp_path):
    """Same repo, different statuses; run `find-card --status deferred`.

    (Seeded `deferred` since the pending abolition: an abolished value in the
    store makes every CLI call print the TOLERATED banner, which pollutes the
    output this test parses.)
    """
    runner = CliRunner()
    store = _store_path(tmp_path)
    add_task(
        store=store,
        id="c-deferred",
        title="P",
        assignee="agent:t",
        repo="owner/repo",
        status="deferred",
    )
    add_task(
        store=store,
        id="c-done",
        title="D",
        assignee="agent:t",
        repo="owner/repo",
        status="done",
    )
    return runner.invoke(
        main,
        ["find-card", "--repo", "owner/repo", "--status", "deferred", "--tasks", store],
    )


def test_find_card_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result = _find_card_across_two_repos(tmp_path)
    # Assert
    assert result.exit_code == 0, result.output


def test_find_card_prints_matching_ids(tmp_path, env):
    # Arrange
    # Act
    result = _find_card_across_two_repos(tmp_path)
    ids = set(result.output.split())
    # Assert — exactly the two matching ids, one per line.
    assert ids == {"c-a", "c-b"}


def test_find_card_no_match_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result = _find_card_with_no_match(tmp_path)
    # Assert — an empty result is not an error.
    assert result.exit_code == 0, result.output


def test_find_card_empty_when_none_match(tmp_path, env):
    # Arrange
    # Act
    result = _find_card_with_no_match(tmp_path)
    # Assert
    assert result.output.strip() == ""


def test_find_card_status_filter_exits_zero(tmp_path, env):
    # Arrange
    # Act
    result = _find_card_filtered_by_status(tmp_path)
    # Assert
    assert result.exit_code == 0, result.output


def test_find_card_status_filter_selects_only_matches(tmp_path, env):
    # Arrange
    # Act — only the deferred card should match.
    result = _find_card_filtered_by_status(tmp_path)
    # Assert
    assert result.output.split() == ["c-deferred"]


def _find_then_emit(tmp_path, env):
    """The documented producer flow: find-card, then emit-event --card-id.

    End-to-end proof the two verbs compose. `released` notifies subscribers
    (C3 default), so the recipient alice is a SUBSCRIBER on the card. Returns
    ``(resolve, card_id, emit, alice, store)``.
    """
    runner = CliRunner()
    store = _store_path(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(
        store=store,
        id="c-rel",
        title="x",
        agent="bob",
        subscribers=["alice"],
        repo="owner/repo",
    )
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    # Resolve repo → card id.
    resolve = runner.invoke(
        main, ["find-card", "--repo", "owner/repo", "--tasks", store]
    )
    card_id = resolve.output.strip()
    # Emit a released event for that card, caused by `ci` (not alice).
    emit = runner.invoke(
        main,
        [
            "emit-event",
            "--type",
            "released",
            "--card-id",
            card_id,
            "--repo",
            "owner/repo",
            "--version",
            "v1",
            "--actor",
            "ci",
            "--tasks",
            store,
        ],
    )
    return resolve, card_id, emit, alice, store


def test_find_card_resolves_the_expected_id(tmp_path, env):
    # Arrange
    # Act
    resolve, card_id, _emit, _alice, _store = _find_then_emit(tmp_path, env)
    # Assert
    assert resolve.exit_code == 0 and card_id == "c-rel"


def test_emit_with_a_resolved_id_exits_zero(tmp_path, env):
    # Arrange
    # Act
    _resolve, _card_id, emit, _alice, _store = _find_then_emit(tmp_path, env)
    # Assert
    assert emit.exit_code == 0, emit.output


def test_find_card_then_emit_with_resolved_id(tmp_path, env):
    # Arrange
    # Act
    _resolve, _card_id, _emit, alice, store = _find_then_emit(tmp_path, env)
    inbox = poll_inbox(alice.id, store=store)
    # Assert — subscriber alice received it via the inbox.
    assert [r["event_type"] for r in inbox] == ["released"]


# EOF
