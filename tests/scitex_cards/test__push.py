#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_cards._push``.

Real HTTP round-trips against a localhost http.server instance — no
mocks (STX-NM / PA-306). AAA + one-assertion-per-test per the
scitex-dev test-quality corpus (STX-TQ002 / STX-TQ007).

Covers:
  * Env resolution (`SCITEX_TODO_AGENT_TURN_URLS` JSON map +
    per-agent `SCITEX_TODO_TURN_URL_<SLUG>` fallback)
  * No-URL → ok=False with reason="no-turn-url-configured"
  * Successful POST → ok=True with the real status code
  * HTTP 4xx/5xx → ok=False with reason="http-error"
  * Transport error (port that no server listens on) →
    ok=False with reason="transport-error"
  * SCITEX_TODO_PUSH_DRY_RUN=1 → ok=True, wire="dry-run"
  * announce_missing_at_boot returns the diff list
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
from contextlib import contextmanager

import pytest

from scitex_cards._db import ENV_DB
from scitex_cards._push import (
    DEFAULT_TIMEOUT_S,
    ENV_DRY_RUN,
    ENV_MAP,
    ENV_PUSH_TIMEOUT_S,
    PER_AGENT_PREFIX,
    _default_timeout_s,
    announce_missing_at_boot,
    deliver,
    turn_url_for,
)
from scitex_cards._users import register_user

# --------------------------------------------------------------------------- #
# Isolation — pin the DEFAULT task store to an empty per-test file            #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _hermetic_resolution(tmp_path):
    """Isolate ``turn_url_for`` from the test HOST's live resolution sources.

    ``turn_url_for`` resolves through scitex-todo's OWN user registry (step
    0, the DEFAULT store via ``resolve_tasks_path(None)``). On a real agent
    host that store at ``~/.scitex/todo/tasks.yaml`` is live and would leak
    a non-None URL into the env-only tests, making them flaky/host-dependent.

    This fixture pins step 0 at an EMPTY per-test store — leaving the env map
    / per-agent env as the only resolution path unless a test opts back in:
      * user-registry tests override ``SCITEX_TODO_TASKS_YAML_SHARED`` with their own
        populated ``tmp_path`` store.
    PA-306-compliant: plain os.environ save/restore, no monkeypatch.
    """
    from scitex_cards._db import connect, init_schema

    store = tmp_path / "_empty_push_store.db"
    conn = connect(str(store))
    try:
        init_schema(conn)
        conn.commit()
    finally:
        conn.close()
    saved = {k: os.environ.get(k) for k in (ENV_DB,)}
    os.environ[ENV_DB] = str(store)
    try:
        yield
    finally:
        for k, prior in saved.items():
            if prior is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prior


# --------------------------------------------------------------------------- #
# Helpers — a minimal stdlib HTTP server we can poke from the same process    #
# --------------------------------------------------------------------------- #


