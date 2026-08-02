#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards gui`` — the ecosystem-standard GUI verbs.

Verbs: ``open`` / ``serve`` / ``status`` / ``stop``, matching figrecipe,
scitex-writer and scitex-scholar. The operator's ``scitex_start_gui_servers``
script loops ``<pkg> gui serve &`` over every SciTeX tool; scitex-cards was the
odd one out — it exposed the board only as ``scitex-cards board``, so his loop
died on ``Error: No such command 'gui'`` and NOTHING ever bound :8051. The
board was never broken; the verb simply did not exist.

This group is a THIN FRONT over the existing ``board`` lifecycle
(:mod:`scitex_cards._cli._board`), not a reimplementation. ``board`` already
owns a pidfile, a stale-pidfile fallback that re-finds a board by port, and a
SIGTERM-then-SIGKILL escalation — strictly more than the generic
pid/state-file pattern the other tools hand-roll. Duplicating it would mean
two lifecycles racing for one pidfile.

The ``board`` verbs are NOT deprecated: they stay the canonical noun for the
dependency-graph board. ``gui`` is the cross-tool alias the operator's script
speaks.
"""

from __future__ import annotations

import click

from ._board import (
    _board_run_server,
    board_status_cmd,
    board_stop_cmd,
)
from ._board_proc import _board_read_pid
from ._compat import spec_command_kwargs, spec_group_kwargs

#: The board's long-standing default. The operator's startup script and the
#: `board` verbs already agree on it; `gui` must not invent a second one.
DEFAULT_PORT = 8051
DEFAULT_HOST = "127.0.0.1"


def register(main: click.Group) -> None:
    """Attach the ``gui`` noun group to the root group."""
    main.add_command(gui_group)


@click.group(
    "gui",
    invoke_without_command=True,
    **spec_group_kwargs(
        summary="Serve the board GUI (open/serve/status/stop).",
        description=(
            "The ecosystem-standard GUI verb group, shared with figrecipe / "
            "scitex-writer / scitex-scholar so one startup script can bring "
            "every SciTeX GUI up the same way. A thin front over the `board` "
            "lifecycle — `board` remains the canonical noun and is not "
            "deprecated."
        ),
        command_categories=(("Core", ("open", "serve", "status", "stop")),),
    ),
)
@click.pass_context
def gui_group(ctx: click.Context) -> None:
    """The ``gui`` noun group — REQUIRES an explicit verb.

    Mirrors the ``board`` group's noun-verb contract (operator directive TG
    13316): a bare noun hard-errors with a redirect rather than guessing.
    """
    if ctx.invoked_subcommand is not None:
        return
    click.echo(
        "ERROR: `scitex-cards gui` needs a verb. Use:\n"
        "  scitex-cards gui serve [--port N] [--host H]  # foreground/blocking\n"
        "  scitex-cards gui open [SURFACE]               # serve + open a browser\n"
        "  scitex-cards gui status [--json]\n"
        "  scitex-cards gui stop",
        err=True,
    )
    ctx.exit(2)


@gui_group.command(
    "serve",
    **spec_command_kwargs(
        summary="Serve the board GUI in the foreground (blocking).",
        description=(
            "The verb the operator's startup script calls. Blocking and "
            "headless by design: it does NOT open a browser (use `gui open` "
            "for that), so it is safe to background with `&` in a loop over "
            "every SciTeX tool. Requires the web extra: "
            "pip install scitex-cards[web]."
        ),
        examples=(
            ("{prog} gui serve", "Serve on 127.0.0.1:8051 (blocking)."),
            ("{prog} gui serve --port 9000", "Serve on another port."),
        ),
    ),
)
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned launch without starting the server.",
)
def gui_serve_cmd(port: int, host: str, dry_run: bool) -> None:
    """Foreground-blocking serve, no browser.

    Example:
      $ scitex-cards gui serve --port 8051
    """
    if dry_run:
        click.echo(f"# dry-run: would serve the board on {host}:{port}, no browser")
        return
    existing = _board_read_pid()
    if existing is not None:
        raise click.ClickException(
            f"the board is already running (pid {existing}). Use "
            "`scitex-cards gui stop` or `scitex-cards gui status`."
        )
    _board_run_server(None, port, no_browser=True, host=host)


@gui_group.command(
    "open",
    **spec_command_kwargs(
        summary="Serve the GUI and open it in a browser.",
        description=(
            "Auto-serves, then opens SURFACE in the default browser. If a "
            "board is ALREADY running we do not start a second one — we just "
            "open the browser at the running instance (starting a rival "
            "server would only lose the port race and confuse the pidfile)."
        ),
        examples=(
            ("{prog} gui open", "Open the board."),
            ("{prog} gui open timeline", "Open the timeline surface."),
        ),
    ),
)
@click.argument("surface", required=False, default="")
@click.option("--port", type=int, default=DEFAULT_PORT, show_default=True)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
def gui_open_cmd(surface: str, port: int, host: str) -> None:
    """Serve + open a browser. Reuses a running board if there is one.

    Example:
      $ scitex-cards gui open
      $ scitex-cards gui open timeline
    """
    url = f"http://{host}:{port}/{surface.lstrip('/')}"

    running = _board_read_pid()
    if running is not None:
        # Already up — just point the browser at it. Do NOT race the port.
        import webbrowser

        click.echo(f"# board already running (pid {running}); opening {url}")
        webbrowser.open(url)
        return

    if surface:
        # The server's own browser-open lands on "/", so drive the browser
        # ourselves once the server is listening.
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        _board_run_server(None, port, no_browser=True, host=host)
        return

    _board_run_server(None, port, no_browser=False, host=host)


# `status` and `stop` are the SAME commands the `board` group exposes, not
# copies of them: one pidfile, one implementation, one set of behaviours to
# keep correct. Re-registering the objects under the `gui` name is the whole
# aliasing story.
gui_group.add_command(board_status_cmd, "status")
gui_group.add_command(board_stop_cmd, "stop")


# EOF
