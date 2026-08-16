#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notify DISPATCHER: deliver card-events to resolved recipients (foundation C4).

This is **C4** of the card-event / notification foundation epic — the
RESOLVER-DRIVEN delivery layer that turns a canonical card-event
(:mod:`scitex_cards._events`) into actual per-recipient pushes. It is the
single built-in CONSUMER of ``kind == "card-event"`` on the hook bus:
:func:`scitex_cards._hooks.dispatch_event` lazily calls
:func:`dispatch_notifications` for every card-event (see the wiring in
:mod:`scitex_cards._hooks._dispatch`).

It stitches together the already-merged foundation pieces, reusing each
as SSOT (no reinvention):

* :func:`scitex_cards._notify.resolve_recipients` (C3) — *which* user-ids
  should be notified for this event on this card.
* :func:`scitex_cards._inbox.enqueue` (the standalone PULL rail) — the
  ACTUAL delivery: append the notification to each recipient's inbox.

## NO fleet-spam — resolver-driven, not broadcast

Delivery targets ONLY the set :func:`resolve_recipients` returns, minus
the actor (we never notify the cause of the event). There is no fan-out
to "every agent" — the notify-config layer (global rules + per-user
mute/watch + per-card overrides) is the single authority on the
recipient set.

## Fail-soft contract (the dispatcher MUST never break a mutation)

A card-event is emitted from inside a board mutation (reassign, complete,
a git-link push, …) via :func:`scitex_cards._events.emit`, which already
swallows bus errors. C4 adds a SECOND layer of isolation:

* the whole dispatch is wrapped fail-soft at the bus wiring point so a
  delivery hiccup can never make ``emit()`` raise;
* **per-recipient** isolation here — one inbox enqueue raising for a
  recipient must NOT stop the others.

## Standalone PULL-inbox sink — the ONLY (and always-works) delivery rail

A direct turn-URL POST CANNOT reach a *containerized* agent — the agent
subscribes outbound to a bus and refuses a direct inbound POST (connection
refused). It also makes the EMITTING mutation wait on a slow/unreachable
network target. So C4's delivery is purely the per-recipient PULL-inbox
(:mod:`scitex_cards._inbox`): it **enqueues** each resolved recipient's
notification into the inbox, persisted in the same store with ZERO sac
dependency and ZERO network. The recipient's scitex-cards client then POLLs
that inbox (``poll_notifications`` MCP tool). The enqueue is FAIL-SOFT: an
inbox error can never break the mutation or ``emit()``.

