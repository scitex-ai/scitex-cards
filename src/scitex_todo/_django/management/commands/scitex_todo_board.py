#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Management command to run the scitex-todo board standalone.

Usage:
    python -m django scitex_todo_board [--tasks PATH] [--addrport HOST:PORT]
    python -m django scitex_todo_board [--tasks PATH] [--port 8051]

Typically invoked via the ``scitex-todo board`` CLI verb.
"""

import os
import sys
import webbrowser

from django.core.management.base import BaseCommand

_LOCALHOST_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_NO_AUTH_WARNING = (
    "\n"
    "⚠️  SCITEX_TODO_BASIC_AUTH is not set — the board is being served with "
    "NO authentication.\n"
    "    Anyone who can reach the bind address can read AND modify your task "
    "store.\n"
    "    Set SCITEX_TODO_BASIC_AUTH=user:pass to enable auth, OR put it "
    "behind a zero-trust proxy\n"
    "    (Cloudflare Access / similar).\n"
)


def _apply_tasks_env(tasks: str) -> None:
    """Export ``SCITEX_TODO_TASKS=tasks`` when the operator passed
    ``--tasks PATH``.

    Lifted out of ``Command.handle`` so the env-precedence behaviour can be
    unit-tested without starting a Django ``runserver``. Empty string (the
    argparse default for a missing ``--tasks``) is a no-op so an inherited
    ``SCITEX_TODO_TASKS`` keeps winning. A non-empty value overrides any
    inherited env (``os.environ[...]`` instead of ``setdefault``) to match
    the resolver's documented precedence: an explicit ``--tasks`` wins
    over ``$SCITEX_TODO_TASKS`` wins over the project store.
    """
    if tasks:
        os.environ["SCITEX_TODO_TASKS"] = tasks


def _resolve_bind(addrport: str, port: int) -> tuple[str, int]:
    """Resolve the effective ``(host, port)`` bind.

    ``--addrport`` is the canonical Django ``runserver`` form (``HOST:PORT``)
    and wins when provided so the operator can bind ``0.0.0.0:8051`` for an
    upstream Cloudflare Tunnel. ``--port`` is the legacy single-value form
    and stays the default for back-compat: when ``--addrport`` is absent,
    we bind ``127.0.0.1:<port>`` exactly as the pre-Cloudflare flow did.
    """
    if not addrport:
        return "127.0.0.1", port
    if ":" not in addrport:
        # Bare port — treat as "<port>" on localhost; same shape as Django's
        # runserver accepts a bare port.
        try:
            return "127.0.0.1", int(addrport)
        except ValueError:
            return "127.0.0.1", port
    host, _, raw_port = addrport.rpartition(":")
    try:
        return host or "127.0.0.1", int(raw_port)
    except ValueError:
        return host or "127.0.0.1", port


def _is_localhost_bind(host: str) -> bool:
    """Return True iff ``host`` binds only to the loopback interface."""
    return host in _LOCALHOST_BIND_HOSTS


def _warn_if_no_auth(host: str, stderr) -> bool:
    """Emit the LOUD no-auth warning to ``stderr`` and return True iff fired.

    Fires only when ``SCITEX_TODO_BASIC_AUTH`` is unset/empty AND the bind is
    non-localhost — i.e. the actual dangerous configuration. Localhost dev
    stays silent so the warning carries signal. We do NOT block startup
    (operator may legitimately be putting Cloudflare Access in front
    INSTEAD of Basic Auth) — this is a nudge, not a gate.
    """
    auth_env = os.environ.get("SCITEX_TODO_BASIC_AUTH", "")
    if auth_env:
        return False
    if _is_localhost_bind(host):
        return False
    stderr.write(_NO_AUTH_WARNING)
    try:
        stderr.flush()
    except Exception:
        pass
    return True


class Command(BaseCommand):
    help = "Run the scitex-todo dependency-graph board as a standalone server"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tasks",
            dest="tasks",
            default="",
            help="Path to tasks.yaml (default: project -> user -> bundled example).",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8051,
            help="Server port (default: 8051). Ignored when --addrport is set.",
        )
        parser.add_argument(
            "--addrport",
            dest="addrport",
            default="",
            help=(
                "HOST:PORT bind, Django-runserver style (e.g. 0.0.0.0:8051). "
                "Use this when fronting the board with Cloudflare Tunnel / "
                "similar zero-trust proxy. REQUIRES SCITEX_TODO_BASIC_AUTH "
                "(or external auth) — a non-localhost bind without auth is "
                "an open task store; a warning is emitted to stderr."
            ),
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Don't open a browser automatically.",
        )

    def handle(self, *args, **options):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_todo._django.settings")

        tasks = options["tasks"]
        port = options["port"]
        addrport = options.get("addrport", "")
        # When the operator passes ``--tasks PATH``, export
        # ``SCITEX_TODO_TASKS=PATH`` so the in-process Django views (and any
        # subprocess they fork) actually resolve to that store. Without this
        # the server fell through the project-store -> user-store -> bundled
        # fallback chain (``resolve_store_path``) and silently ignored
        # ``--tasks`` whenever a ``.scitex/todo/tasks.yaml`` existed at the
        # git-root -- the ``?store=`` query-string we add below only hints
        # the browser, it never reaches the resolver. The helper uses
        # ``os.environ[...]`` (NOT ``setdefault``) so an explicit CLI value
        # wins over any stale inherited env var, matching the resolver
        # precedence documented in ``scitex-todo --help`` ("an explicit
        # --tasks path, then $SCITEX_TODO_TASKS, then the project store, ...").
        _apply_tasks_env(tasks)

        bind_host, bind_port = _resolve_bind(addrport, port)

        # Loud warning when the operator binds publicly without auth. Does
        # NOT block — see _warn_if_no_auth's docstring.
        _warn_if_no_auth(bind_host, sys.stderr)

        # Browser URL always points at the loopback form so an operator
        # binding 0.0.0.0:<port> still opens a usable tab; remote clients
        # consume the public URL (Cloudflare hostname) themselves.
        url = f"http://127.0.0.1:{bind_port}/"
        if tasks:
            url += f"?store={tasks}"

        if not options["no_browser"]:
            import threading

            threading.Timer(1.0, webbrowser.open, args=[url]).start()

        self.stdout.write(f"SciTeX Todo Board running at {url}")
        self.stdout.write("Press Ctrl+C to stop")

        from django.core.management import call_command

        call_command(
            "runserver", f"{bind_host}:{bind_port}", "--noreload"
        )


# EOF
