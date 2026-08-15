#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Health check: is the board GUI RESIDENT on this host, and is it up?

WHY (2026-08-14). The operator opened the board and got a bare
``ERR_CONNECTION_REFUSED``. Nothing was listening on :8051, and nothing
anywhere said anything was wrong — the store was resident, the agent that
maintains the GUI was alive, its heartbeat was green. The one surface he
actually reads was absent and every instrument pointed elsewhere. He had been
told the board was the fleet's primary channel that same night.

A browser cannot be told anything by a server that is not running, so the
loudness has to come from the side that IS running. This check is that side.

DECLARATION FIRST, THEN LIVENESS
--------------------------------
"Nothing is listening" is not by itself a fault — most hosts in this fleet run
no board, and a check that fails on all of them would be turned off within a
day, which is how a real alarm gets lost. Absence is only a fault against a
DECLARATION, and the declaration is the installed unit
(:mod:`scitex_cards._systemd_gui`). So this check reads two facts and reports
four distinct states, using the three-valued ``ok`` the health contract
already defines:

======================  ===========  ==========================================
unit installed?         listening?   verdict
======================  ===========  ==========================================
yes                     yes          ``True``  — resident and up
yes                     no           ``False`` — THE 2026-08-14 FAULT
no                      yes          ``True``  — up, but hand-started: it dies
                                     at the next reboot and takes the board
                                     with it. Passes, and says so in the hint.
no                      no           ``None``  — UNKNOWN. This host never
                                     promised a board; we cannot call that
                                     broken, and we must not call it fine.
======================  ===========  ==========================================

An UNKNOWN does not fail the run but IS named in the report summary, so a host
that quietly serves nothing can never read as a silent pass.

WHAT "LISTENING" PROVES, EXACTLY
--------------------------------
A TCP connect, and nothing more: something holds the port. It does not prove
the board renders. That is deliberate — the failure this exists to catch is
the refused connection, and the honest measurement of a refused connection is
a connect attempt. The detail line says "listening", never "serving", so the
report cannot be read as a claim it did not make.
"""

from __future__ import annotations

import socket
from typing import Any

from ._health_severity import DELIVERY  # noqa: F401  (documents this check's class)
from ._systemd_gui import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    GUI_UNIT_NAME,
    gui_unit_path,
    installed_gui_bind,
)

#: Seconds to wait for the TCP connect. The board is on loopback, so a healthy
#: connect is sub-millisecond; anything approaching this is already a fault.
CONNECT_TIMEOUT_S = 1.0

#: The one command that makes a host's board resident.
_INSTALL_HINT = (
    "make the board resident on this host: `scitex-cards gui install-service` "
    f"then `systemctl --user daemon-reload && systemctl --user enable --now {GUI_UNIT_NAME}`"
)


def _is_listening(host: str, port: int, timeout: float = CONNECT_TIMEOUT_S) -> bool:
    """True when a TCP connect to ``host:port`` succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        # ConnectionRefused / timeout / unreachable are all one answer here:
        # the operator's browser would have shown him the same thing.
        return False


def check_gui_resident(
    *,
    host: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    """Report whether this host serves the board, and whether it promised to.

    Returns the standard ``{ok, detail, hint}`` record; ``ok`` is three-valued
    (see the module docstring for the four states).

    THE BIND IS READ FROM THE INSTALLED UNIT, not assumed. A host installed on
    ``--port 9051`` would otherwise get a confident report about :8051 — a
    manufactured outage on a healthy host, which costs exactly as much trust as
    the missed one this check exists to catch. Explicit ``host``/``port``
    arguments win over both (hermetic tests, one-off probes); with no unit and
    no argument we fall back to the package defaults.
    """
    bind = installed_gui_bind()
    declared = bind is not None
    host = host or (bind[0] if bind else DEFAULT_HOST)
    port = port or (bind[1] if bind else DEFAULT_PORT)
    listening = _is_listening(host, port)
    where = f"http://{host}:{port}"

    if declared and listening:
        return {
            "ok": True,
            "detail": f"board resident ({GUI_UNIT_NAME}) and listening on {where}",
            "hint": None,
        }
    if declared and not listening:
        return {
            "ok": False,
            "detail": (
                f"{GUI_UNIT_NAME} is installed at {gui_unit_path()} "
                f"but NOTHING is listening on {where} — a browser opening the "
                "board gets ERR_CONNECTION_REFUSED"
            ),
            "hint": (
                f"`systemctl --user status {GUI_UNIT_NAME}` for why it is down, "
                f"then `systemctl --user restart {GUI_UNIT_NAME}`; if the unit was "
                "never enabled, `systemctl --user enable --now "
                f"{GUI_UNIT_NAME}`"
            ),
        }
    if listening:
        return {
            "ok": True,
            "detail": (
                f"something is listening on {where}, but no {GUI_UNIT_NAME} is "
                "installed — this board was started by hand and will not survive "
                "a reboot, a logout, or the process being killed"
            ),
            "hint": _INSTALL_HINT,
        }
    return {
        "ok": None,
        "detail": (
            f"nothing listening on {where} and no {GUI_UNIT_NAME} installed — "
            "this host has not declared that it serves a board, so whether that "
            "is correct cannot be measured from here"
        ),
        "hint": (
            f"if this host should serve the board, {_INSTALL_HINT}; if it should "
            "not, nothing to do — the board is served per-host on loopback and "
            "the card data travels via the per-host Postgres sync"
        ),
    }


__all__ = ["CONNECT_TIMEOUT_S", "check_gui_resident"]

# EOF
