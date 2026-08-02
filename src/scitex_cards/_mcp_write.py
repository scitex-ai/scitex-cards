#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The two card-WRITE MCP tools — ``add_task`` and ``update_task``.

Split out of :mod:`scitex_cards._mcp_server` (which its own docstring already
listed as queued) when adding the ``parked`` field pushed that module past the
512-line budget. These two tools are the natural seam: their signatures carry
the ENTIRE card schema, so they grow with every new field, and they were 223 of
the server's 537 lines. Giving them their own module is the fix that holds; a
one-off line-shave would have been undone by the next field.

Registration follows the convention the sibling clusters already use
(:mod:`_mcp_relations`, :mod:`_mcp_skills`): import the ONE ``mcp`` instance
from :mod:`_mcp_server` and register on it with ``@mcp.tool()``. The server
imports this module at its tail for the side effect. ``TOOL_NAMES`` stays in
the server — one canonical registry, not two.

Behaviour is byte-identical to the pre-split tools; the only addition is the
``parked`` parameter.
"""

from __future__ import annotations

import functools
import json

import anyio

from ._backend import get_backend

# From the LEAF (`_mcp_app`), NOT from `_mcp_server` — importing the server here
# closed a cycle (it imports this module at its tail, for the registration side
# effect), so `import scitex_cards._mcp_write` cold raised ImportError.
from ._mcp_app import _ENUM_FIELDS, mcp

#: Transport-safe stand-in for ``""`` on a CLEAR. MCP clients strip
#: empty-string params before the server sees them, which makes the
#: documented ``field=""`` convention unreachable over MCP — the call arrives
#: malformed rather than clearing anything. Same token `list_tasks` already
#: uses for "no blocker", so callers learn one spelling, not two.
_CLEAR = "__none"


@mcp.tool()
async def add_task(
    id: str,
    title: str,
    status: str = "deferred",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    # Operator-co-designed surface (TG 9667).
    task: str | None = None,
    project: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    goal: str | None = None,
    last_activity: str | None = None,
    blocker: str | None = None,
    pr_url: str | None = None,
    issue_url: str | None = None,
    kind: str | None = None,
    parked: str | None = None,  # WHY this card stands; see the docstring
    # Compute-kind metadata (ADR-0002).
    job_id: str | None = None,
    command: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    # Deadline schema (P4 + recurring extension; closes the gap
    # noted in PR #127: callers couldn't SET deadlines via MCP).
    deadline: str | None = None,
    deadlines: list[str] | None = None,
    scheduled: str | None = None,
    created_by: str | None = None,  # creating USER; hook-bypass: line-limit
) -> str:
    """Append a new task to the store. Returns the inserted task as JSON.

    Closed-enum fields (``status`` / ``kind`` / ``blocker``) are gated by
    the writer's validator — typos raise ``TaskValidationError`` with the
    bad value and the valid set.

    ``deadline`` accepts the P4 schema: a bare ISO date / ISO datetime,
    optionally followed by a recurring repeater suffix
    (``+1d``/``+1w``/``+1m``/``+1y``). ``deadlines`` is the multi form (a
    list of the same shape) — mutually exclusive with ``deadline``.
    ``scheduled`` is the corresponding "start work on" stamp (validator
    rejects ``deadline < scheduled``). See ``scitex_cards._model`` +
    ``next_deadline_for_task`` for parse rules.

    A DEADLINE IS A VIEW, NEVER A NOTIFIER. NOTHING FIRES when one
    arrives: no sweep, digest or nudge reads ``deadline``. It feeds the
    ``list_tasks(overdue=True)`` filter and the board view, nothing else
    — and even that filter is PULL-only (you must run the query).

    A RECURRING DEADLINE IS NOT A RECURRING REMINDER, and is worse than
    merely silent: the repeater rolls the next occurrence FORWARD, so it
    is always in the future and ``overdue=True`` NEVER matches it. It
    reaches neither rail; it is a date-pill. Do not set one expecting to
    be reminded — you will not be.

    To BE NUDGED, keep the card open and owned: the stale-active sweep
    nudges the owner of any ``in_progress`` / ``blocked`` card untouched
    beyond the threshold, and the backlog sweep does the same for
    untouched ``deferred`` cards.

    ``parked`` is the one way OUT of that backlog nudge, and it is a REASON,
    not a flag: free text saying WHY this card deliberately stands (a
    north-star umbrella whose real work lives in its children, say). A
    non-empty reason exempts the card from the backlog nudge AND from
    auto-expiry — a standing goal must not be auto-cancelled at the horizon
    for the crime of standing. Whitespace-only is NOT a park: a park with no
    stated reason is exactly the abandonment the sweep should still catch.
    It hides a card from the ALARM, never from the BOARD.
    You may park work you are NOT doing; you may NOT park work you claim to
    BE doing — the stale-active guard over ``in_progress`` ignores this field
    on purpose. (hook-bypass: line-limit.)
    """
    _call = functools.partial(
        get_backend().add_task,
        None,
        id=id,
        title=title,
        status=status,
        scope=scope,
        assignee=assignee,
        priority=priority,
        parent=parent,
        note=note,
        repo=repo,
        depends_on=depends_on,
        blocks=blocks,
        task=task,
        project=project,
        host=host,
        agent=agent,
        goal=goal,
        last_activity=last_activity,
        blocker=blocker,
        pr_url=pr_url,
        issue_url=issue_url,
        kind=kind,
        parked=parked,
        job_id=job_id,
        command=command,
        started_at=started_at,
        finished_at=finished_at,
        deadline=deadline,
        deadlines=deadlines,
        scheduled=scheduled,
        created_by=created_by,  # hook-bypass: line-limit
    )
    inserted = await anyio.to_thread.run_sync(_call)
    return json.dumps(inserted)


@mcp.tool()
async def update_task(
    task_id: str,
    title: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    repo: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    # Operator-co-designed surface (TG 9667).
    task: str | None = None,
    project: str | None = None,
    host: str | None = None,
    agent: str | None = None,
    goal: str | None = None,
    last_activity: str | None = None,
    blocker: str | None = None,
    pr_url: str | None = None,
    issue_url: str | None = None,
    kind: str | None = None,
    parked: str | None = None,  # WHY this card stands; "" un-parks it
    # Compute-kind metadata (ADR-0002).
    job_id: str | None = None,
    command: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    # Deadline schema (P4 + recurring extension) — mirror of the
    # add_task surface so callers can SET deadlines via MCP, not just
    # READ them via list_tasks (PR #127 gap).
    deadline: str | None = None,
    deadlines: list[str] | None = None,
    scheduled: str | None = None,
) -> str:
    """Mutate fields of an existing task. Returns the merged task as JSON.

    TO CLEAR A FIELD OVER MCP, PASS ``"__none"`` — e.g. ``blocker="__none"``.
    The bare ``field=""`` form documented below is correct for the Python and
    CLI surfaces but DOES NOT WORK over MCP: clients strip empty-string
    params before the server sees them, so the call arrives malformed instead
    of clearing anything. ``"__none"`` survives transport and means exactly
    the same thing (it is the same token ``list_tasks`` uses for "no
    blocker"). This applies to ``blocker`` and ``kind`` (delete the key) and
    to every free-text field including ``parked`` (un-park the card).
    ``status`` still refuses to be cleared — a card must carry a decision.

    Pass an empty string (e.g. ``scope=""``) to CLEAR a string field.
    Pass an empty list to CLEAR a list field. Omit a field to leave it
    untouched. Closed-enum values (``status`` / ``kind`` / ``blocker``)
    are gated by the writer's validator.

    The ``""``-clears rule holds for the CLOSED-ENUM fields too:
    ``blocker=""`` DELETES the blocker key (it does not write ``""``, which
    the validator would reject, and it is not the same as ``blocker="none"``
    — a legal enum member that leaves the key PRESENT). Same for ``kind=""``.
    The one exception is ``status``, which cannot be cleared — every card
    must carry a decision — so ``status=""`` raises with the valid set.

    ``parked`` follows the free-text rule: ``parked="<why>"`` parks the card
    (exempt from the backlog nudge and from auto-expiry, still fully visible on
    the board), and ``parked=""`` UN-parks it — the card rejoins the sweep, which
    is exactly what you want when a standing goal becomes real work again. A
    whitespace-only reason is not a park; a park must say WHY, because a park
    with no stated reason is the abandonment the sweep exists to catch.

    ``deadline`` / ``deadlines`` / ``scheduled`` follow the same P4
    schema as ``add_task``. Pass an empty string to CLEAR ``deadline`` /
    ``scheduled``; pass an empty list to CLEAR ``deadlines``. The pair
    ``deadline`` + ``deadlines`` is mutually exclusive; the validator
    will raise if both are set on the resulting task.

    A DEADLINE IS A VIEW, NEVER A NOTIFIER — setting one (recurring or
    not) sends no notification, ever; it only feeds
    ``list_tasks(overdue=True)`` and the board, and a RECURRING one does
    not even reach that filter (the repeater rolls it into the future).
    Owner nudges key on INACTIVITY (``last_activity``), so to be nudged
    keep the card open and owned. See ``add_task``.
    (hook-bypass: line-limit.)
    """
    fields: dict = {}
    for key, value in (
        ("title", title),
        ("status", status),
        ("scope", scope),
        ("assignee", assignee),
        ("priority", priority),
        ("parent", parent),
        ("note", note),
        ("repo", repo),
        ("task", task),
        ("project", project),
        ("host", host),
        ("agent", agent),
        ("goal", goal),
        ("last_activity", last_activity),
        ("blocker", blocker),
        ("pr_url", pr_url),
        ("issue_url", issue_url),
        ("kind", kind),
        ("parked", parked),
        ("job_id", job_id),
        ("command", command),
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("deadline", deadline),
        ("scheduled", scheduled),
    ):
        if value is None:
            continue
        # TRANSPORT-SAFE CLEAR. The documented convention is ``field=""``,
        # and over MCP it is UNREACHABLE: the client strips empty-string
        # params before the server ever sees them, so the call arrives
        # malformed (`"field": }` -> InputValidationError) rather than
        # clearing anything. Measured first-hand 2026-07-18 trying to clear a
        # blocker; the documented escape hatch simply did not exist over the
        # transport most agents use.
        #
        # ``"__none"`` survives transport and means exactly what ``""`` means.
        # Reusing the token `list_tasks` already spends on "no blocker"
        # instead of inventing a second spelling — one convention, two verbs.
        if value == _CLEAR:
            value = ""
        # Closed-enum fields go through VERBATIM — the store owns the
        # ""-clears rule for them (`blocker`/`kind`: delete the key;
        # `status`: refuse loudly, a card must carry a decision). Mapping
        # "" -> None HERE would have deleted `status` behind the store's
        # back, silently producing a status-less card. Free-text fields
        # keep the local translation: "" = clear.
        #
        # `parked` is free text ON PURPOSE, so "" reaches the store as None
        # and un-parks the card. That is the intended escape hatch, not an
        # oversight: a standing goal that becomes real work must be able to
        # rejoin the sweep.
        if key in _ENUM_FIELDS:
            fields[key] = value
        else:
            fields[key] = None if value == "" else value
    # List fields: ``None`` = leave untouched (filtered above);
    # empty list = clear; non-empty list = replace.
    if depends_on is not None:
        fields["depends_on"] = list(depends_on) if depends_on else None
    if blocks is not None:
        fields["blocks"] = list(blocks) if blocks else None
    if deadlines is not None:
        fields["deadlines"] = list(deadlines) if deadlines else None
    merged = await anyio.to_thread.run_sync(
        functools.partial(get_backend().update_task, None, task_id, **fields)
    )
    return json.dumps(merged)


__all__ = ["add_task", "update_task"]
