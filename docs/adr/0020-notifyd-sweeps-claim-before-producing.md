# ADR-0020: notifyd sweeps claim the shared board before producing

**Status:** Accepted

**Date:** 2026-09-14

## Context

The fleet runs a notify daemon on several hosts against one PostgreSQL board.
The database already provides `claim_sweep`, an expiring, transaction-locked
cadence claim, but no production caller used it. Consequently each daemon ran
the reminder and liveness producers. The live notification ledger showed two
reminder rows for the same recipient and synthetic digest card, created one
second apart on different hosts; both were later delivered when the agent
became reachable, causing two full-context turns for one board snapshot.

The existing per-owner fingerprint state is not a concurrency primitive: two
hosts can read the old value before either writes the new one. Inbox
`supersede` also did not prevent replay: it advances the older snapshots out
of the pull inbox, while notifyd correctly reads full history because delivery
state and the user's seen cursor are separate. Notifyd then treated every row
in that history as still deliverable. On restart, one live agent confirmed 64
queued turns in 107 seconds, 55 of them cumulative snapshots up to a week old.

## Decision

Only the always-on notifyd paths claim before producing:

- reminder production uses `notifyd-reminders` for the daemon's actual tick
  interval;
- stale-active, pending-backlog, and blocked-check production share
  `notifyd-liveness` for the configured liveness-sweep interval.

The first host writes the expiring claim under the existing PostgreSQL
transaction-scoped advisory lock. Peers that lose the claim log an explicit
skip and still continue with delivery of already-enqueued notifications.

The interactive `print-stats --notify` path does not take these claims. It is
an explicit operator action and must not silently do nothing because a daemon
recently swept. Non-PostgreSQL and claim-error behaviour remains fail-open as
specified by `claim_sweep`: a visible duplicate is safer than fleet-wide
notification silence.

At the delivery boundary, full inbox history is reduced only for the four
cumulative snapshot keys (`reminder`, `stale-active`, `pending-backlog`, and
`blocked-check`, each with its stable synthetic card id). Only the newest row
of each key is eligible for a push. Distinct DMs, comments, assignments, and
other per-card events retain arrival order and are never coalesced.

## Consequences

One shared board now has one scheduled producer per cadence, regardless of the
number or phase of notify daemons. A winner that fails after claiming can delay
that producer until the finite cadence expires (at most one delivery tick for
reminders or one liveness interval for nudges). Delivery itself is not claimed,
so any daemon may continue draining durable notifications.

An offline agent now receives at most four liveness/reminder snapshot turns
when it reconnects, rather than every historical version of those snapshots.
This is computed from immutable arrival order and needs no destructive cleanup
or coupling between the inbox and delivery ledgers.
