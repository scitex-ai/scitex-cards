#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo`` command-line interface package.

Public entry point is ``main`` (wired to the ``scitex-todo`` console script).
The command tree is split across focused modules:

    _main         root group + core verbs (render-graph, list-tasks, board)
    _introspect   list-python-apis, mcp list-tools           (§1a)
    _completion   install-/print-shell-completion            (§1a)
    _skills       skills {list, get, install}                (§1a)
    _help_wait    help-wait / help-clear verbs

The bulk of the command tree is attached to ``main`` inside ``_main`` itself.
``help-wait`` / ``help-clear`` are wired here instead because ``_main`` and
``_write`` (the natural mutation-verb home) are both already at their line
budget — registering from this thin package root keeps the new verbs in a
focused module without a disruptive refactor of an unrelated oversized file.
"""

from __future__ import annotations

from . import _db as _db_cli
from . import _health as _health_cli
from . import _help_wait as _help_wait_cli
from . import _hub as _hub_cli
from . import _install_stop_hook as _install_stop_hook_cli
from . import _may_stop as _may_stop_cli
from . import _min_client_version as _min_client_version_cli
from . import _serve as _serve_cli
from . import _stop_hook as _stop_hook_cli
from ._main import main

_help_wait_cli.register(main)
# `health` — the package-level health doctor (store / agent-id / notifyd /
# channel). Wired here (like help-wait) to keep the over-budget _main.py
# untouched.
_health_cli.register(main)
# `db` — the SQLite store's operability noun group. ("shadow" until 0.17.x:
# the database was a mirror alongside a canonical YAML file. It is now the
# ONLY store, so nothing shadows anything.) Wired here (like health /
# help-wait) to keep _main.py untouched.
_db_cli.register(main)
# `db set-min-client-version` — attaches itself onto `db_group` via a
# decorator at import time (see the module docstring); `register()` here is
# a no-op kept for this package's convention, since `db_group` is already
# wired onto `main` by `_db_cli.register` above.
_min_client_version_cli.register(main)
# `serve` — the hub RPC surface (remote-hub PR-2). Same thin-root wiring.
_serve_cli.register(main)
# `hub` — provisioning + doctor for the remote rail (remote-hub PR-4).
_hub_cli.register(main)
# `may-stop` — the never-stop detector (exit 0 = may stop, 2 = work exists).
_may_stop_cli.register(main)
#  — the Claude Code Stop hook itself (cards owns both ends of the
# contract; the runtime only registers it). See _stop_hook for why.
_stop_hook_cli.register(main)
# `install-stop-hook` — writes the four lines of settings.json that make the
# hook above actually run. Registration was the last MANUAL step between a
# merged mechanism and a live one, and a manual step is one that silently
# does not happen: the operator asked whether an agent with cards left could
# still stop, and it could, because nothing had wired it anywhere.
_install_stop_hook_cli.register(main)

__all__ = ["main"]

# EOF
