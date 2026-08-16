#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE SECOND DELIVERY RAIL — the Stop hook delivers, then asks for the ack.

WHY. Operator DMs did not reach an agent for roughly three weeks. Delivery had
exactly ONE rail: the MCP channel push. The agent spec whitelisted
``server:scitex-todo`` while ``.mcp.json`` registered the server as
``scitex-cards`` (renamed during the migration), so Claude Code SILENTLY
DISCARDED every push. ``send()`` still returned normally, the drain acked on
that success, and the message was gone. Measured at the time: 228 inbox rows
for that agent, ZERO unseen. The same hazard was later found armed on ~96 spec
entries fleet-wide.

Fixing the one spec is not a fix for the class. A single rail with nothing
independent checking it fails again, silently, because "the transport returned"
is not "the recipient received".

THE ORDER OF OPERATIONS IS THE SAFETY PROPERTY, and it is what these tests
pin: PULL, PRESENT, then require the ack. A hook that merely blocked on unacked
messages would have deadlocked every agent that morning — nothing had been
shown, so nothing COULD have been acked. The hook may only ever demand an ack
for a message it has just put in front of the agent.

BOUNDED. A hook that can refuse forever is a new outage, so the retry limit and
the fail-open paths are pinned here too.

No mocks: a real store, real ``_inbox`` records, the real ``confirm_
notifications`` verb. The defect this guards against was mock-shaped — every
layer reported success while nothing arrived — so a mocked store would test
exactly the wrong thing.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from scitex_cards._cli._stop_hook import evaluate, stop_hook_cmd
from scitex_cards._inbox import enqueue, poll_inbox
from scitex_cards._inbox_confirm import confirm_notifications
from scitex_cards._inbox_present import INJECTED_CONTEXT_CAP, MAX_PRESENTED
from scitex_cards._stop_hook_bound import MAX_PRESENTATIONS

AGENT = "rail-tester"

#: A canonical database under a directory that cannot even be created — the
#: honest stand-in for "the board had a bad day", which must never wedge an
#: agent. MEASURED (2026-07-29) to raise ``RuntimeError: canonical store ...
#: does not exist``, which is the whole point: a fail-open test whose arrange
#: step does not actually fail proves nothing. ``/nonexistent/...`` was tried
#: first and does NOT raise here — it reads as an empty board — so it is
#: exactly the vacuous arrangement this constant exists to avoid.
UNREADABLE_DB = "/proc/1/definitely-not-a-directory/cards.db"

#: An inbox database under a path that cannot even be created, so the message
#: rail fails on its own rather than borrowing the board's failure.
UNREADABLE_INBOX_DB = "/proc/1/definitely-not-a-directory/todo.db"


def _store():
    """The per-test scratch store the suite's conftest pinned us to."""
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


#: Monotonic second-counter for enqueue timestamps. ``_inbox.enqueue`` dedups
#: on ``(event_type, card_id, ts, actor)``, so two sends sharing a timestamp
#: silently collapse into one and the second call returns ``None``. Handing
#: every send its own stamp keeps "two messages" meaning two messages.
_TICK = [0]


def _send(body="please confirm you got this", count=1, agent=AGENT):
    """Enqueue ``count`` unseen operator DMs; return their ids."""
    ids = []
    for i in range(count):
        _TICK[0] += 1
        ids.append(
            enqueue(
                agent,
                event_type="dm",
                card_id=f"dm:operator::{agent}",
                body=f"{body} #{i}",
                actor="operator",
                ts=f"2026-07-29T07:{_TICK[0] // 60:02d}:{_TICK[0] % 60:02d}Z",
                store=_store(),
            )["id"]
        )
    return ids


def _decide(session_id="sess-1", stop_hook_active=False, agent=AGENT, store=None):
    """The hook's decision for ``agent`` — the JSON it would print."""
    return evaluate(
        agent,
        session_id=session_id,
        stop_hook_active=stop_hook_active,
        store=_store() if store is None else store,
    )


# --------------------------------------------------------------------------- #
# 1. A pending unacked message BLOCKS                                         #
# --------------------------------------------------------------------------- #
def test_a_pending_unacked_message_blocks_the_stop():
    # Arrange
    _send()

    # Act
    out = _decide()

    # Assert
    assert out["decision"].get("decision") == "block"


def test_the_block_shows_the_message_text():
    """The block IS the delivery. A block that withholds the text is the
    deadlock: it demands action on something the agent cannot read."""
    # Arrange
    _send(body="migrate off Telegram onto cards DMs")

    # Act
    out = _decide()

    # Assert
    assert "migrate off Telegram onto cards DMs" in out["decision"]["reason"]


def test_the_block_names_the_sender():
    # Arrange
    _send()

    # Act
    out = _decide()

    # Assert
    assert "operator" in out["decision"]["reason"]


def test_the_block_names_the_verb_that_releases_it():
    """A refusal that does not say how to comply leaves the agent stuck."""
    # Arrange
    _send()

    # Act
    out = _decide()

    # Assert
    assert "ack_notifications" in out["decision"]["reason"]


