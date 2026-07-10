#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``board <verb>`` — the dependency-graph board's lifecycle group.

Lifecycle verbs: start / stop / restart / status (operator TG12949/12950/
12951 via lead a2a `b5726672`). Pre-this-change `scitex-todo board` was
a bare NOUN that launched directly — CLI noun-verb violation, and the
operator had no clean way to restart after a card/source change ("port
already in use" trap).

Pidfile at ``~/.scitex/todo/board.pid`` so stop/restart/status are
reliable across terminals. Bare ``scitex-todo board`` (no subcommand)
hard-errors with a redirect (operator directive TG 13316: noun-verb CLI
convention, no bare-noun back-compat).

Extracted from ``_cli/_main.py`` (which had grown past the 512-line
convention) into its own module — same split shape every other
verb-group in this package already uses (``_admin.py``, ``_write.py``,
``_close.py``, …). Pure extraction: no behavior change. ``register()``
is called from ``_main.py``.
"""

from __future__ import annotations

from pathlib import Path as _Path

import click

from scitex_dev.ecosystem import CliHelp, Example, SpecCommand, SpecGroup

BOARD_PIDFILE = _Path.home() / ".scitex" / "todo" / "board.pid"


def _board_pidfile() -> _Path:
    """Return the pidfile path (function so tests can override via env)."""
    import os as _os

    override = _os.environ.get("SCITEX_TODO_BOARD_PIDFILE")
    if override:
        return _Path(override)
    return BOARD_PIDFILE


def _board_pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` is the POSIX 'is this PID up?' probe."""
    import os as _os

    try:
        _os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def _board_read_pid() -> int | None:
    """Read the pidfile; return None when absent/unreadable/dead."""
    pf = _board_pidfile()
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _board_pid_alive(pid):
        # Stale pidfile from a crashed process — clean it up.
        try:
            pf.unlink()
        except OSError:
            pass
        return None
    return pid


def _board_write_pid(pid: int) -> None:
    """Write the pidfile, creating parent dirs as needed."""
    pf = _board_pidfile()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid))


def _board_run_server(
    tasks_path: str | None,
    port: int,
    no_browser: bool,
) -> None:
    """Foreground-blocking server start (the historical board_cmd body).

    Writes the pidfile BEFORE handing off to Django's runserver loop and
    removes it on exit (clean shutdown via Ctrl-C, OR exception). Other
    terminals can `board stop` against the pidfile to SIGTERM us.
    """
    import os as _os

    try:
        import django  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "The board needs the web extra. Install it with:\n"
            "  pip install scitex-todo[web]"
        ) from None

    _os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "scitex_todo._django.settings",
    )
    import django as _dj

    _dj.setup()
    from django.core.management import call_command

    args = ["scitex_todo_board", "--port", str(port)]
    if tasks_path:
        args += ["--tasks", tasks_path]
    if no_browser:
        args += ["--no-browser"]

    _board_write_pid(_os.getpid())
    try:
        call_command(*args)
    finally:
        pf = _board_pidfile()
        try:
            if pf.exists():
                pf.unlink()
        except OSError:
            pass


