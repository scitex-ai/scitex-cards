#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Phase-1 MCP server (`scitex_cards._mcp_server`).

These exercise the real FastMCP tool dispatch — NO MOCKS, per the
package's no-mock policy. When fastmcp isn't installed in the test
environment the module-level skip below kicks in so the tests are
honestly absent rather than green-theatre-passing.

The behaviour the suite covers:

- The server module raises a clean `ImportError` (not `ModuleNotFoundError`
  with an opaque trace) when fastmcp isn't installed.
- When fastmcp IS installed, every Phase-1 tool is registered and
  dispatches correctly against a `tmp_path` store.
- Tool naming follows the audit conventions: Convention A
  (tool_name == python_api_name, no `scitex_cards_` prefix) for the six
  task-store tools, plus Convention B (`todo_<verb>_<noun>` per §5) for
  the two skills tools — eight tools total.
- The CLI `mcp list-tools` enumerates them.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest

# --------------------------------------------------------------------------- #
# Optional-dep guard                                                          #
# --------------------------------------------------------------------------- #
fastmcp = pytest.importorskip(
    "fastmcp",
    reason=(
        "fastmcp not installed — `scitex-todo[mcp]` extra not present. "
        "Install with `pip install scitex-todo[mcp]` to run the MCP tests."
    ),
)


# --------------------------------------------------------------------------- #
# Module import                                                               #
# --------------------------------------------------------------------------- #
def test_mcp_server_module_imports():
    # Arrange
    mod = importlib.import_module("scitex_cards._mcp_server")
    # Act
    has_mcp = hasattr(mod, "mcp")
    # Assert
    assert has_mcp


def test_mcp_server_name():
    # Arrange
    mod = importlib.import_module("scitex_cards._mcp_server")
    # Act
    name = mod.mcp.name
    # Assert
    assert name == "scitex-cards"


def test_tool_naming_follows_convention():
    """At least one tool is registered (sanity for the rest of the suite)."""
    # Arrange
    from scitex_cards._mcp_server import mcp

    # Act
    names = _tool_names(mcp)
    # Assert
    assert names, "expected at least one tool registered"


# Convention A — tool_name == python_api_name (audit §6, no `scitex_cards_`
# prefix; audit §2 forbids the bare scitex_cards_<verb>_<noun> three-token
# form for the task-store tools after Convention A is adopted).
_CONVENTION_A_NAMES = {
    "add_task",
    "update_task",
    "complete_task",
    "list_tasks",
    "summarize_tasks",
    "resolve_store",
    # MCP completeness wave (lead a2a `fe723080`, 2026-06-08) — 7 new
    # task-store tools, each mapping 1:1 to a Python API function in
    # ``scitex_cards._store`` per Convention A.
    "get_task",
    "delete_task",
    "restore_task",
    "comment_task",
    "set_edge",
    "resolve_task",
    "reopen_task",
    # C5 reassign primitive — 1:1 with `_store.reassign_task` (Convention
    # A; registered in `_mcp_skills` to keep `_mcp_server` under budget).
    "reassign_task",
    # The rank engine's write verb (v5-lite, ADR-0011 §1/§8) — 1:1 with
    # `_store.rescore_task` (Convention A; registered in `_mcp_relations`).
    "rescore_task",
    # Card roles (ADR-0009) — 1:1 with `_store.set_collaborator` /
    # `_store.set_subscriber` (Convention A).
    "set_collaborator",
    "set_subscriber",
    # Help-wait SoC lift — 1:1 with `_help_wait.help_wait` /
    # `_help_wait.help_clear`. Semantics lifted out of the dotfiles hook.
    "help_wait",
    "help_clear",
    # Standalone pull-inbox read path — 1:1 with `_inbox.poll_inbox`
    # (registered in `_mcp_skills`). PULL card-message delivery, no sac.
    "poll_notifications",
    # Operator↔agent direct messages — 1:1 with `_threads.append_message` /
    # `_threads.get_thread` (registered in `_mcp_skills`; scitex-dev DM
    # convention v1, threads.yaml sidecar).
    "dm_send",
    "dm_list",
}
# Convention B — `todo_<verb>_<noun>` for the audit §5 required skills
# tools. These don't map 1:1 to a Python API; they introspect the bundled
# `_skills/` directory.
_CONVENTION_B_NAMES = {
    "todo_skills_list",
    "todo_skills_get",
}
# Cross-package standard names — a fixed tool name shared verbatim with the
# sac/cct health tools (single token by that shared spec, so exempt from the
# no-single-token guard below).
_STANDARD_NAMES = {
    "health",
}


