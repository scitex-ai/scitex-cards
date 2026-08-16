#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extension ports — the four interfaces that let `scitex-cards` stay
standalone while fleet-specific behaviour plugs in.

Architectural backbone per operator TG 9678 + lead a2a `fae53b8e`:
`scitex-cards` IS a standalone package that knows nothing about
`sac`, `a2a`, SSH-fanout, the 6-stream fleet — but exposes EXTENSION
PORTS through which that fleet-specific behaviour can plug in. Clean
architecture / dependency-inversion.

Three layers:

```
┌──────────────────────────────────────────────────────────────────┐
│  FLEET ADAPTERS — implement these ports against sac / SSH / a2a /│
│  git. Live OUTSIDE this package (e.g. `scitex-cards-fleet`).      │
└──────────────────────────────────────────────────────────────────┘
                                ↑ implements
┌──────────────────────────────────────────────────────────────────┐
│  EXTENSION PORTS (this module) — `typing.Protocol` interfaces.   │
└──────────────────────────────────────────────────────────────────┘
                                ↑ used-by
┌──────────────────────────────────────────────────────────────────┐
│  CORE — the rest of `scitex_cards`. ZERO knowledge of fleet/sac.  │
│  Consumes ports via constructor injection on `create_board(...)`.│
└──────────────────────────────────────────────────────────────────┘
```

The core ships with default no-op / single-host implementations in
:mod:`scitex_cards._adapters` (LocalFileSync, InProcessPubSub,
NullLiveness, OpenACL) so :command:`pip install scitex-cards` is
independently usable. Fleet deployments inject the real adapters.