@click.group(
    "board",
    cls=SpecGroup,
    command_categories=[("Core", ["start", "stop", "restart", "status"])],
    invoke_without_command=True,
    help_spec=CliHelp(
        summary="Manage the dependency-graph board (start/stop/restart/status).",
        description=(
            "The `board` noun REQUIRES an explicit verb — bare "
            "`{prog} board` hard-errors with a redirect (operator "
            "directive TG 13316: noun-verb CLI convention, no bare-noun "
            "back-compat). Writes a pidfile at ~/.scitex/todo/board.pid "
            "so `stop` / `restart` / `status` work reliably from any "
            "terminal.",
        ),
        examples=(
            Example("{prog} board start --port 8051", "Launch the board server."),
            Example("{prog} board restart", "Stop (if running) then start fresh."),
            Example("{prog} board status", "Report whether the board is running."),
            Example("{prog} board stop", "Stop the running board."),
        ),
        see_also=("{prog} board start --help — the web extra it requires",),
    ),
)
@click.pass_context
def board_group(ctx: click.Context) -> None:
    """The ``board`` noun group — REQUIRES an explicit verb.

    Click runs the group function FIRST, then dispatches the subcommand
    if one is named. When no subcommand is named, we HARD-ERROR with a
    redirect message + exit 2 (Click's standard usage-error code) so
    every existing call site is forced to migrate.

    Operator-direct directive TG 13316 (relayed by lead a2a
    ``c36b0d1e``): the previous deprecation-warn-and-forward path
    (PR #139, v0.7.6) hid the violation from audit tools — replacing it
    with a hard error makes the noun-verb convention enforceable across
    the fleet.

    In-tree call sites updated in this same PR: ``_jobs_provider.py``.
    Host-side systemd unit ``scitex-todo.dashboard.service`` ExecStart
    also needs the same migration — flagged for lead's host-side pass.
    """
    if ctx.invoked_subcommand is not None:
        # User typed `scitex-todo board start/stop/...` — let Click route
        # to the subcommand.
        return
    # Bare `scitex-todo board` — HARD ERROR.
    click.echo(
        "ERROR: `scitex-todo board` (no verb) is no longer supported.\n"
        "Operator directive TG 13316 — noun-verb CLI convention. Use:\n"
        "  scitex-todo board start [--port N] [--no-browser]\n"
        "  scitex-todo board stop\n"
        "  scitex-todo board restart\n"
        "  scitex-todo board status",
        err=True,
    )
    ctx.exit(2)


