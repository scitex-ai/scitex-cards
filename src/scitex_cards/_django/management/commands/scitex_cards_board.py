#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Management command to run the scitex-todo board standalone.

Usage:
    python -m django scitex_cards_board [--tasks PATH] [--port 8051]

Typically invoked via the ``scitex-todo board`` CLI verb.
"""

import os
import webbrowser

from django.core.management.base import BaseCommand


def _apply_tasks_env(tasks: str) -> None:
    """Export ``SCITEX_CARDS_DB=tasks`` when the operator passed ``--tasks PATH``.

    Lifted out of ``Command.handle`` so the env-precedence behaviour can be
    unit-tested without starting a Django ``runserver``. Empty string (the
    argparse default for a missing ``--tasks``) is a no-op so an inherited
    ``$SCITEX_CARDS_DB`` keeps winning. A non-empty value overrides any inherited
    env (``os.environ[...]`` instead of ``setdefault``) so an explicit ``--tasks``
    database path wins.
    """
    if tasks:
        os.environ["SCITEX_CARDS_DB"] = tasks


class Command(BaseCommand):
    help = "Run the scitex-todo dependency-graph board as a standalone server"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tasks",
            dest="tasks",
            default="",
            help="Path to the store database (default: $SCITEX_CARDS_DB).",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8051,
            help="Server port (default: 8051).",
        )
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="Bind address (default: 127.0.0.1). The board is "
            "UNAUTHENTICATED and serves every agent's cards — binding it "
            "off loopback exposes the whole store, so a wider bind must be "
            "asked for explicitly.",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Don't open a browser automatically.",
        )

    def handle(self, *args, **options):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")

        tasks = options["tasks"]
        port = options["port"]
        # When the operator passes ``--tasks PATH``, export ``SCITEX_CARDS_DB=PATH``
        # so the in-process Django views (and any subprocess they fork) actually
        # resolve to that database. The ``?store=`` query-string we add below only
        # hints the browser, it never reaches the resolver. The helper uses
        # ``os.environ[...]`` (NOT ``setdefault``) so an explicit CLI value wins
        # over any stale inherited env var.
        _apply_tasks_env(tasks)
        host = options.get("host") or "127.0.0.1"
        url = f"http://{host}:{port}/"
        if tasks:
            url += f"?store={tasks}"

        if not options["no_browser"]:
            import threading

            threading.Timer(1.0, webbrowser.open, args=[url]).start()

        self.stdout.write(f"SciTeX Todo Board running at {url}")
        self.stdout.write("Press Ctrl+C to stop")

        from django.core.management import call_command

        call_command("runserver", f"{host}:{port}", "--noreload")


# EOF
