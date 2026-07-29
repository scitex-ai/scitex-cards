#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards serve`` — the hub's authenticated RPC surface (remote-hub PR-2).

docs/design/remote-hub-backend.md §3: every backend verb becomes one HTTP
endpoint, ``POST /v1/rpc/<verb>``, JSON kwargs in / the verb's return value
out, so the PR-3 ``HubBackend`` client maps each MCP op to ONE round trip.
Handlers call :class:`scitex_cards._backend.LocalBackend` DIRECTLY — never
``get_backend()``: the server IS the hub, and resolving through the
environment would recurse into an HTTP client if ``SCITEX_CARDS_HUB_URL``
were ever set on the hub host.

Security posture (design §4/§5, sac patterns re-implemented, zero sac code):

- **Loopback only.** ``serve`` binds ``127.0.0.1`` and v1 offers NO
  override flag at all — remote hosts reach it through hub-initiated ssh
  reverse tunnels, so the API never leaves a loopback interface.
- **Bearer tokens**, auto-minted 0600 under ``~/.scitex/cards/tokens/``,
  compared constant-time. Any ``*.token`` file in the dir authenticates
  (per-host tokens are all peers in v1; per-agent tokens are the named v2).
- **Identity rides every request**: ``X-Scitex-Agent`` is REQUIRED (400
  when absent — fail-loud, mirroring the a2a rule that the sender must be
  declared). v1 trust model: the bearer authenticates the HOST, the header
  declares the AGENT; the client wires identity into the verbs' ``by`` /
  ``actor`` / ``created_by`` kwargs itself — the server never silently
  injects it.
- **The store is PINNED at boot.** Requests never choose the store: a body
  carrying ``tasks_path`` / ``store`` is rejected 400 (the GUI's ``?store=``
  retargeting hazard, closed by construction).
- **One JSONL audit line per authenticated request** under
  ``~/.scitex/cards/logs/hub_access.jsonl``.
- **No outbound calls on the serve/bind path, ever** (design §5.7 — the
  2026-06-26 pre-bind hang incident, adopted as a rule).
- ``GET /v1/health`` is the ONE unauthenticated route.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ._backend import BACKEND_VERBS, LocalBackend

#: Verbs whose store parameter is named ``store`` (the dm/inbox
#: compositions); every other backend verb takes ``tasks_path``.
_STORE_KWARG_IS_STORE = frozenset(
    {"dm_send", "dm_list", "poll_notifications", "ack_notifications"}
)

#: Body keys that would retarget the pinned store — rejected outright.
_FORBIDDEN_BODY_KEYS = frozenset({"tasks_path", "store"})

_TOKEN_BYTES = 32


def default_tokens_dir() -> Path:
    return Path.home() / ".scitex" / "cards" / "tokens"


def default_audit_path() -> Path:
    return Path.home() / ".scitex" / "cards" / "logs" / "hub_access.jsonl"


def mint_token(tokens_dir: Path, name: str = "hub") -> Path:
    """Mint ``<name>.token`` (32 url-safe random bytes) at mode 0600.

    Overwrites an existing file of the same name — that IS rotation; the
    old value stops authenticating on the server's next token reload
    (tokens are re-read per request, so rotation needs no restart).
    """
    tokens_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tokens_dir, 0o700)
    path = tokens_dir / f"{name}.token"
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    path.write_text(secrets.token_urlsafe(_TOKEN_BYTES), encoding="utf-8")
    return path


def _load_tokens(tokens_dir: Path) -> list[str]:
    if not tokens_dir.is_dir():
        return []
    out = []
    for p in sorted(tokens_dir.glob("*.token")):
        value = p.read_text(encoding="utf-8").strip()
        if value:
            out.append(value)
    return out


def _authorized(header: str | None, tokens: list[str]) -> bool:
    """Constant-time bearer check against every token in the dir.

    Deliberately iterates ALL tokens on every call (no early exit on a
    prefix mismatch of the header shape) so timing does not reveal which
    token file matched.
    """
    if not header or not header.startswith("Bearer "):
        return False
    presented = header[len("Bearer ") :].strip()
    ok = False
    for token in tokens:
        if hmac.compare_digest(presented, token):
            ok = True
    return ok


