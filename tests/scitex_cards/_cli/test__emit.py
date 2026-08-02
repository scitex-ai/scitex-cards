#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `emit-event` + `find-card` producer CLI verbs (no mocks).

The generic no-import shell-out seam fleet producers (scitex-dev's C7
``released`` / C8 ``pulled`` + future) use to emit canonical card-events
WITHOUT importing :mod:`scitex_cards`. Real per-test store (bootstrapped by
``tests/conftest.py``), real users via ``register_user``, real cards via
``add_task``, CLI driven through ``CliRunner.invoke(main, [...])`` exactly
like the sibling CLI tests — no mocks (STX-NM / PA-306). Covers:

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
def _emit_pulled_no_card():
    """A repo-level `pulled` with --repo and NO --card-id."""
    runner = CliRunner()
    return runner.invoke(
        main,
        ["emit-event", "--type", "pulled", "--repo", "owner/repo"],
    )


def test_emit_event_pulled_no_card_exits_zero(env):
    # Arrange
    # Act
    result = _emit_pulled_no_card()
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_pulled_no_card_emits_a_card_event(env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card().output)
    # Assert
    assert summary["kind"] == "card-event"


def test_emit_event_pulled_no_card_resolves_no_card_id(env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card().output)
    # Assert — the C4 consumer ran but had no card to resolve against.
    assert summary["notify"]["card_id"] is None


def test_emit_event_pulled_no_card_enqueues_nothing(env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card().output)
    # Assert — the intended quiet no-op.
    assert summary["notify"]["enqueued"] == []


def test_emit_event_pulled_no_card_delivers_nothing(env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card().output)
    # Assert — the intended quiet no-op.
    assert summary["notify"]["delivered"] == []


def test_emit_event_pulled_no_card_records_the_skip_reason(env):
    # Arrange
    # Act
    summary = _summary(_emit_pulled_no_card().output)
    # Assert — the no-op is EXPLAINED, not silent.
    assert "no-card-id" in summary["notify"]["skipped"]


# --------------------------------------------------------------------------- #
# emit-event — end-to-end: emit → bus → C4 → standalone inbox                  #
# --------------------------------------------------------------------------- #
def _release_to_subscriber(env):
    """Emit `released` on a card whose SUBSCRIBER is the intended recipient.

    The C3 default rule for `released` is `[subscribers]` (a release
    announcement), so the subscriber `eve` is the recipient; the owner
    `alice` is NOT notified for a release by default. Push rail dry-run so
    the optional accelerator never touches the network (the standalone inbox
    rail is the one under test). Returns ``(result, alice, eve)``.
    """
    runner = CliRunner()
    alice = register_user(kind="agent", names=["alice"])
    eve = register_user(kind="human", names=["eve"])
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps alice's inbox empty
    # so the `released`-to-subscriber assertion is not polluted.
    add_task(
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
        ],
    )
    return result, alice, eve


def test_emit_event_released_exits_zero(env):
    # Arrange
    # Act
    result, _alice, _eve = _release_to_subscriber(env)
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_released_summary_names_the_event_type(env):
    # Arrange
    # Act
    result, _alice, _eve = _release_to_subscriber(env)
    summary = _summary(result.output)
    # Assert
    assert summary["notify"]["event_type"] == "released"


def test_emit_event_released_summary_enqueues_the_subscriber(env):
    # Arrange
    # Act
    result, _alice, eve = _release_to_subscriber(env)
    summary = _summary(result.output)
    # Assert — the printed summary reports the enqueue to the subscriber only.
    assert summary["notify"]["enqueued"] == [eve.id]


def test_emit_event_released_reaches_subscriber_inbox(env):
    # Arrange
    # Act
    _result, _alice, eve = _release_to_subscriber(env)
    eve_inbox = poll_inbox(eve.id)
    # Assert — end-to-end, the subscriber's standalone INBOX received it.
    assert [r["event_type"] for r in eve_inbox] == ["released"]


