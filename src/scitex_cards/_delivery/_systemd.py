#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""systemd user-unit TEMPLATE + install helper for the notify daemon.

Host-enablement is OPERATOR-GATED: this module only WRITES the unit file to
``~/.config/systemd/user/`` and PRINTS the exact ``systemctl --user`` commands
for the operator to run. It NEVER invokes ``systemctl`` / enables / starts the
service itself — that is a deliberate human gate (mirrors the dashboard unit
convention: ``Type=simple`` + ``Restart=on-failure`` + ``WantedBy=default.
target``).

The unit is fully STANDALONE — its ``ExecStart`` is the ``scitex-todo notifyd``
entry point (the foreground run), with no external federation dependency.

ExecStart MUST BE ABSOLUTE
--------------------------
systemd does not run the unit through a login shell and does not inherit the
user's ``PATH``. A BARE ``ExecStart=scitex-todo notifyd`` therefore dies at
``status=203/EXEC`` whenever the console script lives in a venv (it does:
``~/.env-3.11/bin/scitex-todo``) — i.e. the shipped template could not start at
all, and the operator had to hand-patch the path before the service would run.
:func:`resolve_exec_start` resolves the real path at GENERATION time (the
running interpreter's own ``bin/`` first, then ``$PATH``), and RAISES rather
than writing a unit that is guaranteed not to start.

THE MACHINERY NOW LIVES IN :mod:`scitex_cards._systemd_unit`
------------------------------------------------------------
This module is the notify daemon's SPEC plus the zero-argument functions its
callers and tests already use; the rendering and the write are shared with the
board GUI's unit (:mod:`scitex_cards._systemd_gui`). The hard-won part is the
absolute-ExecStart resolution above, and it was the only copy of it in the
package — a second unit copying it would have inherited whichever version its
author happened to read. Every public name here is unchanged and behaves
identically; this is a de-duplication, not a re-design.
"""

from __future__ import annotations

from pathlib import Path

from .._systemd_unit import (  # noqa: F401  (re-export: import surface)
    ExecStartUnresolved,
    UnitSpec,
    console_script_path as _console_script_path,
    enable_commands as _enable_commands,
    install_unit as _install_unit,
    render_unit as _render_unit,
    resolve_exec_start as _resolve_exec_start,
    unit_path as _unit_path,
    unit_template,
    user_unit_dir,
)

#: The systemd user-unit filename. Package-prefixed so the operator can grep
#: ``systemctl --user list-units 'scitex-todo*'`` to see every owned unit.
UNIT_NAME = "scitex-todo-notifyd.service"

#: The console script the unit must launch, and the verb it runs.
CONSOLE_SCRIPT = "scitex-todo"
EXEC_VERB = "notifyd"

#: The notify daemon's unit. ``Type=simple`` (long-running foreground process),
#: ``Restart=on-failure`` (a crashed daemon = silent comm-loss; bring it back),
#: ``WantedBy=default.target`` (start on user login) — mirrors the dashboard
#: unit's conventions. ``ExecStart`` is the standalone foreground entry point.
#:
#: ``on-failure`` and not ``always``, unlike the board GUI's unit: a clean exit
#: here means the daemon was ASKED to stop, whereas an absent board is a fault
#: however it went away.
NOTIFYD_SPEC = UnitSpec(
    unit_name=UNIT_NAME,
    description="scitex-todo notify daemon — standalone notification-delivery loop",
    console_script=CONSOLE_SCRIPT,
    args=(EXEC_VERB,),
    restart="on-failure",
)

#: The unit-file TEMPLATE, with ``{exec_start}`` still a placeholder.
UNIT_TEMPLATE = unit_template(NOTIFYD_SPEC)


def console_script_path() -> Path:
    """Absolute path to the ``scitex-todo`` console script.

    Prefers the RUNNING interpreter's own ``bin/`` (so a venv install writes a
    unit pointing at that venv — the common and correct case), then falls back
    to ``$PATH``. Raises :class:`ExecStartUnresolved` if neither resolves.
    """
    return _console_script_path(CONSOLE_SCRIPT)


def resolve_exec_start() -> str:
    """The ``ExecStart=`` body: an ABSOLUTE console-script path + the verb."""
    return _resolve_exec_start(NOTIFYD_SPEC)


def unit_path() -> Path:
    """Full path to the installed unit file."""
    return _unit_path(NOTIFYD_SPEC)


def render_unit(exec_start: str | None = None) -> str:
    """Render the unit-file text. ``None`` ⇒ resolve an ABSOLUTE ExecStart now."""
    return _render_unit(NOTIFYD_SPEC, exec_start)


def enable_commands() -> str:
    """The exact systemctl commands the OPERATOR runs to enable + start it."""
    return _enable_commands(NOTIFYD_SPEC)


def install_unit(
    *,
    exec_start: str | None = None,
    force: bool = False,
) -> dict:
    """Write the unit file to the user-unit dir. Does NOT run systemctl.

    Parameters
    ----------
    exec_start : str | None
        The ``ExecStart=`` line body. ``None`` (default) resolves the ABSOLUTE
        console-script path via :func:`resolve_exec_start`, which raises
        :class:`ExecStartUnresolved` rather than write an unstartable unit.
    force : bool
        Overwrite an existing unit file. Without it, an existing file is left
        untouched and the result reports ``written=False``.

    Returns
    -------
    dict
        ``{path, written, existed, exec_start, enable_commands}`` — caller
        prints the commands for the operator to run.
    """
    return _install_unit(NOTIFYD_SPEC, exec_start=exec_start, force=force)


__all__ = [
    "CONSOLE_SCRIPT",
    "EXEC_VERB",
    "NOTIFYD_SPEC",
    "UNIT_NAME",
    "UNIT_TEMPLATE",
    "ExecStartUnresolved",
    "console_script_path",
    "enable_commands",
    "install_unit",
    "render_unit",
    "resolve_exec_start",
    "unit_path",
    "user_unit_dir",
]

# EOF
