#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Org-mode export adapter for scitex-cards (P4 PR2, lead-approved 2026-06-12).

Renders the canonical YAML task list to a ``tasks.org`` document with
emacs org-mode wire-shape:

  * <STATE> <title>
    DEADLINE: <YYYY-MM-DD>
    SCHEDULED: <YYYY-MM-DD>
    :PROPERTIES:
    :ID: <task-id>
    :PROJECT: <project>
    :STATUS: <scitex-cards-status>
    :END:

    <note body, free-form markdown>

The mapping below pins the wire-shape so an emacs / org-agenda reader
can ingest the file directly:

  * scitex-cards status -> org TODO STATE
      pending     -> TODO
      in_progress -> INPROGRESS
      blocked     -> WAITING
      done        -> DONE
      deferred    -> SOMEDAY
      failed      -> CANCELLED
      goal        -> GOAL

  * scitex-cards deadline   -> org DEADLINE: line
  * scitex-cards scheduled  -> org SCHEDULED: line
  * scitex-cards parent     -> nesting depth (parent heading -> child heading)
  * scitex-cards note       -> body (free-form markdown)
  * everything else        -> :PROPERTIES: drawer entries

Today's slice is EXPORT only (one-way YAML -> .org). Import (.org ->
YAML) lands when the operator starts editing the .org file in emacs
+ org-agenda; design notes are in the P4 design a2a.

