#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derived scalars that stand in for a card's full ``comments[]``.

WHY THIS EXISTS — measured, twice, a fortnight apart:

    2026-07-17  1,837 cards   /graph ~6 MB     comments[] 4,409,085 B
    2026-07-30  2,854 cards   /graph 19.8 MB   1.0-2.6 s per request

The board polls ``/graph``, and every poll ships every comment of every
card. The payload grows with the store, so this gets worse on its own. The
operator reports it as the DM page stuttering and other browser tabs
slowing — and it is not only their browser: gzip on a payload this size
costs ~1.7 s of SERVER time per request (measured plain 0.67-1.18 s vs
gzip 2.45-2.81 s), and on a single worker that blocks concurrent requests.

These four scalars replace ``comments[]`` for every LIST surface. Measured
2026-07-17: 4,409,085 B of comments become 445,184 B of scalars, a 89.9%
cut of that portion. The full thread stays available from the existing
``/chat/<card_id>`` endpoint, which already preserves each comment's
``kind`` so the route-trace timeline keeps working.

THE NAME ``text_preview`` IS LOAD-BEARING, not decoration. It is a
TRUNCATED copy. If a caller ever posts it back as the comment body it
silently destroys the tail of that comment. The field is named so that
writing such code reads wrong. The same class of bug already exists one
field over: ``board_v3.html`` prefills the note textarea from the payload
and posts it back unconditionally, so dropping ``note`` from the payload
WIPES notes on Save — which is why ``note`` is deliberately NOT part of
this change.
"""

from __future__ import annotations

#: Characters of the last comment kept for the list view. Mirrored by the
#: client helper; changing one without the other makes the client
#: re-truncate (harmless) or render a stale budget (not).
PREVIEW_CHARS = 160


def _truncate(text: str, limit: int = PREVIEW_CHARS) -> str:
    """First ``limit`` characters of ``text``. No ellipsis.

    Deliberately not adding an ellipsis: the client re-truncates to the
    same budget, and an ellipsis would then be counted as content and
    truncated again, drifting the boundary by one character per hop.
    """
    return text[:limit]


def comment_scalars(task: dict) -> dict:
    """The four list-view scalars derived from ``task['comments']``.

    Returns the same keys whatever the input, so a caller never has to
    branch on "did this card have comments" — a card with none reports
    ``comment_count: 0`` and ``last_comment: None`` rather than omitting
    the fields. Absent keys are how a consumer silently reads ``undefined``
    and renders nothing while looking like it worked.

    Tolerates a malformed store: a non-list ``comments`` and non-dict
    entries are treated as absent rather than raising, because ``/graph``
    renders the whole board and one bad row must not blank the page.
    """
    raw = task.get("comments")
    items = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []

    if not items:
        return {
            "comment_count": 0,
            "last_comment": None,
            "first_comment_ts": None,
            "first_comment_author": None,
        }

    first, last = items[0], items[-1]
    return {
        "comment_count": len(items),
        "last_comment": {
            "author": last.get("author"),
            # NOT the comment body. See the module docstring.
            "text_preview": _truncate(str(last.get("text") or "")),
        },
        "first_comment_ts": first.get("ts"),
        "first_comment_author": first.get("author"),
    }


# EOF