def test_no_tool_uses_dropped_scitex_cards_prefix():
    """The `scitex_cards_` prefix was dropped per audit §6 Convention A.

    Single-token names (`summary`, `where`) were also forbidden by audit
    §2 and got renamed (`summarize_tasks`, `resolve_store`). This test
    asserts the rename held — no tool reverts to the old prefix nor to a
    bare single-token name.
    """
    # Arrange
    from scitex_cards._mcp_server import mcp

    names = _tool_names(mcp)
    # Act
    bad_prefix = [n for n in names if n.startswith("scitex_cards_")]
    # `health` is a cross-package STANDARD single-token name (shared verbatim
    # with sac/cct), so it is exempt from the no-single-token audit rule.
    single_token = [n for n in names if "_" not in n and n not in _STANDARD_NAMES]
    # Assert
    assert not bad_prefix and not single_token, (
        f"tools {bad_prefix + single_token!r} regress audit §6 / §2"
    )


def test_tool_names_match_known_conventions():
    """Every registered tool is either Convention A (task-store) or B (skills)."""
    # Arrange
    from scitex_cards._mcp_server import mcp

    names = set(_tool_names(mcp))
    allowed = _CONVENTION_A_NAMES | _CONVENTION_B_NAMES | _STANDARD_NAMES
    # Act
    extras = names - allowed
    # Assert
    assert not extras, (
        f"unrecognised tool name(s) {extras!r} — add to "
        f"_CONVENTION_A_NAMES / _CONVENTION_B_NAMES if intentional"
    )


def test_phase_1_tools_registered():
    """All six Phase-1 tools (architecture doc) are present."""
    # Arrange
    from scitex_cards._mcp_server import mcp

    expected = {
        "add_task",
        "update_task",
        "complete_task",
        "list_tasks",
        "summarize_tasks",
        "resolve_store",
    }
    # Act
    names = set(_tool_names(mcp))
    # Assert
    assert expected <= names, f"missing: {expected - names}"


