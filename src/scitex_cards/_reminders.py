#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nag-until-closed reminder engine — per-owner digest + operator escalation.

The board is the fleet's direction system, so a requested-and-carded task
that silently stops progressing is a real incident (operator, 2026-06-30).
This engine forces the board to keep surfacing such cards until they are
closed — and to ESCALATE the worst ones to the operator (or, when the
assignee is dead, to the card's creator).

Why a DIGEST, not per-card reminders
------------------------------------
Nagging once PER stale card produced a wall of near-identical lines that
trains the owner to ignore the whole stream (operator, 2026-06-30). Instead:
(a) detect an owner has open assigned cards, (b) periodically surface that
scoped list as ONE digest; the SELECTION of which to advance stays agentic.

What it does each sweep
-----------------------
1. Detect the cards that need nagging — the SAME pure detectors the stats
   cron uses (:func:`scitex_cards._stale_active.detect_stale_active` +
   :func:`~scitex_cards._stale_active.detect_pending_backlog`), by owner.
2. Decide, PER OWNER, whether a digest is *due* now (flat cadence tracked in
   a sidecar so we do not re-nag every tick). Closing/touching cards drops
   them from the stale set and the digest stops — the "until closed" guarantee.
3. Enqueue ONE digest per due owner via the standalone delivery rail
   (:func:`scitex_cards._inbox.enqueue`), listing their open stale cards
   (capped) so the agent picks which to advance.
4. ESCALATE: a HIGH-PRIORITY card still stale after
   :data:`DEFAULT_ESCALATE_AFTER` digests to its owner also enqueues a
   rising-urgency notice to the OPERATOR, once per card, until the card
   leaves the stale set. Escalation stays PER CARD (the operator needs the
   specific stuck card, not a digest).
5. LIVENESS ESCALATE (to the CREATOR): when a stale card's owner is NOT
   ALIVE (:func:`scitex_cards._users.is_alive` off the registry ``last_seen``)
   the assignee will never act, so the card escalates to its ``created_by``.
   Distinct from (4): NOT gated on the digest count or priority (a dead owner
   won't recover by waiting), latched once per streak under
   ``creator_escalated``, falling back to the operator when the creator is
   absent or IS the dead owner itself.

Design
------
* The reminder STATE lives in a sidecar ``reminders.yaml`` next to
  ``tasks.yaml`` — card payloads are never mutated (detectors stay pure).
  Two sections: ``owners: {owner_name: {count, last_at}}`` (the per-owner
  digest cadence) and ``cards: {card_id: {escalated, creator_escalated}}``
  (the per-card escalation latches). Owner entries are pruned when the owner
  has no stale cards; card entries are pruned when the card leaves the stale
  set (so a future stall re-escalates on either latch).
* Recipient keying matches the producer/dispatch convention
  (:func:`scitex_cards._notify._resolver._resolve_name_to_id`).
* Pure-ish + injectable: ``enqueue``, ``resolve_key``, ``resolve_user`` and
  ``now`` are injectable so tests drive the cadence/escalation/liveness state
  machine with real fakes (no mocks, no network, no clock).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from pathlib import Path
from typing import Any, Callable

from ._reminder_bodies import (
    DIGEST_CARD_CAP,
    _creator_escalation_body,
    _digest_body,
    _escalation_body,
)
from ._reminder_enqueue import (
    _digest_fingerprint,
    _floor_minutes,
    _iso,
    _safe_enqueue,
    _safe_resolve,
)
from ._reminder_liveness import _card_creator, _owner_liveness
from ._reminder_cadence import resolve_owner_interval
from ._stale.active import detect_pending_backlog, detect_stale_active
from ._throughput import _now_utc, _parse_iso

logger = logging.getLogger(__name__)

#: Sidecar file (sibling of ``tasks.yaml``) holding the reminder state.
REMINDER_SIDECAR_NAME = "reminders.yaml"

#: Digests to an owner before a HIGH-PRIORITY card also escalates to the
#: operator. Escalation fires once per stale streak (reset when the card
#: leaves the stale set).
ENV_ESCALATE_AFTER = "SCITEX_TODO_REMINDER_ESCALATE_AFTER"
DEFAULT_ESCALATE_AFTER = 3

#: A card with ``priority <= this`` is "high priority" (lower int = higher
#: priority, matching the card model). Cards without a priority never
#: escalate (only the owner is nagged).
ENV_ESCALATE_PRIORITY = "SCITEX_TODO_REMINDER_ESCALATE_PRIORITY"
DEFAULT_ESCALATE_PRIORITY = 1

#: Liveness TTL (seconds) for the creator-escalation path: a card's owner
#: whose registry ``last_seen`` is older than this (or absent) is "not alive"
#: and the card escalates to its CREATOR. Mirrors the users-layer default;
#: overridable per-sweep via the ``liveness_ttl`` arg or this env knob.
ENV_LIVENESS_TTL = "SCITEX_TODO_REMINDER_LIVENESS_TTL"
DEFAULT_LIVENESS_TTL = 600

#: The operator identity escalations are addressed to (resolved like any
#: other recipient). Delivery to Telegram is operator-gated config
#: (recipients.yaml + token); the engine only enqueues.
ENV_OPERATOR = "SCITEX_TODO_OPERATOR"
DEFAULT_OPERATOR = "operator"

#: Optional owner ALLOWLIST for a phased rollout. Comma-separated owner
#: names; when set, ONLY those owners are nagged (every other owner is left
#: untouched). Empty / unset = nag every owner. Lets the engine start scoped
#: to one agent and widen deliberately, with no fleet-wide first-sweep storm.
ENV_REMINDER_OWNERS = "SCITEX_TODO_REMINDER_OWNERS"

#: Event types (also the inbox dedup discriminator + the ledger key prefix).
EVENT_DIGEST = "reminder"
EVENT_ESCALATION = "escalation"
#: Liveness-triggered escalation to a card's CREATOR — a DISTINCT path from
#: the staleness→operator :data:`EVENT_ESCALATION`. Fired when the card's
#: owner (assignee) is not alive, so the assignee will never act; the creator
#: is asked to reassign / drive / close. See :func:`sweep_reminders`.
EVENT_CREATOR_ESCALATION = "creator_escalation"

#: Synthetic ``card_id`` for a digest record (a digest is about many cards,
#: not one). One digest per owner per sweep + a fresh ``ts`` keeps the inbox
#: dedup ``(event_type, card_id, ts, actor)`` distinct across sweeps.
DIGEST_CARD_ID = "(digest)"


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    """Read an int knob from the ``reminders:`` config section, else default."""
    raw = cfg.get(key)
    if isinstance(raw, bool):  # bool is an int subclass — never a knob value
        return default
    return raw if isinstance(raw, int) else default


def _owner_allowlist(cfg: dict | None = None) -> set[str]:
    """Owner ALLOWLIST for a phased rollout (empty = nag every owner).

    Env :data:`ENV_REMINDER_OWNERS` (comma-separated) wins, else the
    ``reminders.owners`` config list; an empty result means "all owners".
    """
    raw = os.environ.get(ENV_REMINDER_OWNERS)
    if raw is not None:
        return {o.strip() for o in raw.split(",") if o.strip()}
    owners = (cfg or {}).get("owners")
    if isinstance(owners, (list, tuple)):
        return {str(o).strip() for o in owners if str(o).strip()}
    return set()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _due(last_at: str | None, now: _dt.datetime, *, interval_minutes: float) -> bool:
    """True when a digest is due: never sent, or the flat interval has elapsed.

    Flat cadence (operator knob): re-digest every ``interval_minutes`` while
    stale cards remain (no backoff). ``last_at`` missing/unparseable → due now.
    """
    if not last_at:
        return True
    parsed = _parse_iso(last_at)
    if parsed is None:
        return True
    elapsed_min = (now - parsed).total_seconds() / 60.0
    return elapsed_min >= interval_minutes


def _sidecar_path(store: str | Path | None) -> Path:
    """``reminders.yaml`` under the store's ``runtime/`` dir (scitex convention)."""
    from ._paths import runtime_dir

    return runtime_dir(store) / REMINDER_SIDECAR_NAME


def load_reminder_state(store: str | Path | None = None) -> dict[str, dict]:
    """Load the reminder sidecar → ``{"owners": {...}, "cards": {...}}``.

    Missing / unreadable / malformed sidecar → empty sections (fail-soft: a
    bad sidecar must never break a sweep). Always returns both sections so
    callers can index them without guarding. A legacy ``cards:``-only sidecar
    loads leniently (only the ``escalated`` latch is still meaningful; the
    per-owner cadence rebuilds from the first sweep — no migration needed).
    """
    import yaml

    from ._yaml import safe_load

    path = _sidecar_path(store)
    empty = {"owners": {}, "cards": {}}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty
    except OSError as exc:  # noqa: BLE001 — unreadable sidecar must not break the sweep
        logger.warning("reminders: cannot read %s: %s", path, exc)
        return empty
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("reminders: malformed %s: %s", path, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    owners = data.get("owners")
    cards = data.get("cards")
    return {
        "owners": owners if isinstance(owners, dict) else {},
        "cards": cards if isinstance(cards, dict) else {},
    }


def save_reminder_state(state: dict[str, dict], store: str | Path | None = None) -> None:
    """Atomically persist the reminder sidecar (temp + ``os.replace``)."""
    import yaml

    path = _sidecar_path(store)
    payload = {
        "owners": state.get("owners") or {},
        "cards": state.get("cards") or {},
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
        logger.warning("reminders: cannot write %s: %s", path, exc)


def _is_parked(card: dict | None) -> bool:
    # A blocked card WITH a blocker waits on someone else — owner can't act, so
    # it's not actionable staleness (excluded from the nag). No blocker = ambiguous, kept.
    return bool(card) and card.get("status") == "blocked" and bool(card.get("blocker"))


def _card_priority(card: dict) -> int | None:
    p = card.get("priority")
    return p if isinstance(p, int) else None


def _is_high_priority(card: dict, *, threshold: int) -> bool:
    p = _card_priority(card)
    return p is not None and p <= threshold


def sweep_reminders(
    tasks: list[dict],
    *,
    store: str | Path | None = None,
    now: _dt.datetime | None = None,
    enqueue: Callable[..., Any] | None = None,
    resolve_key: Callable[[str], str] | None = None,
    operator: str | None = None,
    interval_minutes: float | None = None,
    escalate_after: int | None = None,
    escalate_priority: int | None = None,
    owners: set[str] | None = None,
    resolve_user: Callable[[str], Any] | None = None,
    liveness_ttl: int | None = None,
) -> dict[str, list[str]]:
    """One nag sweep: enqueue a due DIGEST per owner + escalate stuck cards.

    Returns ``{"digested": [owner, ...], "escalated": [card_id, ...],
    "creator_escalated": [card_id, ...], "skipped": [owner, ...]}`` (skipped =
    owner has stale cards but the digest is not yet due this sweep).

    The re-digest cadence is a flat interval (operator knob): per owner the
    TIGHTEST of their cards' resolved intervals (per-card
    ``reminder_interval_minutes`` > config > default). ``interval_minutes``
    forces a flat value (a test seam); ``None`` uses config/card resolution.

    Liveness→creator escalation: ``resolve_user`` (name → user record/dict or
    ``None``) and ``liveness_ttl`` (seconds) are injectable seams so tests
    drive the alive/stale/unknown machine with real fakes; left ``None`` they
    resolve to :func:`scitex_cards._users.resolve_user` and the configured TTL.

    Fail-soft: a per-owner enqueue error is logged and the sweep continues.
    Never raises into the caller (notifyd tick).
    """
    from ._config import reminders_config, resolve_interval_minutes

    cur = now or _now_utc()
    cfg = reminders_config()
    esc_after = escalate_after if escalate_after is not None else _env_int(
        ENV_ESCALATE_AFTER, _cfg_int(cfg, "escalate_after", DEFAULT_ESCALATE_AFTER)
    )
    esc_prio = escalate_priority if escalate_priority is not None else _env_int(
        ENV_ESCALATE_PRIORITY,
        _cfg_int(cfg, "escalate_priority", DEFAULT_ESCALATE_PRIORITY),
    )
    operator_name = operator or os.environ.get(ENV_OPERATOR, DEFAULT_OPERATOR)

    if enqueue is None:
        from ._inbox import enqueue as enqueue  # type: ignore[no-redef]
    if resolve_key is None:
        from ._notify._resolver import _resolve_name_to_id

        def resolve_key(name: str) -> str:  # type: ignore[misc]
            return _resolve_name_to_id(name, store=store)

    if resolve_user is None:
        from ._users import resolve_user as _resolve_user_real

        def resolve_user(name: str) -> Any:  # type: ignore[misc]
            return _resolve_user_real(name, store=store)

    ttl = liveness_ttl if liveness_ttl is not None else _env_int(
        ENV_LIVENESS_TTL, _cfg_int(cfg, "liveness_ttl", DEFAULT_LIVENESS_TTL)
    )

    # Index cards by id for priority/title lookup during escalation.
    by_id = {str(t.get("id") or ""): t for t in tasks if t.get("id")}

    # The set of (owner, [StaleCard]) needing a nag this sweep, oldest-first
    # within each owner (the detectors already sort that way).
    buckets: dict[str, list] = {}
    # Remember which ids came from the BACKLOG sweep: the two detectors have
    # deliberately different clocks (active = 2 h, backlog = 24 h) and merging
    # them into one bucket threw that away. See the cadence block below.
    backlog_ids: set[str] = set()
    for owner, cards in detect_stale_active(tasks, now=cur).items():
        buckets.setdefault(owner, []).extend(cards)
    for owner, cards in detect_pending_backlog(tasks, now=cur).items():
        buckets.setdefault(owner, []).extend(cards)
        backlog_ids.update(sc.id for sc in cards if sc.id)

    # Phased-rollout allowlist: when set (arg or env), nag ONLY these owners
    # and leave every other owner untouched (no fleet-wide first-sweep storm).
    allow = owners if owners is not None else _owner_allowlist(cfg)
    if allow:
        buckets = {o: c for o, c in buckets.items() if o in allow}

    state = load_reminder_state(store)
    owner_state: dict[str, dict] = state["owners"]
    card_state: dict[str, dict] = state["cards"]

    stale_owners: set[str] = set()
    stale_card_ids: set[str] = set()
    digested: list[str] = []
    escalated: list[str] = []
    creator_escalated: list[str] = []
    skipped: list[str] = []

    for owner in sorted(buckets):
        if owner == "(unassigned)":
            continue  # nobody to nag; the gap is surfaced by the stats sweep
        # Skip PARKED cards (blocked WITH a blocker = waiting on someone else,
        # owner can't act → noise). Nag only ACTIONABLE staleness.
        cards = [sc for sc in buckets[owner]
                 if sc.id and not _is_parked(by_id.get(sc.id))]
        if not cards:
            continue
        stale_owners.add(owner)
        stale_card_ids.update(sc.id for sc in cards)

        # LIVENESS→CREATOR escalation: if the (registered) owner is NOT ALIVE
        # the assignee will never act, so escalate each of their stale cards to
        # its CREATOR now. Independent of the digest cadence (runs BEFORE the
        # `_due` early-continue) and NOT gated on count/priority — a dead owner
        # won't recover by waiting; the per-card `creator_escalated` latch keeps
        # it a nudge. A non-registered (free-form) owner has no liveness signal.
        owner_user, liveness = _owner_liveness(resolve_user, owner, now=cur, ttl_seconds=ttl)
        if owner_user is not None and liveness.get("status") != "alive":
            for sc in cards:
                centry = card_state.get(sc.id) or {}
                if centry.get("creator_escalated"):
                    card_state[sc.id] = centry
                    continue
                creator = _card_creator(by_id.get(sc.id, {}), owner, operator_name)
                creator_key = _safe_resolve(resolve_key, creator)
                cbody = _creator_escalation_body(sc, owner, liveness.get("age_seconds"))
                if _safe_enqueue(
                    enqueue, creator_key, EVENT_CREATOR_ESCALATION, sc.id, cbody, cur, store
                ):
                    centry["creator_escalated"] = True
                    creator_escalated.append(sc.id)
                card_state[sc.id] = centry

        # Cadence: tightest interval any ACTIVE card asks for. A deferred card
        # can no longer drag the digest onto a 5-minute clock — see
        # _reminder_cadence for why that was an interrupt loop, not a reminder.
        owner_interval = resolve_owner_interval(
            cards,
            backlog_ids=backlog_ids,
            by_id=by_id,
            cfg=cfg,
            resolve_interval_minutes=resolve_interval_minutes,
            forced=interval_minutes,
        )

        entry = owner_state.get(owner) or {}
        count = int(entry.get("count") or 0)
        if not _due(entry.get("last_at"), cur, interval_minutes=owner_interval):
            skipped.append(owner)
            owner_state[owner] = entry
            continue

        # The owner is DUE. The digest TICK still advances — escalation fires
        # after N ticks, so suppressing ticks would silently disarm the
        # operator escalation below (this bit me: the escalation tests caught
        # it). Only the DELIVERY is conditional.
        count += 1
        entry["count"] = count
        entry["last_at"] = _iso(cur)

        # DELIVER ON CHANGE. Re-sending an identical digest wakes the owner for
        # zero new information; observed live 2026-07-11: 26 identical digests
        # in ~2h, same card list, only the counter moving. That trains an agent
        # to ignore the digest — the one signal that must stay un-ignorable. So
        # the wake-up is skipped while the card set (and each card's status) is
        # unchanged, UNTIL the floor elapses: a genuinely stuck owner is still
        # nudged, daily instead of every few minutes.
        # NOTE: suppression must NOT `continue` — the operator escalation below
        # lives in this same iteration, and skipping it would silence the very
        # signal that matters most. Only the digest ENQUEUE is skipped.
        fingerprint = _digest_fingerprint(cards)
        suppress = fingerprint == entry.get("fingerprint") and not _due(
            entry.get("delivered_at"), cur, interval_minutes=_floor_minutes()
        )
        if suppress:
            skipped.append(owner)
        else:
            owner_key = _safe_resolve(resolve_key, owner)
            body = _digest_body(cards, count)
            # The digest is a CUMULATIVE snapshot — supersede any unseen
            # predecessor so a channel-down owner never accumulates a
            # replay-storm of stale digests. Per-card escalations below stay
            # distinct (supersede=False).
            if _safe_enqueue(
                enqueue, owner_key, EVENT_DIGEST, DIGEST_CARD_ID, body, cur, store,
                supersede=True,
            ):
                entry["fingerprint"] = fingerprint
                entry["delivered_at"] = _iso(cur)
                digested.append(owner)
        owner_state[owner] = entry

        # Escalate to the OPERATOR each high-priority card that survived enough
        # digests and has not yet escalated this streak (per card, once).
        if count >= esc_after:
            for sc in cards:
                card = by_id.get(sc.id, {})
                if not _is_high_priority(card, threshold=esc_prio):
                    continue
                centry = card_state.get(sc.id) or {}
                if centry.get("escalated"):
                    card_state[sc.id] = centry
                    continue
                op_key = _safe_resolve(resolve_key, operator_name)
                ebody = _escalation_body(sc, owner, count)
                if _safe_enqueue(
                    enqueue, op_key, EVENT_ESCALATION, sc.id, ebody, cur, store
                ):
                    centry["escalated"] = True
                    escalated.append(sc.id)
                card_state[sc.id] = centry

    # Prune state for owners/cards no longer stale — the nag STOPS when work is
    # closed/touched, and a card's escalation latches reset for a future stall.
    for owner in list(owner_state):
        if owner not in stale_owners:
            del owner_state[owner]
    for cid in list(card_state):
        if cid not in stale_card_ids:
            del card_state[cid]

    save_reminder_state(state, store)
    return {
        "digested": digested,
        "escalated": escalated,
        "creator_escalated": creator_escalated,
        "skipped": skipped,
    }


__all__ = [
    "REMINDER_SIDECAR_NAME",
    "ENV_REMINDER_OWNERS",
    "EVENT_DIGEST",
    "EVENT_ESCALATION",
    "EVENT_CREATOR_ESCALATION",
    "DIGEST_CARD_CAP",
    "DIGEST_CARD_ID",
    "DEFAULT_ESCALATE_AFTER",
    "DEFAULT_ESCALATE_PRIORITY",
    "DEFAULT_OPERATOR",
    "load_reminder_state",
    "save_reminder_state",
    "sweep_reminders",
]

# EOF
