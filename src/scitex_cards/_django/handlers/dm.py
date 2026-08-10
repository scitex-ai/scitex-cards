#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/dm/*`` Django endpoints — the operator↔agent DIRECT-MESSAGE API.

Backs the board's mobile-first ``/chat`` view (operator side of the
scitex-dev DM convention v1; card
``fleet-agent-direct-message-board-pane-20260707``). Distinct from
``handlers/chat.py`` — that is the per-CARD comment thread; this is the
per-AGENT direct-message thread stored in the ``threads.json`` sidecar
(:mod:`scitex_cards._threads`).

Endpoints::

    GET  /dm/threads
      -> 200 + {"agents": [{"name", "kind", "unread", "last_ts",
                            "last_body"}, ...]}
         The union of the ``users:`` registry and any peer that already has
         a DM thread with the operator, sorted by most-recent activity.

    GET  /dm/thread/<peer>[?mark_read=1]
      -> 200 + {"thread": <id>, "peer": <peer>, "messages": [...]}
         The operator's thread with ``peer`` (chronological). With
         ``mark_read=1``, messages addressed TO the operator are flipped
         read (poll-and-ack in one call — what the open thread pane does).

    POST /dm/thread/<peer>   body = {"body": "<text>"}
      -> 200 + {"message": <stored record>}
         Appends ``from=operator`` and dm-dispatches into the agent's
         pull-inbox (the unified channel server pushes it into the agent's
         session). 400 on an empty body.

         This is ALSO the forward path. A forward is not a distinct kind of
         record — it is an ordinary message whose body opens with a
         ``[forwarded from <name>, <ts>]`` banner, the same shape
         claude-code-telegrammer renders for messages forwarded into it
         (``ts/lib/forward.ts:forwardBanner``). Mirroring that instead of
         inventing a second convention means the operator reads one banner
         everywhere, and it needs no new endpoint and no new field.

    POST /dm/thread/<peer>/reaction
         body = {"message_id": "m_…", "emoji": "👍", "action": "add"|"remove"}
      -> 200 + {"event": <stored event>, "reactions": {emoji: [actors]}}
         Appends one APPEND-ONLY reaction event (:mod:`scitex_cards._reactions`)
         and answers with the message's refolded state. 400 on a missing
         message_id/emoji or an unknown action.

The operator's reserved peer name is ``scitex_cards._threads.OPERATOR_NAME``
(``"operator"``). All endpoints honour the ``?store=`` query param the rest
of the board uses, so tests drive a real tmp store.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from scitex_cards import (
    _dm_read,
    _dm_receipt_state,
    _dm_write,
    _reactions,
    _threads,
)
from scitex_cards._django._request_store import (  # noqa: F401  (re-export)
    STORE_REQUEST_ATTR as STORE_REQUEST_ATTR,
)
from scitex_cards._django._request_store import read_store, write_store
from scitex_cards._threads import OPERATOR_NAME


def _store_of(request: HttpRequest):
    """The store this READ resolves to — see :mod:`.._request_store`.

    A trusted middleware's ``request.scitex_store`` wins over the caller's
    ``?store=``. Until 2026-08-06 this read the query and NOTHING else, while
    :func:`_write_store_of` next door already accepted only the attribute —
    the two halves of one contract disagreeing in adjacent functions.
    """
    return read_store(request)


def _write_store_of(request: HttpRequest):
    """The store a WRITE may touch — a TRUSTED ATTRIBUTE or nothing.

    ``_store_of`` reads ``?store=`` from the QUERY, and ``_threads`` derives
    the file it writes from that value. So the caller was choosing which file
    got written. A URL-PATH allowlist never sees a query string, which means
    any gate reasoning about paths was reasoning about the wrong thing — this
    was an arbitrary-write surface, not a hardening nicety. Found by
    scitex-hub in design review, 2026-07-28.

    THE QUERY FALLBACK IS GONE, so a remote caller can no longer pick the
    write target. Only a trusted middleware can, by setting
    :data:`STORE_REQUEST_ATTR` on the request object — deliberately an
    ATTRIBUTE rather than a query parameter, because a remote caller can set
    the latter and cannot set the former.

    WHY THIS IS SAFE TO DELETE NOW, having been unsafe on 2026-07-28. The
    fallback existed because removing it would have dropped the hub's tenancy
    injection for a release window, and the board would then have fallen back
    to its ambient canonical store — ONE store for every tenant. That is no
    longer the situation: scitex-hub's ``TodoBoardTenancyMiddleware`` sets
    ``request.scitex_store`` directly (its own comment calls that the PRIMARY
    CHANNEL and marks the query injection as the legacy one to delete once
    this side stops reading it). Both halves of the migration were in place
    and each side was waiting on the other; measured 2026-07-29.

    ``None`` means NO trusted scope was supplied, and the caller must fall
    back to its own server-side resolution rather than to anything the request
    carried. Failing to a known store is safe; failing to a caller-named one
    is the defect.
    """
    return getattr(request, STORE_REQUEST_ATTR, None)


