#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Make a DM body storable in EVERY backend the store must survive.

WHY THIS EXISTS, measured rather than anticipated
-------------------------------------------------
A NUL byte is legal in SQLite TEXT and ILLEGAL in PostgreSQL TEXT. So a body
that SQLite accepts silently can make the whole store unmigratable. On
2026-07-30 that was not hypothetical twice over:

  * two rows in ``messages`` blocked the PostgreSQL preflight outright; and
  * within ~2 MINUTES of clearing them, a third arrived in ``dm_messages`` --
    written by an agent that was actively trying not to write one, in a message
    ANNOUNCING the fix. Prose *about* the byte is how the byte propagates.

``dm_messages`` is append-only (``dm_messages_no_delete``) and immutable except
its tombstone columns (``dm_messages_immutable`` aborts any UPDATE touching
``body``). So a NUL that reaches that table CANNOT BE CORRECTED IN PLACE, by
design. Write time is not the convenient place to catch it -- it is the ONLY
place. That is what makes this module load-bearing rather than hygiene.

WHY SANITISE AND NOT REJECT
---------------------------
Rejecting would have thrown away a legitimate 4 KB technical message whose only
sin was quoting the byte it was about. Sanitising loses nothing instead:
``record_json`` already stores the body with the byte JSON-escaped, verified
byte-identical to the column, so the ORIGINAL survives in the same row in a form
PostgreSQL accepts. The column gets a visible marker; the record keeps the truth.

WHY U+2400 AND NOT A BACKSLASH ESCAPE
-------------------------------------
The obvious marker is the four characters backslash-x-0-0. It was tried on the
two ``messages`` rows and it is subtly wrong: one of those bodies ALREADY
contained that sequence twice, as prose, so un-escaping produced three NULs
where the original had one. A marker a human can type by accident cannot be
distinguished from content. U+2400 SYMBOL FOR NULL is a single codepoint meaning
exactly "a NUL was here", renders visibly, is storable everywhere, and does not
occur in ordinary prose about escape sequences.

WHY ``chr(0)`` AND NOT A STRING LITERAL -- learned the hard way, here
--------------------------------------------------------------------
The first draft of THIS FILE spelled the constant as a quoted literal and put a
real NUL byte on line 53. Git classifies such a file as binary, so every future
diff of the guard would have been unreviewable -- which is precisely the defect
the two ``messages`` rows were discussing when they were created. A module that
cannot be written safely by hand must not be written by hand: ``chr(0)`` is
constructed, cannot be typed by accident, and keeps this file plain text.

SCOPE IS DELIBERATELY NARROW
----------------------------
Only U+0000 is replaced. Every other C0 control character is legal in
PostgreSQL TEXT, so widening this to "control characters" would mangle
legitimate content to solve a problem that does not exist. The guard is exactly
as wide as the constraint it enforces.
"""

from __future__ import annotations

__all__ = ["NUL", "NUL_MARKER", "unstorable_offsets", "to_storable"]

#: The one codepoint no PostgreSQL TEXT column can hold. Constructed, never
#: written as a literal -- see the module docstring for why that matters.
NUL = chr(0)

#: U+2400 SYMBOL FOR NULL -- visible, storable, not typeable by accident.
NUL_MARKER = "␀"


def unstorable_offsets(text: str) -> list[int]:
    """Return the indices of every codepoint no backend can store.

    Read-only: a caller can ask "would this be a problem?" without changing
    anything. Returns an empty list for the overwhelmingly common clean case,
    and for a non-``str`` input -- deciding what a non-string body IS belongs
    to the caller's own validation, not here.
    """
    if not isinstance(text, str):
        return []
    return [i for i, ch in enumerate(text) if ch == NUL]


def to_storable(text: str) -> tuple[str, list[int]]:
    """Return ``(storable_text, offsets_that_were_replaced)``.

    The clean path returns the SAME object and an empty list, so the guard costs
    one scan and allocates nothing for the ~100% of messages that are already
    fine. When offsets come back non-empty the caller should record them --
    a substitution nobody can see is a lie, and this store is append-only, so
    there is no later opportunity to annotate the row.
    """
    offsets = unstorable_offsets(text)
    if not offsets:
        return text, []
    return text.replace(NUL, NUL_MARKER), offsets


# EOF
