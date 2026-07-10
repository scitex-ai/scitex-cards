#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-todo board`` — dependency-graph board lifecycle.

Lifecycle verbs: start / stop / restart / status (operator TG12949/12950/
12951 via lead a2a `b5726672`). Pre-this-change `scitex-todo board` was
a bare NOUN that launched directly — CLI noun-verb violation, and the
operator had no clean way to restart after a card/source change ("port
already in use" trap).

Pidfile at ``~/.scitex/todo/board.pid`` so stop/restart/status are
reliable across terminals. Bare ``scitex-todo board`` (no subcommand)
hard-errors with a redirect (operator directive TG 13316: noun-verb CLI
convention, no bare-noun back-compat).

Extracted verbatim from ``_main.py`` to keep that module under the
512-line cap; behaviour is unchanged. Attached to the root group via
:func:`register`, matching the sibling ``_notifyd`` / ``_deliver`` modules.
"""

from __future__ import annotations

import click

# Process/pidfile helpers live in the sibling ``_board_proc`` module
# (extracted to keep this file under the 512-line cap). Re-imported here
# so existing call sites + tests keep importing them from ``_board``.
from scitex_todo._cli._board_proc import (
    BOARD_PIDFILE,  # noqa: F401  (public re-export)
    _board_cmdline_is_board,  # noqa: F401  (public re-export)
    _board_pid_alive,
    _board_pid_on_port,  # noqa: F401  (public re-export)
    _board_pidfile,
    _board_read_pid,
    _board_resolve_pid,
    _board_write_pid,
)


def register(main: click.Group) -> None:
    """Attach the ``board`` noun group to the root group."""
    main.add_command(board_group)


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

    # Only the parent/watcher owns the pidfile. The reloader child
    # (RUN_MAIN=true) must not write it (it would clobber the watcher pid
    # that `board stop` targets) nor unlink it on its own restart.
    _is_reloader_child = _os.environ.get("RUN_MAIN") == "true"
    if not _is_reloader_child:
        _board_write_pid(_os.getpid())
    try:
        call_command(*args)
    finally:
        if not _is_reloader_child:
            pf = _board_pidfile()
            try:
                if pf.exists():
                    pf.unlink()
            except OSError:
                pass


@click.group(
    "board",
    invoke_without_command=True,
    help=(
        "Manage the dependency-graph board (start/stop/restart/status).\n\n"
        "The ``board`` noun REQUIRES an explicit verb — bare "
        "``scitex-todo board`` hard-errors with a redirect (operator "
        "directive TG 13316: noun-verb CLI convention, no bare-noun "
        "back-compat).\n\n"
        "Examples:\n"
        "  scitex-todo board start --port 8051\n"
        "  scitex-todo board restart\n"
        "  scitex-todo board status\n"
        "  scitex-todo board stop"
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
    help=(
        "Launch the board server (blocking, foreground). Writes a "
        "pidfile at ~/.scitex/todo/board.pid so other terminals can "
        "`board stop` / `board restart`. Requires the web extra: "
        "pip install scitex-todo[web]\n\n"
        "Example:\n"
        "  $ scitex-todo board start --port 8051"
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled, "
    "or $SCITEX_TODO_TASKS_YAML_SHARED).",
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
    import os as _os

    # Django's autoreloader re-execs this exact command in a child process
    # (it sets RUN_MAIN=true). That child is the process that actually
    # serves; the PARENT/watcher already holds the pidfile. Without this
    # guard the child re-enters here, sees the parent's pidfile, and aborts
    # with "already running" — which is exactly why hot-reload was disabled
    # (hardcoded --noreload) instead of fixed. Skip the double-start guard
    # in the reloader child; let it fall through and serve.
    if _os.environ.get("RUN_MAIN") != "true":
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
    help=(
        "Stop the running board (SIGTERM). Uses the pidfile when valid; "
        "if the pidfile pid is dead/missing, falls back to the (verified) "
        "board serving on --port and cleans up the stale pidfile.\n\n"
        "Example:\n"
        "  $ scitex-todo board stop"
    ),
)
@click.option(
    "--port",
    type=int,
    default=8051,
    show_default=True,
    help="Port to fall back to when the pidfile is stale/missing.",
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
def board_stop_cmd(
    port: int, timeout: float, dry_run: bool, assume_yes: bool
) -> None:
    """SIGTERM the board: pidfile if valid, else the port-found board.

    When the pidfile pid is dead/missing but a verified board is serving
    on ``--port`` (an untracked process holding the port — the
    stale-pidfile incident), we fall back to that PID and clean up the
    stale pidfile. NOTE (OS limit, not a bug): we can only signal a
    process owned by the SAME user — a cross-user kill is denied by the
    kernel and surfaces as a clear error below.

    Example:
      $ scitex-todo board stop
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    if dry_run:
        pid, untracked = _board_resolve_pid(port)
        if pid is None:
            click.echo("# dry-run: board is not running (no pidfile / stale).")
        else:
            note = " (untracked pidfile; found on port)" if untracked else ""
            click.echo(
                f"# dry-run: would SIGTERM pid {pid}{note} "
                f"(timeout {timeout}s, then SIGKILL).",
            )
        return
    import os as _os
    import signal as _signal
    import time as _time

    pid, untracked = _board_resolve_pid(port)
    if pid is None:
        click.echo("# board is not running (no pidfile / stale).")
        return
    if untracked:
        click.echo(
            f"# pidfile stale/missing; found live board on port {port} "
            f"(pid {pid}); stopping it.",
        )
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
    help=(
        "Stop the running board (if any) + start a fresh one. The shape "
        "the operator + lead need to reload after a card/source change."
        "\n\nExample:\n  $ scitex-todo board restart"
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
        pid, untracked = _board_resolve_pid(port)
        if pid is None:
            prefix = "not running"
        elif untracked:
            prefix = "running (untracked pidfile)"
        else:
            prefix = "running"
        click.echo(
            f"# dry-run: would stop (currently {prefix}) then start "
            f"on port {port}, "
            f"tasks={tasks_path or '<default-resolution>'}, "
            f"no-browser={bool(no_browser)}",
        )
        return
    # `stop` is a no-op if nothing's running — that's fine. Pass --port so
    # the stop step can fall back to a port-found board when the pidfile
    # is stale (the stale-pidfile incident this hardening targets).
    ctx.invoke(
        board_stop_cmd, port=port, timeout=5.0, dry_run=False, assume_yes=True
    )
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
    help=(
        "Print whether the board is running + its pid + the pidfile path. "
        "If the pidfile is stale/missing but a verified board is serving "
        "on --port, reports it as running with an 'untracked pidfile' note."
        "\n\nExample:\n  $ scitex-todo board status\n"
        "  $ scitex-todo board status --json"
    ),
)
@click.option(
    "--port",
    type=int,
    default=8051,
    show_default=True,
    help="Port to fall back to when the pidfile is stale/missing.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON object (machine-readable). Required by SciTeX "
    "§2 audit on read verbs.",
)
def board_status_cmd(port: int, as_json: bool) -> None:
    """One-line status: pidfile first, port fallback if it's stale.

    Example:
      $ scitex-todo board status
      $ scitex-todo board status --json
    """
    import json as _json

    pid, untracked = _board_resolve_pid(port)
    pf = _board_pidfile()
    running = pid is not None
    if as_json:
        click.echo(
            _json.dumps(
                {
                    "running": running,
                    "pid": pid,
                    "pidfile": str(pf),
                    "untracked": untracked,
                }
            )
        )
        return
    if not running:
        click.echo(f"# board is NOT running (pidfile: {pf})")
        return
    if untracked:
        click.echo(
            f"# board is running (pid {pid}, port {port}; untracked "
            f"pidfile — pidfile {pf} was stale/missing)"
        )
        return
    click.echo(f"# board is running (pid {pid}, pidfile: {pf})")


# EOF
