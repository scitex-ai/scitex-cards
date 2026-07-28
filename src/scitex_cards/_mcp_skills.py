#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo MCP tools extracted from the budget-bound server module.

:mod:`scitex_cards._mcp_server` sat at its line budget, so two cohesive tool
clusters live here instead and register on the SAME shared ``mcp`` FastMCP
instance — ``_mcp_server`` imports this module at its tail for the
registration side effect, so ``from scitex_cards._mcp_server import mcp``
continues to expose every tool.

Clusters:

  - Skills (Convention B, ``todo_<verb>_<noun>``) — audit §5 required pair;
    file-system introspection on the bundled ``_skills/`` dir.
  - Help-wait (``help_wait`` / ``help_clear``) — the "agent is stuck waiting
    on the operator" card, lifted out of the dotfiles Notification hook so
    scitex-todo owns the semantics. 1:1 with :mod:`scitex_cards._help_wait`.
"""

from __future__ import annotations

import functools
import json

import anyio

from ._backend import get_backend
from ._mcp_app import mcp  # the LEAF — importing _mcp_server here would cycle


def _skills_dir():
    """Return the path to the bundled scitex-todo skill files."""
    from pathlib import Path

    return Path(__file__).parent / "_skills" / "scitex-todo"


@mcp.tool()
async def todo_skills_list() -> str:
    """List bundled scitex-todo skill files. Returns a JSON array of names."""
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return json.dumps([])
    names = sorted(p.name for p in skills_dir.iterdir() if p.is_file())
    return json.dumps(names)


@mcp.tool()
async def todo_skills_get(name: str) -> str:
    """Return the content of one bundled scitex-todo skill file.

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
      by: the actor ($SCITEX_TODO_AGENT_ID → $USER precedence).
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
    tool returns that inbox so any agent's scitex-todo client can poll it
    WITHOUT any external runtime. The optional out-of-band push rail stays a
    parallel accelerator, not a dependency.

    ``agent`` is resolved to its stable user-id via
    :func:`scitex_cards._users.resolve_user` (so a rename still finds the
    inbox); an UNREGISTERED name falls back to itself (the same raw-name key
    the dispatcher enqueued under). Returns a JSON object::

        {"agent": <input>, "recipient_id": <resolved id/name>,
         "notifications": [ {id, event_type, card_id, body, actor, ts, seen},
                            ... ]}

    Args:
      agent: the recipient name / id / host@name to poll for.
      unseen_only: when true (default) return only unseen notifications;
        false returns the full history.
      ack: when true, advance the cursor — mark the RETURNED notifications
        seen so a later poll does not return them again.
    """
    # The composition (user resolution, fail-soft liveness heartbeat, poll)
    # lives in the backend so a remote backend can make it ONE round trip.
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().poll_notifications,
            agent,
            unseen_only=unseen_only,
            ack=ack,
            store=tasks_path,
        )
    )
    return json.dumps(result)


@mcp.tool()
async def health(tasks_path: str | None = None) -> str:
    """Package-level HEALTH check (the ``health`` doctor). Returns a JSON report.

    Broad store / identity / delivery diagnosis — NOT the narrow ``mcp doctor``
    (which only checks the fastmcp install). Runs the checks in
    :func:`scitex_cards._health.health`: ``store_canonical`` (resolved store is
    the canonical, readable+writable, parses with a ``tasks`` key — no
    project shadow), ``agent_id`` ($SCITEX_TODO_AGENT_ID resolvable),
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


def _dm_sender_or_error() -> "tuple[str | None, str | None]":
    """Resolve the calling agent's identity for the dm_* tools.

    Returns ``(sender, None)`` on success or ``(None, <json error>)`` with an
    actionable hint when no identity is configured — the DM record's ``from``
    field must be a REAL agent name, never a blank/'unknown' fallback.
    """
    from ._mcp_channel import resolve_agent_id_optional

    sender = resolve_agent_id_optional()
    if sender is None:
        return None, json.dumps(
            {
                "error": "dm: no agent identity configured. Set "
                "SCITEX_TODO_AGENT_ID=<your-agent> in the MCP server env "
                '(.mcp.json: "SCITEX_TODO_AGENT_ID": '
                "\"${SCITEX_TODO_AGENT_ID}\") so the DM 'from' field names a "
                "real agent."
            }
        )
    return sender, None


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
    ($SCITEX_TODO_AGENT_ID). The operator's reserved peer name is
    ``"operator"`` — the operator reads the thread on the board's /chat view.
    Returns the stored record as JSON.
    """
    sender, err = _dm_sender_or_error()
    if err is not None:
        return err
    record = await anyio.to_thread.run_sync(
        functools.partial(get_backend().dm_send, sender, to, body, store=tasks_path)
    )
    return json.dumps(record)


def _attach_and_compose(
    file_path: str, caption: str | None, tasks_path: str | None
) -> "tuple[dict | None, str | None]":
    """Copy ``file_path`` into the attachment store; return ``(parts, error)``.

    ``parts`` is ``{"body", "attachment"}``. The message body IS the
    reference: the stored url goes on its own line, which is exactly what the
    operator's own uploads produce and what the chat pane already knows how
    to render. No new record field, no second convention, and an older client
    still shows something meaningful instead of nothing.
    """
    import os

    from ._attachments import AttachmentError, store_local_file
    from ._backend import _HUB_URL_ENV

    if os.environ.get(_HUB_URL_ENV):
        return None, json.dumps(
            {
                "error": "dm_send_document: a remote hub is configured "
                f"({_HUB_URL_ENV}), so the file would be copied into the "
                "LOCAL attachment store while the message went to the hub — "
                "the operator would receive a link to nothing. Upload through "
                "the hub's /dm/upload endpoint instead."
            }
        )
    try:
        meta = store_local_file(file_path, store=tasks_path)
    except AttachmentError as exc:
        return None, json.dumps({"error": f"dm_send_document: {exc}"})
    label = (caption or "").strip() or meta["filename"]
    return {"body": f"{label}\n{meta['url']}", "attachment": meta}, None


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
    sender, err = _dm_sender_or_error()
    if err is not None:
        return err
    parts, err = _attach_and_compose(file_path, caption, tasks_path)
    if err is not None:
        return err
    record = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().dm_send, sender, to, parts["body"], store=tasks_path
        )
    )
    return json.dumps({"message": record, "attachment": parts["attachment"]})


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
    sender, err = _dm_sender_or_error()
    if err is not None:
        return err
    # Thread-key + ack + read composition lives in the backend (one RPC later).
    result = await anyio.to_thread.run_sync(
        functools.partial(
            get_backend().dm_list, sender, peer=peer, ack=ack, store=tasks_path
        )
    )
    return json.dumps(result)


__all__ = [
    "dm_list",
    "dm_send",
    "dm_send_document",
    "health",
    "help_clear",
    "help_wait",
    "poll_notifications",
    "reassign_task",
    "todo_skills_get",
    "todo_skills_list",
]

# EOF
