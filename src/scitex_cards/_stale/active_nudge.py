#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stale-active + pending-backlog nudge delivery — the enqueue side.

Pairs with the pure detectors in :mod:`scitex_cards._stale_active`:
that module decides WHICH cards are stale-active / pending-backlog and
groups them by owner; THIS module delivers a concise per-owner nudge
over the SAME rail the owner DIGEST uses — the standalone per-recipient
PULL-INBOX (:func:`scitex_cards._inbox.enqueue`, wrapped by
:func:`scitex_cards._reminder_enqueue._safe_enqueue`). One sweep emits up to
two distinct per-owner lines:

* ``stale-active`` (kind="stale-active"): "close/update the in_progress
  / blocked work you said you were doing".
* ``pending-backlog`` (kind="pending-backlog"): "start or triage the
  pending cards you accepted but never began".

Both ride the SAME ``*/10`` ``--nudge-quiet`` cron — no new cron is
added — and both are fail-soft per owner.

WHY THE INBOX RAIL (the 2026-07-12 fix)
---------------------------------------
The nudge originally pushed on the turn-url wire
(:func:`scitex_cards._push.deliver`). That wire is NOT provisioned for
almost any agent, and a containerized agent cannot be POSTed to at all —
so once the sweep was actually scheduled inside notifyd (v0.8.2) it
delivered to NOBODY::

    notifyd liveness sweep: ERR  scitex-todo    32 pending  wire=http  reason=transport-error
    notifyd liveness sweep: ERR  scitex-types    2 pending  wire=http  reason=no-turn-url-configured
    notifyd liveness sweep: # 0 pending-backlog push(es) sent

The DIGEST (:mod:`scitex_cards._reminders`) has always used the rail that
works: it ENQUEUES into the recipient's per-agent pull-inbox, which every
agent already drains. The nudge now uses the SAME path, the SAME helpers
(:func:`~scitex_cards._reminder_enqueue._safe_resolve` /
:func:`~scitex_cards._reminder_enqueue._safe_enqueue`) and the SAME record
shape, so an agent's existing drain picks nudges up with no change on its
side. The turn-url push survives ONLY as an explicitly OPT-IN, strictly
SECONDARY echo (:data:`ENV_NUDGE_PUSH`) for a host-reachable receiver; it
is never a fallback and never decides whether a nudge counted as delivered.

Why a separate module (not inline in ``_cli/_stats.py``): ``_stats.py``
is at the line cap. Keeping the delivery loop here also keeps the pure
detector free of any delivery import so it stays unit-testable with plain
list-of-dicts inputs.

Behaviour (rides the existing ``*/10`` ``--nudge-quiet`` cron):

* NOT liveness-gated. We nudge the owner regardless of whether the agent
  is currently online. An idle / offline owner is exactly the case where
  a stale active card is most likely forgotten; gating on liveness would
  suppress the most important nudges. Detection is purely time-based
  (``last_activity`` recency) — liveness is a separate concern owned by
  the wake-watcher / mesh.
* Fail-soft per owner. A delivery failure (a bad recipient, an unwritable
  store, even an unexpected raise) for one owner is LOGGED as an error and
  the sweep continues — one bad owner never breaks the batch.
* FAIL LOUD. A failed owner is an ``ERR`` line AND a ``logger.error``; a
  sweep where every ATTEMPTED owner failed emits an unmissable ``!! ALERT``
  line (``reached NOBODY``). A quiet "0 sent" is exactly what let the
  turn-url rail ship broken.
* DELIVER ON CHANGE. The sweep is scheduled (the notifyd low-cadence tick
  as well as the ``*/10`` cron), and re-pushing an IDENTICAL nudge on every
  tick is how a signal becomes noise — the same pathology the digest path
  hit on 2026-07-11 (26 identical digests in ~2 h). So a per-(owner, kind)
  fingerprint of the STALE CARD SET is persisted in a sidecar
  (:data:`NUDGE_SIDECAR_NAME`) and the push is skipped while that set is
  unchanged, UNTIL :data:`ENV_NUDGE_FLOOR_HOURS` elapses (a genuinely stuck
  owner is still nudged daily). Suppressed owners are still LOGGED — a
  silent sweep is one nobody trusts.

Optional hook event: when ``SCITEX_TODO_STALE_ACTIVE_EMIT_HOOK=1`` and
the package's hook dispatcher is importable, the sweep also emits a
``stale-active`` finding so scitex-dev's ecosystem reconcile can consume
it. The primary deliverable is the per-owner nudge; the hook emission is
best-effort and never affects the nudge result.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
from pathlib import Path

