#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Can our channel pushes actually REACH the session?

The client surfaces a ``notifications/claude/channel`` push ONLY from a server
named on its own launch line::

    claude ... --dangerously-load-development-channels server:<name>

and it matches that against the key the server is registered under in the MCP
config. A name the client does not know is not an error anywhere: the push is
discarded on arrival, silently.

That silence is DESTRUCTIVE here, not merely late. A channel notification is
fire-and-forget — MCP hands the server no delivery receipt to wait on — so the
drain marks a record ``seen`` whether or not the push was accepted. A name
mismatch therefore does not delay delivery, it DESTROYS it: the inbox empties,
the session hears nothing, and no check anywhere goes red.

MEASURED 2026-07-24, not hypothetical. The scitex-todo -> scitex-cards rename
re-registered this MCP server as ``scitex-cards`` while agent launch lines still
allowlisted the pre-rename ``scitex-todo``. The whole fleet went deaf to the
board. A self-test notification enqueued at 06:36:24 was consumed and marked
seen within six seconds and never appeared in any session — and it had been that
way since the rename.

A rename is precisely the event this check exists to survive: a published name
changes in one place, not the other, and the failure mode is silence. Compare
``channel_capable`` (can we push at all?) and ``channel_drain`` (is the inbox
being consumed?) — both were GREEN throughout the outage. Neither asks the only
question that matters: does the far end accept what we send?
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The launch flag that allowlists a server's channel pushes.
CHANNEL_FLAG = "--dangerously-load-development-channels"

#: Console-script names that ARE us. Both the current name and the pre-rename one
#: count: during a migration the shim is still us, and a check that only knew the
#: new name would be blind on exactly the agents left behind.
OUR_CLI_NAMES = frozenset({"scitex-cards", "scitex-todo"})

#: Ancestry walk bound. The launcher is a few hops up at most; the cap means a
#: malformed /proc chain can never hang a health run.
_MAX_ANCESTRY = 24


def allowlisted_channel_servers(argv: list[str]) -> set[str]:
    """Server names whose channel pushes the client will surface.

    Accepts both the split form (``--flag server:NAME``) and the joined form
    (``--flag=server:NAME``); a launcher may emit either and a check that
    understood only one would report a false mismatch.
    """
    names: set[str] = set()
    for index, arg in enumerate(argv):
        value: str | None = None
        if arg == CHANNEL_FLAG and index + 1 < len(argv):
            value = argv[index + 1]
        elif arg.startswith(CHANNEL_FLAG + "="):
            value = arg.split("=", 1)[1]
        if value and value.startswith("server:"):
            name = value[len("server:") :].strip()
            if name:
                names.add(name)
    return names


def _mcp_config_blobs(argv: list[str]) -> list[dict[str, Any]]:
    """Every MCP config the session was given: ``--mcp-config`` plus ``~/.mcp.json``.

    ``--mcp-config`` is repeatable and takes EITHER a path or inline JSON, so we
    try both readings. Unparseable entries are skipped, never raised: a health
    check must survive a config it cannot read.
    """
    raws: list[str] = []
    for index, arg in enumerate(argv):
        if arg == "--mcp-config" and index + 1 < len(argv):
            raws.append(argv[index + 1])
        elif arg.startswith("--mcp-config="):
            raws.append(arg.split("=", 1)[1])

    blobs: list[dict[str, Any]] = []
    for raw in raws:
        text = raw
        if not raw.lstrip().startswith("{"):
            try:
                text = Path(raw).read_text(encoding="utf-8")
            except OSError:
                continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            blobs.append(parsed)

    try:
        home_cfg = json.loads((Path.home() / ".mcp.json").read_text(encoding="utf-8"))
        if isinstance(home_cfg, dict):
            blobs.append(home_cfg)
    except (OSError, ValueError, TypeError):
        pass
    return blobs