def _author_of(request: HttpRequest) -> str:
    """Who is writing — the AUTHENTICATED principal, not a constant.

    The write path hardcoded :data:`OPERATOR_NAME`, so any caller admitted by
    any gate posted AS the operator: a human who did not write the message.
    When an authenticated identity exists we use it.

    :data:`OPERATOR_NAME` remains the fallback ONLY for the standalone board,
    which binds loopback and has no auth layer at all — there the sole caller
    IS the operator at their own keyboard. It is a default for the
    single-user case, never an attribution for an anonymous remote caller.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        name = (getattr(user, "get_username", lambda: "")() or "").strip()
        if name:
            return name
    return OPERATOR_NAME


def _registry_agents(store) -> list[dict]:
    """Project the ``users:`` registry onto ``{name, kind}`` rows.

    Fail-soft: a missing/malformed registry yields ``[]`` — the chat view
    still works from thread peers alone.
    """
    try:
        from scitex_cards._users import list_users

        users = list_users(store)
    except Exception:  # noqa: BLE001 — registry optional for the chat view
        return []
    out = []
    for u in users:
        name = u.names[0] if u.names else u.id
        if name and name != OPERATOR_NAME:
            out.append({"name": name, "kind": u.kind})
    return out


def dm_threads_view(request: HttpRequest) -> HttpResponse:
    """GET the operator's agent list + per-agent thread summaries."""
    if request.method != "GET":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method}, status=405
        )
    store = _store_of(request)
    rows: dict[str, dict] = {}
    for agent in _registry_agents(store):
        rows[agent["name"]] = {
            "name": agent["name"],
            "kind": agent["kind"],
            "unread": 0,
            "last_ts": None,
            "last_body": None,
        }
    # Merge in any peer that already has a thread with the operator (covers
    # unregistered senders — the thread store is the SSOT of who talked).
    # THE STORE, NOT THE SIDECAR. `_threads.list_threads` reads `threads.json`,
    # a PER-HOST FILE, and nothing else — so this view showed only the threads
    # of agents running on the same machine as the board. Measured 2026-08-09
    # on the operator's laptop: its sidecar had scitex-agent-container live at
    # 12:33 (that agent runs laptop-side) while scitex-cards sat at 2026-08-02,
    # and five agents on scitex-compute-04 were invisible entirely. All 4150
    # messages were in the store the whole time. Operator's ruling the same
    # day: "never use threads.json but database".
    for key, summary in _dm_read.threads_summary(
        OPERATOR_NAME, store=store
    ).items():
        a, b = summary["peers"]
        if OPERATOR_NAME not in (a, b):
            continue
        peer = b if a == OPERATOR_NAME else a
        row = rows.setdefault(
            peer,
            {
                "name": peer,
                "kind": None,
                "unread": 0,
                "last_ts": None,
                "last_body": None,
            },
        )
        row["unread"] = summary["unread"].get(OPERATOR_NAME, 0)
        last = summary["last"]
        if last is not None:
            row["last_ts"] = last.get("ts")
            row["last_body"] = last.get("body")
    agents = sorted(
        rows.values(), key=lambda r: (r["last_ts"] or "", r["name"]), reverse=True
    )
    return JsonResponse({"agents": agents})