Note on DEADLINE lines: this export is where a scitex-cards deadline can
first *reach* a reminder engine — because ORG-AGENDA is one. scitex-cards
itself is NOT. In scitex-cards a deadline is a VIEW: it feeds the `overdue`
filter and the board, and NEVER sends a notification; a recurring one does
not even reach that filter (the repeater rolls it into the future — see
`_model.is_overdue`). Emitting a `DEADLINE:` here does not change any of
that; it just hands the date to org, where a repeater DOES mean what a
reader expects. Do not infer scitex-cards behaviour from org's.
"""

from __future__ import annotations

from typing import Iterable

# Status -> org TODO state mapping. Keep this aligned with VALID_STATUSES
# in _model.py; an unknown status falls back to bare TODO so a forward-
# compat YAML status doesn't crash the export.
STATUS_TO_ORG: dict[str, str] = {
    "pending": "TODO",
    "in_progress": "INPROGRESS",
    "blocked": "WAITING",
    "done": "DONE",
    "deferred": "SOMEDAY",
    "failed": "CANCELLED",
    # ``cancelled`` (closed as not planned) maps to org CANCELLED — a closed
    # org state, same family as ``failed``. Both are declared after the org
    # ``|`` separator in the ``#+TODO`` line so org-agenda treats them as DONE-
    # type (closed) keywords.
    "cancelled": "CANCELLED",
    "goal": "GOAL",
}

# Properties drawer fields (emitted under :PROPERTIES: in this order).
# `deadline` + `scheduled` are NOT in the drawer — they're first-class
# org timestamps on the line above.
_PROP_FIELDS: tuple[str, ...] = (
    "project",
    "status",
    "priority",
    "agent",
    "host",
    "blocker",
    "kind",
    "repo",
)


def build_org(tasks: Iterable[dict]) -> str:
    """Render a task list to an org-mode document string.

    Parameters
    ----------
    tasks : iterable of dict
        Tasks in the wire shape produced by :func:`scitex_cards.load_tasks`.

    Returns
    -------
    str
        The full org document text (UTF-8). Always ends with a single
        trailing newline so a downstream writer can append cleanly.

    Notes
    -----
    Headings are flat (all top-level ``*``) for the first slice. Parent
    nesting is encoded via the ``:PARENT:`` property (avoids the
    cycle-detection complexity of an actual tree until the operator
    asks for it; org-agenda handles flat-heading TODO state perfectly).
    """
    out: list[str] = []
    out.append("#+TITLE: scitex-cards export")
    out.append("#+TODO: TODO INPROGRESS WAITING | DONE CANCELLED SOMEDAY")
    out.append("#+STARTUP: showall")
    out.append("")
    for task in tasks:
        out.extend(_render_task(task))
        out.append("")
    if out[-1] != "":
        out.append("")
    return "\n".join(out)


def _render_task(task: dict) -> list[str]:
    """Render a single task as a list of org lines."""
    tid = task.get("id") or ""
    title = task.get("title") or tid
    state = STATUS_TO_ORG.get(task.get("status") or "", "TODO")
    head = f"* {state} {title}"
    lines: list[str] = [head]

    # DEADLINE / SCHEDULED timestamp lines — emitted right after the
    # heading per the org-mode convention (org-agenda parses them only
    # in this position). Bare-date form `<YYYY-MM-DD>` covers both bare
    # dates and ISO datetimes (we strip the time component for the
    # agenda; the YAML keeps the full precision).
    timestamps: list[str] = []
    # P4 PR3: prefer the new `deadlines` list when present (each entry
    # becomes its own DEADLINE: token — org agenda treats each
    # independently when emitted on the same heading-stamp line).
    # The repeater suffix (` +1w` / ` ++2m`) passes through verbatim
    # so the wire stays 1:1 with org-mode.
    raw_deadlines = task.get("deadlines")
    if isinstance(raw_deadlines, list) and raw_deadlines:
        for entry in raw_deadlines:
            stamp = _as_org_deadline_stamp(entry)
            if stamp:
                timestamps.append(f"DEADLINE: <{stamp}>")
    else:
        single = _as_org_deadline_stamp(task.get("deadline"))
        if single:
            timestamps.append(f"DEADLINE: <{single}>")
    scheduled = _as_org_deadline_stamp(task.get("scheduled"))
    if scheduled:
        timestamps.append(f"SCHEDULED: <{scheduled}>")
    if timestamps:
        lines.append(" ".join(timestamps))

    # :PROPERTIES: drawer
    props: list[tuple[str, str]] = [("ID", str(tid))]
    parent = task.get("parent")
    if parent:
        props.append(("PARENT", str(parent)))
    for field_name in _PROP_FIELDS:
        value = task.get(field_name)
        if value is None or value == "":
            continue
        props.append((field_name.upper(), str(value)))
    lines.append(":PROPERTIES:")
    for key, value in props:
        lines.append(f":{key}: {value}")
    lines.append(":END:")

    note = task.get("note")
    if isinstance(note, str) and note.strip():
        lines.append("")
        for raw in note.rstrip("\n").splitlines():
            lines.append(f"  {raw}" if raw else "")
    return lines


def _as_org_deadline_stamp(value: object) -> str | None:
    """Convert a deadline/scheduled string to the org timestamp form.

    P4 PR3 — accepts ISO-8601 ± optional ` +Nu` / ` ++Nu` org repeater
    suffix. The bare-date portion is sliced to ``YYYY-MM-DD`` (org
    agenda treats DEADLINE on bare dates uniformly); the repeater
    passes through verbatim so a recurring deadline emits as
    ``<YYYY-MM-DD +1w>`` — 1:1 with org-mode's native shape.
    """
    import re as _re

    if not isinstance(value, str) or not value.strip():
        return None
    m = _re.search(r"\s+(\+\+?\d+[dwmy])$", value)
    base = value[: m.start()].rstrip() if m else value
    repeater = m.group(1) if m else ""
    if len(base) < 10:
        return None
    base_date = base[:10]
    return f"{base_date} {repeater}".rstrip() if repeater else base_date


def _as_org_date(value: object) -> str | None:
    """Convert a task ISO-8601 date / datetime to the org bare-date form.

    org-agenda parses bare ``<YYYY-MM-DD>`` and ``<YYYY-MM-DD HH:MM>``
    timestamps in DEADLINE / SCHEDULED slots. We always emit the bare
    date so the YAML's higher-precision timestamp (offset / time) stays
    in the YAML; consumers wanting the full timestamp can read the
    :PROPERTIES: drawer (a future slice will add :DEADLINE_PRECISE:
    when needed). Returns None for an absent / non-string value.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    # Slice the date portion off "YYYY-MM-DD..." — works for bare dates
    # AND ISO datetimes. Validation has already happened upstream in
    # `_model._parse_iso_date_or_raise`, so we know the prefix is well-
    # formed when this function is reached.
    return value[:10]