The dispatcher NO LONGER makes a synchronous direct-POST to turn-urls
(it used to call :func:`scitex_cards._push.deliver` here, which failed for
containers and slowed the mutation). :func:`scitex_cards._push.deliver` /
``turn_url_for`` stay in the codebase for other callers; a future
NON-BLOCKING push accelerator (e.g. sac's optional C10 out-of-band wake)
can re-add a host-reachable fast-path WITHOUT putting the network back on
the mutation's critical path.

## ``deliver_fn`` parameter (kept for back-compat; no longer called)

:func:`dispatch_notifications` still ACCEPTS a ``deliver_fn`` parameter so
existing callers/tests do not break, but the dispatcher no longer invokes
it synchronously — the inbox enqueue is the delivery. Tests assert on the
``enqueued`` summary (and that a deliberately-raising ``deliver_fn`` is
never called), exercising the real inbox round-trip — no ``unittest.mock``,
no monkeypatch of the wire (STX-NM / PA-306-compliant).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: Event types we deliberately DO NOT deliver via C4.
#:
#: Empty: every card-event (INCLUDING ``commented``) is delivered through
#: the standalone PULL-inbox now. ``commented`` USED to be skipped here and
#: handled by the interactive comment-relay's direct turn-URL POST, but that
#: POST could never reach a containerized owner (connection refused) — so a
#: comment never arrived. Folding ``commented`` into C4 (card
#: ``todo-fold-comment-relay-into-c4-dispatcher-20260626``) makes the inbox
#: the single, always-works comment-delivery rail; the board toast now
#: reflects the inbox queue instead of a stale direct-POST result.
_SKIP_EVENT_TYPES: frozenset[str] = frozenset()


def _event_fields(event: Any) -> tuple[str | None, str | None, str | None, dict]:
    """Extract ``(event_type, card_id, actor, extras)`` from any event shape.

    Accepts:

    * the canonical card-event ENVELOPE — a dict carrying
      ``{"kind": "card-event", "type": ..., "card_id": ..., "actor": ...}``
      (what :meth:`scitex_cards._events.Event.to_dict` builds and the bus
      delivers);
    * an inner event dict without the ``kind`` wrapper (same keys);
    * a typed :class:`scitex_cards._events.Event` (read via attributes).

    ``extras`` collects the remaining envelope fields (``repo`` / ``branch``
    / ``pr_url`` / ``sha`` / ``version`` / any ``extra`` keys) so per-event
    bodies can include a useful detail (e.g. a status transition stashed in
    ``extra``) without this module knowing every field name.
    """
    if isinstance(event, Mapping):
        event_type = event.get("type")
        card_id = event.get("card_id")
        actor = event.get("actor")
        # Everything else (minus the envelope discriminators) is an extra.
        extras = {
            k: v
            for k, v in event.items()
            if k not in ("kind", "type", "card_id", "actor", "ts")
        }
        return event_type, card_id, actor, extras
    # Typed Event (or any object with the attributes).
    event_type = getattr(event, "type", None)
    card_id = getattr(event, "card_id", None)
    actor = getattr(event, "actor", None)
    extras: dict[str, Any] = {}
    for field in ("repo", "branch", "pr_url", "sha", "version"):
        val = getattr(event, field, None)
        if val is not None:
            extras[field] = val
    obj_extra = getattr(event, "extra", None)
    if isinstance(obj_extra, Mapping):
        for k, v in obj_extra.items():
            extras.setdefault(k, v)
    return event_type, card_id, actor, extras


def _body_for(event_type: str, card_id: str, actor: str | None, extras: Mapping) -> str:
    """Build a concise, human-readable per-event notification body.

    Deterministic and side-effect-free. Every branch leads with the card
    id so the recipient can grep for it; the ``actor`` (when known) is
    named so the recipient sees who caused it. Unknown / future event
    types fall through to a generic one-liner rather than failing — a new
    event type should never break delivery.
    """
    by = f" (by {actor})" if actor else ""
    if event_type == "reassigned":
        return f"Card {card_id} reassigned to you{by}"
    if event_type == "completed":
        return f"Card {card_id} completed{by}"
    if event_type == "status_changed":
        # The status transition (if the producer stashed it) lives in extras
        # under from/to or status_from/status_to. Best-effort, never required.
        frm = extras.get("from") or extras.get("status_from")
        to = extras.get("to") or extras.get("status_to")
        if frm or to:
            return f"Card {card_id}: {frm or '?'}→{to or '?'}{by}"
        return f"Card {card_id} status changed{by}"
    if event_type == "created":
        return f"Card {card_id} created{by}"
    if event_type == "committed":
        sha = extras.get("sha")
        sha_s = f" {str(sha)[:10]}" if sha else ""
        repo = extras.get("repo")
        repo_s = f" in {repo}" if repo else ""
        return f"Card {card_id}: new commit{sha_s}{repo_s}{by}"
    if event_type == "pushed":
        branch = extras.get("branch")
        branch_s = f" to {branch}" if branch else ""
        repo = extras.get("repo")
        repo_s = f" in {repo}" if repo else ""
        return f"Card {card_id}: pushed{branch_s}{repo_s}{by}"
    if event_type == "merged":
        pr = extras.get("pr_url")
        pr_s = f" {pr}" if pr else ""
        return f"Card {card_id}: PR merged{pr_s}{by}"
    if event_type == "released":
        version = extras.get("version")
        ver_s = f" {version}" if version else ""
        repo = extras.get("repo")
        repo_s = f" {repo}" if repo else ""
        return f"Card {card_id}: released{repo_s}{ver_s}{by}".replace("  ", " ")
    if event_type == "deployed":
        service = extras.get("service")
        svc_s = f" {service}" if service else ""
        return f"Card {card_id}: deployed{svc_s}{by}"
    if event_type == "pulled":
        return f"Card {card_id}: pulled{by}"
    # Forward-compatible default — never fail on an unseen type.
    return f"Card {card_id}: {event_type}{by}"


def _recipient_to_name(uid: str, *, store: Any | None) -> str | None:
    """Map a recipient user-id to a delivery NAME, or ``None`` if unmappable.

    RETAINED helper (no longer called by :func:`dispatch_notifications` now
    that the inbox is the sole delivery rail) for a FUTURE non-blocking push
    accelerator that would need to resolve a recipient id back to a delivery
    handle. Kept rather than deleted so re-adding that fast-path is a small
    diff, not a re-implementation.

    A recipient is EITHER a stable ``u_*`` id (the common case —
    :func:`resolve_recipients` resolves names to ids) OR a raw name string
    (the back-compat fallback when a card member is not registered).

    * registered id → first of the user's ``names`` (the delivery handle),
      falling back to ``host_at_name`` when ``names`` is somehow empty;
    * unregistered string → it IS already a raw name, so return it verbatim.

    Returns ``None`` only when the id resolves to a registered user that has
    NEITHER a usable name NOR a host_at_name (degenerate — skipped + recorded).
    """
    from .._users import get_user

    user = get_user(uid, store=store)
    if user is None:
        # Not a registered id → it is the raw-name fallback. Deliver to it.
        return uid or None
    for name in user.names or ():
        if isinstance(name, str) and name:
            return name
    if user.host_at_name:
        return user.host_at_name
    return None


def dispatch_notifications(
    event: Any,
    *,
    store: Any | None = None,
    deliver_fn: Callable[..., Mapping] | None = None,
) -> dict:
    """Deliver a card-event to its resolved recipients (the C4 deliverable).

    The single built-in consumer of ``kind == "card-event"``: resolve the
    recipient set (C3), drop the actor, and ENQUEUE a concise per-event
    message into each recipient's standalone PULL-inbox — fail-soft per
    recipient. No synchronous network: the inbox is the delivery rail.

    Parameters
    ----------
    event : Event | Mapping
        The canonical card-event. Accepts the bus ENVELOPE
        (``{"kind": "card-event", "type", "card_id", "actor", ...}``), an
        inner event dict, or a typed :class:`scitex_cards._events.Event`.
    store : path-like, optional
        Store path forwarded to :func:`resolve_recipients` and
        :func:`get_user`. ``None`` resolves via the normal precedence chain.
    deliver_fn : callable, optional
        ACCEPTED for back-compat (the old direct-POST injection seam) but
        NO LONGER CALLED — the dispatcher delivers purely via the inbox, so
        a comment / any event never depends on or awaits a turn-URL POST. A
        future non-blocking push accelerator could re-introduce a fast-path
        without putting the network back on the mutation's critical path.

    Returns
    -------
    dict
        A summary::

            {
                "event_type": <str | None>,
                "card_id": <str | None>,
                "delivered": [],          # always empty now (see below)
                "enqueued": [<recipient id>, ...],
                "skipped": [<reason / name>, ...],
                "errors": [{"recipient": <id>, "error": <str>}, ...],
            }

        ``enqueued`` is THE real result — the recipient ids whose standalone
        PULL-inbox received the notification (the always-works rail).
        ``delivered`` is kept in the shape for back-compat but stays EMPTY:
        the synchronous direct-POST was removed (it could not reach a
        containerized recipient and slowed the emitting mutation). Never
        raises: malformed events, a missing card, AND inbox errors are all
        folded into the summary (fail-soft).
    """
    event_type, card_id, actor, extras = _event_fields(event)
    summary: dict[str, Any] = {
        "event_type": event_type,
        "card_id": card_id,
        "delivered": [],
        "enqueued": [],
        "skipped": [],
        "errors": [],
    }

    # (a) no card → nothing to resolve against (repo-level events without a
    #     card_id, e.g. a release/deploy not tied to a card). No-op.
    if not card_id:
        summary["skipped"].append("no-card-id")
        return summary

    # (b) per-type skip hook (now EMPTY — see _SKIP_EVENT_TYPES). Kept as a
    #     forward-compatible seam should a future event type ever need to be
    #     excluded from inbox delivery. ``commented`` is NO LONGER skipped:
    #     it is delivered through the inbox like every other card-event.
    if event_type in _SKIP_EVENT_TYPES:
        summary["skipped"].append(f"event-type:{event_type}")
        return summary

    # (c) load the card the event is about. Fail-soft on ANY load error
    #     (TaskNotFoundError for an unknown id, or a transient store read
    #     error): a missing/unreadable card must never break the mutation
    #     that emitted the event.
    from .._store import get_task

    try:
        card = get_task(store=store, task_id=card_id)
    except Exception as exc:  # noqa: BLE001 — fail-soft (incl. TaskNotFoundError)
        logger.warning(
            "[scitex-cards._notify] card %r not loadable for %r event; "
            "skipping notify: %s",
            card_id, event_type, exc,
        )
        summary["skipped"].append("card-not-found")
        return summary

    # (d) resolve recipients (C3 — the SSOT for who gets notified).
    from . import resolve_recipients

    try:
        recipients = set(resolve_recipients(event, card, store=store))
    except Exception as exc:  # noqa: BLE001 — a config error must not break emit
        logger.warning(
            "[scitex-cards._notify] resolve_recipients failed for %r event "
            "on card %r; skipping notify: %s",
            event_type, card_id, exc,
        )
        summary["skipped"].append("resolve-failed")
        return summary

    # (e) never notify the ACTOR (the cause of the event). The actor is a
    #     raw name; resolve it to an id the SAME way card members are
    #     resolved, then discard both the id and the raw name from the set.
    if actor:
        from .._users import resolve_user

        actor_user = resolve_user(actor, store=store)
        actor_id = actor_user.id if actor_user is not None else actor
        recipients.discard(actor_id)
        recipients.discard(actor)

    # The human-readable notification body (the inbox is the only rail now).
    body = _body_for(event_type, card_id, actor, extras)

    # (f) STANDALONE PULL-INBOX sink — enqueue per recipient ID. This is the
    #     ONLY (and always-works) delivery rail — NO network: a containerized
    #     recipient PULLs its inbox via the poll_notifications MCP tool. Keyed
    #     on the resolved recipient IDS (u_* ids or raw-name fallbacks), NOT
    #     delivery names, so the recipient resolves its own inbox the same way.
    #     FAIL-SOFT: an inbox error must NEVER break the mutation / emit —
    #     record it and keep going. The event ``ts`` (when known) is passed so
    #     a re-dispatch of the SAME event dedups deterministically.
    event_ts = event.get("ts") if isinstance(event, Mapping) else getattr(
        event, "ts", None
    )
    for uid in sorted(recipients):
        try:
            from .._inbox import enqueue as _enqueue

            record = _enqueue(
                uid,
                event_type=event_type,
                card_id=card_id,
                body=body,
                actor=actor,
                ts=event_ts,
                store=store,
            )
        except Exception as exc:  # noqa: BLE001 — inbox must not break emit
            logger.warning(
                "[scitex-cards._notify] inbox enqueue to %r raised for %r "
                "event on card %r: %s",
                uid, event_type, card_id, exc,
            )
            summary["errors"].append({"recipient": uid, "error": str(exc)})
            continue
        if record is not None:
            summary["enqueued"].append(uid)

    # NOTE: the synchronous direct-POST accelerator (the old steps g + h that
    # mapped each recipient id → a delivery NAME and called ``deliver_fn`` =
    # :func:`scitex_cards._push.deliver` against the recipient's turn-URL) has
    # been REMOVED. It could not reach a containerized recipient (connection
    # refused) AND it put a slow/unreachable network POST on the critical path
    # of the EMITTING mutation. The inbox enqueue above is the delivery; a
    # future NON-BLOCKING push (e.g. sac's optional C10 out-of-band wake) can
    # re-add a host-reachable fast-path off the mutation's critical path. The
    # ``deliver_fn`` parameter is accepted for back-compat but intentionally
    # not invoked; ``delivered`` therefore stays empty.
    return summary


__all__ = ["dispatch_notifications"]

# EOF