from .._reminder_enqueue import _iso, _safe_enqueue, _safe_resolve
from .active import (
    blocked_external_nudge_line,
    detect_blocked_external,
    detect_pending_backlog,
    detect_stale_active,
    pending_backlog_nudge_line,
    stale_active_nudge_line,
)
from .._throughput import _now_utc, _parse_iso

logger = logging.getLogger(__name__)

ENV_EMIT_HOOK = "SCITEX_TODO_STALE_ACTIVE_EMIT_HOOK"

#: OPT-IN secondary echo of each nudge on the turn-url push wire
#: (:func:`scitex_cards._push.deliver`) — for the rare host-reachable receiver
#: that wants an out-of-band ping. Set to ``1`` to enable. It is an ACCELERATOR,
#: never a rail: the inbox enqueue is always attempted and is the ONLY thing
#: that decides delivered/failed (and therefore the suppression). There is NO
#: fallback between the two rails in either direction.
ENV_NUDGE_PUSH = "SCITEX_TODO_NUDGE_PUSH"

#: Sidecar holding the per-(owner, kind) deliver-on-change state. A SIBLING of
#: the reminder sidecar in the store's ``runtime/`` dir — deliberately its own
#: file, not a third section of ``reminders.yaml``: the two sweeps run from
#: DIFFERENT processes (notifyd vs the ``*/10`` stats cron), so sharing one
#: read-modify-write file would let a stale-nudge save clobber the reminder
#: engine's escalation latches.
NUDGE_SIDECAR_NAME = "nudges.yaml"

#: How long an UNCHANGED nudge stays suppressed before it is re-sent anyway.
#: Mirrors :data:`scitex_cards._reminder_enqueue.ENV_DIGEST_FLOOR_HOURS`: without
#: a floor, deliver-on-change would go silent forever on a frozen backlog.
ENV_NUDGE_FLOOR_HOURS = "SCITEX_TODO_NUDGE_FLOOR_HOURS"
DEFAULT_NUDGE_FLOOR_HOURS = 24.0

#: Inbox ``event_type`` per kind (the inbox dedup discriminator + the drain's
#: display key). Same shape as :data:`scitex_cards._reminders.EVENT_DIGEST`.
KIND_STALE_ACTIVE = "stale-active"
KIND_PENDING_BACKLOG = "pending-backlog"
#: The externally-blocked re-check. A SEPARATE kind (not a flavour of
#: stale-active) precisely so it carries its own deliver-on-change state and
#: its own, far more lenient clock: these cards are blocked on something the
#: owner cannot move, so the only honest question is "has your blocker
#: cleared?" — never "why haven't you reconciled this?"
KIND_BLOCKED_CHECK = "blocked-check"

#: Synthetic ``card_id`` per kind — a nudge is ABOUT many cards, not one
#: (mirrors :data:`scitex_cards._reminders.DIGEST_CARD_ID`). Distinct per kind so
#: the ``supersede`` replace only ever collapses an owner's unseen nudge of the
#: SAME kind: a nudge is a cumulative point-in-time snapshot, so a fresh one
#: strictly replaces its unseen predecessor and an owner whose drain is down
#: never accumulates a replay-storm.
NUDGE_CARD_ID = {
    KIND_STALE_ACTIVE: "(stale-active)",
    KIND_PENDING_BACKLOG: "(pending-backlog)",
    KIND_BLOCKED_CHECK: "(blocked-check)",
}


def _floor_minutes() -> float:
    """Suppression floor for an unchanged nudge, in minutes (env-overridable)."""
    raw = os.environ.get(ENV_NUDGE_FLOOR_HOURS)
    try:
        hours = float(raw) if raw is not None else DEFAULT_NUDGE_FLOOR_HOURS
    except (TypeError, ValueError):
        hours = DEFAULT_NUDGE_FLOOR_HOURS
    return hours * 60.0


def _nudge_fingerprint(kind: str, cards) -> str:
    """Identity of one owner's nudge CONTENT — the kind + the stale card SET.

    Order-independent (the set is sorted) and deliberately free of any age /
    wall-clock component: an age ticks on its own, so including it would make
    every sweep look "changed" and defeat the suppression entirely.
    """
    parts = sorted(c.id for c in cards if c.id)
    return hashlib.sha256("|".join([kind, *parts]).encode("utf-8")).hexdigest()


