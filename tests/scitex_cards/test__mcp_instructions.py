#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the MCP server's agent-facing instructions (`_mcp_instructions`).

The bug these lock down: the instructions hard-coded ``'agent:proj-scitex-todo'``
as the example scope. That identity does not exist — ``proj-`` is a dead legacy
prefix (the one :data:`scitex_cards._users.IDENTITY_PREFIXES` exists to STRIP).
Measured against the live store on 2026-07-11:

    cards under the TAUGHT scope  agent:proj-scitex-todo :  2
    cards under the REAL   scope  agent:scitex-todo      : 63

So every agent that followed its own MCP instructions saw ~3% of its cards and
concluded the board had nothing for it. The instructions must therefore name the
agent's OWN resolved identity — and when that identity is unresolvable they must
name NO scope at all, because a silently-wrong example is worse than an honest
absence.

No mocks: the rendered string is asserted directly, and the live-server test
reads the REAL ``mcp.instructions`` off the constructed FastMCP instance.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_cards._mcp_instructions import build_instructions

#: The dead prefix. Anything an agent is TOLD to use must never carry it.
DEAD_PREFIX = "proj-"


# --------------------------------------------------------------------------- #
# The resolved identity is interpolated                                       #
# --------------------------------------------------------------------------- #
def test_instructions_name_the_resolved_identity():
    # Arrange
    agent_id = "scitex-todo"
    # Act
    text = build_instructions(agent_id)
    # Assert
    assert "agent:scitex-todo" in text


def test_instructions_name_whatever_identity_is_resolved():
    # Arrange — any agent, not just this repo's.
    agent_id = "ripple-wm"
    # Act
    text = build_instructions(agent_id)
    # Assert
    assert "agent:ripple-wm" in text and "scitex-todo`" not in text


def _server_instructions_under(env_agent_id: str | None) -> str:
    """Boot the REAL server in a FRESH interpreter and return `mcp.instructions`.

    A subprocess, not an in-process reload: the instructions are built ONCE at
    module import (exactly as they are when an agent starts its MCP server), and
    reloading the module in-process would rebuild the shared FastMCP instance
    without the tools its sibling modules register on it — corrupting the rest of
    the session. This is the honest end-to-end check, and it needs no mocks.
    """
    env = dict(os.environ)
    # The identity now resolves from the post-rename $SCITEX_CARDS_AGENT_ID,
    # which `_env_compat` mirrors onto $SCITEX_TODO_AGENT_ID at import (new
    # name wins). An ambient SCITEX_CARDS_AGENT_ID would therefore clobber
    # whatever we set on the old name, so normalise BOTH prefixes (and both
    # deprecated twins) to a known state before driving the one we want.
    for var in (
        "SCITEX_CARDS_AGENT_ID",
        "SCITEX_TODO_AGENT_ID",
        "SCITEX_CARDS_AGENT",  # deprecated twin: fails loud if set
        "SCITEX_TODO_AGENT",
    ):
        env.pop(var, None)
    if env_agent_id is not None:
        env["SCITEX_CARDS_AGENT_ID"] = env_agent_id
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scitex_cards._mcp_server import mcp; print(mcp.instructions)",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_live_server_instructions_carry_the_env_identity():
    """The REAL server string, built at import, names $SCITEX_TODO_AGENT_ID."""
    # Arrange
    pytest.importorskip("fastmcp", reason="scitex-todo[mcp] extra not installed")
    # Act
    text = _server_instructions_under("test-agent-xyz")
    # Assert
    assert "agent:test-agent-xyz" in text


def test_live_server_instructions_name_no_scope_without_an_identity():
    """With the identity unset the REAL server fabricates no `agent:` example."""
    # Arrange
    pytest.importorskip("fastmcp", reason="scitex-todo[mcp] extra not installed")
    # Act
    text = _server_instructions_under(None)
    # Assert
    fabricated = [
        m.group(0)
        for m in re.finditer(r"agent:[A-Za-z0-9_.-]+", text)
        if m.group(0) != "agent:<your-agent-id>"
    ]
    assert fabricated == [] and "UNRESOLVED" in text


