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

The operator's reserved peer name is ``scitex_cards._threads.OPERATOR_NAME``
(``"operator"``). All endpoints honour the ``?store=`` query param the rest
of the board uses, so tests drive a real tmp store.
"""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from scitex_cards import _threads
from scitex_cards._threads import OPERATOR_NAME


def _store_of(request: HttpRequest):
    """Optional explicit store path from the ``?store=`` query param.

    READ paths only. See :func:`_write_store_of` for why a write must never
    use this.
    """
    return request.GET.get("store") or None


#: Request attribute a TRUSTED middleware may set to select the store for a
#: write. An attribute cannot be forged over HTTP; a query parameter can.
STORE_REQUEST_ATTR = "scitex_store"


def _write_store_of(request: HttpRequest):
    """The store a WRITE may touch — never taken from the request itself.

    ``_store_of`` reads ``?store=`` from the QUERY, and ``_threads`` derives
    the file it writes from that value. So the caller was choosing which file
    got written. A URL-PATH allowlist never sees a query string, which means
    any gate reasoning about paths was reasoning about the wrong thing — this
    was an arbitrary-write surface, not a hardening nicety. Found by
    scitex-hub in design review, 2026-07-28.

    A trusted middleware may still scope the write by setting
    :data:`STORE_REQUEST_ATTR` on the request object. That is deliberately an
    ATTRIBUTE rather than a query parameter: a remote caller can set the
    latter and cannot set the former.

    THIS DOES NOT CLOSE THE HOLE YET, and saying so plainly matters more than
    looking finished. The query seam is LOAD-BEARING today: the hub injects
    tenancy through it (its middleware discards a client ``?store=`` and
    mutates ``request.GET``), and the existing view tests scope themselves the
    same way. Removing it outright was tried and broke both — trading a
    security bug for an outage.

    So this is the MIGRATION SEAM, not the fix: a trusted attribute WINS when
    present, and the query remains the fallback until the hub switches. Once
    it does, delete the fallback and the caller can no longer choose the write
    target. Tracked on
    ``scitex-cards-dm-store-from-query-and-forced-operator-author-20260728``.

    Until then the query value is NARROWED rather than trusted — see
    :func:`_query_store_for_write`.
    """
    trusted = getattr(request, STORE_REQUEST_ATTR, None)
    if trusted:
        return trusted
    return _query_store_for_write(request)


class UnusableWriteStore(ValueError):
    """A write named a store this process must not create or invent."""


def _query_store_for_write(request: HttpRequest):
    """The query store, accepted for a write ONLY if it already exists.

    This is the mitigation shippable WITHOUT the hub, and it is deliberately
    narrow rather than clever. The hub's injected tenancy paths point at REAL
    tenant stores, which exist; an attacker-supplied ``?store=/tmp/evil.yaml``
    does not. Requiring existence kills "write to a path of my choosing"
    while leaving every legitimate caller working.

    It does NOT make the query seam safe: a caller can still name an EXISTING
    store they should not touch, and only the hub's switch to
    :data:`STORE_REQUEST_ATTR` closes that. Narrower is not closed, and this
    should not be read as if it were.

    Refusal is LOUD rather than a silent fall back to server-side resolution,
    because quietly writing somewhere OTHER than the store the caller named is
    how a second board gets manufactured — the exact failure class this
    package already refuses elsewhere.
    """
    from pathlib import Path

    raw = _store_of(request)
    if not raw:
        return None
    if not Path(raw).expanduser().exists():
        raise UnusableWriteStore(
            f"refusing to write to a store that does not exist: {raw}. A write "
            f"may not CREATE a store at a path supplied by the request — that is "
            f"how a decoy board gets manufactured. Point at an existing store, or "
            f"have trusted middleware set request.{STORE_REQUEST_ATTR}."
        )
    return raw


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
    for key, summary in _threads.list_threads(store=store).items():
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
        messages = _threads.get_thread(OPERATOR_NAME, peer, store=store)
        return JsonResponse(
            {"thread": key, "peer": peer, "messages": messages},
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


__all__ = ["dm_thread_view", "dm_threads_view"]

# EOF
