#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the doc-41 MCP/Python parity refresh.

Doc 41 (``41_cli-mcp-gap-analysis.md``) §§C–D described the MCP and
Python surfaces as they were in June: ``add_task`` / ``update_task``
mirroring six legacy fields, ``comment_task`` / ``reopen_task`` queued
as new tools, and a six-verb ``__all__``. Every one of those shipped
since — a consuming agent following the gap list would hand-roll
bridges for tools that already exist, the same dead-instruction class
as the CLI-help / hook-script / skill-docs sweep. §D keeps exactly one
honest follow-up: ``validate_store`` for a future CLI ``validate``
verb (today only ``validate-health`` exists).

These pins read the bundled doc and inspect live signatures / exports
only: no store, no PostgreSQL — they run in containers where the
store-backed suite refuses via the fail-LOUD guard.
"""

from __future__ import annotations

import inspect

import scitex_cards
from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]
from scitex_cards import _mcp_server, _mcp_write
from scitex_cards import _store_mutate

_DOC = "41_cli-mcp-gap-analysis.md"

#: The June-era claim that must not come back: the MCP pair mirrors six
#: legacy fields and the verb tools are still queued.
_STALE_CLAIM = "mirror only the legacy CLI fields"

#: The stale six-verb ``__all__`` fence, verbatim from the old §D.
_STALE_FENCE_LINE = "add_task, complete_task, list_tasks, resolve_store, summarize_tasks,"

#: Shipped MCP field surface the refreshed §C must name.
_SHIPPED_MCP_FIELDS = (
    "status",
    "blocker",
    "kind",
    "project",
    "deadline",
    "depends_on",
    "blocks",
)


def _doc41() -> str:
    return (_skills_root() / _DOC).read_text(encoding="utf-8")


def _section_c(text: str) -> str:
    return text.split("## C.")[1].split("## D.")[0]


def _section_d(text: str) -> str:
    return text.split("## D.")[1].split("## E.")[0]


def test_no_legacy_only_claim_in_section_c():
    assert _STALE_CLAIM not in _section_c(_doc41())


def test_detector_is_not_vacuous():
    assert _STALE_CLAIM in (
        "MCP `add_task` / `update_task` " + _STALE_CLAIM + " (scope,"
    )
    assert _STALE_FENCE_LINE in (
        "x,\n" + _STALE_FENCE_LINE + "\nupdate_task"
    )


def test_section_c_names_the_shipped_field_surface():
    section = _section_c(_doc41())
    for field in _SHIPPED_MCP_FIELDS:
        assert field in section, field
    assert "Convention A" in section


def test_mcp_add_task_carries_the_full_surface():
    params = inspect.signature(_mcp_write.add_task).parameters
    for field in _SHIPPED_MCP_FIELDS:
        assert field in params, field


def test_mcp_update_task_carries_the_full_surface():
    params = inspect.signature(_mcp_write.update_task).parameters
    for field in _SHIPPED_MCP_FIELDS:
        assert field in params, field


def test_mcp_verb_tools_are_live():
    assert callable(_mcp_server.comment_task)
    assert callable(_mcp_server.reopen_task)
    section = _section_c(_doc41())
    assert "comment_task" in section
    assert "reopen_task" in section


def test_no_stale_all_fence_in_section_d():
    assert _STALE_FENCE_LINE not in _section_d(_doc41())


def test_public_api_exports_comment_and_reopen():
    assert "comment_task" in scitex_cards.__all__
    assert "reopen_task" in scitex_cards.__all__
    section = _section_d(_doc41())
    assert "comment_task" in section
    assert "reopen_task" in section


def test_update_task_accepts_arbitrary_fields():
    params = inspect.signature(_store_mutate.update_task).parameters
    assert any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def test_validate_store_stays_the_honest_follow_up():
    section = _section_d(_doc41())
    assert "validate_store" in section
    assert not hasattr(scitex_cards, "validate_store")
