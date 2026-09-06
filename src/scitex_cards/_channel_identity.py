#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent-identity resolution for the scitex-cards channel server.

Answers "whose inbox does this channel drain?" — extracted from
:mod:`scitex_cards._mcp_channel` (which re-exports both functions for
back-compat) to keep that orchestrator under its line budget. Pure logic,
no MCP/session coupling: precedence-based resolution with a fail-loud policy
and deprecated-env-var tolerance.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Env var carrying the agent identity — same key the rest of the package
#: uses (``scitex_cards._store.ENV_AGENT``).
_ENV_AGENT = "SCITEX_CARDS_AGENT_ID"

#: previous name of :data:`_ENV_AGENT`. Renamed 2026-07-02. The CURRENT var
#: wins: when it resolves to a valid id we IGNORE a stale export of this old
#: name (loud warning, no raise). We only fail loud when the current var is
#: absent/invalid AND this old name is still set — a genuine reliance on the
#: renamed-away var the operator must migrate.
_ENV_AGENT_DEPRECATED = "SCITEX_CARDS_AGENT"


def resolve_agent_id(arg: str | None = None) -> str:
    """Resolve the agent id; FAIL LOUD when unresolved.

    Precedence: explicit ``arg`` → ``$SCITEX_CARDS_AGENT_ID``. Deliberately does
    NOT fall back to ``getpass.getuser()`` / ``"unknown"`` — a channel server
    that drains "unknown"'s inbox would silently deliver the wrong agent's
    notifications. The operator mandate (constitution rule 2 "fail fast and
    fail loud, NO silent fallbacks") requires a real identity here.

    Deprecated-var tolerance: the CURRENT var WINS. When ``arg`` /
    ``$SCITEX_CARDS_AGENT_ID`` yields a valid id we return it even if the stale
    ``$SCITEX_CARDS_AGENT`` is also exported — we only log a loud warning that
    the old name is ignored. We fail loud on the old name ONLY when the current
    var is absent/invalid (a genuine reliance on the renamed-away var).

    Raises
    ------
    RuntimeError
        When the id resolves to empty / the ``"unknown"`` sentinel (and the
        deprecated ``$SCITEX_CARDS_AGENT`` is set → migrate hint; otherwise the
        generic unresolved hint), or to an unexpanded ``$``-placeholder.
    """
    deprecated_set = os.environ.get(_ENV_AGENT_DEPRECATED) is not None
    resolved = (arg or os.environ.get(_ENV_AGENT) or "").strip()
    # An id that still looks like an env placeholder (e.g. "$SCITEX_CARDS_AGENT_ID"
    # or "${SCITEX_CARDS_AGENT_ID}") means the launcher passed the literal text
    # instead of expanding it — Claude Code's .mcp.json only expands the
    # ``${VAR}`` (braces) form, never bare ``$VAR``. Draining an inbox keyed by
    # that literal silently delivers nothing; fail loud instead of polling a
    # dead key.
    if resolved.startswith("$"):
        raise RuntimeError(
            f"scitex-cards mcp channel: agent id is an unexpanded placeholder "
            f"({resolved!r}) — the launcher passed the literal text instead of "
            "the value. In .mcp.json use the brace form "
            '"SCITEX_CARDS_AGENT_ID": "${SCITEX_CARDS_AGENT_ID}" (Claude Code does '
            'not expand bare "$VAR"), or pass a literal --agent <id>.'
        )
    if resolved and resolved != "unknown":
        # The current var yields a VALID id → it WINS. If the deprecated name is
        # ALSO exported (the stale-injector incident) warn LOUD but do NOT raise:
        # a correctly-configured agent must not have its digest push disabled by
        # a leftover old-name export.
        if deprecated_set:
            logger.warning(
                "%s is set but was renamed to %s; the stale value is IGNORED "
                "in favor of the current %s. Unset %s to silence this warning.",
                _ENV_AGENT_DEPRECATED,
                _ENV_AGENT,
                _ENV_AGENT,
                _ENV_AGENT_DEPRECATED,
            )
        return resolved
    # The current var did NOT yield a valid id. If the deprecated name is still
    # set the operator is genuinely relying on the renamed-away var → fail loud
    # pointing at the new name (no silent honouring of the old one).
    if deprecated_set:
        raise RuntimeError(
            f"{_ENV_AGENT_DEPRECATED} was renamed to {_ENV_AGENT}; "
            f"unset the old var and set {_ENV_AGENT}=<your-agent> instead "
            f"(the old name is no longer honoured)."
        )
    raise RuntimeError(
        "scitex-cards mcp channel: agent id unresolved — set "
        "SCITEX_CARDS_AGENT_ID=<your-agent> or pass --agent <id>. The channel "
        "server must drain a REAL agent's inbox; no silent fallback to a "
        "blank/'unknown' id."
    )


def resolve_agent_id_optional(arg: str | None = None) -> str | None:
    """Like :func:`resolve_agent_id` but returns ``None`` instead of raising.

    For the UNIFIED server (``scitex-cards mcp start``): when no identity is
    configured we still serve the card tools — only the digest push is disabled.
    A resolvable id enables the push; an unresolved one logs a loud warning and
    returns ``None`` so the caller runs tools-only rather than dying.
    """
    try:
        return resolve_agent_id(arg)
    except Exception as exc:  # noqa: BLE001 — absence ⇒ tools-only, not fatal
        logger.warning(
            "scitex-cards mcp: %s — serving tools only, digest push disabled.", exc
        )
        return None


__all__ = ["resolve_agent_id", "resolve_agent_id_optional"]

# EOF
