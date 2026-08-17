#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The CARD PAYLOAD: how a card survives a round-trip through SQLite intact.

WHY A PAYLOAD COLUMN AT ALL — THE MEASUREMENT THAT DECIDED IT
--------------------------------------------------------------
The obvious S2 read is "SELECT the typed columns and rebuild the dict from them."
It is wrong, and the live store says so out loud (measured 2026-07-13, 1,452 cards):

  * **22 distinct card keys are not in the column mapping at all** — ``deferred_at``
    (20 cards), ``subagent`` (8), ``blocked_by`` (3), ``completed_at``,
    ``tasks_path``, ``canonical_spec``, ``next_action``, and a whole family of
    ad-hoc ``note_*`` fields agents invent as they work. A column-based rebuild
    DROPS every one of them, silently. The card still looks right.
  * **711 distinct key ORDERS** exist across the cards. A column-based rebuild
    imposes one order on all of them, so anything that serializes a card (the CLI
    printing JSON, an API response) changes shape.

Neither would fail a count check, and neither would fail a "looks plausible" read.
They would just be wrong — and being wrong is strictly worse than being slow,
because slow is visible and wrong is not.

So: **the typed columns are the INDEX; ``card_json`` is the TRUTH.** SQL filters
on the indexed columns (that is the entire point — an indexed lookup instead of a
5.8 MB parse), and the row we hand back is decoded from the verbatim payload. The
read is exact BY CONSTRUCTION, not by a mapping someone has to remember to update
when a new field appears. A field this file has never heard of round-trips anyway.

WHY IT IS STILL FAST
--------------------
JSON decoding the matched rows is a fraction of the YAML parse it replaces, and
— unlike the YAML parse — it is paid only on the rows the query actually returns.
That is what makes filtering finally mean something: today
``list_tasks(assignee=...)`` costs the same as listing everything, because the
cost is the parse, not the query.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: Name of the payload column on ``tasks``. Imported by the read guard, which
#: checks this exact name against ``PRAGMA table_info`` — the artifact, not a stamp.
CARD_JSON_COL = "card_json"


def json_or_none(value) -> str | None:
    """Serialize a non-empty list/dict to compact JSON, else ``None``.

    The side-car encoder (``deadlines`` / ``_log_meta`` / ``users.notify``). Kept
    here beside the payload encoder so both JSON policies live in one file.
    """
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def card_payload_json(row: dict) -> str | None:
    """The card, VERBATIM, as JSON — the blob an S2 read reconstructs from.

    STRICT: no ``default=str`` coercion. A card that cannot round-trip through JSON
    losslessly (an exotic scalar, a non-string mapping key) yields ``None`` — and
    that ``NULL`` is LOAD-BEARING: it is what makes the read guard refuse the whole
    DB and fall back to YAML, instead of quietly handing back a card whose fields
    changed shape on the way through. A coercing encoder would have hidden it.

    ``sort_keys`` is deliberately NOT set: the YAML read gives callers the card's
    own key order, so this must too.
    """
    try:
        return json.dumps(row, ensure_ascii=False)
    except (TypeError, ValueError):
        logger.error(
            "!! CARD %r DOES NOT ROUND-TRIP THROUGH JSON. A row stored with this "
            "NULL payload is UNREADABLE, and the read refuses the WHOLE store "
            "rather than serve a lossy copy — so every agent loses the board "
            "until the row is rewritten. Writers must use "
            "card_payload_json_or_raise and refuse instead.",
            row.get("id"),
        )
        return None


class CardNotSerialisableError(TypeError):
    """A card carries a value JSON cannot represent, so it must not be stored."""


def _unserialisable_fields(row: dict) -> list[str]:
    """``["note (datetime)", …]`` — the fields to blame, with their types.

    The whole point of this error over a bare ``TypeError`` from ``json``: the
    caller passed one bad value among a dozen fields and needs to know WHICH.
    ``json.dumps``'s own message names the type and not the key.
    """
    blamed = []
    for key, value in row.items():
        try:
            json.dumps({key: value}, ensure_ascii=False)
        except (TypeError, ValueError):
            blamed.append(f"{key} ({type(value).__name__})")
    return blamed


def card_payload_json_or_raise(row: dict) -> str:
    """The card as JSON, or REFUSE THE WRITE. For every writer of a card row.

    THE DIFFERENCE FROM :func:`card_payload_json`, AND WHY IT IS NOT A STYLE
    CHOICE. That function answers ``None`` for a card JSON cannot carry, and the
    ``NULL`` it produces is documented as load-bearing — it is what makes the
    read refuse rather than hand back a card whose fields changed shape.

    That reasoning is sound for a payload which is ALREADY MISSING. It is not
    sound as a WRITE policy, and the difference was measured on 2026-08-17:

        add_task(..., note=datetime(...))  ->  row stored, card_json NULL
        any next store operation           ->  ExportRefused naming that row

    One ``add_task`` with an ordinary Python value, and the next unrelated
    operation by ANY agent is dead — that is the tasks-variant outage first seen
    on 2026-08-11 and unexplained for five days. The writer had already
    discovered the payload could not be serialised and stored the row anyway.

        refusing the write   the caller gets one message naming the field, and
                             fixes their own call
        storing the NULL     everyone else loses the whole board until somebody
                             unrelated happens to rewrite that row

    Same information, discovered at the same instant; the only difference is who
    pays for it. Today it is everyone except the caller who caused it.

    NOT A LICENCE TO TOLERATE ON READ. This refuses EARLIER, on the way in. The
    read-modify-write door must keep refusing (see
    ``tests/.../test__rmw_refusal_must_not_become_tolerance.py`` — skipping a row
    there DELETES it), and only a pure read that never writes back may skip.
    """
    try:
        return json.dumps(row, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        blamed = _unserialisable_fields(row) or ["(no single field — the card "
                                                 "as a whole is not encodable)"]
        raise CardNotSerialisableError(
            f"card {row.get('id')!r} carries a value JSON cannot represent, so "
            f"it was NOT written: {', '.join(blamed)}.\n"
            "  Storing it would leave a row with a NULL payload, and the read "
            "refuses the WHOLE store on such a row — one bad card makes the "
            "board unreadable for every agent until it is rewritten. Refusing "
            "here costs you this one call instead.\n"
            "  NEXT STEP: pass a JSON-representable value — a datetime as an "
            "ISO-8601 string, a set as a list, a tuple-keyed mapping as a "
            "string-keyed one. Nothing was written; the store is unchanged."
        ) from exc


def card_from_payload(blob: str) -> dict:
    """Decode one ``card_json`` blob back into a card mapping."""
    return json.loads(blob)


__all__ = [
    "CARD_JSON_COL",
    "CardNotSerialisableError",
    "card_from_payload",
    "card_payload_json",
    "card_payload_json_or_raise",
    "json_or_none",
]

# EOF