def test_emit_event_released_inbox_row_names_the_card(env):
    # Arrange
    # Act
    _result, _alice, eve = _release_to_subscriber(env)
    eve_inbox = poll_inbox(eve.id)
    # Assert
    assert [r["card_id"] for r in eve_inbox] == ["card-rel"]


def test_emit_event_released_leaves_the_owner_inbox_empty(env):
    # Arrange
    # Act
    _result, alice, _eve = _release_to_subscriber(env)
    # Assert — the owner is not a `released` recipient.
    assert poll_inbox(alice.id) == []


def _release_caused_by_the_subscriber(env):
    """Emit `released` where the SUBSCRIBER is also the actor.

    alice is a SUBSCRIBER (so she WOULD be a `released` recipient) AND the
    actor. The actor (the cause of the event) must never be self-notified, so
    her inbox must stay empty even though her role matches. Returns
    ``(result, alice)``.
    """
    runner = CliRunner()
    alice = register_user(kind="agent", names=["alice"])
    add_task(id="card-rel", title="x", agent="bob", subscribers=["alice"])
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
        ],
    )
    return result, alice


def test_emit_event_released_by_the_actor_exits_zero(env):
    # Arrange
    # Act
    result, _alice = _release_caused_by_the_subscriber(env)
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_released_summary_drops_the_actor(env):
    # Arrange
    # Act
    result, _alice = _release_caused_by_the_subscriber(env)
    summary = _summary(result.output)
    # Assert — alice caused it, so nothing is enqueued for her.
    assert summary["notify"]["enqueued"] == []


def test_emit_event_released_actor_is_not_notified(env):
    # Arrange
    # Act
    _result, alice = _release_caused_by_the_subscriber(env)
    # Assert — alice caused it, so her inbox stays empty (actor dropped).
    assert poll_inbox(alice.id) == []


def test_emit_event_extra_payload_parsed(env):
    # Arrange — --extra KEY=VALUE flows into the event envelope; with no
    # recipient it stays a quiet no-op but must still exit 0.
    runner = CliRunner()
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
        ],
    )
    # Assert
    assert result.exit_code == 0, result.output


def _emit_malformed_extra():
    """`--extra` missing '=' must fail loud (no silent drop)."""
    runner = CliRunner()
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
        ],
    )


def test_emit_event_malformed_extra_nonzero(env):
    # Arrange
    # Act
    result = _emit_malformed_extra()
    # Assert
    assert result.exit_code != 0


def test_emit_event_malformed_extra_shows_no_traceback(env):
    # Arrange
    # Act
    result = _emit_malformed_extra()
    # Assert — a usage error, not a crash.
    assert "Traceback" not in result.output


# --------------------------------------------------------------------------- #
# emit-event — fail-loud on an unknown type                                   #
# --------------------------------------------------------------------------- #
def _emit_bogus_type():
    """An unknown `--type` must be refused."""
    runner = CliRunner()
    return runner.invoke(main, ["emit-event", "--type", "bogus", "--repo", "r"])


def test_emit_event_bogus_type_fails_loud(env):
    # Arrange
    # Act
    result = _emit_bogus_type()
    # Assert
    assert result.exit_code != 0


def test_emit_event_bogus_type_shows_no_traceback(env):
    # Arrange
    # Act
    result = _emit_bogus_type()
    # Assert — a usage error, not a crash.
    assert "Traceback" not in result.output


def test_emit_event_bogus_type_echoes_the_bad_value(env):
    # Arrange
    # Act
    result = _emit_bogus_type()
    # Assert
    assert "bogus" in result.output


def test_emit_event_bogus_type_lists_a_valid_type(env):
    # Arrange
    # Act
    result = _emit_bogus_type()
    # Assert — a representative valid type is listed so the producer can
    # self-correct.
    assert "released" in result.output


