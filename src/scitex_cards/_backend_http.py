#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``HubBackend`` — the HTTP client half of the backend seam (remote-hub PR-3).

docs/design/remote-hub-backend.md §2/§3: when ``SCITEX_CARDS_HUB_URL`` is
set, :func:`scitex_cards._backend.get_backend` returns THIS class instead of
the local passthrough, and every backend verb becomes exactly one
``POST /v1/rpc/<verb>`` round trip to the hub's ``scitex-cards serve``.
stdlib ``urllib`` only — the same no-new-deps rule the design set.

FAIL-LOUD, NEVER FALL BACK. There is no local-store path anywhere in this
module: a hub that cannot be reached raises with a hint, because a silent
local fallback would mint the separate store copy the one-database ruling
forbids. Concretely:

- URL set but no readable token → :class:`HubBackendError` at the FIRST
  call (not at resolve time — resolution must stay import-safe).
- Connection refused / timeout → ``HubBackendError`` naming the URL and
  the likely cause ("is the tunnel up?").
- HTTP 401 → ``HubBackendError`` pointing at token provisioning.
- HTTP 404 carrying ``TaskNotFoundError`` → re-raised as the REAL
  :class:`scitex_cards._store.TaskNotFoundError`, so callers' error
  handling is identical against either backend.
- HTTP 400 carrying ``TaskValidationError`` → re-raised as the real
  :class:`scitex_cards.TaskValidationError`; any other 400 → ``ValueError``.

IDENTITY. The hub executes verbs under ITS OWN environment, so any verb
that defaults its actor from ``$SCITEX_TODO_AGENT_ID`` would stamp the
HUB's identity onto a remote agent's write. The client therefore injects
its resolved identity into the verbs' existing ``by`` / ``actor`` /
``created_by`` kwargs whenever the caller left them unset — and ALWAYS
sends it as the ``X-Scitex-Agent`` header (the server rejects requests
without it). An explicitly passed identity is always respected.

THE STORE IS THE HUB'S. A caller passing a non-None ``tasks_path`` /
``store`` gets a loud error: a remote client cannot choose the hub's
store, and silently discarding the argument would be a lie.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_TOKEN_ENV = "SCITEX_CARDS_HUB_TOKEN"
_TOKEN_FILE_ENV = "SCITEX_CARDS_HUB_TOKEN_FILE"
_AGENT_ENV = "SCITEX_TODO_AGENT_ID"
_TIMEOUT_S = 60

#: Which kwarg carries the acting identity, per verb, for injection when
#: the caller left it unset. dm_* verbs carry ``sender`` POSITIONALLY and
#: the MCP layer resolves it before the backend is reached, so they are
#: deliberately absent here.
_IDENTITY_KWARG = {
    "add_task": "created_by",
    "complete_task": "by",
    "comment_task": "by",
    "reopen_task": "by",
    "reassign_task": "by",
    "rescore_task": "by",
    "resolve_task": "actor",
}


from ._backend import BackendUnavailableError


class HubBackendError(BackendUnavailableError):
    """A hub transport/auth failure — loud, hinted, never a fallback.

    Subclasses :class:`BackendUnavailableError` so PR-1-era callers
    catching the resolver's old error class keep working now that the
    failure moved from resolve time to first-call time.
    """


def default_token_file() -> Path:
    return Path.home() / ".scitex" / "cards" / "hub.token"


def _resolve_token() -> str:
    """Token precedence: env value > env-named file > the default file.

    An EXPLICITLY-set token-file env that is unreadable is a hard error,
    never a fall-through to the default path: the default file can belong
    to a DIFFERENT hub than the one the explicit config meant (measured
    2026-07-18 — a freshly provisioned host token on the CI machine leaked
    into test rigs through exactly that fall-through, authenticating every
    "token missing" scenario against the wrong hub as a 401). Explicit
    config either works or fails loud.

    Raises :class:`HubBackendError` when nothing readable exists — the
    design's "URL set but no token → hard error at first call, never a
    silent local fallback".
    """
    env_value = os.environ.get(_TOKEN_ENV)
    if env_value and env_value.strip():
        return env_value.strip()
    file_env = os.environ.get(_TOKEN_FILE_ENV)
    if file_env:
        try:
            value = Path(file_env).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise HubBackendError(
                f"${_TOKEN_FILE_ENV} is set to {file_env} but no hub token "
                f"is readable there ({exc}). Fix the path or re-provision "
                "this host; the default token file is deliberately NOT "
                "consulted when the explicit one fails."
            ) from exc
        if value:
            return value
        raise HubBackendError(
            f"${_TOKEN_FILE_ENV} is set to {file_env} but the file is "
            "empty — no hub token. Re-provision this host."
        )
    try:
        value = default_token_file().read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    raise HubBackendError(
        f"SCITEX_CARDS_HUB_URL is set but no hub token is readable "
        f"(checked ${_TOKEN_ENV}, ${_TOKEN_FILE_ENV}, {default_token_file()}). "
        "Provision one with `scitex-cards hub provision` on the hub, or set "
        "the env override. Refusing to fall back to a local store."
    )


