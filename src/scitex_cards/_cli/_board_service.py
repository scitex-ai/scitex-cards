#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards board install-service`` — make the board resident on a host.

WHY IT LIVES ON ``board`` AND NOT ON ``gui``
--------------------------------------------
``gui`` is the ECOSYSTEM-STANDARD verb group — ``open`` / ``serve`` / ``status``
/ ``stop`` — shared with figrecipe, scitex-writer and scitex-scholar so the
operator's ``scitex_start_gui_servers`` script can drive every SciTeX tool the
same way. ``tests/test_cli_gui.py`` pins that group at exactly those four verbs,
and it is right to: a shared convention that each package extends privately
stops being shared. I put this verb there first and that test caught it.

``board`` is this package's OWN noun for the same lifecycle and is explicitly
not deprecated, so a scitex-cards-specific operation belongs on it. That also
matches the existing precedent one directory over: the notify daemon's unit
installer is ``notifyd install-unit``, on the daemon's own noun.

Its own module because ``_board.py`` is at 495 lines against a 512-line cap —
the command is attached by :func:`register`, which takes the group as an
argument rather than importing it, so there is no import cycle.
"""

from __future__ import annotations

import click

from .._systemd_gui import DEFAULT_HOST, DEFAULT_PORT
from ._compat import spec_command_kwargs


def register(board_group: click.Group) -> None:
    """Attach ``install-service`` to the ``board`` noun group."""
    board_group.add_command(board_install_service_cmd, "install-service")


@click.command(
    "install-service",
    **spec_command_kwargs(
        summary="Make the board RESIDENT on this host (systemd user unit).",
        description=(
            "Writes a systemd USER unit that serves the board on loopback and "
            "restarts it whenever it goes away, then prints the exact "
            "`systemctl --user` commands to enable it. OPERATOR-GATED: this "
            "NEVER runs systemctl itself.\n\n"
            "Run it on EVERY host. The board is served per-host on 127.0.0.1 "
            "and the card data travels between hosts via the per-host "
            "Postgres sync — one shared board reachable over the VPN would be "
            "a single point of failure and would vanish with the network.\n\n"
            "Check `loginctl show-user $USER -p Linger` first: without "
            "lingering, a user unit only starts at interactive login, so on a "
            "headless host the board would still be absent after a reboot."
        ),
        examples=(
            ("{prog} board install-service", "Write the unit for 127.0.0.1:8051."),
            ("{prog} board install-service --force", "Overwrite an existing unit."),
        ),
    ),
)
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing unit file (default: leave it untouched).",
)
def board_install_service_cmd(port: int, host: str, force: bool) -> None:
    """Write the systemd user unit (operator-gated) and print the enable commands.

    Example:
      $ scitex-cards board install-service
    """
    from .._systemd_gui import install_gui_unit
    from .._systemd_unit import ExecStartUnresolved

    try:
        result = install_gui_unit(host=host, port=port, force=force)
    except ExecStartUnresolved as exc:
        # Fail LOUDLY: a unit with an unresolvable ExecStart installs fine and
        # then dies at 203/EXEC the moment the operator enables it — a board
        # that is silently absent, which is the exact fault this verb exists
        # to end.
        raise click.ClickException(str(exc)) from exc
    if result["written"]:
        click.echo(f"# wrote systemd user unit: {result['path']}")
        click.echo(f"#   ExecStart={result['exec_start']}")
    elif result["existed"]:
        click.echo(
            f"# unit already exists (NOT overwritten): {result['path']}\n"
            "#   pass --force to overwrite."
        )
    click.echo("#")
    click.echo("# To enable + start it, the OPERATOR runs (this tool does NOT):")
    click.echo(f"#   {result['enable_commands']}")


__all__ = ["board_install_service_cmd", "register"]

# EOF
