#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The messaging rail as a PYTHON API — notifications and direct messages.

Five operations existed only as async MCP tool bodies, so a script, a cron
job or another package could not reach them at all: the logic was welded to
the transport. Audit §6 names exactly this ("MCP tools have no matching
Python API") and it is not a bookkeeping rule — an operation you can only
perform by speaking MCP is an operation the rest of the ecosystem cannot
automate.

WHAT MOVED, AND WHAT DELIBERATELY DID NOT. The composition these functions
perform — user resolution, the fail-soft liveness heartbeat, the thread-key
and ack handling — already lives in the BACKEND, so a remote backend can make
it one round trip. That stays there. What lived in the MCP bodies and moves
here is only the part above it: resolve the calling agent's identity, call the
backend, hand back the result.

TWO DIFFERENCES FROM THE MCP TOOLS, both deliberate:

  * these return DICTS, not JSON strings. The string is the transport's
    concern; a Python caller that has to `json.loads` its own library's return
    value is being charged for someone else's encoding.
  * these RAISE on failure instead of returning ``{"error": ...}``. Fail fast
    and loud: a caller who ignores a returned error dict sends nothing and
    hears nothing, which is the failure mode this package exists to stop. The
    MCP wrappers catch these and re-emit the exact ``{"error": ...}`` payloads
    they always did, so the tool contract is unchanged.

``sender`` defaults to the environment identity ($SCITEX_CARDS_AGENT_ID) the
way the MCP tools resolve it, and can be passed explicitly. The explicit form
is not decoration: PA-306 forbids `monkeypatch`, so a parameter is how a test
drives a sender without reaching into os.environ.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentIdentityUnresolved",
    "RemoteHubAttachmentUnsupported",
    "ack_notifications",
    "dm_list",
    "dm_send",
    "dm_send_document",
    "poll_notifications",
]


class AgentIdentityUnresolved(RuntimeError):
    """No agent identity is configured, so a DM's ``from`` cannot be named.

    Raised rather than defaulted. A blank or ``"unknown"`` sender would make
    the message deliverable and untraceable at once — the recipient sees a DM
    from nobody, and no reply can find its way back.
    """


class RemoteHubAttachmentUnsupported(RuntimeError):
    """A remote hub is configured, so a local attachment would not travel.

    The bytes would land in the LOCAL attachment store while the message went
    to the hub, and the recipient would receive a link to nothing.
    """


# The MCP tool's wording, kept verbatim: it is a published contract, it names
# the env var AND the .mcp.json line to add, and callers may match on it.
_NO_IDENTITY_MESSAGE = (
    "dm: no agent identity configured. Set "
    "SCITEX_CARDS_AGENT_ID=<your-agent> in the MCP server env "
    '(.mcp.json: "SCITEX_CARDS_AGENT_ID": '
    "\"${SCITEX_CARDS_AGENT_ID}\") so the DM 'from' field names a "
    "real agent."
)


def resolve_sender(sender: str | None = None) -> str:
    """Return the DM sender identity, or raise :class:`AgentIdentityUnresolved`.

    An explicit ``sender`` wins; otherwise the environment identity is used.
    """
    if sender is not None:
        return sender
    from ._mcp_channel import resolve_agent_id_optional

    resolved = resolve_agent_id_optional()
    if resolved is None:
        raise AgentIdentityUnresolved(_NO_IDENTITY_MESSAGE)
    return resolved


def poll_notifications(
    agent: str,
    unseen_only: bool = True,
    ack: bool = False,
    store: Any = None,
) -> dict:
    """PULL ``agent``'s pending notifications. READING NEVER CONFIRMS.

    Returns the inbox payload — ``{agent, recipient_id, store, notifications,
    unconfirmed, confirm_with}``. Anything you do not pass to
    :func:`ack_notifications` stays unseen and comes back on the next poll,
    which is the point: a consumer that dies between reading and delivering
    must lose nothing.

    ``ack=True`` is DEPRECATED and destroys undelivered messages by advancing
    the cursor at handover; it is honoured, not recommended. See the MCP
    tool's docstring for the incident that named it.
    """
    from ._backend import get_backend

    return get_backend().poll_notifications(
        agent, unseen_only=unseen_only, ack=ack, store=store
    )


def ack_notifications(agent: str, ids: list[str], store: Any = None) -> dict:
    """CONFIRM delivery of ``ids`` — the only cursor-advancing verb.

    Idempotent. Returns ``{agent, recipient_id, store, requested, confirmed,
    already_confirmed, unknown}``. Compare the returned ``store`` with the one
    :func:`poll_notifications` reported: DIFFERENT positively identifies a
    split; EQUAL is cannot-tell, not all-clear.
    """
    from ._backend import get_backend

    return get_backend().ack_notifications(agent, ids, store=store)


def dm_send(to: str, body: str, store: Any = None, sender: str | None = None) -> dict:
    """Send a text direct message to ``to``. Returns the stored DM record.

    TEXT ONLY — use :func:`dm_send_document` to send a file. Describing a file
    in prose, or pasting a filesystem path, hands the operator something they
    cannot open from a browser.
    """
    from ._backend import get_backend

    return get_backend().dm_send(resolve_sender(sender), to, body, store=store)


def dm_send_document(
    to: str,
    file_path: str,
    caption: str | None = None,
    store: Any = None,
    sender: str | None = None,
) -> dict:
    """Send a FILE to ``to`` as a direct message.

    Copies the bytes into the board's attachment store — the same store, url
    shape and renderer the operator's own uploads use — so a file the caller
    later moves or deletes still arrives. Returns ``{"message": <DM record>,
    "attachment": {url, filename, mime_type, size}}``.

    Raises :class:`RemoteHubAttachmentUnsupported` when a remote hub is
    configured, and ``_attachments.AttachmentError`` for a missing file, a
    non-regular file, or one over the size ceiling.
    """
    import os

    from ._attachments import store_local_file
    from ._backend import _HUB_URL_ENV, get_backend

    resolved_sender = resolve_sender(sender)
    if os.environ.get(_HUB_URL_ENV):
        raise RemoteHubAttachmentUnsupported(
            "dm_send_document: a remote hub is configured "
            f"({_HUB_URL_ENV}), so the file would be copied into the "
            "LOCAL attachment store while the message went to the hub — "
            "the operator would receive a link to nothing. Upload through "
            "the hub's /dm/upload endpoint instead."
        )
    meta = store_local_file(file_path, store=store)
    # THE BODY IS THE REFERENCE: the url goes on its own line, which is what
    # operator-side uploads produce and what the chat pane already renders.
    # No second convention, and an older client still shows something.
    label = (caption or "").strip() or meta["filename"]
    record = get_backend().dm_send(
        resolved_sender, to, f"{label}\n{meta['url']}", store=store
    )
    return {"message": record, "attachment": meta}


def dm_list(
    peer: str | None = None,
    ack: bool = False,
    store: Any = None,
    sender: str | None = None,
) -> dict:
    """Read this agent's DM thread with ``peer`` (default: the operator).

    Returns ``{"thread": <id>, "peer": <peer>, "messages": [...]}`` in
    chronological order. ``ack=True`` marks the messages addressed to this
    agent read, advancing the unread cursor the board's /chat view shows.
    """
    from ._backend import get_backend

    return get_backend().dm_list(
        resolve_sender(sender), peer=peer, ack=ack, store=store
    )


# EOF