class HubBackend:
    """Every backend verb as ONE authenticated RPC to the hub."""

    name = "hub"

    def __init__(self, url: str, *, agent: str | None = None):
        self.url = url.rstrip("/")
        self._agent = agent or os.environ.get(_AGENT_ENV) or ""
        self._token: str | None = None  # resolved lazily at first call

    # -- transport ------------------------------------------------------ #

    def _call(self, verb: str, kwargs: dict[str, Any]) -> Any:
        if self._token is None:
            self._token = _resolve_token()
        if not self._agent:
            raise HubBackendError(
                "no agent identity: set SCITEX_TODO_AGENT_ID (the hub "
                "requires X-Scitex-Agent on every request)"
            )
        # Send kwargs EXACTLY as built — including explicit nulls. None is
        # update_task's documented DELETE sentinel; stripping it here would
        # silently swallow a remote clear (the parked/un-park class). For
        # optional filters a null reaches the verb as None, identical to
        # the default — harmless in that direction, load-bearing in this.
        body = kwargs
        req = urllib.request.Request(
            f"{self.url}/v1/rpc/{verb}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
                "X-Scitex-Agent": self._agent,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self._raise_mapped(verb, exc)
        except urllib.error.URLError as exc:
            raise HubBackendError(
                f"hub unreachable at {self.url} ({exc.reason}) — is the "
                "reverse tunnel up? The client never falls back to a local "
                "store; fix the rail."
            ) from exc

    def _raise_mapped(self, verb: str, exc: urllib.error.HTTPError):
        try:
            payload = json.loads(exc.read())
        except (ValueError, OSError):
            payload = {}
        error = payload.get("error", f"HTTP {exc.code}")
        kind = payload.get("type", "")
        if exc.code == 401:
            raise HubBackendError(
                f"hub rejected the bearer token ({error}) — the token may "
                "have been rotated; re-provision this host."
            ) from exc
        if exc.code == 404 and kind == "TaskNotFoundError":
            from scitex_cards._store import TaskNotFoundError

            raise TaskNotFoundError(error) from exc
        if exc.code == 400:
            if kind == "TaskValidationError":
                from scitex_cards import TaskValidationError

                raise TaskValidationError(error) from exc
            raise ValueError(error) from exc
        raise HubBackendError(f"hub error on {verb}: {error} ({kind})") from exc

    def _forbid_store(self, value: Any) -> None:
        if value is not None:
            raise HubBackendError(
                "a remote client cannot choose the hub's store — the serve "
                "store is pinned hub-side; drop the tasks_path/store argument"
            )

    def _identity(self, verb: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        key = _IDENTITY_KWARG.get(verb)
        if key and not kwargs.get(key):
            kwargs[key] = self._agent
        return kwargs

    # -- task verbs ----------------------------------------------------- #

    def add_task(self, tasks_path: Any = None, **fields: Any) -> dict:
        self._forbid_store(tasks_path)
        return self._call("add_task", self._identity("add_task", fields))

    def update_task(self, tasks_path: Any, task_id: str, **fields: Any) -> dict:
        self._forbid_store(tasks_path)
        fields["task_id"] = task_id
        return self._call("update_task", fields)

    def complete_task(
        self, tasks_path: Any, task_id: str, by: str | None = None
    ) -> dict:
        self._forbid_store(tasks_path)
        kwargs = self._identity("complete_task", {"task_id": task_id, "by": by})
        return self._call("complete_task", kwargs)

    def get_task(self, tasks_path: Any, task_id: str) -> dict:
        self._forbid_store(tasks_path)
        return self._call("get_task", {"task_id": task_id})

    def list_tasks(self, tasks_path: Any = None, **filters: Any) -> list:
        self._forbid_store(tasks_path)
        return self._call("list_tasks", filters)

    def summarize_tasks(
        self,
        tasks_path: Any = None,
        scope: str | None = None,
        assignee: str | None = None,
    ) -> dict:
        self._forbid_store(tasks_path)
        return self._call("summarize_tasks", {"scope": scope, "assignee": assignee})

    def delete_task(self, tasks_path: Any, task_id: str) -> dict:
        self._forbid_store(tasks_path)
        return self._call("delete_task", {"task_id": task_id})

    def restore_task(
        self, tasks_path: Any, task: dict, refs: list[str] | None = None
    ) -> dict:
        self._forbid_store(tasks_path)
        return self._call("restore_task", {"task": task, "refs": refs})

    def comment_task(
        self, tasks_path: Any, task_id: str, text: str, by: str | None = None
    ) -> dict:
        self._forbid_store(tasks_path)
        kwargs = self._identity(
            "comment_task", {"task_id": task_id, "text": text, "by": by}
        )
        return self._call("comment_task", kwargs)

    def resolve_task(
        self, tasks_path: Any, task_id: str, actor: str | None = None
    ) -> dict:
        self._forbid_store(tasks_path)
        kwargs = self._identity("resolve_task", {"task_id": task_id, "actor": actor})
        return self._call("resolve_task", kwargs)

    def reopen_task(self, tasks_path: Any, task_id: str, by: str | None = None) -> dict:
        self._forbid_store(tasks_path)
        kwargs = self._identity("reopen_task", {"task_id": task_id, "by": by})
        return self._call("reopen_task", kwargs)

    def reassign_task(
        self,
        tasks_path: Any,
        task_id: str,
        new_owner: str,
        by: str | None = None,
    ) -> dict:
        self._forbid_store(tasks_path)
        kwargs = self._identity(
            "reassign_task",
            {"task_id": task_id, "new_owner": new_owner, "by": by},
        )
        return self._call("reassign_task", kwargs)

    def rescore_task(
        self,
        tasks_path: Any,
        task_id: str,
        *,
        urgency: int,
        importance: int,
        by: str | None = None,
    ) -> dict:
        self._forbid_store(tasks_path)
        kwargs = self._identity(
            "rescore_task",
            {
                "task_id": task_id,
                "urgency": urgency,
                "importance": importance,
                "by": by,
            },
        )
        return self._call("rescore_task", kwargs)

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
        self._forbid_store(tasks_path)
        return self._call(
            "set_edge",
            {"action": action, "kind": kind, "source": source, "target": target},
        )

    def set_collaborator(
        self, tasks_path: Any = None, *, task_id: str, who: str, action: str = "add"
    ) -> dict:
        self._forbid_store(tasks_path)
        return self._call(
            "set_collaborator", {"task_id": task_id, "who": who, "action": action}
        )

    def set_subscriber(
        self, tasks_path: Any = None, *, task_id: str, who: str, action: str = "add"
    ) -> dict:
        self._forbid_store(tasks_path)
        return self._call(
            "set_subscriber", {"task_id": task_id, "who": who, "action": action}
        )

    # -- help-wait verbs ------------------------------------------------ #

    def help_wait(
        self,
        tasks_path: Any,
        agent: str,
        question: str | None = None,
        host: str | None = None,
    ) -> dict:
        self._forbid_store(tasks_path)
        return self._call(
            "help_wait", {"agent": agent, "question": question, "host": host}
        )

    def help_clear(self, tasks_path: Any, agent: str) -> dict:
        self._forbid_store(tasks_path)
        return self._call("help_clear", {"agent": agent})

    # -- inbox / DM compositions ---------------------------------------- #

    def poll_notifications(
        self,
        agent: str,
        unseen_only: bool = True,
        ack: bool = False,
        store: Any = None,
    ) -> dict:
        self._forbid_store(store)
        if ack:
            # HANDOVER IS NOT CONFIRMATION — warn on the CLIENT too, not just
            # hub-side, so the deprecation reaches the process that wrote it.
            from ._inbox_confirm import warn_ack_on_read

            warn_ack_on_read()
        return self._call(
            "poll_notifications",
            {"agent": agent, "unseen_only": unseen_only, "ack": ack},
        )

    def ack_notifications(
        self,
        agent: str,
        ids: "list[str] | str | None" = None,
        store: Any = None,
    ) -> dict:
        self._forbid_store(store)
        return self._call("ack_notifications", {"agent": agent, "ids": ids})

    def dm_send(self, sender: str, to: str, body: str, store: Any = None) -> dict:
        self._forbid_store(store)
        return self._call("dm_send", {"sender": sender, "to": to, "body": body})

    def dm_list(
        self,
        sender: str,
        peer: str | None = None,
        ack: bool = False,
        store: Any = None,
    ) -> dict:
        self._forbid_store(store)
        return self._call("dm_list", {"sender": sender, "peer": peer, "ack": ack})


__all__ = ["HubBackend", "HubBackendError", "default_token_file"]

# EOF