@board_group.command(
    "start",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Launch the board server (blocking, foreground).",
        description=(
            "Writes a pidfile at ~/.scitex/todo/board.pid so other "
            "terminals can `board stop` / `board restart`. Requires the "
            "web extra: pip install scitex-todo[web]. Refuses to start "
            "if a board is already running (per the pidfile) so two "
            "servers never fight over the same port.",
        ),
        examples=(Example("{prog} board start --port 8051", "Serve on port 8051."),),
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "--port",
    type=int,
    default=8051,
    show_default=True,
    help="Server port.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Don't open a browser automatically.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned launch (port + tasks + browser flag) "
    "without starting the server. Required by SciTeX §2 audit on "
    "mutating verbs.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the interactive confirmation (no-op today; `start` is "
    "non-interactive). Accepted per SciTeX §2 audit on mutating verbs.",
)
def board_start_cmd(
    tasks_path: str | None,
    port: int,
    no_browser: bool,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    """Foreground start. Pidfile written; removed on clean shutdown.

    Example:
      $ scitex-todo board start --port 8051
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    # Guard rail: refuse to start if another board is already up so we
    # don't fight over the pidfile or the port.
    existing = _board_read_pid()
    if existing is not None:
        raise click.ClickException(
            f"board is already running (pid {existing}). Use "
            "`scitex-todo board stop` or `restart`."
        )
    if dry_run:
        click.echo(
            f"# dry-run: would start board on port {port}, "
            f"tasks={tasks_path or '<default-resolution>'}, "
            f"no-browser={bool(no_browser)} "
            f"(pidfile: {_board_pidfile()})",
        )
        return
    _board_run_server(tasks_path, port, no_browser)


@board_group.command(
    "stop",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Stop the running board via the pidfile (SIGTERM).",
        description=(
            "Sends SIGTERM and waits up to --timeout seconds for a "
            "graceful exit before escalating to SIGKILL. No-op (prints "
            "a status line) when no board is running.",
        ),
        examples=(Example("{prog} board stop", "Stop the running board."),),
    ),
)
@click.option(
    "--timeout",
    type=float,
    default=5.0,
    show_default=True,
    help="Seconds to wait for graceful exit before SIGKILL.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned action without sending SIGTERM. Required "
    "by SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the interactive confirmation (no-op today; `stop` is "
    "non-interactive). Accepted per SciTeX §2 audit on mutating verbs.",
)
def board_stop_cmd(timeout: float, dry_run: bool, assume_yes: bool) -> None:
    """Read the pidfile, SIGTERM, wait, escalate to SIGKILL if needed.

    Example:
      $ scitex-todo board stop
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    if dry_run:
        pid = _board_read_pid()
        if pid is None:
            click.echo("# dry-run: board is not running (no pidfile / stale).")
        else:
            click.echo(
                f"# dry-run: would SIGTERM pid {pid} "
                f"(timeout {timeout}s, then SIGKILL).",
            )
        return
    import os as _os
    import signal as _signal
    import time as _time

    pid = _board_read_pid()
    if pid is None:
        click.echo("# board is not running (no pidfile / stale).")
        return
    try:
        _os.kill(pid, _signal.SIGTERM)
    except OSError as e:
        raise click.ClickException(f"could not SIGTERM pid {pid}: {e}")
    # Poll for graceful exit.
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if not _board_pid_alive(pid):
            click.echo(f"# stopped board (pid {pid}).")
            # Clean up pidfile (the foreground process's finally
            # also tries to remove it; this is idempotent).
            pf = _board_pidfile()
            try:
                if pf.exists():
                    pf.unlink()
            except OSError:
                pass
            return
        _time.sleep(0.1)
    # Still alive — escalate.
    try:
        _os.kill(pid, _signal.SIGKILL)
        click.echo(
            f"# board did not exit in {timeout}s; sent SIGKILL to pid {pid}.",
            err=True,
        )
    except OSError as e:
        raise click.ClickException(f"could not SIGKILL pid {pid}: {e}")
    pf = _board_pidfile()
    try:
        if pf.exists():
            pf.unlink()
    except OSError:
        pass


@board_group.command(
    "restart",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Stop the running board (if any) then start a fresh one.",
        description=(
            "The shape the operator + lead need to reload after a "
            "card/source change. Stop is a no-op if nothing is running; "
            "start then follows the same pidfile contract as `board "
            "start`.",
        ),
        examples=(Example("{prog} board restart", "Reload the board."),),
    ),
)
@click.option("--tasks", "tasks_path", default=None, help="Path to tasks.yaml.")
@click.option("--port", type=int, default=8051, show_default=True, help="Server port.")
@click.option("--no-browser", is_flag=True, help="Don't open a browser automatically.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned stop+start without acting. Required by "
    "SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the interactive confirmation (no-op; `restart` is "
    "non-interactive). Accepted per SciTeX §2 audit on mutating verbs.",
)
@click.pass_context
def board_restart_cmd(
    ctx: click.Context,
    tasks_path: str | None,
    port: int,
    no_browser: bool,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    """Stop then start. Both go through the same pidfile contract.

    Example:
      $ scitex-todo board restart
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    if dry_run:
        pid = _board_read_pid()
        prefix = "running" if pid else "not running"
        click.echo(
            f"# dry-run: would stop (currently {prefix}) then start "
            f"on port {port}, "
            f"tasks={tasks_path or '<default-resolution>'}, "
            f"no-browser={bool(no_browser)}",
        )
        return
    # `stop` is a no-op if nothing's running — that's fine.
    ctx.invoke(board_stop_cmd, timeout=5.0, dry_run=False, assume_yes=True)
    ctx.invoke(
        board_start_cmd,
        tasks_path=tasks_path,
        port=port,
        no_browser=no_browser,
        dry_run=False,
        assume_yes=True,
    )


@board_group.command(
    "status",
    cls=SpecCommand,
    help_spec=CliHelp(
        summary="Print whether the board is running, its pid, and the pidfile path.",
        examples=(
            Example("{prog} board status", "Human-readable one-liner."),
            Example("{prog} board status --json", "Structured JSON."),
        ),
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON object (machine-readable). Required by SciTeX "
    "§2 audit on read verbs.",
)
def board_status_cmd(as_json: bool) -> None:
    """One-line status read off the pidfile.

    Example:
      $ scitex-todo board status
      $ scitex-todo board status --json
    """
    import json as _json

    pid = _board_read_pid()
    pf = _board_pidfile()
    running = pid is not None
    if as_json:
        click.echo(
            _json.dumps(
                {
                    "running": running,
                    "pid": pid,
                    "pidfile": str(pf),
                }
            )
        )
        return
    if not running:
        click.echo(f"# board is NOT running (pidfile: {pf})")
        return
    click.echo(f"# board is running (pid {pid}, pidfile: {pf})")


def register(main: click.Group) -> None:
    """Attach the ``board`` noun group to the root CLI."""
    main.add_command(board_group)


# EOF