class _Capture:
    """Holds the last received POST so tests can assert on it."""

    last_path: str = ""
    last_body: bytes = b""
    response_code: int = 200


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def _server(capture: _Capture):
    """Spawn a localhost test server returning `capture.response_code`."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            capture.last_path = self.path
            capture.last_body = self.rfile.read(length)
            self.send_response(capture.response_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a, **kw):  # silence test noise
            return

    port = _free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{port}/turn"
    finally:
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# turn_url_for                                                                #
# --------------------------------------------------------------------------- #


class TestTurnUrlFor:
    """Env resolution: canonical JSON map then per-agent fallback."""

    def test_json_map_returns_canonical_url(self, env):
        # Arrange
        env.set(
            ENV_MAP,
            json.dumps({"alpha": "https://canonical/turn/alpha"}),
        )
        # Act
        url = turn_url_for("alpha")
        # Assert
        assert url == "https://canonical/turn/alpha"

    def test_per_agent_env_fallback(self, env):
        # Arrange
        env.delete(ENV_MAP)
        env.set(PER_AGENT_PREFIX + "PROJ_BETA", "https://b/")
        # Act
        url = turn_url_for("proj-beta")
        # Assert
        assert url == "https://b/"

    def test_no_url_returns_none(self, env):
        # Arrange
        env.delete(ENV_MAP)
        # (no per-agent env set)
        # Act
        url = turn_url_for("ghost")
        # Assert
        assert url is None

    def test_malformed_json_does_not_raise(self, env):
        # Arrange
        env.set(ENV_MAP, "{not json")
        # Act
        url = turn_url_for("anything")
        # Assert
        assert url is None


# --------------------------------------------------------------------------- #
# turn_url_for — user-registry lookup (precedence 0, the primary source)      #
# --------------------------------------------------------------------------- #


class TestUserRegistryResolution:
    """scitex-todo's OWN ``users:`` registry as the file-local, NO-bearer
    PRIMARY source (step 0). Real temp store via ``register_user`` + the
    ``SCITEX_TODO_TASKS_YAML_SHARED`` env so ``turn_url_for(agent)`` (which resolves the
    DEFAULT store) reads the same file (no mocks per STX-NM / PA-306).
    """

    def _isolate(self, env, store):
        """Point the default store at ``store`` and strip env precedence.

        So the user registry is the only resolution path that can win.
        """
        env.delete(ENV_MAP)
        for k in list(os.environ):
            if k.startswith(PER_AGENT_PREFIX):
                env.delete(k)

    def test_explicit_turn_url_from_registry_is_returned(self, env, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        register_user(
            kind="agent",
            names=["proj-reg"],
            turn_url="https://reg/v1/turn/proj-reg",
            store=store,
        )
        self._isolate(env, store)
        # Act
        url = turn_url_for("proj-reg")
        # Assert
        assert url == "https://reg/v1/turn/proj-reg"

    def test_a2a_port_from_registry_derives_turn_url(self, env, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        register_user(
            kind="agent",
            names=["proj-port"],
            host_at_name="my-host@proj-port",
            a2a_port=19007,
            store=store,
        )
        self._isolate(env, store)
        # Act
        url = turn_url_for("proj-port")
        # Assert
        assert url == "http://my-host:19007/v1/turn"

    def test_registry_resolves_by_host_at_name(self, env, tmp_path):
        # Arrange — the card owner string may be the host@name join key.
        store = tmp_path / "tasks.yaml"
        register_user(
            kind="agent",
            names=["display-only"],
            host_at_name="h@proj-join",
            turn_url="https://join/turn",
            store=store,
        )
        self._isolate(env, store)
        # Act
        url = turn_url_for("h@proj-join")
        # Assert
        assert url == "https://join/turn"

    def test_registry_wins_over_env_map(self, env, tmp_path):
        # Arrange — both the user registry AND the env map resolve; the
        # file-local registry (step 0) must win over the env map (step 1).
        store = tmp_path / "tasks.yaml"
        register_user(
            kind="agent",
            names=["proj-both"],
            turn_url="https://registry/turn",
            store=store,
        )
        env.set(ENV_MAP, json.dumps({"proj-both": "https://env-map/turn"}))
        # Act
        url = turn_url_for("proj-both")
        # Assert
        assert url == "https://registry/turn"

    def test_user_without_endpoint_falls_through_to_env(self, env, tmp_path):
        # Arrange — a registered user with NO endpoint must not short-circuit;
        # resolution falls through to the env map (step 1).
        store = tmp_path / "tasks.yaml"
        register_user(kind="agent", names=["proj-noep"], store=store)
        env.set(ENV_MAP, json.dumps({"proj-noep": "https://env-fallback/turn"}))
        # Act
        url = turn_url_for("proj-noep")
        # Assert
        assert url == "https://env-fallback/turn"

    def test_unregistered_agent_still_returns_none_loud(self, env, tmp_path):
        # Arrange — nothing resolves anywhere → loud None preserved.
        store = tmp_path / "tasks.yaml"
        register_user(kind="agent", names=["someone-else"], store=store)
        self._isolate(env, store)
        # Act
        url = turn_url_for("ghost")
        # Assert
        assert url is None


# --------------------------------------------------------------------------- #
# deliver                                                                     #
# --------------------------------------------------------------------------- #


class TestDeliver:
    """The push wire: HTTP success / 4xx / transport / dry-run / no-url."""

    def test_no_url_returns_explicit_reason(self, env):
        # Arrange
        env.delete(ENV_MAP)
        env.delete(ENV_DRY_RUN)
        # Act
        r = deliver("ghost", "hi", kind="nudge")
        # Assert
        assert r["reason"] == "no-turn-url-configured"

    def test_dry_run_short_circuits_with_dry_run_wire(self, env, capsys):
        # Arrange
        env.set(ENV_DRY_RUN, "1")
        env.set(ENV_MAP, json.dumps({"a": "http://nope/"}))
        # Act
        r = deliver("a", "hi", kind="notify")
        # Assert
        assert r["wire"] == "dry-run"

    def test_successful_post_returns_ok(self, env):
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            # Act
            r = deliver("alpha", "ping", kind="nudge")
        # Assert
        assert r["ok"] is True

    def test_post_carries_agent_and_body(self, env):
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            deliver("alpha", "hi-from-test", kind="nudge")
        # Act
        payload = json.loads(cap.last_body)
        # Assert
        assert (payload["agent"], payload["body"]) == ("alpha", "hi-from-test")

    def test_post_carries_text_field_aliased_to_body(self, env):
        # Regression guard: SAC's /v1/turn (and claude-code-telegrammer's
        # TURN_URL) require a `text` key — pre-fix scitex-todo only sent
        # `body`, so the SAC receiver returned HTTP 400 "missing or empty
        # 'text' field" and the whole nudge chain died on arrival
        # (proj-scitex-todo P3a(c) pilot, 2026-06-13; lead a2a 8afe659e).
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            deliver("alpha", "hi-from-pilot", kind="notify")
        # Act
        payload = json.loads(cap.last_body)
        # Assert
        assert payload["text"] == "hi-from-pilot"

    def test_succeeds_against_text_strict_receiver(self, env):
        # End-to-end via a real localhost http.server that mimics SAC's
        # /v1/turn validation: 400 when `text` is missing or empty, 200
        # otherwise. Pre-fix this test would fail (HTTP 400 → reason=
        # http-error). With the `text` alias in the payload it passes.
        # Arrange
        received: dict = {}

        class _TextStrictHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {}
                text = (body.get("text") or "").strip()
                if not text:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"missing or empty text field"}')
                    return
                received["text"] = text
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a, **kw):  # silence test noise
                return

        port = _free_port()
        httpd = http.server.HTTPServer(("127.0.0.1", port), _TextStrictHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        env.delete(ENV_DRY_RUN)
        env.set(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/v1/turn"}),
        )
        try:
            # Act
            result = deliver("alpha", "hi from pilot", kind="notify")
        finally:
            httpd.shutdown()
        # Assert — payload satisfied the text-strict receiver.
        assert result["reason"] == "delivered"

    def test_http_4xx_returns_http_error(self, env):
        # Arrange
        cap = _Capture()
        cap.response_code = 404
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            # Act
            r = deliver("alpha", "ping", kind="nudge")
        # Assert
        assert r["reason"] == "http-error"

    def test_transport_error_when_no_server(self, env):
        # Arrange
        port = _free_port()  # bind+release → port currently unbound
        env.delete(ENV_DRY_RUN)
        env.set(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/turn"}),
        )
        # Act
        r = deliver("alpha", "ping", kind="nudge", timeout=1.0)
        # Assert
        assert r["reason"] == "transport-error"

    def test_read_timeout_treated_as_dispatched_ok(self, env):
        # Receiver's /v1/turn runs the turn synchronously up to ~120 s
        # but the cron must not block that long. When the client times
        # out, the request body was already fully sent; the receiver is
        # mid-turn, not unreachable. deliver() flips this to
        # ``ok=True, reason="dispatched"`` so the cron's nudge batch
        # doesn't fail over one slow turn (lead a2a `0b59485f`).
        # Arrange — a real localhost server that accepts the connect +
        # request body but never writes a response, forcing a read
        # timeout on the client side.
        accept_event = threading.Event()

        class _NeverRespondHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                # Drain the request body so the client's send phase
                # completes — this mirrors the receiver having
                # accepted the request and started a long turn.
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                accept_event.set()
                # Sleep longer than the client timeout — receiver is
                # "still computing the turn".
                import time

                time.sleep(5)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a, **kw):
                return

        port = _free_port()
        httpd = http.server.HTTPServer(("127.0.0.1", port), _NeverRespondHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        env.delete(ENV_DRY_RUN)
        env.set(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/v1/turn"}),
        )
        try:
            # Act — sub-second timeout to keep the test fast; the handler
            # sleeps 5 s, so we will time out long before it responds.
            r = deliver("alpha", "ping", kind="notify", timeout=0.5)
        finally:
            httpd.shutdown()
        # Assert — receiver got the request body, so the cron treats
        # this as a successful dispatch even though we never read a
        # response.
        assert r["reason"] == "dispatched"

    def test_interactive_read_timeout_fails_loud_fast(self, env):
        # Operator P1 (2026-06-25): the board's comment relay runs inline
        # on POST /comment, so a slow/stalled receiver must NOT hang the
        # board ~30 s AND a notify miss must be VISIBLE. With
        # ``dispatched_is_ok=False`` (the relay's flavor) a read-timeout
        # returns FAST with ``ok=False, reason="timeout"`` instead of the
        # background "dispatched" white-lie — so the UI can toast loud.
        # Arrange — a real localhost server that accepts the request body
        # but never responds, forcing a client read-timeout (no mocks).
        class _NeverRespondHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                import time

                time.sleep(5)  # longer than the client timeout

            def log_message(self, *a, **kw):
                return

        port = _free_port()
        httpd = http.server.HTTPServer(("127.0.0.1", port), _NeverRespondHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        env.delete(ENV_DRY_RUN)
        env.set(ENV_MAP, json.dumps({"alpha": f"http://127.0.0.1:{port}/v1/turn"}))
        try:
            # Act — short timeout + interactive flavor; the handler sleeps
            # 5 s so we time out well before any response.
            import time as _t

            t0 = _t.monotonic()
            r = deliver(
                "alpha",
                "ping",
                kind="comment-relay",
                timeout=0.5,
                dispatched_is_ok=False,
            )
            elapsed = _t.monotonic() - t0
        finally:
            httpd.shutdown()
        # Assert — fast (well under the old 30 s) AND fails loud, not the
        # silent "dispatched" success the background path uses.
        assert elapsed < 3.0 and r["ok"] is False and r["reason"] == "timeout"

    def test_default_timeout_env_override(self, env):
        # Arrange — env value parsed at call-time, not module import.
        env.set(ENV_PUSH_TIMEOUT_S, "12.5")
        # Act
        v = _default_timeout_s()
        # Assert
        assert v == 12.5

    def test_default_timeout_falls_back_to_constant_when_env_unset(self, env):
        # Arrange
        env.delete(ENV_PUSH_TIMEOUT_S)
        # Act
        v = _default_timeout_s()
        # Assert
        assert v == DEFAULT_TIMEOUT_S


# --------------------------------------------------------------------------- #
# announce_missing_at_boot                                                    #
# --------------------------------------------------------------------------- #


class TestAnnounceMissing:
    """Boot-time WARN: lists distinct agents with no turn URL."""

    def test_returns_missing_agents(self, env):
        # Arrange
        env.delete(ENV_MAP)
        tasks = [
            {"agent": "alpha"},
            {"agent": "beta"},
            {"agent": "alpha"},  # dedup
            {"agent": ""},  # ignored
            {"agent": None},  # ignored
        ]
        # Act
        missing = announce_missing_at_boot(tasks)
        # Assert
        assert missing == ["alpha", "beta"]

    def test_configured_agents_dropped_from_missing(self, env):
        # Arrange
        env.set(ENV_MAP, json.dumps({"alpha": "http://x/"}))
        tasks = [{"agent": "alpha"}, {"agent": "beta"}]
        # Act
        missing = announce_missing_at_boot(tasks)
        # Assert
        assert missing == ["beta"]
