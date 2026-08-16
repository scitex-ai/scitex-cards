#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Size guards for the scitex-cards channel push path.

The Claude Agent SDK reads server-initiated ``notifications/claude/channel``
messages through a **stdio** JSON reader with a hard 1 MB per-message buffer
(``JSON message exceeded maximum buffer size of 1048576 bytes``). On
2026-07-02 a batch of 180 solver apptainer containers died on boot because an
oversized pushed channel message overflowed that reader. These pure helpers
harden OUR side so a pushed message can never overflow the SDK reader and so a
huge unseen backlog cannot burst all at once on first connect.

Everything here is pure + unit-testable (no live MCP session, no store IO).
"""

from __future__ import annotations

#: Hard cap on the UTF-8 byte length of a pushed ``content`` body. Chosen at
#: 256 KiB — a quarter of the SDK's 1 MB (1048576-byte) stdio reader buffer, so
#: even with the ``meta`` block, JSON framing/escaping (which can inflate bytes
#: ~2x for non-ASCII), and base-line RPC envelope, a single push stays far
#: under the limit with generous headroom. Bodies are truncated to this cap.
MAX_CONTENT_BYTES = 256 * 1024  # 262144

#: Per-``meta``-value UTF-8 byte cap. ``meta`` values are small and known
#: (ids, timestamps, event types), so this is belt-and-suspenders only — a
#: pathological value can never contribute meaningfully to the frame size.
MAX_META_VALUE_BYTES = 4 * 1024  # 4096

#: Max records pushed in a SINGLE ``drain_once`` call, across all recipient
#: keys combined. Prevents a first-connect burst (e.g. a 600-message backlog
#: flooding the session in one tick). The remaining unseen records stay unseen
#: and drain on the next poll tick (~5 s later), a few dozen at a time.
MAX_PUSH_PER_DRAIN = 50


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Return ``text`` truncated to at most ``max_bytes`` UTF-8 bytes.

    Multibyte-safe: never splits a multibyte character (a dangling partial
    sequence at the boundary is dropped via ``errors="ignore"``), so the result
    is always valid UTF-8 whose encoded length is ``<= max_bytes``.
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _bounded_content(body: str | None, card_id: str | None) -> str:
    """Cap the pushed ``content`` body at :data:`MAX_CONTENT_BYTES` UTF-8 bytes.

    When ``body`` fits, it is returned unchanged. When it exceeds the cap it is
    truncated on a UTF-8 char boundary and a pointer suffix is appended so the
    reader can find the full text on the board. The FINAL string (truncated
    prefix + suffix) is guaranteed ``<= MAX_CONTENT_BYTES`` UTF-8 bytes.

    Parameters
    ----------
    body : str | None
        The raw notification body (``None`` is treated as ``""``).
    card_id : str | None
        The board card id, used to build an actionable pointer. Falsy ⇒ a
        generic "see the board" pointer.
    """
    body = body or ""
    encoded = body.encode("utf-8")
    if len(encoded) <= MAX_CONTENT_BYTES:
        return body

    if card_id:
        suffix = f"\n\n[truncated — see card {card_id} on the board]"
    else:
        suffix = "\n\n[truncated — see the board]"
    suffix_bytes = len(suffix.encode("utf-8"))
    # Reserve room for the suffix so the final content still fits the cap.
    budget = MAX_CONTENT_BYTES - suffix_bytes
    prefix = _truncate_utf8(body, budget)
    return prefix + suffix


def _bounded_meta_value(value: str) -> str:
    """Clamp a single ``meta`` value to :data:`MAX_META_VALUE_BYTES`.

    Belt-and-suspenders: ``meta`` values are small/known so this rarely fires;
    it only defends against a pathological oversized value. Always returns a
    ``str``.
    """
    return _truncate_utf8(str(value), MAX_META_VALUE_BYTES)


def _dm_wire_meta(rec: dict, meta: dict) -> dict:
    """Map a direct-message record onto the a2a-compatible wire shape.

    Fleet DM convention (scitex-dev spec v1, card
    fleet-agent-direct-message-board-pane-20260707): a pushed DM must render
    agent-side exactly like an a2a channel message —
    ``<channel source="<sender>" conversation_id="dm:<a>::<b>" ...>`` — so
    agents parse it with ZERO change. For ``event_type == "dm"`` records the
    dispatcher enqueues the sender in ``actor`` and the sorted thread key in
    ``card_id``; here we lift them into the a2a field names: ``meta.source``
    becomes the SENDER (not the channel's own label) and
    ``meta.conversation_id`` carries the thread key. Non-DM records pass
    through untouched (digests keep the configured channel source).
    """
    if rec.get("event_type") == "dm":
        sender = rec.get("actor") or ""
        if sender:
            meta["source"] = _bounded_meta_value(sender)
        meta["conversation_id"] = meta.get("card_id", "")
    return meta


__all__ = [
    "MAX_CONTENT_BYTES",
    "MAX_META_VALUE_BYTES",
    "MAX_PUSH_PER_DRAIN",
    "_bounded_content",
    "_bounded_meta_value",
    "_dm_wire_meta",
]

# EOF