# --------------------------------------------------------------------------- #
# find-card — repo → card id(s)                                            #
# --------------------------------------------------------------------------- #
def _find_card_across_two_repos():
    """Two cards on the queried repo, one on another; run `find-card`."""
    runner = CliRunner()
    add_task(id="c-a", title="A", assignee="agent:t", repo="owner/repo")
    add_task(id="c-b", title="B", assignee="agent:t", repo="owner/repo")
    add_task(id="c-c", title="C", assignee="agent:t", repo="owner/other")
    return runner.invoke(main, ["find-card", "--repo", "owner/repo"])


def _find_card_with_no_match():
    """No card carries the queried repo; run `find-card`."""
    runner = CliRunner()
    add_task(id="c-a", title="A", assignee="agent:t", repo="owner/other")
    return runner.invoke(main, ["find-card", "--repo", "owner/repo"])


def _find_card_filtered_by_status():
    """Same repo, different statuses; run `find-card --status deferred`.

    (Seeded `deferred` since the pending abolition: an abolished value in the
    store makes every CLI call print the TOLERATED banner, which pollutes the
    output this test parses.)
    """
    runner = CliRunner()
    add_task(
        id="c-deferred",
        title="P",
        assignee="agent:t",
        repo="owner/repo",
        status="deferred",
    )
    add_task(
        id="c-done",
        title="D",
        assignee="agent:t",
        repo="owner/repo",
        status="done",
    )
    return runner.invoke(
        main,
        ["find-card", "--repo", "owner/repo", "--status", "deferred"],
    )


def test_find_card_exits_zero(env):
    # Arrange
    # Act
    result = _find_card_across_two_repos()
    # Assert
    assert result.exit_code == 0, result.output


def test_find_card_prints_matching_ids(env):
    # Arrange
    # Act
    result = _find_card_across_two_repos()
    ids = set(result.output.split())
    # Assert — exactly the two matching ids, one per line.
    assert ids == {"c-a", "c-b"}


def test_find_card_no_match_exits_zero(env):
    # Arrange
    # Act
    result = _find_card_with_no_match()
    # Assert — an empty result is not an error.
    assert result.exit_code == 0, result.output


def test_find_card_empty_when_none_match(env):
    # Arrange
    # Act
    result = _find_card_with_no_match()
    # Assert
    assert result.output.strip() == ""


def test_find_card_status_filter_exits_zero(env):
    # Arrange
    # Act
    result = _find_card_filtered_by_status()
    # Assert
    assert result.exit_code == 0, result.output


def test_find_card_status_filter_selects_only_matches(env):
    # Arrange
    # Act — only the deferred card should match.
    result = _find_card_filtered_by_status()
    # Assert
    assert result.output.split() == ["c-deferred"]


def _find_then_emit(env):
    """The documented producer flow: find-card, then emit-event --card-id.

    End-to-end proof the two verbs compose. `released` notifies subscribers
    (C3 default), so the recipient alice is a SUBSCRIBER on the card. Returns
    ``(resolve, card_id, emit, alice)``.
    """
    runner = CliRunner()
    alice = register_user(kind="agent", names=["alice"])
    add_task(
        id="c-rel",
        title="x",
        agent="bob",
        subscribers=["alice"],
        repo="owner/repo",
    )
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    # Resolve repo → card id.
    resolve = runner.invoke(main, ["find-card", "--repo", "owner/repo"])
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
        ],
    )
    return resolve, card_id, emit, alice


def test_find_card_resolves_the_expected_id(env):
    # Arrange
    # Act
    resolve, card_id, _emit, _alice = _find_then_emit(env)
    # Assert
    assert resolve.exit_code == 0 and card_id == "c-rel"


def test_emit_with_a_resolved_id_exits_zero(env):
    # Arrange
    # Act
    _resolve, _card_id, emit, _alice = _find_then_emit(env)
    # Assert
    assert emit.exit_code == 0, emit.output


def test_find_card_then_emit_with_resolved_id(env):
    # Arrange
    # Act
    _resolve, _card_id, _emit, alice = _find_then_emit(env)
    inbox = poll_inbox(alice.id)
    # Assert — subscriber alice received it via the inbox.
    assert [r["event_type"] for r in inbox] == ["released"]


# EOF
