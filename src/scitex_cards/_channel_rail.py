#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the DURABLE channel rail and NAME the messages that never arrived.

WHY THIS EXISTS
---------------
"Did I miss anything across that restart?" has one trustworthy answer and
several worthless ones. ``a2a_inbox`` is an in-memory BUFFER: a restart
empties it, so it returns 0 whether or not anything was sent — the same
answer in the good case and the bad case, which is no answer at all. The
durable rail records every message with a delivery stamp, so an
``delivered_at IS NULL`` row is a message that genuinely did not arrive.

On 2026-08-10 that distinction cost two other agents real work: the buffer
read empty, the conclusion was "messages may have been lost", and two peers
were asked to resend things the rail could have shown were never lost.

WHY A COMMAND RATHER THAN A DOCUMENTED QUERY
--------------------------------------------
The query was written down, validated end-to-end, and STILL not run at the
restart on 2026-08-15 — because running it depended on remembering to. The
constitution's line is "prefer durable automation to manual steps, and never
rely on memory"; a procedure that is only ever recalled is the counterexample.

THE POSITIVE CONTROL IS MANDATORY, AND IT IS FIRST
--------------------------------------------------
A filtered zero means nothing until the rail is known to be populated. The
first attempt at this query went to the PER-AGENT ``runtime/<agent>/state.db``
shards, which carry the ``channel_events`` TABLE but ZERO ROWS. That query
succeeds, returns 0, and reads exactly like an all-clear. It is a dead query.

So the count is checked BEFORE the filters, and a rail that is missing,
unreadable or empty yields :data:`Control.EMPTY` / :data:`Control.UNREADABLE`
and a report whose every signal is ``CANNOT_TELL``. CANNOT TELL IS NOT A PASS.

NO WATERMARK. DELIBERATELY.
---------------------------
An ``id > N`` exclusion was added here once, to skip a row believed to be
permanently undelivered, and TWO DAYS LATER that row was delivered — the
condition the filter existed for had resolved itself, leaving a filter that
silently excluded 348 ids on a premise nothing re-checked. A blind spot is
worse than the noise it removed. If a permanently-NULL row ever genuinely
appears, exclude it THEN, by id, with a dated reason and a re-check date.

``ts`` IS A FLOAT EPOCH, NOT ISO
-------------------------------
Two separate agents have lost time to reading it as a string. It is rendered
here, once, so no caller has to know.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

__all__ = [
    "Control",
    "Signal",
    "UndeliveredMessage",
    "UndeliveredReport",
    "default_rail_path",
    "format_epoch",
    "read_undelivered",
]

#: The SINGLE TOP-LEVEL rail. Every agent's traffic lands here.
#:
#: NOT taken from ``$SCITEX_AGENT_CONTAINER_STATE_DB``, and that is the whole
#: point: sac sets that var per-agent to a PRIVATE SHARD (e.g.
#: ``/state/scitex-cards/state.db``) which has the ``channel_events`` table and
#: zero rows in it. Honouring it would rebuild, as a default, the exact dead
#: query this module's positive control exists to catch.
DEFAULT_RAIL = "~/.scitex/agent-container/runtime/state.db"

#: Explicit override for deployments where ``$HOME`` is NOT the sac owner's
#: home — notably inside a container, where ``~`` is the container user
#: (``/home/agent``) while the rail is bind-mounted at the owner's real path.
#: Set this once in the systemd unit / cron line that runs the cadence check.
ENV_RAIL = "SCITEX_CARDS_RAIL_DB"

#: How much of a message body to carry back. Enough to recognise the message
#: and resend it without asking the peer what they said.
PREVIEW_CHARS = 80


class Control(str, Enum):
    """Whether the positive control proved the rail worth filtering.

    Three-valued because "no rows matched" and "nothing could be read" are
    different answers, and collapsing them is how a dead query gets reported
    as an all-clear.
    """

    #: The rail was read and holds rows — filtered results below mean something.
    PASSED = "passed"
    #: ``channel_events`` exists but is EMPTY — the per-agent-shard trap.
    EMPTY = "empty"
    #: Missing file, missing table, or an unreadable database.
    UNREADABLE = "unreadable"