class _RpcServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the pinned serve-time configuration."""

    daemon_threads = True

    def __init__(self, addr, handler, *, store, tokens_dir: Path, audit_path: Path):
        super().__init__(addr, handler)
        self.store = store
        self.tokens_dir = tokens_dir
        self.audit_path = audit_path
        self.backend = LocalBackend()
        self._audit_lock = threading.Lock()

    def audit(self, agent: str, verb: str, status: int) -> None:
        """Append one JSONL line; failures must never break the request."""
        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "agent": agent,
                "verb": verb,
                "status": status,
            }
        )
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_lock, open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


class RpcHandler(BaseHTTPRequestHandler):
    """``GET /v1/health`` (public) + ``POST /v1/rpc/<verb>`` (bearer)."""

    server: _RpcServer  # typing aid; set by ThreadingHTTPServer machinery

    # Quiet the default stderr-per-request logging; the audit file is the log.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — http.server contract
        if self.path == "/v1/health":
            self._send_json(200, {"ok": True, "verbs": len(BACKEND_VERBS)})
            return
        if self.path == "/v1/whoami":
            # The doctor's authenticated round-trip probe (design §4): proves
            # the bearer works AND echoes the identity header back, so the
            # client can verify its declared identity survives the transport
            # intact. Also the "cheap authenticated read" the TTL-scheduling
            # guidance wants — no store I/O at all.
            tokens = _load_tokens(self.server.tokens_dir)
            if not _authorized(self.headers.get("Authorization"), tokens):
                self._send_json(401, {"error": "missing or invalid bearer token"})
                return
            agent = (self.headers.get("X-Scitex-Agent") or "").strip()
            if not agent:
                self._send_json(400, {"error": "X-Scitex-Agent header is required"})
                return
            self._send_json(200, {"agent": agent})
            return
        self._send_json(404, {"error": f"no route {self.path!r}"})

    def do_POST(self) -> None:  # noqa: N802 — http.server contract
        if not self.path.startswith("/v1/rpc/"):
            self._send_json(404, {"error": f"no route {self.path!r}"})
            return
        verb = self.path[len("/v1/rpc/") :]

        tokens = _load_tokens(self.server.tokens_dir)
        if not _authorized(self.headers.get("Authorization"), tokens):
            self._send_json(401, {"error": "missing or invalid bearer token"})
            return

        agent = (self.headers.get("X-Scitex-Agent") or "").strip()
        if not agent:
            self._send_json(
                400,
                {
                    "error": "X-Scitex-Agent header is required — every "
                    "request must declare the acting agent"
                },
            )
            return

        if verb not in BACKEND_VERBS:
            self.server.audit(agent, verb, 404)
            self._send_json(
                404,
                {"error": f"unknown verb {verb!r}", "verbs": sorted(BACKEND_VERBS)},
            )
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            kwargs = json.loads(raw or b"{}")
            if not isinstance(kwargs, dict):
                raise ValueError("body must be a JSON object of verb kwargs")
        except (ValueError, json.JSONDecodeError) as exc:
            self.server.audit(agent, verb, 400)
            self._send_json(400, {"error": f"bad request body: {exc}"})
            return

        forbidden = _FORBIDDEN_BODY_KEYS.intersection(kwargs)
        if forbidden:
            self.server.audit(agent, verb, 400)
            self._send_json(
                400,
                {
                    "error": "the serve store is pinned at boot; a request "
                    f"may not retarget it via {sorted(forbidden)}"
                },
            )
            return

        store_kwarg = "store" if verb in _STORE_KWARG_IS_STORE else "tasks_path"
        kwargs[store_kwarg] = self.server.store

        from scitex_cards._store import TaskNotFoundError

        try:
            from scitex_cards import TaskValidationError
        except ImportError:  # pragma: no cover — always importable in-tree
            TaskValidationError = ValueError  # type: ignore[assignment]

        try:
            result = getattr(self.server.backend, verb)(**kwargs)
        except TaskNotFoundError as exc:
            self.server.audit(agent, verb, 404)
            self._send_json(404, {"error": str(exc), "type": "TaskNotFoundError"})
            return
        except (TaskValidationError, ValueError, TypeError) as exc:
            self.server.audit(agent, verb, 400)
            self._send_json(400, {"error": str(exc), "type": type(exc).__name__})
            return
        except Exception as exc:  # noqa: BLE001 — no tracebacks over the wire
            self.server.audit(agent, verb, 500)
            self._send_json(500, {"error": str(exc), "type": type(exc).__name__})
            return

        self.server.audit(agent, verb, 200)
        self._send_json(200, result)


def make_server(
    *,
    store,
    port: int = 8765,
    tokens_dir: Path | None = None,
    audit_path: Path | None = None,
) -> _RpcServer:
    """Build the loopback RPC server (not yet serving).

    Mints ``hub.token`` when the tokens dir holds no token at all, so a
    first ``serve`` is usable without a separate mint step. Never makes an
    outbound call (design §5.7). The bind address is not a parameter: v1
    is loopback-only by construction.
    """
    tokens_dir = tokens_dir or default_tokens_dir()
    audit_path = audit_path or default_audit_path()
    if not _load_tokens(tokens_dir):
        mint_token(tokens_dir, "hub")
    return _RpcServer(
        ("127.0.0.1", port),
        RpcHandler,
        store=store,
        tokens_dir=tokens_dir,
        audit_path=audit_path,
    )


__all__ = [
    "RpcHandler",
    "default_audit_path",
    "default_tokens_dir",
    "make_server",
    "mint_token",
]

# EOF
