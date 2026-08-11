#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The verb-level backend seam (remote-hub design, docs/design/remote-hub-backend.md §2).

One seam, above the storage engine and below the MCP/CLI surfaces: every
store-touching MCP tool calls ``get_backend().<verb>(...)`` instead of
importing the ``_store`` / ``_threads`` / ``_inbox`` / ``_help_wait`` verbs
directly. Two implementations exist by design:

- :class:`LocalBackend` — the passthrough to today's locked in-process verbs.
  ZERO behavior change: each method delegates to exactly the call the MCP
  tool made before the seam existed (including the dm/inbox compositions,
  which move here so a future HTTP backend can map each to ONE round trip).
- :class:`scitex_cards._backend_http.HubBackend` — the HTTP client for
  ``scitex-cards serve``, selected by ``SCITEX_CARDS_HUB_URL``.

A hub that cannot actually be used fails LOUD at the first call — never a
silent fall-through to the local file. A silent local fallback would mint
exactly the "separate copy of the store" the one-database ruling forbids
(operator, 2026-07-17; ADR-0010/0011).

``resolve_store`` and ``health`` are deliberately NOT backend verbs: they
stay local and become backend-AWARE (reporting which backend is active) when
the HTTP backend lands.

THE CURRENCY VISIBILITY ASYMMETRY (incident 2026-07-29). This seam is the
PYTHON rail, and it is deliberately NOT gated by
``_currency.check_currency()``: an agent whose CLI/MCP rail is already
REFUSING a stale install still reaches the operator through here, and taking
that last rail away would be strictly worse than the bug it fixes (settled;
see ``_currency.py``). It is, however, the only place such an agent will ever
look — DMs keep arriving, so nothing else prompts them to suspect their card
rail is dead. So the messaging verbs below call the NON-RAISING
``warn_if_stale_once()``, which logs once per process naming the sibling rail.
Deliberately NOT wired into the CLI (``_cli/_main.py``) or the MCP server
(``_cli/_mcp.py``): both already call ``check_currency()``, and reporting the
same fact twice on a rail that is already erroring is noise.
"""

from __future__ import annotations

import os
from typing import Any

from . import _help_wait, _inbox, _store, _threads
from ._dm import read as _dm_read
from ._currency import warn_if_stale_once
from ._inbox_confirm import confirm_notifications, warn_ack_on_read

_HUB_URL_ENV = "SCITEX_CARDS_HUB_URL"

#: The complete backend verb surface, 1:1 with the MCP task/DM/inbox tools
#: (docs/design/remote-hub-backend.md §3 coverage table). A backend MUST
#: implement every name; the parity test walks this tuple.
BACKEND_VERBS: tuple[str, ...] = (
    "add_task",
    "update_task",
    "complete_task",
    "get_task",
    "list_tasks",
    "summarize_tasks",
    "delete_task",
    "restore_task",
    "comment_task",
    "resolve_task",
    "reopen_task",
    "reassign_task",
    "rescore_task",
    "set_edge",
    "set_collaborator",
    "set_subscriber",
    "help_wait",
    "help_clear",
    "poll_notifications",
    "ack_notifications",
    "dm_send",
    "dm_list",
)


class BackendUnavailableError(RuntimeError):
    """Raised when the configured backend cannot be constructed.

    Deliberately loud: the alternative (falling back to the local file when
    the hub is configured but unreachable) would silently write a second
    store — the exact state the one-database ruling forbids.
    """


class LocalBackend:
    """Passthrough to the locked in-process verbs — zero behavior change.

    Every method body is byte-for-byte the call its MCP tool made before
    the seam existed. The dm/inbox methods carry the COMPOSITION (thread
    key, ack, user resolution, heartbeat) that used to live inline in
    ``_mcp_skills`` — moved here so the future HTTP backend can implement
    each as one RPC instead of re-composing client-side.
    """

    name = "local"

    # -- task verbs (1:1 with _store) ----------------------------------- #

    def add_task(self, tasks_path: Any = None, **fields: Any) -> dict:
        return _store.add_task(tasks_path, **fields)

    def update_task(self, tasks_path: Any, task_id: str, **fields: Any) -> dict:
        return _store.update_task(tasks_path, task_id, **fields)

    def complete_task(
        self, tasks_path: Any, task_id: str, by: str | None = None
    ) -> dict:
        return _store.complete_task(tasks_path, task_id, by=by)

    def get_task(self, tasks_path: Any, task_id: str) -> dict:
        return _store.get_task(tasks_path, task_id)

    def list_tasks(self, tasks_path: Any = None, **filters: Any) -> list:
        return _store.list_tasks(tasks_path, **filters)

    def summarize_tasks(
        self,
        tasks_path: Any = None,
        scope: str | None = None,
        assignee: str | None = None,
    ) -> dict:
        return _store.summarize_tasks(tasks_path, scope=scope, assignee=assignee)

    def delete_task(self, tasks_path: Any, task_id: str) -> dict:
        return _store.delete_task(tasks_path, task_id)

    def restore_task(
        self,
        tasks_path: Any,
        task: dict,
        refs: list[str] | None = None,
    ) -> dict:
        return _store.restore_task(tasks_path, task=task, refs=refs)

    def comment_task(
        self,
        tasks_path: Any,
        task_id: str,
        text: str,
        by: str | None = None,
    ) -> dict:
        return _store.comment_task(tasks_path, task_id, text, by=by)

    def resolve_task(
        self, tasks_path: Any, task_id: str, actor: str | None = None
    ) -> dict:
        return _store.resolve_task(tasks_path, task_id, actor=actor)

    def reopen_task(self, tasks_path: Any, task_id: str, by: str | None = None) -> dict:
        return _store.reopen_task(tasks_path, task_id, by=by)

    def reassign_task(
        self,
        tasks_path: Any,
        task_id: str,
        new_owner: str,
        by: str | None = None,
    ) -> dict:
        return _store.reassign_task(tasks_path, task_id, new_owner, by=by)

    def rescore_task(
        self,
        tasks_path: Any,
        task_id: str,
        *,
        urgency: int,
        importance: int,
        by: str | None = None,
    ) -> dict:
        return _store.rescore_task(
            tasks_path, task_id, urgency=urgency, importance=importance, by=by
        )

    # -- relationship verbs --------------------------------------------- #

    def set_edge(
        self,
        tasks_path: Any = None,
        *,
        action: str,
        kind: str,
        source: str,
        target: str,
    ) -> dict:
        return _store.set_edge(
            tasks_path, action=action, kind=kind, source=source, target=target
        )

    def set_collaborator(
        self,
        tasks_path: Any = None,
        *,
        task_id: str,
        who: str,
        action: str = "add",
    ) -> dict:
        return _store.set_collaborator(
            tasks_path, task_id=task_id, who=who, action=action
        )

    def set_subscriber(
        self,
        tasks_path: Any = None,
        *,
        task_id: str,
        who: str,
        action: str = "add",
    ) -> dict:
        return _store.set_subscriber(
            tasks_path, task_id=task_id, who=who, action=action
        )

    # -- help-wait verbs ------------------------------------------------ #

    def help_wait(
        self,
        tasks_path: Any,
        agent: str,
        question: str | None = None,
        host: str | None = None,
    ) -> dict:
        return _help_wait.help_wait(tasks_path, agent, question=question, host=host)

    def help_clear(self, tasks_path: Any, agent: str) -> dict:
        return _help_wait.help_clear(tasks_path, agent)

    # -- inbox (composition: user resolution + heartbeat + poll) -------- #

    def poll_notifications(
        self,
        agent: str,
        unseen_only: bool = True,
        ack: bool = False,
        store: Any = None,
    ) -> dict:
        # CURRENCY VISIBILITY (module docstring): non-raising, warn-once.
        warn_if_stale_once()
        # HANDOVER IS NOT CONFIRMATION (_inbox_confirm): ack=True advances the
        # cursor at handover, so a consumer that dies before delivering has
        # destroyed the message. Deprecated, NOT changed — sac reads this path.
        deprecation = warn_ack_on_read() if ack else None
        from ._users import resolve_user, touch_user

        user = resolve_user(agent, store=store)
        recipient_id = user.id if user is not None else agent
        # Liveness heartbeat — fail-soft, exactly as the tool did inline:
        # a stamping failure must never break the poll.
        try:
            touch_user(agent, store=store)
        except Exception:  # noqa: BLE001 — heartbeat must not break the poll
            import logging

            logging.getLogger(__name__).warning(
                "poll_notifications: heartbeat failed for %r", agent, exc_info=True
            )
        notifications = _inbox.poll_inbox(
            recipient_id, unseen_only=unseen_only, mark_seen=ack, store=store
        )
        from ._inbox_receipt import unconfirmed_ids

        payload = {
            "agent": agent,
            "recipient_id": recipient_id,
            "notifications": notifications,
            # The ids still awaiting confirmation, and the verb that confirms
            # them: the safe loop must be the OBVIOUS one to write from here.
            #
            # TWO THINGS THIS USED TO GET WRONG, both of which made the field
            # incapable of ever reporting the backlog it exists to report.
            #
            # It keyed on `seen`. The channel drain advances `seen` when it
            # pushes a record, so every pushed notification looked confirmed the
            # moment it was delivered — while `confirmed_at`, the only actual
            # evidence of delivery, stayed NULL. `is_confirmed` now owns that
            # distinction for every caller (see `_inbox_receipt`).
            #
            # And it was computed over `notifications`, i.e. over THIS PAGE.
            # The default page is unseen-only, and the drain has already marked
            # the rows seen, so the page is empty and the field was empty with
            # it — by construction, regardless of which column it read. Fixing
            # only the column would have produced a correct predicate applied to
            # nothing, and looked fixed.
            #
            # "What is still awaiting confirmation" is a property of the INBOX.
            # Measured 2026-08-11: a consumer polled, was told nothing was
            # outstanding, and had to query the rail directly to find the
            # notification it had just acted on.
            "unconfirmed": unconfirmed_ids(recipient_id, store=store),
            "confirm_with": "ack_notifications",
        }
        if deprecation is not None:
            payload["ack_on_read_deprecated"] = deprecation
        return payload

    def ack_notifications(
        self,
        agent: str,
        ids: "list[str] | str | None" = None,
        store: Any = None,
    ) -> dict:
        # CURRENCY VISIBILITY (module docstring): non-raising, warn-once.
        warn_if_stale_once()
        return confirm_notifications(agent, ids, store=store)

    # -- DMs (composition: thread key + ack + read) --------------------- #

    def dm_send(self, sender: str, to: str, body: str, store: Any = None) -> dict:
        # CURRENCY VISIBILITY (module docstring): the confirmed entry point
        # from the incident. Non-raising and warn-once by contract, so the DM
        # still goes out even when the currency check itself is unhappy.
        warn_if_stale_once()
        return _threads.append_message(sender, to, body, store=store)

    def dm_list(
        self,
        sender: str,
        peer: str | None = None,
        ack: bool = False,
        store: Any = None,
    ) -> dict:
        # CURRENCY VISIBILITY (module docstring): non-raising, warn-once.
        warn_if_stale_once()
        other = peer or _threads.OPERATOR_NAME
        key = _threads.thread_key(sender, other)
        if ack:
            _threads.mark_read(key, sender, store=store)
            # AND THE RECEIPT GOES TO THE STORE, for the same reason the board's
            # does: the messages below now come from `dm_messages`, so an ack
            # that only touched the sidecar would leave a thread permanently
            # unread. Idempotent by `(message_id, reader)`.
            from ._dm import write as _dm_write

            unread_ids = [
                m["id"]
                for m in _dm_read.unread_for(sender, store=store, thread_id=key)
            ]
            if unread_ids:
                _dm_write.mark_read(unread_ids, sender, store=store)
        # THE STORE, NOT THE SIDECAR — the agent-facing half of the same defect.
        #
        # PRs #776/#777 moved the BOARD's list and pane onto `dm_messages` and
        # left THIS verb — the one every agent calls — on
        # `_threads.get_thread`, which reads `threads.json`, A PER-HOST FILE.
        #
        # Reproduced 2026-08-09 by two readers on one store, which is what makes
        # it undeniable rather than a hunch:
        #   scitex-agent-container (laptop)     dm_list(peer="…-04") -> 1 message
        #   scitex-agent-container-04 (compute) IDENTICAL query      -> []
        # Same DSN, same store_uuid 1d55dd6e-3d2a-4c24-a429-a78835ab988f, no
        # SQLite fallback, Postgres reachable from both, and — checked, because
        # it was the obvious suspect — THE SAME PACKAGE VERSION 0.32.3 on both.
        # The only difference was which host's `threads.json` each client read.
        #
        # That is the operator's complaint in his own words: his messages do not
        # arrive at the agents' terminals. A per-host file cannot carry a
        # cross-host conversation, so an agent could not read its own peer DMs.
        messages = _dm_read.messages_in(key, store=store)
        return {"thread": key, "peer": other, "messages": messages}


_LOCAL_BACKEND = LocalBackend()


def get_backend():
    """Resolve the active backend from the environment, per call.

    ``SCITEX_CARDS_HUB_URL`` unset (the default everywhere today) returns the
    shared :class:`LocalBackend` — behavior identical to before the seam.
    Set, it returns a fresh :class:`scitex_cards._backend_http.HubBackend`
    bound to that URL (fresh per call, deliberately: the client re-reads
    the token file lazily, so a rotation on the hub is picked up without
    any restart). A hub that cannot actually be used fails LOUD at the
    first call (:class:`HubBackendError`, a
    :class:`BackendUnavailableError` subclass) — never a silent local
    fallback, which would write a store the hub never sees.
    """
    url = os.environ.get(_HUB_URL_ENV)
    if url:
        from ._backend_http import HubBackend

        return HubBackend(url)
    return _LOCAL_BACKEND


__all__ = [
    "BACKEND_VERBS",
    "BackendUnavailableError",
    "LocalBackend",
    "get_backend",
]

# EOF