class Signal(str, Enum):
    """A three-valued answer to "is there undelivered traffic?"."""

    #: Undelivered rows exist and are named in the matching tuple.
    FOUND = "found"
    #: A REAL zero, backed by a passing positive control.
    CLEAR = "clear"
    #: The control failed. No claim is made — this is NOT a pass.
    CANNOT_TELL = "cannot_tell"


@dataclass(frozen=True, slots=True)
class UndeliveredMessage:
    """One rail row that carries no delivery stamp.

    Named rather than counted: a count tells you something broke, this tells
    you what to resend and to whom.
    """

    id: int
    #: The OTHER end — the sender for inbound, the recipient for outbound.
    peer: str
    #: Float epoch, exactly as the rail stores it.
    ts: float
    #: First :data:`PREVIEW_CHARS` characters of the body.
    preview: str

    @property
    def when(self) -> str:
        """The timestamp as a human reads it."""
        return format_epoch(self.ts)

    def describe(self) -> str:
        """One line naming the message."""
        return f"id={self.id} peer={self.peer} {self.when} {self.preview!r}"


@dataclass(frozen=True, slots=True)
class UndeliveredReport:
    """The declared answer shape. Never a bare count, never a shifting dict.

    A caller reads the same field names on every path, including the failure
    paths, so it never has to guess which key exists this time.
    """

    agent: str
    rail: str
    #: The positive control — checked FIRST, and three-valued.
    control_passed: Control
    #: Total rows on the rail. The control's evidence.
    total_rows: int
    inbound_undelivered: Signal
    outbound_undelivered: Signal
    #: Rows addressed TO this agent that never arrived.
    inbound: tuple[UndeliveredMessage, ...] = ()
    #: Rows sent BY this agent that never landed.
    outbound: tuple[UndeliveredMessage, ...] = ()
    #: Why the control failed, when it did. Empty when it passed.
    detail: str = ""

    def __post_init__(self) -> None:
        """Enforce the invariants that make a zero here trustworthy."""
        if self.total_rows < 0:
            raise ValueError(f"total_rows cannot be negative: {self.total_rows}")

        signals = (self.inbound_undelivered, self.outbound_undelivered)
        if self.control_passed is not Control.PASSED:
            # THE invariant this whole module exists for: a failed control can
            # never produce an all-clear.
            if any(s is not Signal.CANNOT_TELL for s in signals):
                raise ValueError(
                    f"control_passed={self.control_passed.value} but a signal "
                    f"claims an answer ({signals[0].value}/{signals[1].value}); "
                    "a failed control means CANNOT_TELL, never a pass"
                )
            if self.inbound or self.outbound:
                raise ValueError(
                    "rows reported despite a failed positive control — "
                    "nothing was trustworthily read"
                )
            if not self.detail:
                raise ValueError("a failed control must say why")
        else:
            if any(s is Signal.CANNOT_TELL for s in signals):
                raise ValueError(
                    "control passed, so neither signal may be CANNOT_TELL"
                )
            if self.total_rows == 0:
                raise ValueError("control cannot pass on an empty rail")

        for name, signal, rows in (
            ("inbound", self.inbound_undelivered, self.inbound),
            ("outbound", self.outbound_undelivered, self.outbound),
        ):
            if signal is Signal.FOUND and not rows:
                raise ValueError(f"{name}_undelivered=FOUND but no rows named")
            if signal is Signal.CLEAR and rows:
                raise ValueError(f"{name}_undelivered=CLEAR but {len(rows)} rows given")

    @property
    def cannot_tell(self) -> bool:
        """Whether this report declines to answer. Distinct from "found zero"."""
        return self.control_passed is not Control.PASSED

    @property
    def actionable(self) -> bool:
        """Whether any message needs resending."""
        return bool(self.inbound or self.outbound)

    def describe(self) -> str:
        """Multi-line human rendering, control first."""
        lines = [f"rail: {self.rail}", f"agent: {self.agent}"]
        if self.cannot_tell:
            lines.append(
                f"POSITIVE CONTROL: {self.control_passed.value.upper()} — {self.detail}"
            )
            lines.append("CANNOT TELL — this is NOT an all-clear.")
            return "\n".join(lines)

        lines.append(f"POSITIVE CONTROL: PASSED ({self.total_rows} rows on the rail)")
        for label, signal, rows in (
            ("INBOUND  undelivered", self.inbound_undelivered, self.inbound),
            ("OUTBOUND undelivered", self.outbound_undelivered, self.outbound),
        ):
            lines.append(f"{label}: {len(rows)}")
            lines.extend(f"    {row.describe()}" for row in rows)
            if signal is Signal.CLEAR:
                lines.append("    (a real zero — control-backed)")
        return "\n".join(lines)


