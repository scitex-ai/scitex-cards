#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detect notifications stranded in a backend the rail no longer reads.

THE INCIDENT THIS EXISTS FOR, measured 2026-08-14. The notification rail cut
over from SQLite to PostgreSQL on 2026-08-11. The cutover moved the RAIL and
left the BACKLOG behind:

    cards.db      365 rows, frozen, newest 2026-08-11T07:05:27Z
    of those     149 UNSEEN, and 0 of 149 present in PostgreSQL
    addressed to operator (130), scitex-dev (6), sac-04 (5), and five others
    134 of the 149 were `dm` — messages to people, not card churn

Among them: an answer the operator had asked for and was waiting on, written 35
seconds after he asked, and a retraction of a false outage report from another
agent. He concluded the agent was dead. It was not; its reply was in a file
nothing read any more.

NOTHING DETECTED THIS FOR THREE DAYS. Every call reported success — the writes
succeeded, the reads succeeded, and both were about different databases. It
surfaced only because someone went looking. That is the gap this check closes:
a cutover that leaves a backlog behind must not also be silent.

WHY IT IS A DELIVERY CHECK AND NOT A BLOCKING ONE. The store is fine, the rail
is fine, and cards read and write correctly. What is broken is that some
messages will never arrive. That is exactly the DELIVERY severity: real, worth
waking someone for, and not an outage.

THE ANSWER IS THREE-VALUED. "No stranded backlog" and "I could not look" are
different facts, and collapsing the second into the first is how this defect
stayed invisible in the first place — so an unreadable legacy file reports
ok=None (unknown), never ok=True.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Reported when there is a backlog, so the operator has the one command that
#: shows them rather than a description of the problem.
_HINT = (
    "Notifications were enqueued into a backend the rail no longer reads, so "
    "they can never be delivered. Inspect with: sqlite3 <path> \"select "
    "recipient, count(*) from inbox where seen=0 group by recipient\". Migrate "
    "them through the package's own enqueue path (never raw SQL), keeping a "
    "pre-image of the file first; nothing is deleted."
)


def _legacy_inbox_path(store) -> "Path | None":
    """The SQLite inbox path, or None when this build cannot name one."""
    try:
        from ._inbox_sqlite import inbox_db_path

        return Path(inbox_db_path(store))
    except Exception:  # noqa: BLE001 — a health check must not raise
        return None


def _unseen_in(path: Path) -> "int | None":
    """Unseen rows in a legacy inbox file. None means COULD NOT TELL.

    Opened read-only, so a health check can never mutate the very file it is
    reporting on.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        names = {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'"
        )}
        if "inbox" not in names:
            return 0
        row = conn.execute("select count(*) from inbox where seen=0").fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()


def _recipients_in(path: Path) -> str:
    """A per-recipient breakdown for the detail line, or '' if unavailable."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return ""
    try:
        rows = list(
            conn.execute(
                "select recipient, count(*) as k from inbox where seen=0 "
                "group by recipient order by k desc limit 5"
            )
        )
        return ", ".join(f"{r[0]}:{r[1]}" for r in rows)
    except Exception:  # noqa: BLE001
        return ""
    finally:
        conn.close()


def check_no_stranded_backlog(store=None) -> dict:
    """Is there an undelivered backlog in a backend the rail has left?

    Returns the standard ``{ok, detail, hint}``. ``ok`` is three-valued:
    True (nothing stranded), False (a real backlog), None (could not tell).
    """
    from ._inbox_backend import POSTGRES, backend

    try:
        active = backend()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": None,
            "detail": f"cannot determine the active inbox backend: {exc}",
            "hint": "Resolve the store first; this check depends on it.",
        }

    if active != POSTGRES:
        return {
            "ok": True,
            "detail": f"inbox backend is {active}; no cutover to strand behind",
            "hint": None,
        }

    path = _legacy_inbox_path(store)
    if path is None:
        return {
            "ok": None,
            "detail": "cannot resolve the legacy inbox path to check it",
            "hint": "This build names no SQLite inbox; verify by hand.",
        }
    if not path.exists():
        return {
            "ok": True,
            "detail": f"no legacy inbox file at {path}",
            "hint": None,
        }

    unseen = _unseen_in(path)
    if unseen is None:
        return {
            "ok": None,
            "detail": f"legacy inbox {path} exists but could not be read",
            "hint": (
                "UNKNOWN is not OK: a file that cannot be read may hold "
                "undelivered messages. Check permissions and re-run."
            ),
        }
    if unseen == 0:
        return {
            "ok": True,
            "detail": f"legacy inbox {path} holds no unseen rows",
            "hint": None,
        }

    who = _recipients_in(path)
    return {
        "ok": False,
        "detail": (
            f"{unseen} UNDELIVERED notification(s) stranded in {path}, which "
            f"the rail no longer reads (backend is {active})"
            + (f" — top recipients: {who}" if who else "")
        ),
        "hint": _HINT,
    }


__all__ = ["check_no_stranded_backlog"]

# EOF
