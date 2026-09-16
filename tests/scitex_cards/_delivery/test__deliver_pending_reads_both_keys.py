#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The delivery pass reads BOTH inbox keys a recipient's notifications live under.

THE LAST READER THAT DID NOT. A producer enqueues under whatever
``_notify.resolve_recipients`` returned — the stable ``u_*`` id when the agent
is registered, the raw name otherwise. Every other reader on this rail already
tries both: ``_inbox_confirm.recipient_keys`` exists for exactly this, and
``_mcp_channel`` and ``_inbox_present`` both use it. ``deliver_pending`` polled
``recipient.user`` alone, so a ``recipients.json`` row spelled with the raw
name reads the empty drawer whenever that name resolves to a ``u_*`` id — the
messages sitting in the store, readable, while the reader looks elsewhere.

That is the silent-miss shape this entire rail exists to catch: it produces no
error, no fault and no missing-delivery signal. The pass reports zero pending
and succeeds.

WHY THE DRAFT SHIPPED WITHOUT A BEHAVIOURAL TEST, AND WHAT THIS REVISION ADDS
=============================================================================
The author wrote three behavioural tests and could not get them green —
*including the control* — and shipped only the wiring pin, because a test whose
colour they could not explain is not evidence. The reason, now measured:
``deliver_pending``'s key resolution runs ``recipient_keys(user, store)`` →
``resolve_user`` → ``load_users`` → the DATABASE, *always*. On a local
``tmp_path`` store with no registered user, ``recipient_keys("rawname")``
returns ``["rawname"]`` — the single key the pass already read — and the fix is
a behavioural NO-OP. The author's file-store arrangement could not produce the
``rawname → u_*`` split that makes the fix matter, so every attempt (control
included) looked the same. That is the wall, not a wrong code change.

This revision supplies the one thing the draft's arrangement was missing: a
user ACTUALLY REGISTERED in the store's database, so that
``recipient_keys("rawname", store)`` returns ``["rawname", "<u_* id>"]``.
The store is the harness's writable PostgreSQL, addressed through the
file-path label pattern (``tmp_path / "tasks.yaml"``) that the rest of the
``_delivery`` suite uses — ``database_for`` resolves the label to the ambient
store, so ``register_user`` / ``enqueue`` / ``resolve_user`` all hit the same
database the harness bootstraps. No ``SCITEX_CARDS_TEST_DSN`` guard is needed:
the same harness that makes ``test__loop.py`` green makes this green.

A real ``RecorderChannel`` is injected through the ``channels=`` seam, so no
wire or installed entry point is touched (STX-NM / PA-306).
"""

from __future__ import annotations

import inspect
import json

import pytest

from scitex_cards._delivery import _loop
from scitex_cards._delivery._loop import deliver_pending
from scitex_cards._inbox import enqueue

from ._fakes import RecorderChannel


def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_recipients(tmp_path, mapping: dict) -> None:
    """Write ``recipients.json`` next to the store with ``{users: mapping}``."""
    path = tmp_path / "recipients.json"
    path.write_text(json.dumps({"users": mapping}), encoding="utf-8")


def _seed(store, recipient: str, *, card_id: str, ts: str) -> str:
    """Enqueue one notification into ``recipient``'s inbox; return its id."""
    rec = enqueue(
        recipient,
        event_type="reassigned",
        card_id=card_id,
        body=f"Card {card_id} reassigned to you",
        actor="bob",
        ts=ts,
        store=store,
    )
    if rec is None:
        raise AssertionError("enqueue returned no notification record")
    return rec["id"]


def _register_user(kind: str, name: str, store) -> str:
    """Register an agent in the store's database; return its ``u_*`` id.

    The sanity check (the id is a real ``u_*`` value, distinct from the raw
    name) belongs to the arrangement, not the assertion under test — so the
    test body carries exactly one assert (STX-TQ007).
    """
    from scitex_cards import _users

    user = _users.register_user(kind=kind, names=[name], store=store)
    if not user.id or user.id == name:
        raise AssertionError(
            f"register_user({name!r}) did not mint a distinct u_* id: {user.id!r}"
        )
    return user.id


# --------------------------------------------------------------------------- #
# (0) the wiring pin — kept verbatim from the draft                           #
# --------------------------------------------------------------------------- #
def test_the_pass_asks_for_every_key_the_recipient_can_live_under() -> None:
    """THE WIRING PIN. Honest about what it proves: the shared helper is called
    at this site. It does not prove the union is correct (the behavioural tests
    beside it do), but it buys that a later refactor dropping the helper goes
    red rather than silently restoring the defect — the failure mode that let
    this one reader diverge from the other three in the first place.
    """
    # Arrange
    source = inspect.getsource(_loop.deliver_pending)
    # Act
    uses_shared_helper = "recipient_keys" in source
    # Assert
    assert uses_shared_helper


# --------------------------------------------------------------------------- #
# (1) the regression: note under u_* id, recipient spelled with raw name      #
# --------------------------------------------------------------------------- #
def test_note_under_registered_id_is_delivered_when_recipient_is_raw_name(tmp_path) -> None:
    """THE DEFECT, MEASURED: the producer filed the note under the ``u_*`` id
    (the agent is registered), the ``recipients.json`` row is spelled with the
    raw name, and the pass still reaches it. Before the fix,
    ``deliver_pending`` read the raw-name drawer alone and reported zero
    pending while the message sat in the store — the silent miss.
    ``recipient_keys("dp-rawname-probe", store)`` returns
    ``["dp-rawname-probe", "<u_* id>"]`` because the id IS registered, so the
    union read finds the note.
    """
    # Arrange
    store = _store(tmp_path)
    u_id = _register_user("agent", "dp-rawname-probe", store)
    note_id = _seed(store, u_id, card_id="c-reg", ts="2026-06-27T10:00:00Z")
    _write_recipients(tmp_path, {"dp-rawname-probe": {"channels": [{"kind": "log"}]}})
    recorder = RecorderChannel()
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert any(c["notification"]["id"] == note_id for c in recorder.calls)


# --------------------------------------------------------------------------- #
# (2) the control: note under raw name, recipient spelled with raw name       #
# --------------------------------------------------------------------------- #
def test_note_under_raw_name_is_delivered_when_recipient_is_raw_name(tmp_path) -> None:
    """CONTROL (already worked before the change; must not regress): the note
    lives under the raw name and the recipient is the raw name, so the
    raw-name drawer is the one that finds it. No user registration is needed
    — this is the pre-registry world the fix must not break.
    """
    # Arrange
    store = _store(tmp_path)
    note_id = _seed(store, "dp-control-raw", card_id="c-ctl", ts="2026-06-27T10:00:00Z")
    _write_recipients(tmp_path, {"dp-control-raw": {"channels": [{"kind": "log"}]}})
    recorder = RecorderChannel()
    # Act
    deliver_pending(store=store, channels={"log": recorder})
    # Assert
    assert any(c["notification"]["id"] == note_id for c in recorder.calls)
