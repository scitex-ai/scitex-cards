#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The FastMCP instance and its shared constants — a LEAF module.

WHY THIS MODULE EXISTS: it holds the two things every MCP tool module needs
(`mcp`, to decorate with; `_ENUM_FIELDS`, to validate against) and it imports
NOTHING that imports it back. That is the whole job.

Before this, `mcp` lived in `_mcp_server`, and every satellite tool module
(`_mcp_write`, `_mcp_relations`, `_mcp_skills`, `_mcp_channel`) did
`from ._mcp_server import mcp`. But `_mcp_server` imports the satellites at its
tail — it must, because importing them is what REGISTERS their tools. So the
graph was a cycle, and like every cycle it worked in exactly one direction:

    import scitex_cards._mcp_server   -> runs to the tail, pulls in _mcp_write,
                                        which imports _mcp_server back (already
                                        in sys.modules, `mcp` bound). FINE.

    import scitex_cards._mcp_write    -> imports _mcp_server, which runs to its
                                        tail and asks _mcp_write for `add_task`
                                        — not defined yet, _mcp_write is 30 lines
                                        in. ImportError. NOT FINE.

It stayed invisible because nothing imports a satellite first. That is luck.
(Found 2026-07-14 by a test that imports every module first, in a subprocess.)

WHY NOT A LAZY RE-EXPORT, as used for the `_model` / `_store_write` cycle: the
tail import in `_mcp_server` is not a re-export, it is a SIDE EFFECT — the
`@mcp.tool` decorators run at import. Deferring it to first attribute access
would leave `add_task` and `update_task` UNREGISTERED on a server that looked
healthy. A cycle whose import has a side effect must be broken by extracting the
shared symbol, not by making the import lazy.

DO NOT move `mcp` back into `_mcp_server`, and do not import `_mcp_server` from
here. `tests/scitex_cards/test__import_order.py` imports every module first, in a
subprocess, and will fail if this becomes a cycle again.
"""

from __future__ import annotations

try:
    from fastmcp import FastMCP
except ImportError as _exc:  # pragma: no cover — exercised in the doctor test
    raise ImportError(
        "scitex-cards MCP tools require the [mcp] extra. Install with:\n"
        "  pip install 'scitex-cards[all]'"
    ) from _exc

from ._channel_identity import resolve_agent_id_optional
from ._mcp_instructions import build_instructions
from ._store_enums import CLEARABLE_ENUM_FIELDS, UNCLEARABLE_ENUM_FIELDS

# Closed-enum fields — the store owns what `""` means on each of them, so this
# surface must NOT pre-translate them (see `update_task`). Sourced from
# `_store_enums` rather than re-listed, so the two cannot drift.
_ENUM_FIELDS: frozenset[str] = frozenset(
    CLEARABLE_ENUM_FIELDS + UNCLEARABLE_ENUM_FIELDS
)

# The instructions name THIS agent's OWN scope, interpolated from its resolved
# identity ($SCITEX_TODO_AGENT_ID) — never a hard-coded example, which is how
# every agent came to be taught the scope of the long-dead `proj-scitex-todo`.
# An UNRESOLVED identity names no scope at all; see `_mcp_instructions`.
mcp = FastMCP(
    name="scitex-cards",
    instructions=build_instructions(resolve_agent_id_optional()),
)

__all__ = ["mcp", "_ENUM_FIELDS"]