def default_rail_path() -> Path:
    """Where to look when the caller named no rail.

    ``$SCITEX_CARDS_RAIL_DB`` wins when set, and when it is set it is the
    ONLY candidate — an explicit override that silently fell back to a
    default would be indistinguishable from the override working, which is
    the failure mode this whole module exists to prevent. Otherwise the
    documented top-level path under ``$HOME``.
    """
    override = os.environ.get(ENV_RAIL, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(DEFAULT_RAIL).expanduser()


def format_epoch(ts: float) -> str:
    """Render the rail's FLOAT EPOCH ``ts`` as a UTC timestamp.

    Says so loudly when it cannot, rather than inventing a plausible date.
    """
    try:
        moment = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return f"<unrenderable ts {ts!r}>"
    return moment.strftime("%Y-%m-%d %H:%M:%SZ")


_UNDELIVERED_SQL = """
    SELECT id, {peer}, ts, COALESCE(substr(content, 1, {n}), '')
      FROM channel_events
     WHERE {mine} = ? AND delivered_at IS NULL
     ORDER BY id
"""


def _fetch(conn: sqlite3.Connection, mine: str, peer: str, agent: str) -> tuple:
    sql = _UNDELIVERED_SQL.format(peer=peer, mine=mine, n=PREVIEW_CHARS)
    return tuple(
        UndeliveredMessage(id=int(row[0]), peer=str(row[1] or ""), ts=row[2], preview=str(row[3]))
        for row in conn.execute(sql, (agent,))
    )


def _cannot_tell(
    agent: str, rail: str, control: Control, detail: str, total: int = 0
) -> UndeliveredReport:
    return UndeliveredReport(
        agent=agent,
        rail=rail,
        control_passed=control,
        total_rows=total,
        inbound_undelivered=Signal.CANNOT_TELL,
        outbound_undelivered=Signal.CANNOT_TELL,
        detail=detail,
    )


def read_undelivered(agent: str, rail: "str | Path | None" = None) -> UndeliveredReport:
    """Report undelivered rail traffic for ``agent``. Read-only.

    Runs the positive control FIRST and refuses to filter a rail it could not
    prove is populated — the failure mode is CANNOT_TELL, never a quiet pass.
    """
    path = Path(rail).expanduser() if rail is not None else default_rail_path()
    shown = str(path)

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return _cannot_tell(
            agent,
            shown,
            Control.UNREADABLE,
            f"cannot open rail: {exc} — pass --rail, or set ${ENV_RAIL} when "
            "$HOME is not the sac owner's home (e.g. inside a container)",
        )

    try:
        # POSITIVE CONTROL — before any filter, always.
        try:
            total = int(conn.execute("SELECT count(*) FROM channel_events").fetchone()[0])
        except sqlite3.Error as exc:
            return _cannot_tell(agent, shown, Control.UNREADABLE, f"cannot read channel_events: {exc}")

        if total == 0:
            return _cannot_tell(
                agent,
                shown,
                Control.EMPTY,
                "channel_events holds 0 rows — a filtered zero here would prove "
                "nothing (the per-agent shards look exactly like this)",
            )

        try:
            inbound = _fetch(conn, mine="target", peer="source", agent=agent)
            outbound = _fetch(conn, mine="source", peer="target", agent=agent)
        except sqlite3.Error as exc:
            return _cannot_tell(agent, shown, Control.UNREADABLE, f"query failed: {exc}", total)
    finally:
        conn.close()

    return UndeliveredReport(
        agent=agent,
        rail=shown,
        control_passed=Control.PASSED,
        total_rows=total,
        inbound_undelivered=Signal.FOUND if inbound else Signal.CLEAR,
        outbound_undelivered=Signal.FOUND if outbound else Signal.CLEAR,
        inbound=inbound,
        outbound=outbound,
    )


# EOF
