#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical card-event model + single emit seam (foundation C1).

This is **C1** of the card-event / notification foundation epic: a
TYPED canonical event plus a single :func:`emit` that wraps the existing
hook bus (:func:`scitex_cards._hooks.dispatch_event`). It is **pure model
+ a thin emit hook** — there is NO delivery / notification logic here
(that arrives in a later card). The point of C1 is to give every future
PRODUCER (push-hook, merge-Action, release pipeline, deploy step, …)
ONE shape to emit, so consumers can discriminate uniformly.

## Why a typed layer on top of the dict bus

:mod:`scitex_cards._hooks` already fans a *dict* event to built-in
handlers + ``scitex_cards.hooks`` entry-point plugins; ``comment_task``
already emits a ``{"kind": "card-message", ...}`` event through it. C1
does NOT replace that — it adds a typed :class:`Event` whose
:meth:`Event.to_dict` produces a *self-describing envelope*:

.. code-block:: python

    {"kind": "card-event", "type": "<EventType>", ...}

The stable ``"kind": "card-event"`` marker lets existing dict-consumers
tell a C1 event apart from the legacy ``push`` / ``done`` /
``card-message`` / ``unblock`` kinds — the back-compat contract is
preserved: this module NEVER mutates or emits those legacy kinds, and
``comment_task``'s current event is untouched.

## Fail-loud construction (SciTeX constitution)

