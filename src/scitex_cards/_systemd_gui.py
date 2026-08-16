#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The board GUI as a RESIDENT service — its unit spec and installer.

WHY THIS EXISTS (2026-08-14 incident)
-------------------------------------
The operator opened the board and got a bare ``ERR_CONNECTION_REFUSED``.
Nothing was listening on :8051 — on his laptop or on the host the agent had
been moved to. The board had been serving nowhere for hours, and the only
thing that had ever started it was a human: a startup script he ran by hand.
The card store was resident (``scitex-cards-pg.service``), the agent that
maintains the GUI was alive, its heartbeat was green — and the surface he
actually reads was absent, with no signal anywhere that it should have been
there. Every liveness instrument read green while the thing being measured
was gone.

That is a MISSING DECLARATION, not a missing restart. Until a unit says "this
host serves the board", nothing can notice that it does not: absence is only
detectable against a declaration. So this module ships the declaration.

WHERE IT RUNS — settled by the operator 2026-08-14
--------------------------------------------------
On EVERY host, bound to ``127.0.0.1``. The tempting alternative — serve it
once and reach it over the VPN — was proposed and REJECTED: 「一つの場所を見る
と単一障害点になったり、vpn が切れると見れなくなったりしてしまいます」. What
travels between hosts is the DATA, over each host's :55432 Postgres and its
existing sync; the UI is local everywhere. So a broken VPN or a downed host
costs nobody their board, and this unit NEVER widens the bind.

RESTART=ALWAYS, NOT ON-FAILURE
------------------------------
The notify daemon uses ``on-failure`` because its clean exit means "I was
asked to stop". For the board the opposite holds: its ABSENCE is the fault,
however it went away. A board that exits 0 — killed by an OOM sweep, stopped
by a stale ``gui stop``, ended by a parent terminal closing — leaves the
operator staring at exactly the refused connection this unit exists to
prevent. Stopping it is therefore ``systemctl --user stop``, which is what
"resident service" means.

WHY ExecStart CARRIES ``--force``
---------------------------------
``gui serve`` refuses to start when the board pidfile names a live process,
and that refusal is right for a human at a terminal. For the resident service
it would be a trap: one hand-started board (or one leftover pidfile) and the
unit fails on every restart, forever, which reproduces the outage with extra
steps. ``--force`` is a documented takeover that is a no-op when nothing is
running. The unit is the declared owner of the board on this host.

NO ``Environment=`` LINE, DELIBERATELY
--------------------------------------
systemd does not source the login shell, so a unit that depended on
``$SCITEX_CARDS_DB`` from ``~/.bashrc`` would start, refuse the unconfigured
store, and crash-loop. Verified under a fully stripped environment
(``env -i``) that the store resolves from ``~/.scitex/cards/config.json``
alone, so no environment is baked into the unit — baking a store target into
a unit file would also create a second place where the store is declared, and
the store has exactly one identity.
"""

from __future__ import annotations

from ._systemd_unit import UnitSpec, install_unit, render_unit, unit_path

#: The unit filename. Package-prefixed, so ``systemctl --user list-units
#: 'scitex-cards*'`` shows the store, the peer sync and the board together.
GUI_UNIT_NAME = "scitex-cards-gui.service"

#: The console script the unit launches.
GUI_CONSOLE_SCRIPT = "scitex-cards"

#: THE BOARD'S DEFAULT BIND — one definition, imported by ``_cli/_gui.py``
#: rather than repeated there. Loopback is not a default to be overridden on a
#: whim; see "WHERE IT RUNS" above.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8051

#: Seconds between restarts. Short, because the store this board reads may
#: itself still be coming up at boot: rather than declare a dependency on a
#: unit owned by another package (and absent on hosts with a file-backed
#: store), we let a failed start simply be retried.
RESTART_SEC = 5


def gui_unit_spec(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> UnitSpec:
    """The board GUI's :class:`~scitex_cards._systemd_unit.UnitSpec`.

    ``--host`` and ``--port`` are written EXPLICITLY into ``ExecStart`` rather
    than left to the CLI's defaults: the unit file is where an operator reads
    what this host serves, and a unit that says only ``gui serve`` answers that
    question with "whatever the installed version happens to default to".
    """
    return UnitSpec(
        unit_name=GUI_UNIT_NAME,
        description=(
            "scitex-cards board GUI — the card board on http://"
            f"{host}:{port} (loopback; card data travels via the per-host "
            "Postgres sync, not via this port)"
        ),
        console_script=GUI_CONSOLE_SCRIPT,
        args=("gui", "serve", "--host", host, "--port", str(port), "--force"),
        restart="always",
        restart_sec=RESTART_SEC,
    )


def gui_unit_path():
    """Where the GUI unit file lives (installed or not).

    Takes no bind arguments: there is ONE board unit per host, and its filename
    does not vary with the port it serves. A per-port filename would let a host
    accumulate several units quietly fighting for one pidfile.
    """
    return unit_path(gui_unit_spec())


def render_gui_unit(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    exec_start: str | None = None,
) -> str:
    """Render the GUI unit text, resolving an ABSOLUTE ``ExecStart``."""
    return render_unit(gui_unit_spec(host=host, port=port), exec_start)


def install_gui_unit(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    force: bool = False,
) -> dict:
    """Write the GUI unit to ``~/.config/systemd/user``. Never runs systemctl.

    Returns the standard
    ``{path, written, existed, exec_start, enable_commands}`` result; the
    caller prints ``enable_commands`` for the operator to run.
    """
    return install_unit(gui_unit_spec(host=host, port=port), force=force)


def gui_unit_is_installed() -> bool:
    """True when THIS HOST has declared that it serves the board.

    The health check reads this to tell two very different states apart: a host
    that promised a board and has none (a FAULT) versus a host that never
    promised one (not applicable). Without the declaration those look identical
    from the outside, which is why the 2026-08-14 outage was invisible.
    """
    return gui_unit_path().is_file()


def installed_gui_bind() -> tuple[str, int] | None:
    """The bind the INSTALLED unit declares, or ``None`` when none is installed.

    Read back out of ``ExecStart`` rather than assumed, because the check that
    probes the board must probe the port this host actually serves. Assuming
    the default would mean a host installed on ``--port 9051`` gets a confident
    report about :8051 — a manufactured outage on a healthy host, which costs
    exactly as much trust as the missed one it is meant to catch.

    Falls back to the declared defaults for any field the unit does not name,
    and returns ``None`` (never a guess) when the file is absent or unreadable.
    """
    path = gui_unit_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("ExecStart="):
            continue
        argv = line[len("ExecStart=") :].split()
        host, port = DEFAULT_HOST, DEFAULT_PORT
        for flag, value in zip(argv, argv[1:]):
            if flag == "--host":
                host = value
            elif flag == "--port":
                try:
                    port = int(value)
                except ValueError:
                    # A hand-edited unit with a non-numeric port will not start
                    # either; keep the declared default rather than crash the
                    # health report, whose job is to stay answerable.
                    pass
        return (host, port)
    return None


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "GUI_CONSOLE_SCRIPT",
    "GUI_UNIT_NAME",
    "RESTART_SEC",
    "gui_unit_is_installed",
    "gui_unit_path",
    "gui_unit_spec",
    "install_gui_unit",
    "render_gui_unit",
]

# EOF