def test_the_block_offers_a_path_that_needs_no_mcp():
    """STANDALONE. An agent with scitex-cards and nothing else must be able
    to comply, or the hook blocks where the actor cannot remediate."""
    # Arrange
    _send()

    # Act
    out = _decide()

    # Assert
    assert "scitex-cards inbox ack" in out["decision"]["reason"]


def test_the_hook_read_does_not_mark_anything_seen():
    """READING NEVER CONFIRMS. If the hook acked what it showed, an agent
    that crashed mid-turn would have lost the message permanently."""
    # Arrange
    _send()
    _decide()

    # Act
    still_unseen = poll_inbox(AGENT, unseen_only=True, mark_seen=False, store=_store())

    # Assert
    assert len(still_unseen) == 1


# --------------------------------------------------------------------------- #
# 2. Acking RELEASES                                                          #
# --------------------------------------------------------------------------- #
def test_acking_the_delivered_ids_releases_the_block():
    # Arrange
    ids = _send()
    confirm_notifications(AGENT, ids, store=_store())

    # Act
    out = _decide()

    # Assert
    assert out["decision"] == {}


def test_acking_only_some_ids_still_blocks_on_the_rest():
    """Per-id confirmation, not all-or-nothing: the unconfirmed one returns."""
    # Arrange
    ids = _send(count=3)
    confirm_notifications(AGENT, ids[:2], store=_store())

    # Act
    out = _decide()

    # Assert
    assert ids[2] in out["decision"]["reason"]


# --------------------------------------------------------------------------- #
# 3. THE DEADLOCK GUARD — never demand an ack for a message not shown         #
# --------------------------------------------------------------------------- #
def test_the_block_demands_an_ack_only_for_ids_whose_text_it_showed():
    """LOAD-BEARING. On the morning of the outage the queued messages had
    never been shown, so no agent COULD have acked them; a hook that demanded
    an ack anyway would have converted a silent-loss bug into a fleet-wide
    deadlock. The ids named in the ack call must be exactly the ids whose
    bodies are in the same reason string."""
    # Arrange — far more mail than one turn can present.
    _send(count=MAX_PRESENTED * 3)

    # Act
    reason = _decide()["decision"]["reason"]
    demanded = json.loads("[" + reason.split("ids=[")[1].split("]")[0] + "]")

    # Assert
    assert all(reason.count(nid) >= 2 for nid in demanded)


def test_messages_beyond_the_turns_capacity_are_not_demanded():
    """They are not lost either: unconfirmed means unseen means redelivered."""
    # Arrange
    sent = _send(count=MAX_PRESENTED * 3)

    # Act
    reason = _decide()["decision"]["reason"]
    demanded = json.loads("[" + reason.split("ids=[")[1].split("]")[0] + "]")

    # Assert
    assert len(demanded) < len(sent)


def test_a_message_that_was_not_shown_is_never_demanded():
    """THE DEADLOCK GUARD, stated directly. Every id the hook withholds must
    be absent from the ack call it emits — otherwise the agent is told to
    confirm something it cannot read, which is a refusal it cannot satisfy."""
    # Arrange
    sent = _send(count=MAX_PRESENTED * 3)

    # Act
    reason = _decide()["decision"]["reason"]
    demanded = json.loads("[" + reason.split("ids=[")[1].split("]")[0] + "]")
    withheld = [nid for nid in sent if nid not in demanded]

    # Assert
    assert not any(nid in reason for nid in withheld)


def test_the_withheld_remainder_is_reported_rather_than_hidden():
    """An omission the agent cannot see is a lie about their inbox — that is
    exactly how three weeks of DMs went unnoticed."""
    # Arrange
    _send(count=MAX_PRESENTED * 3)

    # Act
    out = _decide()

    # Assert
    assert "more unread" in out["decision"]["reason"]


# --------------------------------------------------------------------------- #
# 4. FAIL-OPEN — an unreadable store WARNS, it does not block                 #
# --------------------------------------------------------------------------- #
def test_an_unreadable_board_allows_the_stop(env):
    """Our own bug must never be the reason an agent cannot finish a turn."""
    # Arrange — the canonical database does not exist, so reading it raises.
    env.set("SCITEX_CARDS_DB", UNREADABLE_DB)

    # Act
    out = _decide(store=None)

    # Assert
    assert out["decision"] == {}


def test_an_unreadable_board_says_why_it_is_silent(env):
    """Silence with no explanation is how the original outage stayed hidden."""
    # Arrange
    env.set("SCITEX_CARDS_DB", UNREADABLE_DB)

    # Act
    out = _decide(store=None)

    # Assert
    assert any("board rail unavailable" in w for w in out["warnings"])


def test_an_unreadable_inbox_allows_the_stop(env):
    """The message rail fails on its own terms — a mail read that raises must
    not wedge the agent any more than a board read that raises."""
    # Arrange — an inbox database at a path that cannot even be created.
    env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")
    env.set("SCITEX_CARDS_INBOX_DB", UNREADABLE_INBOX_DB)

    # Act
    out = _decide()

    # Assert
    assert out["decision"] == {}


