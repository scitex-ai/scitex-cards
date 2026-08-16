#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic systemd USER-unit rendering + operator-gated install.

ONE implementation of "make this scitex-cards process resident on this host",
parameterised by a :class:`UnitSpec`. Two specs use it today: the notify daemon
(:mod:`scitex_cards._delivery._systemd`) and the board GUI
(:mod:`scitex_cards._systemd_gui`).

WHY IT IS SHARED RATHER THAN COPIED
-----------------------------------
The delivery daemon's installer already carried the hard-won part — resolving
an ABSOLUTE ``ExecStart`` at generation time and RAISING rather than writing a
unit guaranteed to die at ``status=203/EXEC`` (systemd does not run units
through a login shell and does not inherit ``$PATH``, and the console script
lives in a venv). When the GUI needed the same treatment, copying that routine
would have meant the next unit inherits whichever copy its author happened to
read. ``_cli/_gui.py`` already records this exact lesson about the
unconfigured-store refusal: *a refusal implemented once per door is a refusal
that will be missing from the next door somebody adds.*

WHAT STAYS OPERATOR-GATED
-------------------------
This module WRITES the unit file to ``~/.config/systemd/user/`` and PRINTS the
``systemctl --user`` commands. It NEVER invokes systemctl, enables, or starts
anything — enabling a service on a host is a human decision. That gate is on
INSTALL, once per host; it is not a gate on every boot, which is the whole
point of having a unit at all.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: Where the project lives — the ``Documentation=`` line of every unit.
DOCUMENTATION_URL = "https://github.com/scitex-ai/scitex-cards"


class ExecStartUnresolved(RuntimeError):
    """Raised when a spec's console script cannot be located.

    We FAIL LOUDLY here on purpose: writing a unit with a bare (or guessed)
    command produces a service that fails at ``203/EXEC`` the moment the
    operator enables it — a silent-at-install, broken-at-runtime defect. An
    error at generation time is strictly better.
    """


@dataclass(frozen=True)
class UnitSpec:
    """Everything that differs between one scitex-cards unit and another.

    Attributes
    ----------
    unit_name : str
        The ``.service`` filename. Package-prefixed so the operator can
        ``systemctl --user list-units 'scitex-*'`` and see every owned unit.
    description : str
        The unit's ``Description=``. Written for someone reading
        ``systemctl --user status`` at 3am, not for a changelog.
    console_script : str
        The console script the unit launches (e.g. ``scitex-cards``). Resolved
        to an ABSOLUTE path at generation time — never written bare.
    args : tuple[str, ...]
        The arguments appended to the console script, in order.
    restart : str
        systemd's ``Restart=`` policy. ``on-failure`` for a process whose clean
        exit means "I was asked to stop"; ``always`` for one whose absence is
        itself the fault, regardless of how it went away.
    restart_sec : int
        Seconds between restarts.
    timeout_start_sec : int
        Seconds systemd waits for startup before calling it failed.
    """

    unit_name: str
    description: str
    console_script: str
    args: tuple[str, ...] = ()
    restart: str = "on-failure"
    restart_sec: int = 5
    timeout_start_sec: int = 30


def console_script_path(console_script: str) -> Path:
    """Absolute path to ``console_script``.

    Prefers the RUNNING interpreter's own ``bin/`` (so a venv install writes a
    unit pointing at that venv — the common and correct case), then falls back
    to ``$PATH``. Raises :class:`ExecStartUnresolved` if neither resolves.
    """
    candidate = Path(sys.executable).parent / console_script
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which(console_script)
    if found:
        path = Path(found)
        if not path.is_absolute():
            path = path.resolve()
        return path
    raise ExecStartUnresolved(
        f"cannot locate the `{console_script}` console script — looked in the "
        f"running interpreter's bin dir ({Path(sys.executable).parent}) and on "
        "$PATH. systemd does NOT use your login PATH, so the unit needs an "
        "ABSOLUTE ExecStart and one cannot be derived here. Install the package "
        "into the environment you are generating the unit from (e.g. "
        f"`{sys.executable} -m pip install -U scitex-cards`), or pass an explicit "
        "exec_start."
    )


def resolve_exec_start(spec: UnitSpec) -> str:
    """The ``ExecStart=`` body: an ABSOLUTE console-script path + the args."""
    return " ".join((str(console_script_path(spec.console_script)), *spec.args))


def unit_template(spec: UnitSpec) -> str:
    """The unit text with ``{exec_start}`` still a placeholder.

    Kept separate from :func:`render_unit` so a caller can show the shape of a
    unit without resolving an executable that may not exist yet.
    """
    return (
        "[Unit]\n"
        f"Description={spec.description}\n"
        f"Documentation={DOCUMENTATION_URL}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart={exec_start}\n"
        f"Restart={spec.restart}\n"
        f"RestartSec={spec.restart_sec}\n"
        f"TimeoutStartSec={spec.timeout_start_sec}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_unit(spec: UnitSpec, exec_start: str | None = None) -> str:
    """Render the unit-file text. ``None`` ⇒ resolve an ABSOLUTE ExecStart now."""
    return unit_template(spec).format(
        exec_start=exec_start or resolve_exec_start(spec)
    )


def user_unit_dir() -> Path:
    """Resolve ``~/.config/systemd/user`` honouring ``$XDG_CONFIG_HOME``.

    Tests point ``$XDG_CONFIG_HOME`` at a tmp dir to assert the helper writes
    there (and does NOT shell out to systemctl).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def unit_path(spec: UnitSpec) -> Path:
    """Full path to the installed unit file."""
    return user_unit_dir() / spec.unit_name


def enable_commands(spec: UnitSpec) -> str:
    """The exact systemctl commands the OPERATOR runs to enable + start it."""
    return (
        "systemctl --user daemon-reload && "
        f"systemctl --user enable --now {spec.unit_name}"
    )


def install_unit(
    spec: UnitSpec,
    *,
    exec_start: str | None = None,
    force: bool = False,
) -> dict:
    """Write ``spec``'s unit file to the user-unit dir. Does NOT run systemctl.

    Parameters
    ----------
    spec : UnitSpec
        The unit to install.
    exec_start : str | None
        The ``ExecStart=`` line body. ``None`` (default) resolves the ABSOLUTE
        console-script path via :func:`resolve_exec_start`, which raises
        :class:`ExecStartUnresolved` rather than write an unstartable unit.
    force : bool
        Overwrite an existing unit file. Without it, an existing file is left
        untouched and the result reports ``written=False`` — a hand-edited unit
        is somebody's deliberate work and is not silently reverted.

    Returns
    -------
    dict
        ``{path, written, existed, exec_start, enable_commands}`` — caller
        prints the commands for the operator to run by hand.
    """
    path = unit_path(spec)
    existed = path.exists()
    if existed and not force:
        return {
            "path": str(path),
            "written": False,
            "existed": True,
            "exec_start": None,
            "enable_commands": enable_commands(spec),
        }
    # Resolve BEFORE touching the filesystem: an unresolvable ExecStart must
    # abort the install, not leave a half-written unit behind.
    resolved = exec_start or resolve_exec_start(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit(spec, resolved), encoding="utf-8")
    return {
        "path": str(path),
        "written": True,
        "existed": existed,
        "exec_start": resolved,
        "enable_commands": enable_commands(spec),
    }


__all__ = [
    "DOCUMENTATION_URL",
    "ExecStartUnresolved",
    "UnitSpec",
    "console_script_path",
    "enable_commands",
    "install_unit",
    "render_unit",
    "resolve_exec_start",
    "unit_path",
    "unit_template",
    "user_unit_dir",
]

# EOF
