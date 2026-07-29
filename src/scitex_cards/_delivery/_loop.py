#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The delivery loop — read pending notifications, hand them to channels.

:func:`deliver_pending` is slice 1's one-shot delivery pass (the CLI
``scitex-todo deliver`` command + a future cron/loop runner call it). It is
the orchestrator that wires the parts together while honouring the hard
separation of concerns:

* READ-ONLY on the inbox — ``poll_inbox(unseen_only=False, mark_seen=False)``.
  The loop NEVER flips a user's ``seen`` cursor and NEVER calls ``ack``;
  ``seen`` is the USER's read state, totally separate from delivery state.
* The :class:`~scitex_cards._delivery._ledger.Ledger` is the SOLE delivery
  truth — it decides what's already sent / retry-due.
* :func:`~scitex_cards._delivery._recipients.should_deliver_now` is the policy
  gate (quiet-hours/consent); a False result is a NON-terminal ``skipped``.
* Fail-soft per item: a channel that RAISES is caught, recorded ``failed``
  (retryable), surfaced to stderr, and never stops the other items.
* Fail-soft is NOT fail-silent: a recipient whose INBOX cannot be read is
  counted (``unreadable``) and named in ``faults``, and the pass then reports
  ``pending=None`` — "I could not determine what is pending" — rather than the
  ``0`` that made a day-long outage look like an idle daemon.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

from .._inbox import poll_inbox
from ._channel import DeliveryChannel, DeliveryResult, Status
from ._ledger import MAX_ATTEMPTS, Ledger
from ._recipients import Recipient, load_recipients, should_deliver_now
from ._registry import discover_channels


def _warn(msg: str) -> None:
    """Surface a per-item delivery fault to stderr (fail-loud)."""
    print(f"[scitex-todo delivery] WARNING: {msg}", file=sys.stderr)


def _resolve_channels(
    channels: "dict[str, DeliveryChannel] | None",
) -> dict[str, DeliveryChannel]:
    """Default to discovered channels; accept an injected mapping for tests."""
    if channels is not None:
        return dict(channels)
    return discover_channels()


def _still_owed(ledger: Ledger, user: str, note_id: str, chan_name: str) -> bool:
    """Does the ledger still owe this (notification, channel) a delivery?

    False in exactly two cases, both permanent:

    * ALREADY DELIVERED — the ledger is the truth; never re-send.
    * PERMANENTLY FAILED — the retry budget is exhausted. That comm-miss was
      recorded + surfaced loudly once; re-attempting or re-warning every run
      would spam. The terminal marker stays in the ledger for audit.

    A False here is NOT "nothing to do" for the tick as a whole — it is one
    settled item. The pending COUNT this function feeds is what lets the tick
    line say ``pending=0`` honestly.
    """
    if ledger.already_done(user, note_id, chan_name):
        return False
    return not ledger.is_terminal(user, note_id, chan_name)


