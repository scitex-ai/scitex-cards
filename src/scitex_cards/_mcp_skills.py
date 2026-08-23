#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards MCP tools extracted from the budget-bound server module.

:mod:`scitex_cards._mcp_server` sat at its line budget, so two cohesive tool
clusters live here instead and register on the SAME shared ``mcp`` FastMCP
instance — ``_mcp_server`` imports this module at its tail for the
registration side effect, so ``from scitex_cards._mcp_server import mcp``
continues to expose every tool.

Clusters:

  - Skills (Convention B, ``cards_<verb>_<noun>``) — audit §5 required pair;
    file-system introspection on the bundled ``_skills/`` dir.
  - Help-wait (``help_wait`` / ``help_clear``) — the "agent is stuck waiting
    on the operator" card, lifted out of the dotfiles Notification hook so
    scitex-cards owns the semantics. 1:1 with :mod:`scitex_cards._help_wait`.
"""

from __future__ import annotations

import functools
import json

import anyio

from . import _messaging
from ._backend import get_backend
from ._mcp_app import mcp  # the LEAF — importing _mcp_server here would cycle


def _skills_dir():
    """Return the path to the bundled scitex-cards skill files."""
    from pathlib import Path

    return Path(__file__).parent / "_skills" / "scitex-cards"


@mcp.tool()
async def cards_skills_list() -> str:
    """List bundled scitex-cards skill files. Returns a JSON array of names."""
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return json.dumps([])
    names = sorted(p.name for p in skills_dir.iterdir() if p.is_file())
    return json.dumps(names)


@mcp.tool()
async def cards_skills_get(name: str) -> str:
    """Return the content of one bundled scitex-cards skill file.

    `name` must match a file in the bundled skills dir (e.g.
    `"01_installation.md"`). Returns a JSON object
    ``{"name": str, "content": str}`` or
    ``{"name": str, "error": "not found"}`` if the name doesn't resolve.
    """
    skills_dir = _skills_dir()
    target = skills_dir / name
    # Guard path traversal — only allow direct children of skills_dir.
    if target.parent.resolve() != skills_dir.resolve() or not target.is_file():
        return json.dumps({"name": name, "error": "not found"})
    return json.dumps({"name": name, "content": target.read_text(encoding="utf-8")})


@mcp.tool()
async def reassign_task(
    task_id: str,
    new_owner: str,
    by: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Atomically change a card's owner (C5 reassign primitive).

    1:1 with :func:`scitex_cards._store.reassign_task` (Convention A; lives
    here only to keep ``_mcp_server`` under its line budget). In one locked
    write sets ``agent = assignee = new_owner`` and
    ``scope = "agent:<new_owner>"``, appends an audit comment, and emits a
    canonical ``reassigned`` card-event (the notification path — delivery
    is C4, a separate card). Idempotent: reassigning to the SAME current
    owner is a no-op (no write, no event); the returned ``changed`` flag is
    then ``False``.

    Args:
      task_id: the card id.
      new_owner: the new owning agent.
      by: the actor ($SCITEX_CARDS_AGENT_ID → $USER precedence).
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().reassign_task, tasks_path, task_id, new_owner, by=by
        )
    )
    return json.dumps(result)


@mcp.tool()
async def help_wait(
    agent: str,
    question: str | None = None,
    host: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """UPSERT the canonical "agent is waiting on the operator" card.

    Card contract (id ``help-<agent>-waiting``, title ``[help] <agent>
    waiting on operator decision``, status ``blocked``, blocker
    ``operator-decision``, assignee + ``scope=agent:<agent>``, ``host`` from
    the arg or best-effort hostname, ``note`` from ``question`` or a
    placeholder). Idempotent: a re-run refreshes note + last_activity in
    place and never duplicates. Returns the upserted card as JSON.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().help_wait, tasks_path, agent, question=question, host=host
        )
    )
    return json.dumps(result)


