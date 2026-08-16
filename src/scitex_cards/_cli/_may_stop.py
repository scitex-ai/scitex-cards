#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards may-stop`` — the never-stop detector's CLI face.

Exit code IS the contract (sac's Stop hook keys on it):

* ``0``  — nothing runnable; the agent MAY stop.
* ``2``  — runnable work exists; STDERR carries the numbered hint list
  (sac's idle-at-prompt re-drive injects it as the resume prompt), STDOUT
  carries the JSON verdict.

Registered from the thin ``_cli/__init__`` root like ``serve``/``hub``
(``_main`` is at its line budget).
"""

from __future__ import annotations

import json
import sys

import click

from .._may_stop import may_stop
from ._compat import spec_command_kwargs


@click.command(
    "may-stop",
    **spec_command_kwargs(
        summary="Exit 0 iff the agent has NO runnable work; else exit 2 + hints.",
        description=(
            "The never-stop detector's CLI face. The EXIT CODE is the "
            "contract: 0 = may stop, 2 = runnable work exists, with the "
            "numbered hint list on stderr and the JSON verdict on stdout."
        ),
        examples=(
            ("{prog} may-stop --agent scitex-cards", ""),
        ),
    ),
)
@click.option(
    "--agent",
    default=None,
    help=(
        "Agent to check. Defaults to $SCITEX_CARDS_AGENT_ID; there is NO "
        "fallback — an unresolvable identity fails loudly rather than "
        "guessing (see _store._default_agent)."
    ),
)
def may_stop_cmd(agent):
    """Exit 0 iff the agent has NO runnable work; else exit 2 + hints.

    \b
    Example:
      $ scitex-cards may-stop --agent scitex-cards
    """
    from .._store import _default_agent

    verdict = may_stop(_default_agent(agent), None)
    click.echo(json.dumps(verdict))
    if not verdict["runnable"]:
        return
    lines = [
        f"{i}. {item['card_id']} — {item['reason']} — {item['next_action']}"
        for i, item in enumerate(verdict["items"], start=1)
    ]
    idle = verdict.get("idle_seconds")
    header = (
        f"may-stop: {len(verdict['items'])} runnable item(s) for "
        f"{verdict['agent']}"
        + (f" (idle {idle}s)" if idle is not None else "")
        + " — an agent does not stop while the board holds work:"
    )
    print("\n".join([header, *lines]), file=sys.stderr)
    sys.exit(2)


def register(main) -> None:
    main.add_command(may_stop_cmd)


__all__ = ["may_stop_cmd", "register"]

# EOF