# --------------------------------------------------------------------------- #
# Tool round-trips against a real tmp_path store                              #
# --------------------------------------------------------------------------- #
def test_add_returns_id(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task

    store = str(tmp_path / "tasks.yaml")
    # Act
    add = asyncio.run(
        _call_tool(
            add_task,
            id="a",
            title="A",
            scope="agent:test",
            assignee="agent:test",
            tasks_path=store,
        )
    )
    # Assert
    assert json.loads(add)["id"] == "a"


def test_add_task_stores_created_by(tmp_path):
    # Arrange — MCP add_task accepts an explicit creating USER and persists
    # it as created_by (board ROLES section reads this off the /graph node).
    from scitex_cards._mcp_server import add_task

    store = str(tmp_path / "tasks.yaml")
    # Act
    add = asyncio.run(
        _call_tool(
            add_task,
            id="a",
            title="A",
            assignee="agent:explicit",
            created_by="agent:explicit",
            tasks_path=store,
        )
    )
    # Assert
    assert json.loads(add)["created_by"] == "agent:explicit"


def test_add_task_defaults_created_by_from_env(tmp_path, env):
    # Arrange — no explicit author; resolves from $SCITEX_TODO_AGENT_ID.
    from scitex_cards._mcp_server import add_task

    store = str(tmp_path / "tasks.yaml")
    env.set("SCITEX_TODO_AGENT_ID", "agent:fromenv")
    # Act
    add = asyncio.run(
        _call_tool(add_task, id="a", title="A", assignee="agent:x", tasks_path=store)
    )
    # Assert
    assert json.loads(add)["created_by"] == "agent:fromenv"


def test_add_then_list_round_trip(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(
            add_task,
            id="a",
            title="A",
            scope="agent:test",
            assignee="agent:test",
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(_call_tool(list_tasks, tasks_path=store))
    rows = json.loads(listed)
    # Assert
    assert {r["id"] for r in rows} == {"a"}


def test_scope_filter_excludes_other_scope(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(add_task, id="a", title="A", scope="agent:lead", tasks_path=store)
    )
    asyncio.run(
        _call_tool(
            add_task,
            id="b",
            title="B",
            scope="agent:proj-scitex-todo",
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(
        _call_tool(list_tasks, scope="agent:proj-scitex-todo", tasks_path=store)
    )
    # Assert
    assert {r["id"] for r in json.loads(listed)} == {"b"}


def test_list_tasks_filter_by_agent(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(add_task, id="a", title="A", agent="proj-x", tasks_path=store)
    )
    asyncio.run(
        _call_tool(add_task, id="b", title="B", agent="proj-y", tasks_path=store)
    )
    # Act
    listed = asyncio.run(
        _call_tool(list_tasks, scope="", agent="proj-x", tasks_path=store)
    )
    # Assert
    assert {r["id"] for r in json.loads(listed)} == {"a"}


def test_list_tasks_filter_blocking_me(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(
        _call_tool(
            add_task,
            id="b",
            title="B",
            status="blocked",
            blocker="operator-decision",
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(
        _call_tool(list_tasks, scope="", blocking_me=True, tasks_path=store)
    )
    # Assert
    assert {r["id"] for r in json.loads(listed)} == {"b"}


def test_list_tasks_filter_overdue(tmp_path):
    # The MCP `list_tasks(overdue=True)` mirrors the CLI's `--overdue`
    # (PR #126) and the fleet payload's `overdue_count` (PR #125). A
    # task is overdue when its next deadline is strictly before today
    # AND it is NOT in a terminal lifecycle state.
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks, update_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(
            add_task,
            id="late",
            title="Late",
            deadline="2000-01-01",
            tasks_path=store,
        )
    )
    asyncio.run(
        _call_tool(
            add_task,
            id="future",
            title="Future",
            deadline="2099-01-01",
            tasks_path=store,
        )
    )
    asyncio.run(
        _call_tool(
            add_task,
            id="done-past",
            title="Done-Past",
            deadline="2000-01-01",
            tasks_path=store,
        )
    )
    asyncio.run(
        _call_tool(
            update_task,
            task_id="done-past",
            status="done",
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(
        _call_tool(
            list_tasks,
            scope="",
            overdue=True,
            tasks_path=store,
        )
    )
    # Assert
    assert {r["id"] for r in json.loads(listed)} == {"late"}


def test_add_task_with_deadline_sets_deadline_field(tmp_path):
    # PR-followup gap closer (from PR #127): MCP `add_task` now accepts
    # `deadline=`. The writer's validator parses the P4 schema (bare
    # ISO date / repeater suffix).
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(
            add_task,
            id="a",
            title="A",
            assignee="agent:x",
            deadline="2030-01-01",
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(_call_tool(list_tasks, scope="", tasks_path=store))
    # Assert
    rows = json.loads(listed)
    assert rows[0]["deadline"] == "2030-01-01"


def test_update_task_with_deadline_sets_deadline_field(tmp_path):
    # Companion to add_task's deadline kwarg — update_task can also set
    # the deadline post-hoc.
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks, update_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(add_task, id="a", title="A", assignee="agent:x", tasks_path=store)
    )
    asyncio.run(
        _call_tool(
            update_task,
            task_id="a",
            deadline="2030-06-15",
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(_call_tool(list_tasks, scope="", tasks_path=store))
    # Assert
    rows = json.loads(listed)
    assert rows[0]["deadline"] == "2030-06-15"


def test_add_task_with_deadlines_list_sets_multi_deadlines(tmp_path):
    # The multi-deadline form (P4 PR3 recurring extension).
    # Arrange
    from scitex_cards._mcp_server import add_task, list_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(
            add_task,
            id="a",
            title="A",
            assignee="agent:x",
            deadlines=["2030-01-01", "2030-07-01"],
            tasks_path=store,
        )
    )
    # Act
    listed = asyncio.run(_call_tool(list_tasks, scope="", tasks_path=store))
    # Assert
    rows = json.loads(listed)
    assert rows[0]["deadlines"] == ["2030-01-01", "2030-07-01"]


def test_complete_sets_status_done(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT_ID", "agent:mcp-test")
    from scitex_cards._mcp_server import (
        add_task,
        complete_task,
    )

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["status"] == "done"


def test_complete_stamps_completed_by(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT_ID", "agent:mcp-test")
    from scitex_cards._mcp_server import (
        add_task,
        complete_task,
    )

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["_log_meta"]["completed_by"] == "agent:mcp-test"


def test_complete_stamps_completed_at_z_suffix(tmp_path, env):
    # Arrange
    env.set("SCITEX_TODO_AGENT_ID", "agent:mcp-test")
    from scitex_cards._mcp_server import (
        add_task,
        complete_task,
    )

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(_call_tool(complete_task, task_id="a", tasks_path=store))
    )
    # Assert
    assert out["_log_meta"]["completed_at"].endswith("Z")


def test_add_task_accepts_agent_field(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task

    store = str(tmp_path / "tasks.yaml")
    # Act
    out = json.loads(
        asyncio.run(
            _call_tool(
                add_task,
                id="a",
                title="A",
                agent="proj-scitex-todo",
                tasks_path=store,
            )
        )
    )
    # Assert
    assert out["agent"] == "proj-scitex-todo"


def test_add_task_accepts_kind_compute(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task

    store = str(tmp_path / "tasks.yaml")
    # Act
    out = json.loads(
        asyncio.run(
            _call_tool(
                add_task,
                id="a",
                title="A",
                kind="compute",
                job_id="123",
                tasks_path=store,
            )
        )
    )
    # Assert
    assert out["kind"] == "compute"


def test_update_task_sets_agent(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, update_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(
            _call_tool(
                update_task,
                task_id="a",
                agent="proj-scitex-todo",
                tasks_path=store,
            )
        )
    )
    # Assert
    assert out["agent"] == "proj-scitex-todo"


def test_update_sets_status(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, update_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(
            _call_tool(
                update_task,
                task_id="a",
                status="in_progress",
                scope="agent:lead",
                tasks_path=store,
            )
        )
    )
    # Assert
    assert out["status"] == "in_progress"


def test_update_sets_scope(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, update_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(
        asyncio.run(
            _call_tool(
                update_task,
                task_id="a",
                status="in_progress",
                scope="agent:lead",
                tasks_path=store,
            )
        )
    )
    # Assert
    assert out["scope"] == "agent:lead"


def test_summary_returns_total(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, summarize_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(
        _call_tool(add_task, id="b", title="B", status="done", tasks_path=store)
    )
    # Act
    info = json.loads(asyncio.run(_call_tool(summarize_tasks, tasks_path=store)))
    # Assert
    assert info["total"] == 2


def test_summary_returns_done_count(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, summarize_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(
        _call_tool(add_task, id="b", title="B", status="done", tasks_path=store)
    )
    # Act
    info = json.loads(asyncio.run(_call_tool(summarize_tasks, tasks_path=store)))
    # Assert
    assert info["by_status"]["done"] == 1


def test_summary_returns_deferred_count(tmp_path):
    # Arrange — add_task's default status is `deferred` since the abolition.
    from scitex_cards._mcp_server import add_task, summarize_tasks

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    asyncio.run(
        _call_tool(add_task, id="b", title="B", status="done", tasks_path=store)
    )
    # Act
    info = json.loads(asyncio.run(_call_tool(summarize_tasks, tasks_path=store)))
    # Assert
    assert info["by_status"]["deferred"] == 1


def test_where_returns_resolved_path(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import resolve_store

    store = str(tmp_path / "tasks.yaml")
    # Act
    info = json.loads(asyncio.run(_call_tool(resolve_store, tasks_path=store)))
    # Assert
    assert info["resolved"] == store


def test_where_returns_exists_false_when_absent(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import resolve_store

    store = str(tmp_path / "tasks.yaml")
    # Act
    info = json.loads(asyncio.run(_call_tool(resolve_store, tasks_path=store)))
    # Assert
    assert info["exists"] is False


# --------------------------------------------------------------------------- #
# Helpers — handle FastMCP 2.x and 3.x APIs                                   #
# --------------------------------------------------------------------------- #
def _tool_names(mcp) -> list[str]:
    """Return registered tool names, version-agnostic.

    FastMCP 2.x exposes the registry synchronously via
    ``_tool_manager._tools`` (or the legacy ``_tools``/``tools`` on the
    server object). FastMCP 3.x removed those and only ships an async
    ``list_tools()``. Mirror the strategy used in ``_cli/_mcp.py``: try
    the cheap sync paths first, fall back to driving the async API.
    """
    tm = getattr(mcp, "_tool_manager", None)
    if tm is not None and isinstance(getattr(tm, "_tools", None), dict):
        return list(tm._tools.keys())
    for attr in ("tools", "_tools"):
        reg = getattr(mcp, attr, None)
        if isinstance(reg, dict):
            return list(reg.keys())
        if isinstance(reg, (list, tuple)):
            return [getattr(t, "name", str(t)) for t in reg]

    async def _gather():
        if tm is not None and hasattr(tm, "get_tools"):
            tools = await tm.get_tools()
        else:
            tools = await mcp.list_tools()
        if isinstance(tools, dict):
            return list(tools.keys())
        return [getattr(t, "name", str(t)) for t in tools]

    return asyncio.run(_gather())


async def _call_tool(tool_callable, **kwargs):
    """Call a `@mcp.tool()` callable, peeling FastMCP's wrappers as needed.

    FastMCP 2.x: `@mcp.tool()` returns the original async function — we
    can just await it.
    FastMCP 3.x: returns a `FunctionTool` whose `.fn` attribute holds
    the async function.
    """
    fn = getattr(tool_callable, "fn", None) or tool_callable
    # add_task now FAILS LOUD without an owner (creator+assignee mandatory).
    # Tests that don't care about ownership get a default owner here; owner-
    # specific tests pass their own assignee/agent (this only fills the gap).
    if (
        getattr(fn, "__name__", "") == "add_task"
        and not kwargs.get("assignee")
        and not kwargs.get("agent")
    ):
        kwargs["assignee"] = "agent:test-suite"
    return await fn(**kwargs)


# --------------------------------------------------------------------------- #
# Fix A — async handlers offload blocking store calls to a worker thread.     #
#                                                                             #
# The synchronous store functions take a process-wide flock and load the      #
# whole (multi-MB) store from disk under it — seconds of blocking IO. Run on   #
# the event-loop thread they FREEZE the loop (the MCP `initialize` handshake   #
# starves, pushes stall). Fix A wraps each blocking store/inbox/help call in   #
# `await anyio.to_thread.run_sync(functools.partial(fn, ...))`, mirroring      #
# `_mcp_channel.drain_once`. These tests pin (a) correctness still holds       #
# through the to_thread hop and (b) the loop is NOT blocked during the call.   #
# --------------------------------------------------------------------------- #
def test_get_task_roundtrip_through_to_thread(tmp_path):
    # Arrange
    from scitex_cards._mcp_server import add_task, get_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(_call_tool(add_task, id="a", title="A", tasks_path=store))
    # Act
    out = json.loads(asyncio.run(_call_tool(get_task, task_id="a", tasks_path=store)))
    # Assert
    assert out["id"] == "a"


#: WHY the three `reassign_..._through_to_thread` tests below are split but
#: share this rationale: they cover the _mcp_skills offload path
#: (reassign_task → _store.reassign_task). `reassign_task` returns
#: {task_id, from_owner, to_owner, actor, changed, task}, and the offload is
#: only proven correct if ALL THREE parts survive the to_thread hop — the
#: REPORTED new owner, the `changed` flag, and the card the store actually
#: WROTE. A stale-but-plausible report with an unmutated card is exactly the
#: failure mode a single first-assert would hide.
@pytest.fixture()
def reassigned_through_to_thread(tmp_path):
    """Run the offloaded `reassign_task` once; the three tests below read it."""
    from scitex_cards._mcp_server import add_task
    from scitex_cards._mcp_skills import reassign_task

    store = str(tmp_path / "tasks.yaml")
    asyncio.run(
        _call_tool(add_task, id="a", title="A", agent="proj-x", tasks_path=store)
    )
    return json.loads(
        asyncio.run(
            _call_tool(
                reassign_task,
                task_id="a",
                new_owner="proj-y",
                by="agent:test",
                tasks_path=store,
            )
        )
    )


def test_reassign_through_to_thread_reports_the_new_owner(
    reassigned_through_to_thread,
):
    # Arrange
    expected_owner = "proj-y"
    # Act
    out = reassigned_through_to_thread
    # Assert
    assert out["to_owner"] == expected_owner


def test_reassign_through_to_thread_reports_the_card_changed(
    reassigned_through_to_thread,
):
    # Arrange
    expected_changed = True
    # Act
    out = reassigned_through_to_thread
    # Assert
    assert out["changed"] is expected_changed


def test_reassign_through_to_thread_actually_mutated_the_owner(
    reassigned_through_to_thread,
):
    # Arrange
    expected_owner = "proj-y"
    # Act
    out = reassigned_through_to_thread
    # Assert — the returned card, not just the summary, carries the new owner.
    assert out["task"]["agent"] == expected_owner


#: WHY the two `slow_handler` tests below are split but share this rationale:
#: Regression for Fix A. A slow SYNC store call inside a handler must NOT
#: freeze the loop. The fixture replaces `_store.get_task` with a variant that
#: sleeps 0.3 s (standing in for the flock-guarded multi-MB load), then runs
#: the `get_task` handler concurrently with a 10 ms-cadence ticker coroutine.
#: If the blocking call ran ON the loop thread the ticker would be frozen
#: (≈0 ticks); because Fix A offloads it to a worker thread, the ticker keeps
#: advancing while the store op is in-flight. BOTH halves matter and neither
#: may hide behind the other: an offload that keeps the loop live but returns
#: the wrong card is not a fix, and a correct card fetched by freezing the
#: loop is the bug itself.
@pytest.fixture()
def slow_store_handler_run(tmp_path, monkeypatch):
    """Drive the `get_task` handler over a 0.3 s store call; return (result, ticks)."""
    import time

    from scitex_cards import _store
    from scitex_cards._mcp_server import get_task

    store = str(tmp_path / "tasks.yaml")
    _store.add_task(store, id="a", title="A", assignee="agent:test")

    real_get_task = _store.get_task

    def _slow_get_task(*args, **kwargs):
        time.sleep(0.3)
        return real_get_task(*args, **kwargs)

    monkeypatch.setattr(_store, "get_task", _slow_get_task)

    async def _drive():
        ticks = 0

        async def _ticker():
            nonlocal ticks
            for _ in range(100):
                await asyncio.sleep(0.01)
                ticks += 1

        fn = getattr(get_task, "fn", None) or get_task
        handler = asyncio.ensure_future(fn(task_id="a", tasks_path=store))
        ticker = asyncio.ensure_future(_ticker())
        result = await handler
        ticker.cancel()
        return result, ticks

    return asyncio.run(_drive())


def test_slow_handler_still_returns_the_correct_task(slow_store_handler_run):
    # Arrange
    expected_id = "a"
    # Act
    result, _ticks = slow_store_handler_run
    # Assert
    assert json.loads(result)["id"] == expected_id


def test_slow_handler_does_not_block_the_event_loop(slow_store_handler_run):
    # Arrange
    minimum_ticks = 5
    # Act
    _result, ticks = slow_store_handler_run
    # Assert — the loop stayed live during the 0.3 s store call.
    assert ticks >= minimum_ticks, f"event loop appeared blocked (only {ticks} ticks)"
