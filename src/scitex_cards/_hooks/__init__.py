#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook-consumer entry-point contract for the scitex-cards board.

Lead a2a `6fff33d6` + `fbffb879`, 2026-06-14 — operator-mandated. Loose
coupling between scitex-cards (the board / SSoT) and event PRODUCERS
(SAC's push-hook, dev's merge-Action, future federated producers).
The board never knows what produces an event; it just accepts the
canonical wire shape and records it.

This package was a single flat module until the C2 hook-bus card; it
is now split into focused submodules (validation, built-in handlers,
bounded plugin dispatch, top-level dispatch) and this ``__init__``
re-exports the public surface so every
``from scitex_cards._hooks import ...`` caller keeps working unchanged:

  * :mod:`._validate`  — :data:`VALID_EVENT_KINDS`, :class:`HookEventError`,
    :func:`event_validate` (incl. the C2 ``card-event`` branch).
  * :mod:`._handlers`  — built-in ``push`` / ``done`` / ``unblock`` handlers.
  * :mod:`._plugins`   — entry-point discovery + the C2 per-plugin
    wall-time budget (:func:`_run_plugins`, :data:`PLUGIN_TIMEOUT_S`,
    :data:`PLUGIN_TIMEOUT_ENV`).
  * :mod:`._dispatch`  — :func:`dispatch_event` (unchanged summary shape).

## Entry-point group

    scitex_cards.hooks

External producers register a Python callable as a plugin under this
group. Each plugin is invoked for every event with the canonical
event dict. Example consumer-side wire (in another package's
`pyproject.toml`):

.. code-block:: toml

   [project.entry-points."scitex_cards.hooks"]
   my-notifier = "my_pkg.hooks:on_event"

The callable signature is::

    def on_event(event: dict) -> None: ...

Plugin failures are caught + logged but never propagate — one bad
plugin must NOT silently break the board's own record-keeping.

## Handler ordering + criticality (dev coordination 2026-06-14)

Handlers MAY declare two optional function attributes to influence
dispatch:

.. code-block:: python

    def on_event(event: dict) -> None:
        ...

    on_event.priority = 10     # int, default 100. LOWER = runs FIRST.
    on_event.critical = True   # bool, default False. If True and the
                               # handler RAISES (or, since C2, TIMES
                               # OUT), the dispatcher aborts the chain
                               # and re-raises so the producer (HTTP/CLI)
                               # sees a 500 / non-zero exit.

Sort order is ``(priority asc, entry-point-name asc)`` — stable
across packaging-metadata implementations.

The `ci-result` event chain uses both attributes: dev's owner-map
handler (priority=10, critical=True) mutates ``event["owner"]``
BEFORE SAC's a2a-delivery handler (priority=200) reads it.
Delivering a verdict to a wrong-or-no agent is worse than no
delivery, hence the critical attribute on the owner-map.

The event dict is passed BY REFERENCE through every handler — early
handlers may mutate it for later handlers to consume. Since C2 each
handler runs in a worker thread joined with a wall-time budget; the
join is a happens-before edge so the mutation a finished handler made
is visible to the next ordered handler (see :mod:`._plugins`).

## Per-plugin wall-time budget (C2)

A slow/hung entry-point plugin used to block the WHOLE producer (the
root cause behind the earlier comment-post slowness). C2 runs each
handler under a wall-time budget (:data:`PLUGIN_TIMEOUT_S`, env-
overridable via :data:`PLUGIN_TIMEOUT_ENV`; ``<= 0`` disables it for
in-process tests). A timed-out handler is recorded in ``plugin_errors``
with ``"timeout": true``; a timed-out ``critical`` handler aborts the
chain. Python cannot force-kill a thread, so the worker is a DAEMON:
the hung plugin keeps running in the background until it returns or the
process exits — strictly better than hanging the producer forever.

## Canonical event kinds

Five event kinds drive the inbound-write contract today. New kinds
can be added later by extending the dispatcher; the wire shape is
forward-compatible (unknown keys are ignored by `event_validate`).

### `push` event

A producer (typically SAC's push-hook) saw a git push. Payload::

    {
        "kind": "push",
        "repo": "owner/repo",            # required, non-empty
        "branch": "develop",             # required, non-empty
        "commit_sha": "abc123def...",    # required, non-empty
        "author": "agent-name",          # optional
        "message": "commit message",     # optional
        "card_ids": ["card-id-1", ...]   # optional — board cards to
                                         # annotate; if absent, the
                                         # built-in handler is a noop
                                         # at the card level (plugins
                                         # may still react)
    }

Built-in handler: for each ``card_id``, append a comment to the card
with the commit_sha + message. **Idempotent**: a comment whose text
ALREADY contains the commit_sha is not re-appended.

### `done` event

A producer (typically dev's merge-Action via GitHub) saw a PR merge.
Payload::

    {
        "kind": "done",
        "repo": "owner/repo",            # required, non-empty
        "pr_number": 158,                # required, int
        "pr_url": "https://.../pull/158",# required, non-empty
        "author": "merger",              # optional
        "merged_at": "ISO-8601 UTC",     # optional
        "card_ids": ["card-id-1", ...]   # optional
    }

Built-in handler: for each ``card_id``, set ``pr_url`` + flip
``status`` to ``done``. **Idempotent**: re-call with the same
``pr_url`` on an already-done card with the same pr_url is a noop.

### `card-event` event (C1 canonical envelope)

The typed canonical event (:mod:`scitex_cards._events`). Carries the
stable ``"kind": "card-event"`` discriminator + an inner ``type`` from
:data:`scitex_cards._events.EVENT_TYPES` + a required ``card_id``. There
is no built-in handler yet (C5) — a validated card-event flows straight
to plugins.

## Wire surface (loose-coupling contract)

Producers can call the consumer via THREE equivalent surfaces — pick
whichever is closest to the producer's process model:

1. **HTTP** — ``POST /hooks/push`` / ``POST /hooks/done`` with the
   payload as JSON body. Used by SAC's push-hook + dev's GitHub
   Action.
2. **CLI** — ``scitex-cards hook push --payload <FILE_OR_->`` and
   ``scitex-cards hook done --payload <FILE_OR_->`` (``-`` reads the
   payload from stdin). Used by shell scripts that already have shell
   access (e.g. the bundled ``.githooks/`` git → card hooks).
3. **Python** — ``from scitex_cards._hooks import dispatch_event;
   dispatch_event({"kind": "push", ...})``. Used by in-process
   producers.

All three converge on :func:`dispatch_event`, which runs the
built-in handler + every entry-point plugin in turn.
"""

from __future__ import annotations

from ._dispatch import dispatch_event
from ._plugins import (
    ENTRY_POINT_GROUP,
    PLUGIN_TIMEOUT_ENV,
    PLUGIN_TIMEOUT_S,
    HookPluginTimeoutError,
    _iter_entry_points,
    _run_plugins,
)
from ._validate import VALID_EVENT_KINDS, HookEventError, event_validate

__all__ = [
    "ENTRY_POINT_GROUP",
    "PLUGIN_TIMEOUT_ENV",
    "PLUGIN_TIMEOUT_S",
    "HookEventError",
    "HookPluginTimeoutError",
    "VALID_EVENT_KINDS",
    "dispatch_event",
    "event_validate",
    "_iter_entry_points",
    "_run_plugins",
]

# EOF
