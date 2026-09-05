#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards: a canonical task-card store with pluggable adapters.

The task store (one ``tasks`` table in the canonical database) is the source of
truth. Adapters render or import it; the mermaid adapter (store -> dependency
PNG) ships today. See the project roadmap for org and Web-UI adapters.

Quick Start
-----------
>>> import scitex_cards as card
>>> tasks = card.load_tasks()                    # doctest: +SKIP
>>> src = card.build_mermaid(tasks)              # doctest: +SKIP
>>> card.render(src, "tasks.png")                # doctest: +SKIP
'mmdc'
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
def _resolve_version(read_version=None) -> str:
    """The installed version, read on demand. See the note above for why.

    ONE DIST NAME. This loop used to try the current name and then fall back to
    a transition-window name for un-cutover editable installs. The retired name
    is gone, which left the loop iterating the SAME string twice: a second
    `version()` call that can only raise the same `PackageNotFoundError` the
    first one did, and a fallback chain with nothing to fall back to.

    `read_version` defaults to `importlib.metadata.version`. It is a parameter
    so the UNINSTALLED branch is reachable without rewriting the stdlib module
    (PA-306 §3) — that branch is exactly the one that cannot be reached in an
    environment where this package IS installed, which is every environment the
    suite runs in.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover — only on ancient Pythons
        return "0.0.0+local"
    read = version if read_version is None else read_version
    try:
        return read("scitex-cards")
    except PackageNotFoundError:
        return "0.0.0+local"


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
_LAZY_IMPORTS = {
    "TaskValidationError": ("._model", "TaskValidationError"),
    # Agent career — host@name identity join key + agent-directory port
    # (ADR-0009). Library seams; exposed here so consumers can wire a
    # provider / canonicalise ids without reaching into the private module.
    "AGENT_DIRECTORY_GROUP": ("._ports", "AGENT_DIRECTORY_GROUP"),
    "AgentDirectoryPort": ("._ports", "AgentDirectoryPort"),
    "AgentIdentityError": ("._ports", "AgentIdentityError"),
    "AgentInfo": ("._ports", "AgentInfo"),
    "EmptyAgentDirectory": ("._ports", "EmptyAgentDirectory"),
    "canonical_agent_id": ("._ports", "canonical_agent_id"),
    "dedup_agents": ("._ports", "dedup_agents"),
    "parse_agent_id": ("._ports", "parse_agent_id"),
    "resolve_agent_directory": ("._ports", "resolve_agent_directory"),
    "ENV_AGENT": ("._store", "ENV_AGENT"),
    "ENV_SCOPE": ("._store", "ENV_SCOPE"),
    "TaskNotFoundError": ("._store", "TaskNotFoundError"),
    "add_task": ("._store", "add_task"),
    "comment_task": ("._store", "comment_task"),
    "complete_task": ("._store", "complete_task"),
    "delete_task": ("._store", "delete_task"),
    "get_task": ("._store", "get_task"),
    "list_tasks": ("._store", "list_tasks"),
    "reassign_task": ("._store", "reassign_task"),
    "reopen_task": ("._store", "reopen_task"),
    "resolve_store": ("._store", "resolve_store"),
    "resolve_task": ("._store", "resolve_task"),
    "restore_task": ("._store", "restore_task"),
    "set_collaborator": ("._store", "set_collaborator"),
    "set_edge": ("._store", "set_edge"),
    "set_subscriber": ("._store", "set_subscriber"),
    "summarize_tasks": ("._store", "summarize_tasks"),
    "update_task": ("._store", "update_task"),
    # MCP tools whose Python function already existed at module level under
    # exactly this name and was simply never exported (audit §6 parity). An
    # MCP tool with no Python API is a capability only an AGENT can reach:
    # not callable from a script, a cron job, or another package, and not
    # testable without standing up a transport. These four cost nothing to
    # publish because the function is already there.
    "health": ("._health", "health"),
    "help_clear": ("._help_wait", "help_clear"),
    "help_wait": ("._help_wait", "help_wait"),
    "rescore_task": ("._store_rescore", "rescore_task"),
    # The messaging rail. These five DID NOT have a function to publish — they
    # existed only as async MCP tool bodies, so the logic was welded to the
    # transport and no script could reach it. `_messaging` is that extraction:
    # the MCP tools now delegate to these and keep their JSON contract.
    "ack_notifications": ("._messaging", "ack_notifications"),
    "dm_list": ("._messaging", "dm_list"),
    "dm_send": ("._messaging", "dm_send"),
    "dm_send_document": ("._messaging", "dm_send_document"),
    "poll_notifications": ("._messaging", "poll_notifications"),
}


def __getattr__(name: str):
    """PEP 562 lazy loader — resolve public-API names on first access.

    Imports the source submodule, fetches the attribute, caches it
    into module ``globals()`` so subsequent accesses skip the lookup.
    Unknown names raise ``AttributeError`` per the PEP.
    """
    if name == "__version__":
        value = _resolve_version()
        globals()["__version__"] = value
        return value
    target = _LAZY_IMPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod_path, attr = target
    value = getattr(importlib.import_module(mod_path, __name__), attr)
    globals()[name] = value
    return value


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
    "EmptyAgentDirectory",
    "ENV_AGENT",
    "ENV_SCOPE",
    "TaskNotFoundError",
    "TaskValidationError",
    "ack_notifications",
    "add_task",
    "canonical_agent_id",
    "comment_task",
    "complete_task",
    "dedup_agents",
    "delete_task",
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
]

# EOF
