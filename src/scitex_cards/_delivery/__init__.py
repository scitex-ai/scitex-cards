#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone notification-DELIVERY loop for scitex-todo.

scitex-todo already EMITS card-events and ENQUEUEs per-recipient
notifications into the YAML pull-inbox (:mod:`scitex_cards._inbox`), but
nothing pushes those records OUT to recipients — they sit unread. This
package adds scitex-todo's OWN standalone delivery rail: a loop that reads
each user's pending notifications and hands them to the channels configured
for that user (log, telegram, …), tracking what was delivered in a keyed
dedup ledger so nothing is double-sent.

A self-contained delivery rail, fully independent — this package has ZERO
dependency on any external agent runtime in either direction. The
first-class recipient here is a "user" (not an "agent"): policy + addressing
live in :mod:`scitex_cards._delivery._recipients`, never inside a channel.

Hard separation of concerns
---------------------------
* The user's inbox ``seen`` cursor is the USER's READ state — the delivery
  loop NEVER flips it (read-only ``poll_inbox(mark_seen=False)``).
* The :class:`~scitex_cards._delivery._ledger.Ledger` is the SOLE source of
  delivery truth (what was sent / failed / is retry-eligible).
* Channels are dumb transports: they hand off bytes and report
  ``sent | failed | skipped`` — they never decide WHETHER to deliver.

Slice 1 ships the port + a stdlib-logging channel + registry + ledger +
recipients + the loop + a ``scitex-todo deliver`` one-shot CLI command. Slice 2
adds the always-on daemon (:func:`run_notifyd`, single-instance-locked +
signal-aware, with throttled terminal-comm-miss re-surfacing), the
``scitex-todo notifyd`` CLI verb, and an operator-gated systemd user-unit
template + install helper.
"""

from __future__ import annotations

from ._channel import DeliveryChannel, DeliveryResult, Status
from ._daemon import (
    DaemonAlreadyRunning,
    pidfile_path,
    report_terminal_misses,
    run_notifyd,
)
from ._liveness import (
    DeliveryLiveness,
    TickHealth,
    TickState,
    assess_delivery_liveness,
    liveness_path,
)
from ._loop import deliver_pending
from ._recipients import load_recipients, should_deliver_now
from ._registry import discover_channels

__all__ = [
    "DaemonAlreadyRunning",
    "DeliveryChannel",
    "DeliveryLiveness",
    "DeliveryResult",
    "Status",
    "TickHealth",
    "TickState",
    "assess_delivery_liveness",
    "deliver_pending",
    "discover_channels",
    "liveness_path",
    "load_recipients",
    "pidfile_path",
    "report_terminal_misses",
    "run_notifyd",
    "should_deliver_now",
]

# EOF