@csrf_exempt
def dm_thread_view(request: HttpRequest, peer: str) -> HttpResponse:
    """GET the operator↔``peer`` thread, or POST a new operator message."""
    if request.method not in {"GET", "POST"}:
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method}, status=405
        )
    store = _store_of(request)
    if not peer or not peer.strip():
        return JsonResponse({"error": "empty peer name"}, status=400)
    peer = peer.strip()

    if request.method == "GET":
        key = _threads.thread_key(OPERATOR_NAME, peer)
        # Poll-and-ack: the open pane passes mark_read=1 so viewing the
        # thread clears the operator-side unread counter.
        if request.GET.get("mark_read") in ("1", "true"):
            # mark_read DOES write (it advances the unread cursor), but it is
            # part of the READ transaction and must share the read's scope:
            # acking against a different store than the one just rendered would
            # clear the wrong board's counter. So it stays on `store` until the
            # hub moves its tenancy injection off the query string, at which
            # point reads and this ack move together.
            #
            # Residual, stated rather than hidden: until then a caller can still
            # steer this ack at a path of their choosing. It flips read flags
            # rather than injecting content, so it is strictly less severe than
            # the append path closed below — but it is NOT closed, and it is
            # part of the same coordinated fix on
            # scitex-cards-dm-store-from-query-and-forced-operator-author-20260728.
            _threads.mark_read(key, _author_of(request), store=store)
            # AND THE RECEIPT GOES TO THE STORE, because that is now where the
            # unread COUNT comes from. #776 moved the badge onto `dm_receipts`
            # via `unread_for_conn`; leaving the ack on the sidecar alone would
            # mean opening a thread never cleared its badge — the count would be
            # correct and permanently unclearable, which is a worse bug than the
            # stale one it replaced.
            #
            # Idempotent by primary key `(message_id, reader)`, so a re-open
            # inserts nothing and returns 0 rather than erroring.
            reader = _author_of(request)
            unread_ids = [
                m["id"]
                for m in _dm_read.unread_for(reader, store=store, thread_id=key)
            ]
            if unread_ids:
                _dm_write.mark_read(unread_ids, reader, store=store)
        # THE STORE, NOT THE SIDECAR — the other half of #776.
        #
        # #776 moved the thread LIST onto the database and left this PANE on
        # `_threads.get_thread`, which reads `threads.json`. That split is worse
        # than the bug it half-fixed: the sidebar counted unread from the store
        # while the pane rendered messages from a per-host file, so the operator
        # saw a badge of 2 on a thread whose agent messages were not displayed
        # at all. His words: "the number of new messages shown on the left
        # sidebar is wrong; it does not reflect the actual states." It reflected
        # the store correctly; the pane beside it did not.
        #
        # A count and a list that answer from different sources will disagree
        # eventually by construction. Reading both from `dm_messages` is what
        # makes the badge and the pane the same claim.
        messages = _dm_read.messages_in(
            _threads.thread_key(OPERATOR_NAME, peer), store=store
        )
        # Reactions ride ALONGSIDE the messages, never inside them. The stored
        # DM records stay byte-identical to what an older client already
        # understands, and the v5 design's rule that a message is immutable
        # (docs/design/dm-into-cards-db.md §3.2) is not quietly broken by the
        # wire shape. A client that ignores this key behaves exactly as before.
        #
        # `receipts` rides in the same seat for the same reason, and answers a
        # different question: `reactions` is what someone CHOSE to say about a
        # message, `receipts` is whether the message got THERE. It is derived
        # per message id from `dm_receipts` (rows written by the reader), never
        # from a transport call returning — see _dm_receipt_state.
        return JsonResponse(
            {
                "thread": key,
                "peer": peer,
                "messages": messages,
                "reactions": _reactions.thread_reactions(key, store=store),
                "receipts": _dm_receipt_state.receipt_state_for_thread(
                    key, store=store
                ),
            },
            json_dumps_params={"default": str},
        )

    # POST — the operator sends a message to `peer`.
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, str) or not body.strip():
        return JsonResponse({"error": "dm send requires non-empty 'body'"}, status=400)
    record = _threads.append_message(
        _author_of(request), peer, body, store=_write_store_of(request)
    )
    return JsonResponse({"message": record}, json_dumps_params={"default": str})


@csrf_exempt
def dm_reaction_view(request: HttpRequest, peer: str) -> HttpResponse:
    """POST one reaction event onto a message in the operator↔``peer`` thread.

    The THREAD is derived server-side from ``(operator, peer)`` — the caller
    names a message and an emoji, never a thread id. A client that could name
    the thread could attach a reaction to a conversation it is not part of;
    deriving it means the URL already carries that authority.
    """
    if request.method != "POST":
        return JsonResponse(
            {"error": "method-not-allowed", "method": request.method}, status=405
        )
    if not peer or not peer.strip():
        return JsonResponse({"error": "empty peer name"}, status=400)
    peer = peer.strip()
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "reaction requires a JSON object"}, status=400)

    message_id = payload.get("message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        return JsonResponse(
            {"error": "reaction requires a non-empty 'message_id'"}, status=400
        )
    action = payload.get("action", _reactions.ACTION_ADD)
    if action not in _reactions.ACTIONS:
        return JsonResponse(
            {
                "error": f"unknown action {action!r}",
                "valid": list(_reactions.ACTIONS),
            },
            status=400,
        )
    try:
        emoji = _reactions.validate_emoji(payload.get("emoji"))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    key = _threads.thread_key(OPERATOR_NAME, peer)
    event = _reactions.append_reaction_event(
        thread=key,
        message_id=message_id.strip(),
        actor=_author_of(request),
        emoji=emoji,
        action=action,
        store=_write_store_of(request),
    )
    # Answer with the REFOLDED state rather than echoing the event, so the
    # tapping client renders the same chips every other client will see on its
    # next poll instead of guessing the result of its own write.
    folded = _reactions.thread_reactions(key, store=_write_store_of(request))
    return JsonResponse(
        {"event": event, "reactions": folded.get(message_id.strip(), {})},
        json_dumps_params={"default": str},
    )


__all__ = ["dm_reaction_view", "dm_thread_view", "dm_threads_view"]

# EOF