# --------------------------------------------------------------------------- #
# An UNRESOLVED identity fabricates nothing                                   #
# --------------------------------------------------------------------------- #
#: The ONLY `agent:` mention the unresolved branch may carry — an explicit
#: placeholder that names nobody, as opposed to a fabricated concrete identity.
PLACEHOLDER_SCOPE = "agent:<your-agent-id>"

#: WHY the three `says_so_and_says_how_to_discover` tests below are split but
#: share this rationale: an honest absence is a THREE-part contract, and the
#: original single test hid parts 2 and 3 behind part 1's assert. The
#: instructions must (a) ADMIT the identity is unresolved rather than silently
#: printing a wrong example, (b) name the env var that fixes it, and (c) point
#: at the tools that let an agent discover its own identity. Dropping any one
#: of the three leaves an agent stuck with no example AND no way forward, which
#: is the failure this file exists to prevent.


@pytest.mark.parametrize("unresolved", [None, ""])
def test_unresolved_identity_invents_no_scope_example(unresolved):
    # Arrange
    permitted = PLACEHOLDER_SCOPE
    # Act
    text = build_instructions(unresolved)
    # Assert — no `agent:<something>` example is fabricated. The only permitted
    # mention is the explicit `<your-agent-id>` placeholder, which names nobody.
    fabricated = [
        m.group(0)
        for m in re.finditer(r"agent:[A-Za-z0-9_.-]+", text)
        if m.group(0) != permitted
    ]
    assert fabricated == []


@pytest.mark.parametrize("unresolved", [None, ""])
def test_unresolved_identity_admits_the_identity_is_unresolved(unresolved):
    # Arrange
    admission = "UNRESOLVED"
    # Act
    text = build_instructions(unresolved)
    # Assert — an honest absence, not a silently-wrong example.
    assert admission in text


@pytest.mark.parametrize("unresolved", [None, ""])
def test_unresolved_identity_names_the_env_var_that_fixes_it(unresolved):
    # Arrange
    env_var = "SCITEX_TODO_AGENT_ID"
    # Act
    text = build_instructions(unresolved)
    # Assert — the agent is told WHICH knob turns the absence into an identity.
    assert env_var in text


@pytest.mark.parametrize("unresolved", [None, ""])
def test_unresolved_identity_points_at_the_discovery_tools(unresolved):
    # Arrange
    discovery_tools = ("list_tasks", "resolve_store")
    # Act
    text = build_instructions(unresolved)
    # Assert — a way forward, not just an apology.
    assert all(tool in text for tool in discovery_tools)


def test_unresolved_branch_never_mentions_the_dead_prefix():
    # Arrange
    unresolved = None
    # Act
    text = build_instructions(unresolved)
    # Assert
    assert DEAD_PREFIX not in text


# --------------------------------------------------------------------------- #
# No dead `proj-*` identity examples on any agent-facing surface              #
# --------------------------------------------------------------------------- #
def test_no_dead_prefix_anywhere_in_the_mcp_surface():
    """Neither the instructions nor ANY tool description may teach `proj-*`.

    The MCP surface is what an agent reads without being asked to — the
    instructions at session start and every tool's description in its tool
    list. One dead identity in there mis-teaches the whole fleet.
    """
    # Arrange
    pytest.importorskip("fastmcp", reason="scitex-todo[mcp] extra not installed")
    from scitex_cards._mcp_server import mcp  # noqa: PLC0415

    # Act
    surfaces = [mcp.instructions or ""]
    for tool in _registered_tools(mcp):
        surfaces.append(getattr(tool, "description", "") or "")
    offenders = [s for s in surfaces if DEAD_PREFIX in s]

    # Assert
    assert offenders == []


