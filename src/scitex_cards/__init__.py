#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards: a canonical task-card store with pluggable adapters.

The task store (one ``tasks`` table in the canonical database) is the source of
truth. Adapters render or import it; the mermaid adapter (store -> dependency
PNG) ships today. See the project roadmap for org and Web-UI adapters.

Quick Start
-----------
>>> import scitex_cards as cards
>>> cards.add_task(None, id="c1", title="Wire the notify rail")  # doctest: +SKIP
>>> cards.list_tasks(None, status="in_progress")                 # doctest: +SKIP
"""

from __future__ import annotations

# Environment dual-read shim — MUST run before anything reads SCITEX_CARDS_*
# env vars (mirrors SCITEX_CARDS_* onto the old names the code still reads).


# `__version__` resolves LAZILY, in __getattr__ below. The reason is measured,
# not stylistic: importing `importlib.metadata` at module scope cost 223 ms of
# a 425 ms cold import — more than half — because reading package metadata
# drags in email.message (96 ms), email.utils (74 ms) and zipfile (41 ms).
#
# This block used to sit directly above the comment explaining that the PEP 562
# machinery exists "to keep cold-start well under the audit-cli §10 budget
# (500 ms)". The module stated the goal and then broke it on the next
# statement, which is how a package with an otherwise correct lazy-import
# design ended up over budget.
#
# The public surface is unchanged: `scitex_cards.__version__` still answers.
# It just pays for the metadata reader when someone asks for a version, which
# tab-completion never does.
def _resolve_version(read_version=None, tree_file=None) -> str:
    """The version, resolved from the TREE when this is a source checkout.

    WHY THE TREE COMES FIRST, measured 2026-09-17 on one host, one checkout,
    five answers to "which release is this?":

        /uvwork/venv-agent (editable)      0.51.3
        /opt/venv-sac      (a copy)        0.52.1
        repo .venv         (editable)      0.53.0
        /uvwork/venv-py311 (editable)      0.53.0
        pyproject.toml     (the tree)      0.53.1

    `importlib.metadata.version()` answers THE VERSION THE ENV WAS INSTALLED AT,
    and for an editable install that number is frozen at install time while the
    code underneath keeps moving. So `__version__` — and every surface that
    prints it, including the board's leaf band and the DM page's title — reports
    a release that is not the code being run. That is the same user-visible lie
    as incident-version-string-lies-orphaned-distinfo-20260712 (an orphaned
    dist-info froze scitex-todo at 0.7.26 while the code ran newer); the
    mechanism here is a FROZEN editable metadata rather than an orphan, and the
    fix is the same shape: ask the thing that cannot drift.

    So: if a `pyproject.toml` with a `version` sits beside the package, that is
    the answer — it is the tree we are actually importing from. Otherwise the
    installed metadata is right (a site-packages copy's code and metadata ship
    together, which is why /opt/venv-sac's 0.52.1 is honest), and the last resort
    stays the local sentinel.

    `read_version` and `tree_file` are both injectable — the first so the
    metadata branch is reachable without rewriting the stdlib module, the second
    so the NO-TREE branch (a site-packages install) is reachable without faking
    a filesystem. Both were previously unreachable in a test, which is how a
    resolver keeps a branch nobody has ever run.
    """
    tree = _version_from_tree(package_file=tree_file)
    if tree is not None:
        return tree
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover — only on ancient Pythons
        return "0.0.0+local"
    read = version if read_version is None else read_version
    try:
        return read("scitex-cards")
    except PackageNotFoundError:
        return "0.0.0+local"


def _version_from_tree(package_file=None) -> str | None:
    """The `version` declared by the pyproject beside this package, or None.

    None means "not a source checkout", and the caller falls back to the
    installed metadata — a wheel installed into site-packages has no pyproject
    above it, and its metadata is not stale because it shipped with the code.

    `re` and `pathlib` are imported HERE, not at module scope, for the same
    measured reason the metadata reader is lazy (223 ms of a 425 ms cold import
    was `importlib.metadata`): this module's cold path is a budget, and a version
    lookup is the only caller that needs either of them.
    """
    import re
    from pathlib import Path

    package_file = package_file or __file__
    if package_file is None:  # pragma: no cover — namespace-package shape
        return None
    # WALK UP, do not count directories: this repo uses a src-layout
    # (`<repo>/src/scitex_cards/__init__.py`), so the pyproject is THREE levels
    # above the module file, not two — and a flat-layout consumer of this
    # function would be wrong the other way. Walking finds the declaration in
    # either shape and finds nothing in site-packages, which is the answer the
    # caller needs there.
    package_dir = Path(package_file).resolve().parent
    for candidate in (package_dir, *list(package_dir.parents)[:3]):
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            return None
        # A narrow literal match, not a TOML parse: `tomllib` would be a third
        # import on a path that runs on first attribute access, and the
        # declaration read here is a single top-level line.
        found = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
        if found:
            return found.group(1)
    return None


#: Public API — Convention A (audit §6: every public Python API must match a
#: registered MCP tool name 1:1). The MCP tool surface is documented in
#: ``_skills/scitex-cards/05_mcp-tools.md`` and registered in ``_mcp_server.py``.
#:
#: Render / mermaid / paths / model helpers used to be re-exported here.
#: They were moved off the top level (audit §6) but remain importable from
#: their submodules:
#:
#:     from scitex_cards._diagram import (
#:         render, render_with_kroki, render_with_mmdc, find_chromium,
#:         RenderError, build_mermaid, STATUS_STYLE,
#:     )
#:     from scitex_cards._model import (
#:         load_tasks, save_tasks, VALID_STATUSES, TaskValidationError,
#:     )
#:     from scitex_cards._paths import resolve_tasks_path

# PEP 562 lazy attribute resolution — keeps `import scitex_cards` cold-start
# well under the audit-cli §10 budget (500 ms) by deferring every submodule
# load until the attribute is actually touched. Click tab-completion taps
# `import scitex_cards` once per Tab press, so the savings compound.
#
# Public surface stays identical: every name in ``__all__`` resolves on
# ``scitex_cards.NAME`` access via :func:`__getattr__`, and gets cached in
# ``globals()`` for O(1) repeat lookups.
def __getattr__(name: str):
    """PEP 562 lazy loader — resolve public-API names on first access.

    One static branch per name, deliberately verbose: the hub static
    reader enumerates ``__getattr__`` for bare single-arg-literal
    ``import_module()`` assigns and rejects indirection (``.get`` /
    ``getattr()`` / tuple-unpack), so a table-driven loader is
    metadata-invisible on dev and every plugin skips. Runtime cost is
    unchanged: each branch imports once, then the globals() cache
    below serves all later accesses without re-entering here.

    Unknown names raise ``AttributeError`` per the PEP.
    """
    from importlib import import_module

    if name == "__version__":
        _v = _resolve_version()
    elif name == 'TaskValidationError':
        _m = import_module("scitex_cards._model")
        _v = _m.TaskValidationError
    # Agent career — host@name identity join key + agent-directory port (ADR-0009).
    elif name == 'AGENT_DIRECTORY_GROUP':
        _m = import_module("scitex_cards._ports")
        _v = _m.AGENT_DIRECTORY_GROUP
    elif name == 'AgentDirectoryPort':
        _m = import_module("scitex_cards._ports")
        _v = _m.AgentDirectoryPort
    elif name == 'AgentIdentityError':
        _m = import_module("scitex_cards._ports")
        _v = _m.AgentIdentityError
    elif name == 'AgentInfo':
        _m = import_module("scitex_cards._ports")
        _v = _m.AgentInfo
    elif name == 'EmptyAgentDirectory':
        _m = import_module("scitex_cards._ports")
        _v = _m.EmptyAgentDirectory
    elif name == 'canonical_agent_id':
        _m = import_module("scitex_cards._ports")
        _v = _m.canonical_agent_id
    elif name == 'dedup_agents':
        _m = import_module("scitex_cards._ports")
        _v = _m.dedup_agents
    elif name == 'parse_agent_id':
        _m = import_module("scitex_cards._ports")
        _v = _m.parse_agent_id
    elif name == 'resolve_agent_directory':
        _m = import_module("scitex_cards._ports")
        _v = _m.resolve_agent_directory
    elif name == 'ENV_AGENT':
        _m = import_module("scitex_cards._store")
        _v = _m.ENV_AGENT
    elif name == 'ENV_SCOPE':
        _m = import_module("scitex_cards._store")
        _v = _m.ENV_SCOPE
    elif name == 'TaskNotFoundError':
        _m = import_module("scitex_cards._store")
        _v = _m.TaskNotFoundError
    elif name == 'add_task':
        _m = import_module("scitex_cards._store")
        _v = _m.add_task
    elif name == 'comment_task':
        _m = import_module("scitex_cards._store")
        _v = _m.comment_task
    elif name == 'complete_task':
        _m = import_module("scitex_cards._store")
        _v = _m.complete_task
    elif name == 'delete_task':
        _m = import_module("scitex_cards._store")
        _v = _m.delete_task
    elif name == 'get_task':
        _m = import_module("scitex_cards._store")
        _v = _m.get_task
    elif name == 'list_tasks':
        _m = import_module("scitex_cards._store")
        _v = _m.list_tasks
    elif name == 'reassign_task':
        _m = import_module("scitex_cards._store")
        _v = _m.reassign_task
    elif name == 'reopen_task':
        _m = import_module("scitex_cards._store")
        _v = _m.reopen_task
    elif name == 'resolve_store':
        _m = import_module("scitex_cards._store")
        _v = _m.resolve_store
    elif name == 'resolve_task':
        _m = import_module("scitex_cards._store")
        _v = _m.resolve_task
    elif name == 'restore_task':
        _m = import_module("scitex_cards._store")
        _v = _m.restore_task
    elif name == 'set_collaborator':
        _m = import_module("scitex_cards._store")
        _v = _m.set_collaborator
    elif name == 'set_edge':
        _m = import_module("scitex_cards._store")
        _v = _m.set_edge
    elif name == 'set_subscriber':
        _m = import_module("scitex_cards._store")
        _v = _m.set_subscriber
    elif name == 'summarize_tasks':
        _m = import_module("scitex_cards._store")
        _v = _m.summarize_tasks
    elif name == 'update_task':
        _m = import_module("scitex_cards._store")
        _v = _m.update_task
    # MCP tools whose Python function already existed at module level (audit §6 parity).
    elif name == 'health':
        _m = import_module("scitex_cards._health")
        _v = _m.health
    elif name == 'help_clear':
        _m = import_module("scitex_cards._help_wait")
        _v = _m.help_clear
    elif name == 'help_wait':
        _m = import_module("scitex_cards._help_wait")
        _v = _m.help_wait
    elif name == 'rescore_task':
        _m = import_module("scitex_cards._store_rescore")
        _v = _m.rescore_task
    # The messaging rail — extracted from async MCP tool bodies so scripts can reach it.
    elif name == 'ack_notifications':
        _m = import_module("scitex_cards._messaging")
        _v = _m.ack_notifications
    elif name == 'dm_hide':
        _m = import_module("scitex_cards._messaging")
        _v = _m.dm_hide
    elif name == 'dm_list':
        _m = import_module("scitex_cards._messaging")
        _v = _m.dm_list
    elif name == 'dm_send':
        _m = import_module("scitex_cards._messaging")
        _v = _m.dm_send
    elif name == 'dm_send_document':
        _m = import_module("scitex_cards._messaging")
        _v = _m.dm_send_document
    elif name == 'DmExchangeError':
        _m = import_module("scitex_cards._dm.exchange")
        _v = _m.DmExchangeError
    elif name == 'NotificationWatchUnavailable':
        _m = import_module("scitex_cards._notification_watch")
        _v = _m.NotificationWatchUnavailable
    elif name == 'poll_notifications':
        _m = import_module("scitex_cards._messaging")
        _v = _m.poll_notifications
    elif name == 'watch_notifications':
        _m = import_module("scitex_cards._messaging")
        _v = _m.watch_notifications
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = _v
    return _v


def __dir__():
    """Make tab-completion / ``dir(scitex_cards)`` see the public surface
    even before any attribute has been touched."""
    return sorted(set(__all__) | set(globals()))


__all__ = [
    "__version__",
    "AGENT_DIRECTORY_GROUP",
    "AgentDirectoryPort",
    "AgentIdentityError",
    "AgentInfo",
    "DmExchangeError",
    "EmptyAgentDirectory",
    "ENV_AGENT",
    "ENV_SCOPE",
    "NotificationWatchUnavailable",
    "TaskNotFoundError",
    "TaskValidationError",
    "ack_notifications",
    "add_task",
    "canonical_agent_id",
    "comment_task",
    "complete_task",
    "dedup_agents",
    "delete_task",
    "dm_hide",
    "dm_list",
    "dm_send",
    "dm_send_document",
    "get_task",
    "health",
    "help_clear",
    "help_wait",
    "list_tasks",
    "parse_agent_id",
    "poll_notifications",
    "reassign_task",
    "reopen_task",
    "rescore_task",
    "resolve_agent_directory",
    "resolve_store",
    "resolve_task",
    "restore_task",
    "set_collaborator",
    "set_edge",
    "set_subscriber",
    "summarize_tasks",
    "update_task",
    "watch_notifications",
]

# EOF