See ADR-0006 in ``docs/adr/`` for the full design rationale,
deployment wiring examples, and the lead-approved Consequences.
"""

from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    # Import-time circular avoidance — Task is the shared payload type;
    # we only need it for static typing here.
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TaskSyncPort — where does the durable data live, and how does it sync?
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskSyncPort(Protocol):
    """Durable storage + cross-host sync of the task store.

    The core writes through this port; the adapter decides what
    "durable" means and how (if at all) state propagates across hosts.

    **Default impl**: :class:`scitex_cards._adapters.LocalFileSync` —
    atomic ruamel write to ``~/.scitex/todo/tasks.yaml``, no cross-host
    awareness. A single-user installing ``scitex-cards`` gets a working
    local board with this default.

    **Fleet impl** (lives outside this package, e.g.
    ``scitex_cards_fleet.GitTaskSyncAdapter``): write → git commit → push
    to GitHub. Other hosts pull. Cross-host pull is a side-loop in the
    adapter; the core never sees it.

    The adapter is free to add caching / batching / mtime-fingerprinting;
    the core only requires that ``load()`` and ``save()`` are atomic and
    that ``reload_if_changed()`` returns True when the underlying store
    has been mutated since the last ``load()``.
    """

    def load(self) -> list[dict]:
        """Return the current task list (oldest-first document order).

        Each task is a plain dict matching the schema validated by
        :func:`scitex_cards._model.load_tasks`. The adapter MAY enforce
        additional validation; the core's :class:`Task` dataclass is the
        canonical shape.
        """
        ...

    def save(self, tasks: list[dict]) -> None:
        """Persist the (already-mutated) task list atomically.

        MUST round-trip preserve any hand-written YAML comments and key
        order in the existing store (ruamel-style). MUST be safe to call
        concurrently with another writer on the same host (file-lock or
        equivalent).
        """
        ...

    def reload_if_changed(self) -> bool:
        """Detect external mutations; return True if the store changed.

        Used by the board's AutoRefresh poll loop. The default impl
        compares mtime against the last-loaded snapshot; fleet impls MAY
        consult a sidecar `/agents-rev` style fingerprint endpoint.
        """
        ...


# ---------------------------------------------------------------------------
# NotificationPort — pub/sub for "task changed" events
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationPort(Protocol):
    """Publish + subscribe to task-change events.

    The core publishes on every mutation (status flip, blocker change,
    comment append, …). Subscribers are decoupled from the core via the
    channel name string; the adapter routes the event to the right
    place.

    **Default impl**: :class:`scitex_cards._adapters.InProcessPubSub` —
    a simple in-process callback registry. Fine for a single-host
    standalone installation.

    **Fleet impl** (e.g. ``scitex_cards_fleet.SacChannelNotificationAdapter``):
    publishes on ``scitex-cards:task:<project>/<local-id>`` over the sac
    a2a/channel bus. The wake-generalize + empty-beacon-fix work in
    ``scitex-agent-container`` makes this reliable for waking idle
    agent subscribers — every task update can wake the relevant agent
    (HANDOFF.md north-star pillar #4 synergy).

    Channel naming convention (recommended, not enforced):

    .. code-block:: text

        scitex-cards:task:<project>/<local-id>     — a specific task changed
        scitex-cards:task:<project>/*              — any task in a project
        scitex-cards:task:*                        — every task (UI firehose)
    """

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish an event on the named channel.

        Payload shape (convention):

        .. code-block:: python

            {
                "task_id": "<project>/<local-id>",
                "changes": {<field>: <new-value>, ...},
                "ts": "<ISO-8601 UTC>",
                "actor": "<agent or operator name>",
            }
        """
        ...

    def subscribe(
        self, channel: str, handler: Callable[[dict[str, Any]], None]
    ) -> None:
        """Register ``handler`` to be invoked with the payload each time
        an event is published on ``channel`` (or any channel matching the
        adapter's glob semantics; the default impl supports literal
        match + suffix ``*`` glob).

        Implementations SHOULD be idempotent on duplicate subscription
        (same handler + same channel registered twice = single delivery
        per event).
        """
        ...


# ---------------------------------------------------------------------------
# LivenessPort — fleet agent status feed for the liveness panel
# ---------------------------------------------------------------------------


@runtime_checkable
class LivenessPort(Protocol):
    """Surface "are these agents alive?" state for the FE liveness panel.

    The core renders the colored dots / Fleet tab from this output; HOW
    the state is gathered is the adapter's problem.

    **Default impl**: :class:`scitex_cards._adapters.NullLiveness` —
    returns ``[]``. The FE renders an empty Fleet tab + a "watcher not
    installed" hint. Acceptable for standalone single-user installs.

    **Fleet impl** (e.g.
    ``scitex_cards_fleet.SacAgentsLivenessAdapter``): SSH-fanout per
    fleet-liveness task's ADR-0002 to ``sac agents list --json`` on each
    peer host, aggregated into one return value with UNREACHABLE markers
    for peers that fail (per the fail-loud principle from ADR-0005).
    """

    def list_agents(self) -> list[dict[str, Any]]:
        """Return one entry per agent the adapter knows about.

        Entry shape (convention, the FE depends on these keys):

        .. code-block:: python

            {
                "name": "scitex-cards",
                "host": "ywata-note-win",
                "status": "running" | "idle" | "working" | "stopped" | "unreachable",
                "heartbeat": "<ISO-8601 UTC of last heartbeat>",
                "current_task": "<one-line summary>" | None,
                "last_activity": "<relative string, e.g. '12s ago'>",
                "context_pct": <0-100 int or None>,
                "quota_pct": <0-100 int or None>,
                "as_of": "<ISO-8601 UTC of when this row was sampled>",
                "error": <only on status='unreachable'; the SSH/transport error>,
            }
        """
        ...


# ---------------------------------------------------------------------------
# IdentityACLPort — "can ACTOR perform ACTION on TASK?"
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentityACLPort(Protocol):
    """Answer access-control queries before any write.

    The core consults this on every mutation; the adapter decides
    authority. The "actor" string is whatever identity scheme the
    adapter uses (an agent name, an OS username, a Gitea user, etc.).

    **Default impl**: :class:`scitex_cards._adapters.OpenACL` — everyone
    can do everything. Acceptable for single-user installs.

    **Fleet impl** (e.g.
    ``scitex_cards_fleet.SacFleetGroupsACLAdapter``): consults
    ``sac fleet groups list --json`` plus a future per-task
    ``acl: {read: [<groups>], write: [<groups>]}`` field. Wires to
    task #2 (``e1-sac-fleet-acl``) when that lands. Until then, the
    fleet runs with OpenACL too — same default as standalone.
    """

    def can_read(self, actor: str, task: dict) -> bool:
        """True iff ``actor`` may read ``task``."""
        ...

    def can_write(self, actor: str, task: dict, field: str) -> bool:
        """True iff ``actor`` may write ``field`` on ``task``."""
        ...


# ===========================================================================
# Agent career — the `host@name` identity join key + AgentDirectoryPort
# ===========================================================================
#
# ADR-0009 ("task-driven-feedback / four careers"), Agent-career section.
#
# Two single-sources-of-truth meet here:
#
#   - scitex-agent-container (sac) is SSOT for agent RUNTIME — whether an
#     agent exists / is running / is stopped on a given host.
#   - scitex-cards is SSOT for board MEMBERSHIP — who may be an assignee,
#     collaborator, or subscriber on the board (HUMANS included; humans
#     have a board identity but no sac runtime).
#
# They join on the canonical agent id **`host@name`** and connect via the
# :class:`AgentDirectoryPort` below (an entry-point provider). sac exposes
# an agent-directory provider; scitex-cards ENRICHES its board when a
# provider is installed and works STANDALONE otherwise. Rows from any
# provider are deduped by their `host@name` join key.
#
# This is the scitex-cards SIDE only: the Protocol, the standalone-safe
# :class:`EmptyAgentDirectory` default, the identity helpers, and the
# resolver. The sac-side provider that implements the port is a separate
# package concern (it registers under :data:`AGENT_DIRECTORY_GROUP`).


#: Entry-point group an external agent-directory provider registers under.
#: A provider (e.g. scitex-agent-container) ships a zero-arg factory that
#: returns an :class:`AgentDirectoryPort`-shaped object::
#:
#:     [project.entry-points."scitex_cards.agent_directory"]
#:     sac = "scitex_agent_container.todo_directory:provider"
#:
#: Mirrors :data:`scitex_cards._hooks.ENTRY_POINT_GROUP` ("scitex_cards.hooks").
AGENT_DIRECTORY_GROUP = "scitex_cards.agent_directory"


class AgentIdentityError(ValueError):
    """A caller passed a malformed agent identity string.

    Raised by :func:`canonical_agent_id` / :func:`parse_agent_id` on an
    empty / whitespace-only name or a structurally invalid ``host@name``.
    The message always echoes the offending value (fail-loud per the
    SciTeX constitution).
    """


def canonical_agent_id(name: str, host: str | None = None) -> str:
    """Return the canonical agent id in **`host@name`** form.

    The canonical join key between scitex-cards board membership and the
    sac agent runtime (ADR-0009). One agent may run on exactly one host,
    so the pair ``(host, name)`` uniquely identifies it.

    Resolution rules (in order):

    1. If ``name`` already contains ``@``, it is treated as an
       *already-qualified* ``host@name`` and returned **as-is** after
       validation — passing an already-joined id through this function is
       idempotent (``canonical_agent_id("h@a") == "h@a"``). An explicit
       ``host`` argument is ignored in this case (the embedded host wins);
       this keeps the function a pure normaliser rather than a re-joiner.
    2. Else, if ``host`` is truthy (non-empty after strip), return
       ``f"{host}@{name}"``.
    3. Else (no host known), fall back to the **bare** ``name``. A bare id
       is valid and round-trips through :func:`parse_agent_id` with an
       empty host — it represents an agent whose host is not yet known
       (e.g. a board-only human member, or a row before the runtime
       provider has reported in).

    Fail-loud: an empty / whitespace-only ``name`` raises
    :class:`AgentIdentityError`. A ``name`` containing ``@`` is validated
    as a well-formed ``host@name`` (non-empty host AND non-empty name, a
    single ``@``) before being returned.

    Parameters
    ----------
    name : str
        The agent's short name, OR an already-qualified ``host@name``.
    host : str, optional
        The host the agent runs on. Ignored when ``name`` already
        contains ``@``.

    Examples
    --------
    >>> canonical_agent_id("worker-1", "ywata-note-win")
    'ywata-note-win@worker-1'
    >>> canonical_agent_id("ywata-note-win@worker-1")  # already-qualified
    'ywata-note-win@worker-1'
    >>> canonical_agent_id("worker-1")  # no host → bare fallback
    'worker-1'
    """
    if name is None or not str(name).strip():
        raise AgentIdentityError(
            f"agent name must be a non-empty string (got {name!r})"
        )
    name = str(name).strip()
    if "@" in name:
        # Already-qualified: validate as host@name and return as-is.
        host_part, sep, name_part = name.partition("@")
        if not sep or not host_part.strip() or not name_part.strip():
            raise AgentIdentityError(
                f"malformed already-qualified agent id {name!r}; expected "
                "'host@name' with a non-empty host and name"
            )
        if "@" in name_part:
            raise AgentIdentityError(
                f"malformed agent id {name!r}; expected a single '@' "
                "separating host and name"
            )
        return f"{host_part.strip()}@{name_part.strip()}"
    if host is not None and str(host).strip():
        return f"{str(host).strip()}@{name}"
    return name


def parse_agent_id(host_at_name: str) -> tuple[str, str]:
    """Split a canonical agent id into its ``(host, name)`` pair.

    Inverse of :func:`canonical_agent_id`. A bare id (no ``@``) yields an
    **empty** host string — ``parse_agent_id("worker-1") == ("", "worker-1")``
    — so callers can branch on ``host == ""`` to mean "host unknown".

    Fail-loud: an empty / whitespace-only input, or a malformed
    ``host@name`` (empty host, empty name, or more than one ``@``), raises
    :class:`AgentIdentityError` echoing the bad value.

    Examples
    --------
    >>> parse_agent_id("ywata-note-win@worker-1")
    ('ywata-note-win', 'worker-1')
    >>> parse_agent_id("worker-1")
    ('', 'worker-1')
    """
    if host_at_name is None or not str(host_at_name).strip():
        raise AgentIdentityError(
            f"agent id must be a non-empty string (got {host_at_name!r})"
        )
    raw = str(host_at_name).strip()
    if "@" not in raw:
        return ("", raw)
    host_part, _, name_part = raw.partition("@")
    if "@" in name_part:
        raise AgentIdentityError(
            f"malformed agent id {raw!r}; expected a single '@' "
            "separating host and name"
        )
    if not host_part.strip() or not name_part.strip():
        raise AgentIdentityError(
            f"malformed agent id {raw!r}; expected 'host@name' with a "
            "non-empty host and name"
        )
    return (host_part.strip(), name_part.strip())


@dataclass
class AgentInfo:
    """One agent row surfaced by an :class:`AgentDirectoryPort`.

    The shared shape scitex-cards uses to enrich board membership with
    runtime facts from a provider. The ``host_at_name`` field is the
    canonical join key (see :func:`canonical_agent_id`) and the dedup key
    (see :func:`dedup_agents`).

    Attributes
    ----------
    host_at_name : str
        Canonical ``host@name`` join key. REQUIRED and the only field the
        core relies on for identity; everything else is descriptive.
    name : str
        The agent's short name (the part after ``@``).
    host : str
        The host the agent runs on (the part before ``@``); ``""`` when
        the host is unknown (a bare id).
    status : str | None
        Runtime status as the provider reports it — conventionally one of
        ``"running"`` / ``"idle"`` / ``"stopped"`` / ``"unknown"`` —
        or ``None`` when the provider declines to say.
    extra : dict
        Open bag for provider-specific fields (heartbeat, current task,
        quota %, …). The core never interprets these; downstream
        consumers may. Defaults to an empty dict.
    """

    host_at_name: str
    name: str
    host: str
    status: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentDirectoryPort(Protocol):
    """Read-only feed of agent-runtime rows to enrich board membership.

    scitex-cards is SSOT for board *membership*; scitex-agent-container is
    SSOT for agent *runtime*. This port is how the runtime SSOT feeds the
    board so a member row can show "running / stopped" without scitex-cards
    importing sac (ADR-0009). The join key is ``host@name``.

    **Default impl**: :class:`EmptyAgentDirectory` — ``list_agents()``
    returns ``[]`` and ``get_agent()`` returns ``None``. Installed by
    default so the board works STANDALONE when no provider is present
    (mirrors how :class:`scitex_cards._adapters.OpenACL` is the default
    :class:`IdentityACLPort`).

    **Provider impl** (lives OUTSIDE this package, e.g. in
    scitex-agent-container): registers a zero-arg factory under
    :data:`AGENT_DIRECTORY_GROUP` that returns an object satisfying this
    Protocol — typically wrapping ``sac agents list --json``. Discovered
    by :func:`resolve_agent_directory`.

    The port is a LIBRARY SEAM, not a board verb: there is intentionally
    no MCP tool for it. Membership stays authoritative on the todo side;
    the directory only annotates.
    """

    def list_agents(self) -> list[AgentInfo]:
        """Return every agent the provider knows about (may be empty)."""
        ...

    def get_agent(self, host_at_name: str) -> AgentInfo | None:
        """Return the agent whose canonical id is ``host_at_name``.

        ``None`` when the provider has no such agent.
        """
        ...


class EmptyAgentDirectory:
    """Standalone-safe default :class:`AgentDirectoryPort`.

    Knows about zero agents — the board runs with no runtime enrichment
    and never depends on a provider being installed. This is what
    :func:`resolve_agent_directory` returns when no entry-point provider
    is registered.

    Examples
    --------
    >>> d = EmptyAgentDirectory()
    >>> d.list_agents()
    []
    >>> d.get_agent("anyhost@anyname") is None
    True
    """

    def list_agents(self) -> list[AgentInfo]:
        return []

    def get_agent(self, host_at_name: str) -> AgentInfo | None:  # noqa: ARG002
        return None


def dedup_agents(agents: Iterable[AgentInfo]) -> list[AgentInfo]:
    """De-duplicate ``agents`` by their ``host_at_name`` join key.

    **First wins**: when two rows share a ``host_at_name``, the first one
    encountered is kept and later duplicates are dropped. Order is
    otherwise preserved (stable). Used to merge rows from one or more
    providers onto the board without double-listing an agent.

    Examples
    --------
    >>> a = AgentInfo("h@x", "x", "h", "running")
    >>> b = AgentInfo("h@x", "x", "h", "stopped")  # same key, later
    >>> [r.status for r in dedup_agents([a, b])]
    ['running']
    """
    seen: set[str] = set()
    out: list[AgentInfo] = []
    for agent in agents:
        key = agent.host_at_name
        if key in seen:
            continue
        seen.add(key)
        out.append(agent)
    return out


def resolve_agent_directory(
    entry_points: Iterable | None = None,
) -> AgentDirectoryPort:
    """Return an installed agent-directory provider, or the empty default.

    Discovers a provider registered under :data:`AGENT_DIRECTORY_GROUP`
    and calls its zero-arg factory to obtain the port object. Returns
    :class:`EmptyAgentDirectory` when no provider is installed — so the
    board is always usable STANDALONE.

    Multi-provider resolution: if more than one provider is registered,
    the one whose entry-point name sorts FIRST lexicographically wins
    (deterministic + stable across packaging-metadata implementations).
    A provider whose factory fails to load or raises is logged and
    skipped — one broken provider must not break the board (mirrors
    :func:`scitex_cards._hooks._run_plugins`).

    Parameters
    ----------
    entry_points : iterable, optional
        Explicit set of entry-point-shaped objects (each with a ``.name``
        attribute and a ``.load()`` method returning the zero-arg
        factory) to use instead of packaging-metadata discovery. ``None``
        (the default) reads the real :data:`AGENT_DIRECTORY_GROUP` group
        via :func:`_iter_agent_directory_entry_points`. This is the
        in-process injection seam (mirrors
        :func:`scitex_cards._hooks._run_plugins`'s ``entry_points=``): tests
        pass a concrete list of real fake entry points — no monkeypatch of
        ``importlib.metadata`` required (PA-306-compliant).
    """
    eps = _iter_agent_directory_entry_points() if entry_points is None else entry_points
    # Sort by entry-point name (lex asc) so multi-provider resolution is
    # deterministic + stable across packaging-metadata implementations.
    for ep in sorted(eps, key=lambda e: e.name):
        name = ep.name
        try:
            factory = ep.load()
            provider = factory()
        except Exception as exc:  # noqa: BLE001 — packaging/provider surprises
            logger.warning(
                "scitex_cards.agent_directory provider %r failed to load: %s",
                name,
                exc,
            )
            continue
        return provider
    return EmptyAgentDirectory()


def _iter_agent_directory_entry_points() -> Iterable:
    """Yield entry points in :data:`AGENT_DIRECTORY_GROUP`.

    Wraps the cross-version ``importlib.metadata`` surface exactly like
    :func:`scitex_cards._hooks._iter_entry_points` does for the hooks
    group. Returns ``[]`` on any packaging surprise.
    """
    try:
        eps = importlib.metadata.entry_points()
    except Exception:  # noqa: BLE001 — packaging surprises
        return []
    # 3.10+: eps is an EntryPoints, supports .select(group=)
    select = getattr(eps, "select", None)
    if callable(select):
        return select(group=AGENT_DIRECTORY_GROUP)
    # 3.9 fallback: dict-like keyed by group.
    return eps.get(AGENT_DIRECTORY_GROUP, [])


__all__ = [
    "TaskSyncPort",
    "NotificationPort",
    "LivenessPort",
    "IdentityACLPort",
    # Agent career — host@name identity join key + agent-directory port.
    "AGENT_DIRECTORY_GROUP",
    "AgentDirectoryPort",
    "AgentIdentityError",
    "AgentInfo",
    "EmptyAgentDirectory",
    "canonical_agent_id",
    "dedup_agents",
    "parse_agent_id",
    "resolve_agent_directory",
]