@mcp.tool()
async def help_clear(
    agent: str,
    tasks_path: str | None = None,
) -> str:
    """Resolve the ``help-<agent>-waiting`` card (status=done, clear blocker).

    No-op (no error) when the card does not exist. Returns a JSON object
    ``{"task_id": <id>, "cleared": bool, ...}``.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(get_backend().help_clear, tasks_path, agent)
    )
    return json.dumps(result)


@mcp.tool()
async def poll_notifications(
    agent: str,
    unseen_only: bool = True,
    ack: bool = False,
    tasks_path: str | None = None,
) -> str:
    """PULL an agent's pending card-message notifications (STANDALONE).

    The standalone (zero external runtime) delivery read path: the C4
    dispatcher ENQUEUEs each card-event into the recipient's per-recipient
    pull-inbox (a sibling ``inboxes:`` section in the shared store); this
    tool returns that inbox so any agent's scitex-cards client can poll it
    WITHOUT any external runtime. The optional out-of-band push rail stays a
    parallel accelerator, not a dependency.

    READING NEVER CONFIRMS. This call hands notifications over; it does NOT
    advance the cursor. Confirm what you ACTUALLY DELIVERED with
    ``ack_notifications(agent, ids)``. Anything you never confirm is still
    unseen and COMES BACK on the next poll — so a consumer that dies between
    read and confirm loses nothing.

    ``agent`` is resolved to its stable user-id via
    :func:`scitex_cards._users.resolve_user` (so a rename still finds the
    inbox); an UNREGISTERED name falls back to itself (the same raw-name key
    the dispatcher enqueued under). Returns a JSON object::

        {"agent": <input>, "recipient_id": <resolved id/name>,
         "store": <the store these rows were read from>,
         "notifications": [ {id, event_type, card_id, body, actor, ts, seen},
                            ... ],
         "unconfirmed": [<ids still awaiting ack_notifications>],
         "confirm_with": "ack_notifications"}

    ``store`` names the target this poll actually read. ``ack_notifications``
    reports the same field, and COMPARING THE TWO is the point: if you poll
    one store and confirm against another, every call still succeeds — this
    one returns nothing and the confirmation answers ``unknown`` for every id,
    which is indistinguishable from "there was nothing to deliver". Two labels
    that disagree name that fault outright.

    Two that AGREE do not clear you. They cover only your own read and write;
    the delivery daemon resolves its target separately and stamps nothing, so
    notifications can be arriving from a store neither label mentions. Equal
    is CANNOT-TELL.

    Args:
      agent: the recipient name / id / host@name to poll for.
      unseen_only: when true (default) return only unseen notifications;
        false returns the full history.
      ack: DEPRECATED — DO NOT USE. Marks the returned notifications seen at
        HANDOVER, so a consumer that reads them and then fails to deliver has
        PERMANENTLY DESTROYED them: they leave the unseen set and no retry can
        find them. Measured on the live store 2026-07-29 — five operator DMs
        enqueued correctly, four marked SEEN, the agent saw none of them, and
        the operator asked twice, eleven minutes apart, because nothing came
        back. Still honoured (sac reads this path today) but it emits a
        DeprecationWarning and an ``ack_on_read_deprecated`` field in the
        payload. Use ``ack=false`` + ``ack_notifications`` instead.
    """
    # The composition (user resolution, fail-soft liveness heartbeat, poll)
    # lives in the backend so a remote backend can make it ONE round trip.
    result = await anyio.to_thread.run_sync(
        functools.partial(
            _messaging.poll_notifications,
            agent,
            unseen_only=unseen_only,
            ack=ack,
            store=tasks_path,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def ack_notifications(
    agent: str,
    ids: list[str],
    tasks_path: str | None = None,
) -> str:
    """CONFIRM delivery of specific notifications — the ONLY cursor-advancing verb.

    Call this AFTER you have actually delivered each notification, passing the
    ids you delivered. Anything you do not confirm stays unseen and is
    REDELIVERED on the next ``poll_notifications`` — that redelivery is the
    whole point: a consumer that dies between reading and confirming must lose
    nothing. (The reverse — confirming at handover — destroyed five operator
    DMs on the live store on 2026-07-29; see ``poll_notifications``' ``ack``.)

    IDEMPOTENT. Confirming the same id twice is a no-op, never an error, so a
    retrying consumer is not punished for retrying. An id this agent's inbox
    never held is likewise a no-op. The payload distinguishes the cases::

        {"agent": <input>, "recipient_id": <resolved id/name>,
         "store": <the store this confirmation was applied to>,
         "requested": [...],           # what you asked to confirm
         "confirmed": [...],           # flipped unseen -> seen by THIS call
         "already_confirmed": [...],   # were already seen (a fine retry)
         "unknown": [...]}             # no such id in this inbox

    ``unknown`` SAYS NOTHING ABOUT THE DATABASE. It reads as "those ids do not
    exist", but a confirmation sent to the WRONG STORE answers exactly the
    same way — every id unknown, no error, nothing to retry. That is why
    ``store`` is here: compare it with the ``store`` your poll reported.

    THE COMPARISON IS ONE-SIDED. DIFFERENT means you are confirming somewhere
    you never read — a split, positively identified. EQUAL means only that
    YOUR read and YOUR write went to the same place; it does NOT mean no
    split exists, because the daemon that pushes notifications to you
    resolves its own target and reports nothing. A third store can be feeding
    your inbox while these two labels agree. Treat equal as CANNOT-TELL.

    Args:
      agent: the recipient name / id / host@name whose inbox to confirm in.
      ids: the notification ids (the ``id`` field of each polled record) that
        were successfully delivered.
    """
    result = await anyio.to_thread.run_sync(
        functools.partial(_messaging.ack_notifications, agent, ids, store=tasks_path)
    )
    return json.dumps(result)


@mcp.tool()
async def health(tasks_path: str | None = None) -> str:
    """Package-level HEALTH check (the health doctor). Returns a JSON report.

    Broad store / identity / delivery diagnosis — NOT the narrow ``mcp doctor``
    (which only checks the fastmcp install). Runs the checks in
    :func:`scitex_cards._health.health`: ``store_canonical`` (resolved store is
    the canonical, readable+writable, parses with a ``tasks`` key — no
    project shadow), ``agent_id`` ($SCITEX_CARDS_AGENT_ID resolvable),
    ``notifyd_alive`` (delivery-daemon pidfile probe), ``channel_drain`` (this
    agent's unseen vs seen inbox backlog), and ``channel_capable``
    (``_mcp_channel`` importable). Returns the cross-package standard shape
    ``{"package", "ok", "checks":[{name,ok,detail,hint}], "summary"}`` — every
    failing check carries an actionable ``hint``; the call never raises.
    """
    from ._health import health as _health_check

    result = await anyio.to_thread.run_sync(
        functools.partial(_health_check, store=tasks_path)
    )
    return json.dumps(result)


def _refusal(exc: Exception, prefix: str = "") -> str:
    """Render a messaging-layer refusal as this tool's ``{"error": ...}`` string.

    The Python API in :mod:`scitex_cards._messaging` RAISES; the MCP contract
    RETURNS an error object. Converting in ONE place is deliberate — a per-tool
    ``except`` that builds its own dict is how two tools start reporting the
    same refusal in two shapes.
    """
    return json.dumps({"error": f"{prefix}{exc}"})


@mcp.tool()
async def dm_send(
    to: str,
    body: str,
    tasks_path: str | None = None,
) -> str:
    """Send a DIRECT MESSAGE to a peer (operator or another agent).

    Appends the canonical DM record ``{id, thread, from, to, body, ts, read}``
    to the pair's thread in the ``threads.json`` sidecar (thread id
    ``dm:<a>::<b>``, peers sorted) and enqueues a ``dm`` notification into the
    recipient's pull-inbox so the unified channel server delivers it into
    their live session. ``from`` is THIS agent's resolved identity
    ($SCITEX_CARDS_AGENT_ID). The operator's reserved peer name is
    ``"operator"`` — the operator reads the thread on the board's /chat view.
    Returns the stored record as JSON.

    TEXT ONLY. To send a FILE — a PDF, a screenshot, a log — use
    ``dm_send_document(to=..., file_path=..., caption=...)`` instead. Do NOT
    describe the file in prose here and do NOT paste a filesystem path: the
    operator reads this thread in a browser, where a path on your machine is
    not something they can open. ``dm_send_document`` copies the bytes into
    the board's attachment store so the file itself arrives.
    """
    try:
        record = await anyio.to_thread.run_sync(
            functools.partial(_messaging.dm_send, to, body, store=tasks_path)
        )
    except _messaging.AgentIdentityUnresolved as exc:
        return _refusal(exc)
    return json.dumps(record)


@mcp.tool()
async def dm_send_document(
    to: str,
    file_path: str,
    caption: str | None = None,
    tasks_path: str | None = None,
) -> str:
    """Send a FILE to a peer as a direct message (the DM attachment path).

    Mirrors claude-code-telegrammer's ``send_document`` deliberately: same
    three arguments (recipient, local path, optional caption), so an agent
    that already knows how to hand the operator a PDF over Telegram does the
    same thing here. ``file_path`` is an absolute path to a file THIS agent
    can read.

    The bytes are COPIED into the board's attachment store — the same store,
    same ``attachments/<YYYY-MM>/<uuid>/<name>`` url and same renderer the
    operator's own uploads use. The original path is never recorded and never
    served from, so a file the agent later moves or deletes still reaches the
    operator intact.

    The stored url is appended to the message body on its own line, which is
    how the chat pane recognises an attachment. ``caption`` becomes the
    message text; without one the filename is used, so the thread list shows
    something readable rather than a bare url.

    Returns ``{"message": <stored DM record>, "attachment": {url, filename,
    mime_type, size}}``. Refusals (missing file, not a regular file, over the
    25 MB ceiling) come back as ``{"error": ...}`` naming what to fix.
    """
    from ._attachments import AttachmentError

    try:
        result = await anyio.to_thread.run_sync(
            functools.partial(
                _messaging.dm_send_document,
                to,
                file_path,
                caption=caption,
                store=tasks_path,
            )
        )
    except _messaging.AgentIdentityUnresolved as exc:
        return _refusal(exc)
    except _messaging.RemoteHubAttachmentUnsupported as exc:
        return _refusal(exc)
    except AttachmentError as exc:
        # The attachment layer's messages name the file and the limit but not
        # the caller, and this tool's refusals have always been prefixed.
        return _refusal(exc, prefix="dm_send_document: ")
    return json.dumps(result)


@mcp.tool()
async def dm_list(
    peer: str | None = None,
    ack: bool = False,
    tasks_path: str | None = None,
) -> str:
    """Read THIS agent's DM thread with ``peer`` (default: the operator).

    Returns ``{"thread": <id>, "peer": <peer>, "messages": [...]}`` in
    chronological order. ``ack=true`` additionally marks the messages
    addressed to this agent as read (advances the unread cursor the board's
    /chat view displays).
    """
    # Thread-key + ack + read composition lives in the backend (one RPC later).
    try:
        result = await anyio.to_thread.run_sync(
            functools.partial(
                _messaging.dm_list, peer=peer, ack=ack, store=tasks_path
            )
        )
    except _messaging.AgentIdentityUnresolved as exc:
        return _refusal(exc)
    return json.dumps(result)


__all__ = [
    "ack_notifications",
    "dm_list",
    "dm_send",
    "dm_send_document",
    "health",
    "help_clear",
    "help_wait",
    "poll_notifications",
    "reassign_task",
    "cards_skills_get",
    "cards_skills_list",
]

# EOF