def _floor_elapsed(delivered_at: str | None, now: _dt.datetime) -> bool:
    """True when the unchanged-nudge floor has elapsed (or is unknown)."""
    if not delivered_at:
        return True
    parsed = _parse_iso(delivered_at)
    if parsed is None:
        return True
    return (now - parsed).total_seconds() / 60.0 >= _floor_minutes()


def _sidecar_path(store: str | Path | None) -> Path:
    """``nudges.yaml`` under the store's ``runtime/`` dir (scitex convention)."""
    from .._paths import runtime_dir

    return runtime_dir(store) / NUDGE_SIDECAR_NAME


def load_nudge_state(store: str | Path | None = None) -> dict[str, dict]:
    """Load the nudge sidecar → ``{kind: {owner: {fingerprint, delivered_at}}}``.

    Missing / unreadable / malformed sidecar → empty sections (fail-soft: a bad
    sidecar must never break a sweep — the worst case is one re-push).
    """
    import yaml

    from .._yaml import safe_load

    path = _sidecar_path(store)
    empty: dict[str, dict] = {
        KIND_STALE_ACTIVE: {},
        KIND_PENDING_BACKLOG: {},
        KIND_BLOCKED_CHECK: {},
    }
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty
    except OSError as exc:  # noqa: BLE001 — unreadable sidecar must not break the sweep
        logger.warning("stale-nudge: cannot read %s: %s", path, exc)
        return empty
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("stale-nudge: malformed %s: %s", path, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    for kind in empty:
        section = data.get(kind)
        if isinstance(section, dict):
            empty[kind] = section
    return empty


def save_nudge_state(
    state: dict[str, dict], store: str | Path | None = None
) -> None:
    """Atomically persist the nudge sidecar (temp + ``os.replace``)."""
    import yaml

    path = _sidecar_path(store)
    payload = {
        kind: state.get(kind) or {}
        for kind in (KIND_STALE_ACTIVE, KIND_PENDING_BACKLOG, KIND_BLOCKED_CHECK)
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 — a failed state write must not break delivery
        logger.warning("stale-nudge: cannot write %s: %s", path, exc)


def _push_echo(owner: str, body: str, *, kind: str, lines: list[str]) -> None:
    """OPT-IN secondary echo on the turn-url wire (:data:`ENV_NUDGE_PUSH`).

    Strictly cosmetic relative to the inbox enqueue: the result is SURFACED but
    never counted, never arms the suppression, and never substitutes for a
    failed enqueue (no silent fallback between rails). Fully guarded — a dead
    receiver must not disturb the sweep.
    """
    from .._push import NOTIFY_TIMEOUT_S, deliver

    try:
        result = deliver(owner, body, kind=kind, timeout=NOTIFY_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — the echo is best-effort.
        lines.append(f"  ~   {owner:30}  push echo raised: {exc}")
        return
    lines.append(
        f"  ~   {owner:30}  push echo ok={bool(result.get('ok'))} "
        f"wire={result.get('wire')} reason={result.get('reason')}"
    )


def _deliver_per_owner(
    by_owner: dict,
    *,
    kind: str,
    label: str,
    body_fn,
    lines: list[str],
    state: dict[str, dict],
    now: _dt.datetime,
    store: str | Path | None,
    enqueue,
    resolve_key,
) -> dict[str, int]:
    """Deliver one per-owner nudge of ``kind`` for each owner bucket.

    Shared loop for both the stale-active and pending-backlog sweeps. Delivery
    is an ENQUEUE into the owner's pull-inbox — the rail the digest already
    uses and every agent already drains (see the module docstring). Fail-soft
    per owner (a failed/raising enqueue is logged as an ERROR and surfaced,
    never aborts the batch); ``(unassigned)`` is surfaced but not delivered.

    Returns ``{"detected", "delivered", "failed", "suppressed"}`` so the caller
    can scream when an entire sweep reached nobody.

    ``body_fn(owner, cards)`` composes the nudge body; ``label`` is the
    short noun used in the log lines (e.g. "stale" / "pending").

    ``state`` is this KIND's slice of the sidecar (``{owner: {fingerprint,
    delivered_at}}``), mutated in place: the enqueue is SKIPPED (but still
    logged) while the owner's stale card set is unchanged and the floor has
    not elapsed. Only the enqueue is conditional — every owner is still
    visited, fingerprinted, and surfaced.
    """
    card_id = NUDGE_CARD_ID[kind]
    echo = os.environ.get(ENV_NUDGE_PUSH) == "1"
    counts = {"detected": 0, "delivered": 0, "failed": 0, "suppressed": 0}
    for owner, cards in sorted(by_owner.items()):
        if owner == "(unassigned)":
            # No inbox exists for the unassigned bucket; surface the gap but
            # don't attempt a delivery.
            lines.append(
                f"  -  {owner:30}  {len(cards)} {label} (no owner)"
            )
            continue
        counts["detected"] += 1
        entry = state.get(owner) or {}
        fingerprint = _nudge_fingerprint(kind, cards)
        unchanged = fingerprint == entry.get("fingerprint")
        if unchanged and not _floor_elapsed(entry.get("delivered_at"), now):
            counts["suppressed"] += 1
            lines.append(
                f"  ..  {owner:30}  {len(cards)} {label}  suppressed "
                f"(unchanged since {entry.get('delivered_at')}; "
                f"floor {_floor_minutes() / 60:g}h)"
            )
        else:
            body = body_fn(owner, cards)
            recipient = _safe_resolve(resolve_key, owner)
            # ``supersede``: a nudge is a cumulative snapshot, so a fresh one
            # replaces its unseen predecessor (exactly like the owner digest).
            delivered = _safe_enqueue(
                enqueue, recipient, kind, card_id, body, now, store,
                supersede=True,
            )
            if delivered:
                # Only a DELIVERED nudge arms the suppression — a failed
                # enqueue must be retried next sweep, not silently swallowed.
                counts["delivered"] += 1
                entry["fingerprint"] = fingerprint
                entry["delivered_at"] = _iso(now)
                lines.append(
                    f"  OK  {owner:30}  {len(cards)} {label}  "
                    f"wire=inbox  inbox={recipient}"
                )
            else:
                counts["failed"] += 1
                logger.error(
                    "[scitex-todo._stale_active_nudge] %s nudge for %s was NOT "
                    "delivered: inbox enqueue to %r failed — this owner will "
                    "NOT see their %d %s card(s)",
                    kind, owner, recipient, len(cards), label,
                )
                lines.append(
                    f"  ERR {owner:30}  {len(cards)} {label}  "
                    f"wire=inbox  inbox={recipient}  reason=enqueue-failed"
                )
            if echo:
                _push_echo(owner, body, kind=kind, lines=lines)
        if entry:
            state[owner] = entry
        else:
            # Never delivered (failed / raised) — leave NO entry, so the next
            # sweep retries this owner instead of reading a half-written one.
            state.pop(owner, None)

    # Prune owners who no longer have cards of this kind: the nudge STOPS when
    # the work is closed/touched, and a future stall re-delivers immediately.
    for owner in list(state):
        if owner not in by_owner:
            del state[owner]
    return counts


def _summary_lines(kind: str, counts: dict[str, int]) -> list[str]:
    """Per-kind summary — and an unmissable ALERT when nobody was reached.

    A quiet ``# 0 push(es) sent`` is what let a totally-dead rail ship (every
    owner ERR, zero delivered, one bland summary line). So: when at least one
    owner was ATTEMPTED and EVERY attempt failed, the sweep says so in capitals
    and at ``logger.error``. Zero delivered because everyone was SUPPRESSED is
    the healthy steady state and is NOT an alert.
    """
    out = [
        f"# {kind}: {counts['detected']} owner(s) detected, "
        f"{counts['delivered']} delivered (inbox), "
        f"{counts['suppressed']} suppressed, {counts['failed']} failed"
    ]
    attempted = counts["delivered"] + counts["failed"]
    if attempted and not counts["delivered"]:
        msg = (
            f"!! ALERT {kind}: 0 of {attempted} attempted nudge(s) delivered — "
            f"this sweep reached NOBODY (every owner's inbox enqueue failed)"
        )
        logger.error("[scitex-todo._stale_active_nudge] %s", msg)
        out.append(f"# {msg}")
    return out


def sweep_and_nudge(
    tasks: list[dict],
    *,
    store: str | Path | None = None,
    now: _dt.datetime | None = None,
    enqueue=None,
    resolve_key=None,
) -> list[str]:
    """Detect stale-active + pending-backlog cards; nudge each owner.

    Returns a list of human-readable log lines (per owner per kind plus
    per-kind summaries, including an ``!! ALERT`` line when a kind reached
    nobody) so the caller (notifyd / the CLI cron) can echo them. Never raises:
    every per-owner delivery is guarded so the whole sweep is fail-soft — one
    bad owner (or one bad kind) never breaks the rest.

    Delivery is an ENQUEUE into each owner's pull-inbox — the rail the owner
    DIGEST already uses (:mod:`scitex_cards._reminders`) and every agent already
    drains. ``enqueue`` (default :func:`scitex_cards._inbox.enqueue`) and
    ``resolve_key`` (default the producer/dispatch resolver
    :func:`scitex_cards._notify._resolver._resolve_name_to_id`) are injectable
    seams, mirroring :func:`scitex_cards._reminders.sweep_reminders`.

    DELIVER ON CHANGE: an owner whose stale card set is identical to the last
    DELIVERED one is skipped (and logged as suppressed) until
    :data:`ENV_NUDGE_FLOOR_HOURS` elapses. ``store`` selects the sidecar that
    carries that state across runs; ``now`` is a clock seam for tests.
    """
    cur = now or _now_utc()
    state = load_nudge_state(store)

    if enqueue is None:
        from .._inbox import enqueue as enqueue  # type: ignore[no-redef]
    if resolve_key is None:
        from .._notify._resolver import _resolve_name_to_id

        def resolve_key(name: str) -> str:  # type: ignore[misc]
            return _resolve_name_to_id(name, store=store)

    by_owner = detect_stale_active(tasks, now=cur)
    lines: list[str] = []
    counts = _deliver_per_owner(
        by_owner,
        kind=KIND_STALE_ACTIVE,
        label="stale",
        body_fn=stale_active_nudge_line,
        lines=lines,
        state=state[KIND_STALE_ACTIVE],
        now=cur,
        store=store,
        enqueue=enqueue,
        resolve_key=resolve_key,
    )

    if os.environ.get(ENV_EMIT_HOOK) == "1":
        _emit_hook(by_owner, lines)

    lines.extend(_summary_lines(KIND_STALE_ACTIVE, counts))

    # Pending-backlog sweep — distinct status set, threshold, and wording.
    by_owner_pending = detect_pending_backlog(tasks, now=cur)
    counts_pending = _deliver_per_owner(
        by_owner_pending,
        kind=KIND_PENDING_BACKLOG,
        label="pending",
        body_fn=pending_backlog_nudge_line,
        lines=lines,
        state=state[KIND_PENDING_BACKLOG],
        now=cur,
        store=store,
        enqueue=enqueue,
        resolve_key=resolve_key,
    )
    lines.extend(_summary_lines(KIND_PENDING_BACKLOG, counts_pending))

    # Blocked-check sweep — the cards the owner CANNOT move (blocked on a
    # dependency / compute job / another agent / an operator decision). These
    # used to ride the 2 h stale-active clock, which nudged owners 12x a day
    # about walls they were powerless to move; that is not a signal, it is
    # training to ignore the channel. Here they get their own lenient clock
    # and their own question: "has your blocker cleared?"
    by_owner_blocked = detect_blocked_external(tasks, now=cur)
    counts_blocked = _deliver_per_owner(
        by_owner_blocked,
        kind=KIND_BLOCKED_CHECK,
        label="blocked",
        body_fn=blocked_external_nudge_line,
        lines=lines,
        state=state[KIND_BLOCKED_CHECK],
        now=cur,
        store=store,
        enqueue=enqueue,
        resolve_key=resolve_key,
    )
    lines.extend(_summary_lines(KIND_BLOCKED_CHECK, counts_blocked))

    save_nudge_state(state, store)
    return lines


def _emit_hook(by_owner: dict, lines: list[str]) -> None:
    """Best-effort: emit a ``stale-active`` finding via the hook bus.

    Never raises into the sweep — a missing dispatcher or a plugin error
    is logged and ignored. Appends a one-line marker to ``lines``.
    """
    try:
        from .._hooks import dispatch_event

        owners = {k: len(v) for k, v in by_owner.items()}
        dispatch_event(
            {
                "kind": "stale-active",
                "source": "scitex-todo._stale_active_nudge",
                "owners": owners,
                "total": sum(owners.values()),
            }
        )
        lines.append(f"  hook  stale-active emitted ({len(owners)} owner(s))")
    except Exception as exc:  # noqa: BLE001 — best-effort.
        logger.debug(
            "[scitex-todo._stale_active_nudge] hook emit skipped: %s", exc,
        )


__all__ = [
    "ENV_EMIT_HOOK",
    "ENV_NUDGE_FLOOR_HOURS",
    "ENV_NUDGE_PUSH",
    "DEFAULT_NUDGE_FLOOR_HOURS",
    "NUDGE_SIDECAR_NAME",
    "NUDGE_CARD_ID",
    "KIND_STALE_ACTIVE",
    "KIND_PENDING_BACKLOG",
    "load_nudge_state",
    "save_nudge_state",
    "sweep_and_nudge",
]
