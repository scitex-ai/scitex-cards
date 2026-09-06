#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The slice-3 concrete channel: Telegram Bot API delivery via STDLIB HTTP.

:class:`TelegramChannel` POSTs one ``sendMessage`` call per notification to
``https://api.telegram.org/bot<TOKEN>/sendMessage``. It is creds-DEPENDENT —
the bot token is read (lazily) from ``SCITEX_CARDS_TELEGRAM_BOT_TOKEN`` (or an
``__init__`` override). ``address`` is the destination Telegram ``chat_id``.

Why stdlib ``urllib`` (and a transport seam)
--------------------------------------------
The delivery rail has ZERO heavy deps and ZERO external imports, so HTTP goes
through stdlib :mod:`urllib.request`. To keep the channel testable WITHOUT
mocks or a live network, the wire call is isolated behind an injectable
``http_post(url, data) -> (status_code, headers, body)`` seam: production
uses :func:`_urllib_post`; tests inject a REAL fake recorder/responder.

Status mapping (per scitex-dev's guidance)
------------------------------------------
* ``2xx`` → ``sent`` (handed off to Telegram).
* ``429`` → ``failed`` WITH ``retry_after`` parsed from the ``Retry-After``
  header or telegram's ``parameters.retry_after`` JSON field. RETRYABLE, NOT
  terminal — the ledger honors the hint and rides backoff toward success.
* ``403`` / other ``4xx`` → ``failed`` with NO ``retry_after`` (a persistently
  bad chat / blocked bot is a real comm-miss; it rides exponential backoff to
  ``failed_terminal`` so the misconfig surfaces).
* ``5xx`` / network error / urllib raising → ``failed`` (retryable; the loop
  also catches a raise as a failed attempt).

Rate limits: the Bot API allows ~30 msg/s globally and ~1 msg/s per chat. We
do NOT add an internal rate-limiter in this slice (the daemon tick + per-
recipient cadence keep volume low) but DO handle the 429 path above. An
internal token-bucket limiter is a future option if volume ever grows.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from .._channel import DeliveryResult, Status

#: Env var the bot token is read from (lazily) when no override is given.
TOKEN_ENV = "SCITEX_CARDS_TELEGRAM_BOT_TOKEN"

#: Telegram Bot API base; the token is interpolated into the path segment.
API_BASE = "https://api.telegram.org"

#: Network timeout (seconds) for the stdlib POST.
HTTP_TIMEOUT_SEC = 15

#: An ``http_post`` returns ``(status_code, headers, body)``.
HttpPost = Callable[[str, bytes], "tuple[int, dict, str]"]


def _urllib_post(url: str, data: bytes) -> "tuple[int, dict, str]":
    """Default transport: POST ``data`` (form-encoded) via stdlib urllib.

    Returns ``(status_code, headers_dict, body_text)``. A non-2xx HTTP status
    arrives as :class:`urllib.error.HTTPError`, which IS a response object —
    we read its status / headers / body so the caller can map 429 / 403 / 5xx
    uniformly. A genuine transport failure (DNS, connection reset) raises
    :class:`urllib.error.URLError`, which we let propagate to the caller's
    except handler (mapped to a retryable ``failed``).
    """
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — body is best-effort on an error
            body = ""
        return exc.code, dict(exc.headers or {}), body


def _message_text(notification: dict) -> str:
    """Build the message text from a notification (body, then a fallback).

    Prefer the human-readable ``body``; when absent, synthesise a terse line
    from ``event_type`` + ``card_id`` so a notification is NEVER sent empty.
    """
    body = (notification.get("body") or "").strip()
    if body:
        return body
    event_type = notification.get("event_type", "?")
    card_id = notification.get("card_id", "?")
    return f"[{event_type}] card {card_id}"


def _parse_retry_after(headers: dict, body: str) -> float | None:
    """Extract a 429 retry hint from the ``Retry-After`` header or JSON.

    Telegram returns the wait either as the HTTP ``Retry-After`` header or as
    ``parameters.retry_after`` in the JSON body. Prefer the header; fall back
    to the JSON. Returns ``None`` when neither is present/parseable.
    """
    raw = None
    for key, value in (headers or {}).items():
        if isinstance(key, str) and key.lower() == "retry-after":
            raw = value
            break
    if raw is not None:
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            pass
    try:
        payload = json.loads(body) if body else {}
        hint = (payload.get("parameters") or {}).get("retry_after")
        if hint is not None:
            return float(hint)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


class TelegramChannel:
    """Deliver a notification via the Telegram Bot ``sendMessage`` endpoint.

    Implements the :class:`~scitex_cards._delivery._channel.DeliveryChannel`
    Protocol. Token is resolved lazily (env or override); the wire POST is
    injectable for testing.
    """

    #: Registry + ledger key for this channel.
    name = "telegram"

    def __init__(
        self,
        *,
        token: str | None = None,
        http_post: HttpPost | None = None,
    ):
        """Create the channel.

        Parameters
        ----------
        token : str | None
            Override the bot token. When ``None`` (the default) the token is
            read LAZILY from :data:`TOKEN_ENV` inside :meth:`deliver`, so a
            channel can be constructed (and registered) before the env is set.
        http_post : HttpPost | None
            Transport seam ``http_post(url, data) -> (status, headers, body)``.
            Defaults to :func:`_urllib_post`. Tests inject a real fake.
        """
        self._token = token
        self._http_post: HttpPost = http_post or _urllib_post

    def _resolve_token(self) -> str | None:
        """Return the bot token (override first, else the env), or ``None``."""
        if self._token:
            return self._token
        return os.environ.get(TOKEN_ENV) or None

    def deliver(
        self,
        *,
        recipient: str,
        address: str,
        notification: dict,
    ) -> DeliveryResult:
        """POST the notification to Telegram and map the HTTP outcome.

        ``address`` is the destination ``chat_id``. No token → fail-loud
        ``failed`` (rides to terminal so the misconfig surfaces). See the
        module docstring for the full status mapping.
        """
        token = self._resolve_token()
        if not token:
            return DeliveryResult(
                status=Status.FAILED,
                channel=self.name,
                detail=f"no telegram bot token (set {TOKEN_ENV})",
            )

        text = _message_text(notification)
        url = f"{API_BASE}/bot{token}/sendMessage"
        from urllib.parse import urlencode

        data = urlencode({"chat_id": address, "text": text}).encode("utf-8")

        try:
            status_code, headers, body = self._http_post(url, data)
        except Exception as exc:  # noqa: BLE001 — network/urllib → retryable
            return DeliveryResult(
                status=Status.FAILED,
                channel=self.name,
                detail=f"telegram transport error: {type(exc).__name__}: {exc}",
            )

        if 200 <= status_code < 300:
            return DeliveryResult(
                status=Status.SENT,
                channel=self.name,
                detail=f"telegram sendMessage chat_id={address}",
            )

        if status_code == 429:
            # NOTE: a 429 currently counts toward MAX_ATTEMPTS like any failure,
            # so sustained per-chat throttling could false-terminal a reachable
            # chat. Benign for now — notifyd surfaces it with this "rate-limited
            # (429)" detail so it's visible + clearable, and volume is low.
            # Revisit (separate/higher retry budget for 429) only if
            # throttle-induced terminals are ever actually observed.
            retry_after = _parse_retry_after(headers, body)
            return DeliveryResult(
                status=Status.FAILED,
                channel=self.name,
                detail=f"telegram rate-limited (429) chat_id={address}",
                retry_after=retry_after,
            )

        # 403 (blocked / bad chat) + every other 4xx: a real comm-miss. NO
        # retry_after → rides exponential backoff to failed_terminal.
        if 400 <= status_code < 500:
            return DeliveryResult(
                status=Status.FAILED,
                channel=self.name,
                detail=(
                    f"telegram rejected ({status_code}) chat_id={address}: "
                    f"{body[:200]}"
                ),
            )

        # 5xx (or anything else): transient server error → retryable.
        return DeliveryResult(
            status=Status.FAILED,
            channel=self.name,
            detail=f"telegram server error ({status_code}) chat_id={address}",
        )


__all__ = ["TOKEN_ENV", "TelegramChannel"]

# EOF