def _runs_our_cli(spec: dict[str, Any]) -> bool:
    """True when this MCP entry actually RUNS our CLI.

    Matched on the PROGRAM TOKEN, never on a substring of the whole command
    line. A plain "is our name in here anywhere" test is worse than useless: the
    sac channel entry runs ``sac mcp channel --name scitex-cards``, so a
    substring test claims sac's server as ours, finds it allowlisted, and
    reports the channel healthy — masking the exact outage this module exists to
    catch. Caught live on 2026-07-24 before this shipped.

    So a token only counts when it is the program being executed:
      * ``-m <module>`` — the module form, ``python -m scitex_cards...``
      * any other token whose PREVIOUS token is a flag is that flag's VALUE
        (``--name scitex-cards`` names somebody else's server) and is skipped
      * otherwise the token's basename must BE one of our console scripts
    """
    import os

    tokens = [str(spec.get("command") or "")]
    args = spec.get("args")
    if isinstance(args, list):
        tokens.extend(str(a) for a in args)

    for index, token in enumerate(tokens):
        previous = tokens[index - 1] if index else ""
        if previous == "-m":
            if token.startswith(("scitex_cards", "scitex_todo")):
                return True
            continue
        if previous.startswith("-"):
            continue
        if os.path.basename(token) in OUR_CLI_NAMES:
            return True
    return False


def registered_server_names(blobs: list[dict[str, Any]]) -> set[str]:
    """Keys under which THIS package's MCP server is registered.

    Identified by the command it runs, never by assuming a name — the name is
    the very thing under test.
    """
    names: set[str] = set()
    for blob in blobs:
        servers = blob.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        for key, spec in servers.items():
            if isinstance(spec, dict) and _runs_our_cli(spec):
                names.add(str(key))
    return names


def _session_launch_argv() -> list[str] | None:
    """The argv of the client process that launched us, or ``None`` if there is none.

    Walks OUR OWN parent chain rather than scanning the process table: on a host
    running many agents, a global scan would happily read a sibling's launch line
    and report on somebody else's session.
    """
    import os

    pid = os.getpid()
    for _ in range(_MAX_ANCESTRY):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        argv = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
        if any(a == CHANNEL_FLAG or a.startswith(CHANNEL_FLAG + "=") for a in argv):
            return argv
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        except OSError:
            return None
        ppid = 0
        for line in status.splitlines():
            if line.startswith("PPid:"):
                try:
                    ppid = int(line.split()[1])
                except (IndexError, ValueError):
                    ppid = 0
                break
        if ppid <= 1:
            return None
        pid = ppid
    return None


def check_channel_reaches_session() -> dict[str, Any]:
    """ok when our registered MCP name is allowlisted for channel pushes.

    Three-valued on purpose. "No launcher to read" (CLI use, tests, a headless
    capsule, a non-Linux host with no ``/proc``) is NOT a failure — the question
    does not apply there — but it is reported in the detail rather than being
    dressed up as a pass, because a check that cannot run must say so.
    """
    argv = _session_launch_argv()
    if argv is None:
        return {
            "ok": True,
            "detail": (
                "not applicable: no Claude launcher with "
                f"{CHANNEL_FLAG} in this process's ancestry "
                "(CLI / headless / non-Linux) — channel reachability unevaluated"
            ),
            "hint": None,
        }

    return evaluate_reachability(
        allowlisted_channel_servers(argv),
        registered_server_names(_mcp_config_blobs(argv)),
    )


def evaluate_reachability(allowed: set[str], registered: set[str]) -> dict[str, Any]:
    """Decide reachability from the two name sets — the pure half, so the real
    outage is reproducible in a test without a live ``/proc`` or MCP session."""
    if not registered:
        return {
            "ok": True,
            "detail": (
                "not applicable: no MCP server entry for this package found in "
                "the session's config(s) — nothing to push from. "
                f"allowlisted: {sorted(allowed) or 'none'}"
            ),
            "hint": None,
        }

    reachable = sorted(registered & allowed)
    if reachable:
        return {
            "ok": True,
            "detail": (
                f"channel pushes are accepted: registered as {reachable[0]!r} "
                f"and allowlisted (allowlist: {sorted(allowed)})"
            ),
            "hint": None,
        }

    missing = sorted(registered)
    return {
        "ok": False,
        "detail": (
            f"channel pushes are DISCARDED: this server is registered as "
            f"{missing} but the session only accepts channel pushes from "
            f"{sorted(allowed) or 'no server at all'}. Every card event and DM "
            "is consumed from the inbox and then dropped on arrival — the "
            "session goes silently deaf to the board."
        ),
        "hint": (
            "add "
            + " ".join(f"{CHANNEL_FLAG} server:{name}" for name in missing)
            + " to this agent's launch line (keep any pre-rename name during a "
            "migration), then RESTART the session — the allowlist is read at "
            "launch, so an edit alone changes nothing."
        ),
    }


__all__ = [
    "CHANNEL_FLAG",
    "OUR_CLI_NAMES",
    "allowlisted_channel_servers",
    "check_channel_reaches_session",
    "evaluate_reachability",
    "registered_server_names",
]

# EOF