#: Identity-TEACHING forms of the dead prefix: a `proj-*` name sitting where an
#: agent id belongs (a scope literal, an identity flag, an identity field, or
#: the identity env var). Deliberately narrow so it does NOT fire on the places
#: `proj-` legitimately survives: the prefix-STRIPPING table in
#: `_users/_identity.py`, historical card ids / task-dir paths, prose about the
#: naming drift itself, and the unrelated `.proj-hide` CSS class.
DEAD_IDENTITY_EXAMPLE = re.compile(
    r"""(?ix)
    (?:
        agent:proj-                                             # scope literal
      | --(?:agent|assignee|author|by|scope|id-prefix)\s+
            ["']?(?:agent:)?proj-                               # CLI flag
      | \b(?:agent|assignee|author|scope|owner)\s*[:=]\s*
            ["']?(?:agent:)?proj-                               # field / kwarg
      | SCITEX_TODO_AGENT_ID\s*=\s*["']?proj-                   # the env var
    )
    """
)

#: Scanned files that keep a `proj-*` identity ON PURPOSE, each a deliberate
#: carve-out rather than a blanket exemption:
#:
#: - ``_cli/_sync_github.py`` — WRITES ``agent="proj-scitex-dev"`` onto the
#:   bundle card it imports. That is a data/behaviour bug of the same family
#:   (the card lands owned by a dead identity), but fixing it changes what the
#:   verb WRITES, not what an agent is TOLD, so it is out of scope for the
#:   instructions fix and is left for a follow-up.
_ALLOWED = {"_cli/_sync_github.py"}


def _agent_facing_files(root: Path) -> list[Path]:
    """Every surface that TEACHES an agent how to address itself.

    Shipped package source + the shipped skills + the two entry-point docs.
    Deliberately EXCLUDES the historical record (``docs/adr/``, ``docs/audit/``,
    ``CHANGELOG.md``) and the bundled example store, where a `proj-*` name is a
    faithful record of what was true then, not an instruction for now.
    """
    pkg = root / "src" / "scitex_cards"
    files = [p for p in pkg.rglob("*.py")]
    files += list((pkg / "_skills").rglob("*.md"))
    files += [
        p
        for p in (root / "README.md", root / "docs" / "CHEATSHEET-fleet-todo.md")
        if p.exists()
    ]
    return files


#: The repo root, and whether we are reading a source checkout at all. Hoisted
#: out of the test as a `skipif` guard (rather than an in-body `pytest.skip`)
#: so the test body holds its one real assertion and nothing else.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_IS_SOURCE_CHECKOUT = (_REPO_ROOT / "src" / "scitex_cards").is_dir()


@pytest.mark.skipif(
    not _IS_SOURCE_CHECKOUT, reason="not running from a source checkout"
)
def test_no_dead_proj_identity_examples_in_shipped_surfaces():
    # Arrange
    root = _REPO_ROOT

    # Act
    offenders: list[str] = []
    for path in _agent_facing_files(root):
        rel = path.relative_to(root).as_posix()
        if any(rel.endswith(a) for a in _ALLOWED):
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if DEAD_IDENTITY_EXAMPLE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    # Assert
    assert offenders == [], (
        "dead `proj-*` identity examples still shipped — an agent that copies "
        "one filters on a scope holding nothing:\n" + "\n".join(offenders)
    )


def _registered_tools(mcp):
    """Return the registered tool objects across fastmcp's drifting registry API.

    Mirrors the strategy in ``tests/scitex_cards/test__mcp_server.py`` and
    ``_cli/_mcp.py``: prefer the tool manager's private map, fall back to the
    async listing.
    """
    import asyncio  # noqa: PLC0415

    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None) if tm is not None else None
    if isinstance(tools, dict):
        return list(tools.values())

    async def _collect():
        if tm is not None and hasattr(tm, "get_tools"):
            got = await tm.get_tools()
        else:
            got = await mcp.list_tools()
        return list(got.values()) if isinstance(got, dict) else list(got)

    return asyncio.run(_collect())


# EOF