The ``type`` field MUST be one of :data:`EVENT_TYPES`; an unknown value
raises :class:`EventValidationError` echoing the bad value. Construction
fails loud — but :func:`emit` NEVER raises to the caller (it wraps the
bus the same way ``dispatch_event`` swallows plugin errors), so a
producer is never broken by emit.
"""

from __future__ import annotations

import dataclasses
import scitex_logging as slogging
from typing import Any, Iterable

from ._store import _utc_now_iso

logger = slogging.getLogger(__name__)


#: Stable envelope discriminator. Every dict produced by
#: :meth:`Event.to_dict` carries ``"kind": CARD_EVENT_KIND`` so existing
#: dict-consumers (which switch on ``kind``) can tell a C1 canonical
#: event apart from the legacy ``push`` / ``done`` / ``card-message`` /
#: ``unblock`` kinds. Do NOT reuse a legacy kind here — that would break
#: the discrimination contract.
CARD_EVENT_KIND = "card-event"


class EventType:
    """Closed set of canonical card-event types.

    A namespace of string constants (NOT a real ``enum.Enum`` — the wire
    value IS the string, and producers/consumers compare raw strings on
    the dict envelope). :data:`EVENT_TYPES` is the validation frozenset.
    """

    CREATED = "created"
    REASSIGNED = "reassigned"
    REASSIGNED_BATCH = "reassigned_batch"
    STATUS_CHANGED = "status_changed"
    COMMENTED = "commented"
    COMPLETED = "completed"
    RANK_CHANGED = "rank_changed"
    COMMITTED = "committed"
    PUSHED = "pushed"
    MERGED = "merged"
    RELEASED = "released"
    PULLED = "pulled"
    DEPLOYED = "deployed"


#: Frozenset of every valid :class:`Event.type` value. Validation uses
#: this; producers/tests can iterate it for exhaustive coverage.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventType.CREATED,
        EventType.REASSIGNED,
        EventType.REASSIGNED_BATCH,
        EventType.STATUS_CHANGED,
        EventType.COMMENTED,
        EventType.COMPLETED,
        EventType.RANK_CHANGED,
        EventType.COMMITTED,
        EventType.PUSHED,
        EventType.MERGED,
        EventType.RELEASED,
        EventType.PULLED,
        EventType.DEPLOYED,
    }
)


class EventValidationError(ValueError):
    """A producer built an :class:`Event` with an out-of-set ``type``.

    Raised by :meth:`Event.__post_init__` — fail-loud per the SciTeX
    constitution. The message echoes the bad value so the producer can
    fix its emit site.
    """


@dataclasses.dataclass
class Event:
    """Canonical card-event — the ONE shape every C1 producer emits.

    Parameters
    ----------
    type : str
        Required. MUST be one of :data:`EVENT_TYPES`; otherwise
        :class:`EventValidationError` is raised at construction.
    card_id : str | None
        The board card this event concerns, if any. Repo-level events
        (release / deploy / a push not tied to a card) may leave it None.
    actor : str | None
        Who did it (agent id, operator, merger, …).
    ts : str
        UTC ISO-8601 timestamp. Auto-stamped via
        :func:`scitex_cards._store._utc_now_iso` when not supplied, so two
        producers never disagree on the timestamp format.
    repo : str | None
        ``owner/repo`` for git-flavoured events.
    branch : str | None
        Branch name for push / commit events.
    pr_url : str | None
        Pull-request URL for merge / done events.
    sha : str | None
        Commit sha for commit / push events.
    version : str | None
        Release version / tag for release events.
    extra : dict
        Free-form additional payload (kept under one key so the envelope
        top-level stays a stable, known set of fields).
    """

    type: str
    card_id: str | None = None
    actor: str | None = None
    ts: str | None = None
    repo: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    sha: str | None = None
    version: str | None = None
    extra: dict = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise EventValidationError(
                f"unknown event type {self.type!r}; must be one of "
                f"{sorted(EVENT_TYPES)}"
            )
        if self.ts is None:
            # Auto-stamp with the repo's canonical UTC-ISO helper so the
            # ts format matches comments / timeline entries exactly.
            self.ts = _utc_now_iso()
        if self.extra is None:  # defensive — caller passed extra=None
            self.extra = {}

    # ------------------------------------------------------------------ #
    # Wire envelope
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        """Return the plain-dict envelope for the hook bus.

        Carries the stable ``"kind": "card-event"`` discriminator + the
        canonical ``type`` + every non-None field. The ``extra`` dict is
        merged at the top level *after* the known fields (so a known
        field always wins over a stray same-named ``extra`` key).
        """
        envelope: dict[str, Any] = {
            "kind": CARD_EVENT_KIND,
            "type": self.type,
            "ts": self.ts,
        }
        for field in ("card_id", "actor", "repo", "branch", "pr_url", "sha", "version"):
            val = getattr(self, field)
            if val is not None:
                envelope[field] = val
        # Merge extra under the known fields without letting it clobber
        # the canonical envelope keys.
        for k, v in (self.extra or {}).items():
            if k not in envelope:
                envelope[k] = v
        return envelope

    # ------------------------------------------------------------------ #
    # Ergonomic constructors — one per EventType
    # ------------------------------------------------------------------ #
    @classmethod
    def card_created(cls, card_id: str, actor: str | None = None, **kw: Any) -> "Event":
        return cls(type=EventType.CREATED, card_id=card_id, actor=actor, **kw)

    @classmethod
    def reassigned(cls, card_id: str, actor: str | None = None, **kw: Any) -> "Event":
        return cls(type=EventType.REASSIGNED, card_id=card_id, actor=actor, **kw)

    @classmethod
    def reassigned_batch(
        cls, card_id: str, actor: str | None = None, **kw: Any
    ) -> "Event":
        """A ONE-event summary of a BULK reassignment (``reassign_all``).

        Models the ACT of moving every card owned by one agent to another,
        NOT the individual rows it touched — so a rename that reassigns 158
        cards emits ONE ``reassigned_batch`` (with ``count`` + ``card_ids``
        in ``extra``), never 158 per-card ``reassigned`` events. ``card_id``
        is a synthetic batch marker (e.g. ``"batch:<old>-><new>"``); it is
        not a real board card.
        """
        return cls(type=EventType.REASSIGNED_BATCH, card_id=card_id, actor=actor, **kw)

    @classmethod
    def status_changed(
        cls, card_id: str, actor: str | None = None, **kw: Any
    ) -> "Event":
        return cls(type=EventType.STATUS_CHANGED, card_id=card_id, actor=actor, **kw)

    @classmethod
    def commented(cls, card_id: str, actor: str | None = None, **kw: Any) -> "Event":
        return cls(type=EventType.COMMENTED, card_id=card_id, actor=actor, **kw)

    @classmethod
    def rank_changed(cls, card_id: str, actor: str | None = None, **kw: Any) -> "Event":
        """The rank engine re-scored ``card_id`` and the TOTAL ORDER moved.

        ONE event per rescore — for the card whose axes changed, never one
        per shifted neighbour (an N-card cascade of events per drag would
        be the notification storm ADR-0011's two-nudger design forbids).
        Consumers wanting the full new order read it from the store; the
        event's ``extra`` carries the audited transition (axes + rank,
        old→new, and ``of`` = the scored-set size).
        """
        return cls(type=EventType.RANK_CHANGED, card_id=card_id, actor=actor, **kw)

    @classmethod
    def completed(cls, card_id: str, actor: str | None = None, **kw: Any) -> "Event":
        return cls(type=EventType.COMPLETED, card_id=card_id, actor=actor, **kw)

    @classmethod
    def committed(
        cls,
        card_id: str | None = None,
        *,
        repo: str | None = None,
        sha: str | None = None,
        **kw: Any,
    ) -> "Event":
        return cls(type=EventType.COMMITTED, card_id=card_id, repo=repo, sha=sha, **kw)

    @classmethod
    def pushed(
        cls,
        card_id: str | None = None,
        *,
        repo: str | None = None,
        branch: str | None = None,
        sha: str | None = None,
        **kw: Any,
    ) -> "Event":
        return cls(
            type=EventType.PUSHED,
            card_id=card_id,
            repo=repo,
            branch=branch,
            sha=sha,
            **kw,
        )

    @classmethod
    def merged(
        cls,
        card_id: str | None = None,
        *,
        repo: str | None = None,
        pr_url: str | None = None,
        sha: str | None = None,
        **kw: Any,
    ) -> "Event":
        return cls(
            type=EventType.MERGED,
            card_id=card_id,
            repo=repo,
            pr_url=pr_url,
            sha=sha,
            **kw,
        )

    @classmethod
    def released(
        cls,
        *,
        repo: str | None = None,
        version: str | None = None,
        card_id: str | None = None,
        **kw: Any,
    ) -> "Event":
        return cls(
            type=EventType.RELEASED,
            card_id=card_id,
            repo=repo,
            version=version,
            **kw,
        )

    @classmethod
    def pulled(
        cls,
        *,
        repo: str | None = None,
        card_id: str | None = None,
        **kw: Any,
    ) -> "Event":
        return cls(type=EventType.PULLED, card_id=card_id, repo=repo, **kw)

    @classmethod
    def deployed(
        cls,
        *,
        repo: str | None = None,
        service: str | None = None,
        card_id: str | None = None,
        **kw: Any,
    ) -> "Event":
        # ``service`` is a deploy-specific field; tuck it into extra so
        # the canonical envelope top-level stays a known, stable set.
        extra = dict(kw.pop("extra", {}) or {})
        if service is not None:
            extra.setdefault("service", service)
        return cls(
            type=EventType.DEPLOYED, card_id=card_id, repo=repo, extra=extra, **kw
        )


def emit(
    event: Event,
    *,
    store: Any | None = None,
    entry_points: Iterable | None = None,
) -> dict | None:
    """Emit a canonical :class:`Event` onto the hook bus.

    Thin seam over :func:`scitex_cards._hooks.dispatch_event`: build the
    ``event.to_dict()`` envelope and dispatch it. The bus fans the event to
    the built-in C4 notify consumer + every entry-point plugin.

    NEVER raises to the caller. ``dispatch_event`` already swallows
    plugin errors, but we wrap the whole call defensively (mirroring
    ``comment_task``'s bus-dispatch try/except) so a producer's primary
    work is never broken by an emit failure.

    Parameters
    ----------
    event : Event
        The typed canonical event to emit.
    store : path-like, optional
        Override the task-store path, forwarded to :func:`dispatch_event`
        (and through it to the C4 consumer + inbox). ``None`` (default)
        resolves via the normal precedence chain. The ``emit-event`` CLI
        verb threads ``--tasks`` here so the bus/inbox target the SAME store
        the producer points at — deterministic, no env-var mutation.
    entry_points : iterable, optional
        Explicit plugin entry points (``.name`` + ``.load()``), forwarded
        to :func:`dispatch_event`. ``None`` reads the real
        ``scitex_cards.hooks`` group. This is the in-process injection seam
        tests use (no monkeypatch of importlib — PA-306-compliant).

    Returns
    -------
    dict | None
        The :func:`dispatch_event` summary dict (carrying ``kind`` /
        ``card_writes`` / ``plugin_count`` / ``plugin_errors`` plus, for a
        card-event that ran the C4 consumer, a ``notify`` sub-summary with
        ``enqueued`` / ``delivered``). ADDITIVE: this used to return
        ``None``; the value lets a producer / the ``emit-event`` CLI verb
        report what the dispatch did. Returns ``None`` only when the
        dispatch itself raised (the fail-soft branch below) — never
        propagating the error.
    """
    try:
        from . import _hooks

        return _hooks.dispatch_event(
            event.to_dict(), store=store, entry_points=entry_points
        )
    except Exception:  # noqa: BLE001 — emit must never break a producer
        logger.warning(
            "scitex_cards._events.emit: bus dispatch failed for type=%r card_id=%r",
            getattr(event, "type", "?"),
            getattr(event, "card_id", None),
            exc_info=True,
        )
        return None


# EOF