def _attempt_one(
    *,
    recipient: Recipient,
    channel_cfg,
    channel: DeliveryChannel,
    note: dict,
    note_id: str,
    ledger: Ledger,
    now: _dt.datetime,
) -> str:
    """Run + record ONE (notification, channel) attempt; return its outcome.

    Returns one of ``"sent" | "failed" | "skipped"`` (the recorded outcome)
    or ``"noop"`` when the item was passed over without a ledger write
    (already sent, or a failure not yet retry-due).
    """
    chan_name = channel.name
    user = recipient.user

    # 1. Settled already — delivered, or permanently failed. See _still_owed.
    if not _still_owed(ledger, user, note_id, chan_name):
        return "noop"

    # 2. A prior failure that is NOT yet due for retry → skip silently.
    if ledger.has_failure(user, note_id, chan_name) and not ledger.retry_eligible(
        user, note_id, chan_name, now
    ):
        return "noop"

    # 3. Policy gate (quiet-hours/consent). False → non-terminal skipped.
    if not should_deliver_now(user, note):
        ledger.record(
            user,
            note_id,
            chan_name,
            DeliveryResult(status=Status.SKIPPED, channel=chan_name),
            now,
        )
        return "skipped"

    # 4. Deliver. A channel raising is caught → retryable failure.
    try:
        result = channel.deliver(
            recipient=user,
            address=channel_cfg.address,
            notification=note,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft per item.
        _warn(
            f"channel {chan_name!r} raised delivering {note_id} to {user}: "
            f"{type(exc).__name__}: {exc}"
        )
        result = DeliveryResult(
            status=Status.FAILED,
            channel=chan_name,
            detail=f"{type(exc).__name__}: {exc}",
        )
    if not isinstance(result, DeliveryResult):
        _warn(
            f"channel {chan_name!r} returned a non-DeliveryResult "
            f"({result!r}) for {note_id}/{user}; treating as failed"
        )
        result = DeliveryResult(
            status=Status.FAILED,
            channel=chan_name,
            detail="channel returned non-DeliveryResult",
        )

    entry = ledger.record(user, note_id, chan_name, result, now)

    # This attempt may have EXHAUSTED the retry budget — the ledger promotes
    # such a failure to a terminal state. Surface that comm-miss LOUDLY (once)
    # and report it distinctly so it is never silently dropped.
    if ledger.is_terminal(user, note_id, chan_name):
        _warn(
            f"TERMINAL comm-miss: channel {chan_name!r} could not deliver "
            f"{note_id} to {user} after {MAX_ATTEMPTS} attempts — giving up. "
            f"detail={entry.get('detail')!r}"
        )
        return "failed_terminal"
    if result.status == Status.SENT:
        return "sent"
    if result.status == Status.SKIPPED:
        return "skipped"
    return "failed"


def deliver_pending(
    store: str | Path | None = None,
    *,
    channels: "dict[str, DeliveryChannel] | None" = None,
    now: _dt.datetime | None = None,
) -> dict:
    """Run one delivery pass over every configured recipient.

    For each recipient (from ``recipients.json``), READ their pending
    notifications (read-only ``poll_inbox``) and, for every
    (notification, configured-channel) pair, attempt delivery subject to the
    ledger dedup/backoff + the policy gate. One bad channel or recipient
    never stops the others.

    Parameters
    ----------
    store : str | Path | None
        Task-store override; resolves the inbox + ledger + recipients dir.
    channels : dict[str, DeliveryChannel] | None
        Injected channel mapping (TEST seam). ``None`` → discovered via the
        ``scitex_cards.delivery_channels`` entry-point group.
    now : datetime.datetime | None
        Override "now" for deterministic backoff in tests; defaults to UTC.

    Returns
    -------
    dict
        ``{"sent": n, "failed": n, "skipped": n, "failed_terminal": n,
        "unreadable": n, "pending": n | None, "faults": [...],
        "outcomes": [...]}`` where each outcome is
        ``{recipient, notification_id, channel, outcome}`` for every item that
        produced a ledger write this run (``noop`` items — already-sent /
        not-yet-retry-due / already-terminal — are NOT listed).
        ``failed_terminal`` counts items whose retry budget was exhausted THIS
        run (a comm-miss surfaced loudly to stderr). ``unreadable`` counts
        recipients whose inbox could not be read at all, and ``pending`` is
        THREE-VALUED: an int when every inbox was readable, else ``None`` —
        "could not determine", never a stand-in ``0``.
    """
    now = now or _dt.datetime.now(_dt.timezone.utc)
    resolved_channels = _resolve_channels(channels)
    ledger = Ledger.load(store)
    recipients = load_recipients(store)

    counts = {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "failed_terminal": 0,
        "unreadable": 0,
    }
    outcomes: list[dict] = []
    faults: list[str] = []
    pending = 0

    for recipient in recipients:
        # READ-ONLY: full history, never advance the user's seen cursor.
        try:
            notes = poll_inbox(
                recipient.user,
                unseen_only=False,
                mark_seen=False,
                store=store,
            )
        except Exception as exc:  # noqa: BLE001 — one bad recipient ≠ all.
            # COUNTED, not merely warned: with this recipient's inbox unread we
            # no longer know what is pending, and the pass must say so.
            _warn(
                f"failed to read inbox for {recipient.user!r}: "
                f"{type(exc).__name__}: {exc}; skipping recipient"
            )
            counts["unreadable"] += 1
            faults.append(f"inbox[{recipient.user}]: {type(exc).__name__}: {exc}")
            continue

        for note in notes:
            note_id = note.get("id")
            if not note_id:
                continue
            for channel_cfg in recipient.channels:
                channel = resolved_channels.get(channel_cfg.kind)
                if channel is None:
                    _warn(
                        f"no channel registered for kind "
                        f"{channel_cfg.kind!r} (recipient {recipient.user}); "
                        f"skipping that channel"
                    )
                    continue
                if _still_owed(ledger, recipient.user, note_id, channel.name):
                    pending += 1
                outcome = _attempt_one(
                    recipient=recipient,
                    channel_cfg=channel_cfg,
                    channel=channel,
                    note=note,
                    note_id=note_id,
                    ledger=ledger,
                    now=now,
                )
                if outcome == "noop":
                    continue
                counts[outcome] += 1
                outcomes.append(
                    {
                        "recipient": recipient.user,
                        "notification_id": note_id,
                        "channel": channel.name,
                        "outcome": outcome,
                    }
                )

    # THREE-VALUED: an unreadable inbox means the pending count is a guess, and
    # a guess reported as 0 is what made a broken daemon look idle. Say unknown.
    return {
        **counts,
        "pending": None if counts["unreadable"] else pending,
        "faults": faults,
        "outcomes": outcomes,
    }


__all__ = ["deliver_pending"]

# EOF
