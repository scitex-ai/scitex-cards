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
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: The systemd user-unit filename. Package-prefixed so the operator can grep
#: ``systemctl --user list-units 'scitex-todo*'`` to see every owned unit.
UNIT_NAME = "scitex-todo-notifyd.service"

#: The unit-file TEMPLATE. ``Type=simple`` (long-running foreground process),
#: ``Restart=on-failure`` (a crashed daemon = silent comm-loss; bring it back),
#: ``WantedBy=default.target`` (start on user login) — mirrors the dashboard
#: unit's conventions. ``ExecStart`` is the standalone foreground entry point.
UNIT_TEMPLATE = """\
[Unit]
Description=scitex-todo notify daemon — standalone notification-delivery loop
Documentation=https://github.com/scitex-ai/scitex-cards
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
TimeoutStartSec=30

[Install]
WantedBy=default.target
"""

#: The console script the unit must launch, and the verb it runs.
CONSOLE_SCRIPT = "scitex-todo"
EXEC_VERB = "notifyd"


class ExecStartUnresolved(RuntimeError):
    """Raised when the ``scitex-todo`` console script cannot be located.

    We FAIL LOUDLY here on purpose: writing a unit with a bare (or guessed)
    command produces a service that fails at ``203/EXEC`` the moment the
    operator enables it — a silent-at-install, broken-at-runtime defect. An
    error at generation time is strictly better.
    """


def console_script_path() -> Path:
    """Absolute path to the ``scitex-todo`` console script.

    Prefers the RUNNING interpreter's own ``bin/`` (so a venv install writes a
    unit pointing at that venv — the common and correct case), then falls back
    to ``$PATH``. Raises :class:`ExecStartUnresolved` if neither resolves.
    """
    candidate = Path(sys.executable).parent / CONSOLE_SCRIPT
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which(CONSOLE_SCRIPT)
    if found:
        path = Path(found)
        if not path.is_absolute():
            path = path.resolve()
        return path
    raise ExecStartUnresolved(
        f"cannot locate the `{CONSOLE_SCRIPT}` console script — looked in the "
        f"running interpreter's bin dir ({Path(sys.executable).parent}) and on "
        "$PATH. systemd does NOT use your login PATH, so the unit needs an "
        "ABSOLUTE ExecStart and one cannot be derived here. Install scitex-todo "
        "into the environment you are generating the unit from (e.g. "
        f"`{sys.executable} -m pip install -U scitex-todo`), or pass an explicit "
        "exec_start."
    )


def resolve_exec_start() -> str:
    """The ``ExecStart=`` body: an ABSOLUTE console-script path + the verb."""
    return f"{console_script_path()} {EXEC_VERB}"


def user_unit_dir() -> Path:
    """Resolve ``~/.config/systemd/user`` honouring ``$XDG_CONFIG_HOME``.

    Tests point ``$XDG_CONFIG_HOME`` at a tmp dir to assert the helper writes
    there (and does NOT shell out to systemctl).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def unit_path() -> Path:
    """Full path to the installed unit file."""
    return user_unit_dir() / UNIT_NAME


def render_unit(exec_start: str | None = None) -> str:
    """Render the unit-file text. ``None`` ⇒ resolve an ABSOLUTE ExecStart now."""
    return UNIT_TEMPLATE.format(exec_start=exec_start or resolve_exec_start())


def enable_commands() -> str:
    """The exact systemctl commands the OPERATOR runs to enable + start it."""
    return (
        "systemctl --user daemon-reload && "
        f"systemctl --user enable --now {UNIT_NAME}"
    )


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
        prints the commands for the operator to run by hand.
    """
    path = unit_path()
    existed = path.exists()
    if existed and not force:
        return {
            "path": str(path),
            "written": False,
            "existed": True,
            "exec_start": None,
            "enable_commands": enable_commands(),
        }
    # Resolve BEFORE touching the filesystem: an unresolvable ExecStart must
    # abort the install, not leave a half-written unit behind.
    resolved = exec_start or resolve_exec_start()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit(resolved), encoding="utf-8")
    return {
        "path": str(path),
        "written": True,
        "existed": existed,
        "exec_start": resolved,
        "enable_commands": enable_commands(),
    }


__all__ = [
    "CONSOLE_SCRIPT",
    "EXEC_VERB",
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