def test_an_unreadable_inbox_says_why_it_is_silent(env):
    # Arrange
    env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")
    env.set("SCITEX_CARDS_INBOX_DB", UNREADABLE_INBOX_DB)

    # Act
    out = _decide()

    # Assert
    assert any("message rail unavailable" in w for w in out["warnings"])


def test_an_unresolvable_agent_id_allows_the_stop():
    """No identity means no inbox to read; refusing here helps nobody."""
    # Arrange

    # Act
    result = CliRunner().invoke(stop_hook_cmd, ["--agent", "unknown"], input="{}")

    # Assert
    assert json.loads(result.stdout) == {}


def test_a_failing_hook_still_exits_zero():
    # Arrange

    # Act
    result = CliRunner().invoke(stop_hook_cmd, ["--agent", "unknown"], input="{}")

    # Assert
    assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# 5. BOUNDED — a hook that can refuse forever is a new outage                 #
# --------------------------------------------------------------------------- #
def test_the_same_message_stops_blocking_after_the_retry_limit():
    """We stop escalating. We do NOT stop remembering: the record is left
    unseen in the store for the operator and for every other rail."""
    # Arrange
    _send()
    for _ in range(MAX_PRESENTATIONS):
        _decide(session_id="looping")

    # Act
    out = _decide(session_id="looping")

    # Assert
    assert out["decision"] == {}


def test_giving_up_on_a_message_is_announced():
    """A bound that gives up quietly is indistinguishable from the outage."""
    # Arrange
    _send()
    for _ in range(MAX_PRESENTATIONS):
        _decide(session_id="looping")

    # Act
    out = _decide(session_id="looping")

    # Assert
    assert any("giving up on" in w for w in out["warnings"])


def test_a_message_the_hook_gave_up_on_is_still_unseen_in_the_store():
    """Giving up on BLOCKING must never mean marking it delivered."""
    # Arrange
    _send()
    for _ in range(MAX_PRESENTATIONS + 1):
        _decide(session_id="looping")

    # Act
    still_unseen = poll_inbox(AGENT, unseen_only=True, mark_seen=False, store=_store())

    # Assert
    assert len(still_unseen) == 1


def test_the_retry_budget_is_per_message_not_per_agent():
    """A newly arrived message gets its own chances, whatever came before."""
    # Arrange
    _send(body="the old one")
    for _ in range(MAX_PRESENTATIONS + 1):
        _decide(session_id="looping")
    fresh = _send(body="the new one")

    # Act
    out = _decide(session_id="looping")

    # Assert
    assert fresh[0] in out["decision"]["reason"]


# --------------------------------------------------------------------------- #
# 6. The reason must fit the channel it is injected through                   #
# --------------------------------------------------------------------------- #
def test_the_reason_fits_the_injected_context_cap():
    """Over-cap hook output is spilled to a file and replaced by a preview —
    a message that got spilled was not delivered."""
    # Arrange
    _send(body="x" * 5000, count=MAX_PRESENTED * 3)

    # Act
    out = _decide()

    # Assert
    assert len(out["decision"]["reason"]) < INJECTED_CONTEXT_CAP


def test_the_hook_prints_its_decision_as_json_on_stdout():
    """The Claude Code contract: stdout is the decision, stderr is commentary."""
    # Arrange
    _send()

    # Act
    result = CliRunner().invoke(stop_hook_cmd, ["--agent", AGENT], input="{}")

    # Assert
    assert json.loads(result.stdout)["decision"] == "block"


# --------------------------------------------------------------------------- #
# 7. The backend PRODUCTION actually runs                                     #
# --------------------------------------------------------------------------- #
#
# This suite pins ``SCITEX_CARDS_INBOX_BACKEND=yaml`` for every test while the
# fleet runs SQLite (see tests/scitex_cards/conftest.py and the note in
# tests/scitex_cards/_delivery/test__tick_truth.py). A delivery rail proven
# only on the break-glass backend is proven on a configuration nobody runs —
# and "measured somewhere else" is how the original outage stayed invisible.
# So the block/release pair is re-run against SQLite explicitly.


def test_a_pending_message_blocks_on_the_sqlite_backend(env):
    # Arrange
    env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")
    _send()

    # Act
    out = _decide()

    # Assert
    assert out["decision"].get("decision") == "block"


def test_acking_releases_the_block_on_the_sqlite_backend(env):
    # Arrange
    env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")
    ids = _send()
    confirm_notifications(AGENT, ids, store=_store())

    # Act
    out = _decide()

    # Assert
    assert out["decision"] == {}


def test_the_sqlite_read_does_not_mark_anything_seen(env):
    # Arrange
    env.set("SCITEX_CARDS_INBOX_BACKEND", "sqlite")
    _send()
    _decide()

    # Act
    still_unseen = poll_inbox(AGENT, unseen_only=True, mark_seen=False, store=_store())

    # Assert
    assert len(still_unseen) == 1


# EOF
