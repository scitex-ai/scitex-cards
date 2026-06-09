#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Env-var-driven HTTP Basic Auth middleware for the scitex-todo board.

The board is read-AND-write (drag-reorder persists, Resolve buttons mutate the
YAML store). When the operator exposes the board off ``127.0.0.1`` — e.g. via
Cloudflare Tunnel — anyone who can reach the bind address would otherwise be
able to read AND modify the task store. This middleware is the prerequisite
auth layer.

Contract
--------
* Env var: ``SCITEX_TODO_BASIC_AUTH=user:password`` (single-user; minimal-viable).
* When UNSET: the middleware is a passthrough — board behaves exactly as today
  so localhost-only dev is unchanged.
* When SET: every request returns ``401 Unauthorized`` with
  ``WWW-Authenticate: Basic realm="scitex-todo"`` unless the request carries a
  valid ``Authorization: Basic <base64(user:password)>`` header.
* Constant-time comparison via ``hmac.compare_digest`` so timing leaks can't
  recover the credential a character at a time.

A malformed Authorization header (missing ``Basic ``, undecodable base64, no
``:`` separator, non-utf8 bytes) is treated as "no creds" and yields a 401 —
the middleware never raises, so a probing client can't crash the board.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os

from django.http import HttpResponse

_ENV_KEY = "SCITEX_TODO_BASIC_AUTH"
_REALM = "scitex-todo"


def _expected_credentials() -> tuple[str, str] | None:
    """Parse ``$SCITEX_TODO_BASIC_AUTH`` into ``(user, password)``.

    Returns ``None`` when the env var is unset / empty / malformed (no ``:``
    separator) so the middleware can passthrough — same shape as "auth not
    configured". The malformed case is intentionally lenient: we'd rather
    leave the board reachable than hard-fail startup on a typo.
    """
    raw = os.environ.get(_ENV_KEY, "")
    if not raw:
        return None
    if ":" not in raw:
        return None
    user, _, password = raw.partition(":")
    if not user or not password:
        return None
    return user, password


def _parse_authorization_header(header_value: str) -> tuple[str, str] | None:
    """Decode an ``Authorization: Basic ...`` header into ``(user, password)``.

    Returns ``None`` on ANY malformedness (missing scheme, undecodable b64, no
    ``:`` separator, non-utf8 bytes). The caller treats ``None`` as "no creds"
    and returns 401 — the middleware never raises.
    """
    if not header_value:
        return None
    parts = header_value.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, encoded = parts
    if scheme.lower() != "basic":
        return None
    try:
        decoded_bytes = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if ":" not in decoded:
        return None
    user, _, password = decoded.partition(":")
    return user, password


def _unauthorized_response() -> HttpResponse:
    """Build the 401 response with the standard ``WWW-Authenticate`` challenge."""
    response = HttpResponse("Unauthorized", status=401, content_type="text/plain")
    response["WWW-Authenticate"] = f'Basic realm="{_REALM}"'
    return response


class BasicAuthMiddleware:
    """Gate every board request on ``SCITEX_TODO_BASIC_AUTH``.

    The expected credentials are read on EACH request (not at process start)
    so the operator can ``export SCITEX_TODO_BASIC_AUTH=...`` after the
    server is already running — useful when wiring up Cloudflare Tunnel +
    learning what the auth flow looks like without a restart loop.

    The check is constant-time: both the username and the password are
    compared with ``hmac.compare_digest`` against the expected values, so a
    brute-force attacker can't recover the credential a character at a time
    via response-timing.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        expected = _expected_credentials()
        if expected is None:
            # Auth NOT configured -> passthrough. Preserves the
            # "localhost dev with no env var" UX.
            return self.get_response(request)

        provided = _parse_authorization_header(
            request.META.get("HTTP_AUTHORIZATION", "")
        )
        if provided is None:
            return _unauthorized_response()

        expected_user, expected_password = expected
        provided_user, provided_password = provided

        user_ok = hmac.compare_digest(
            expected_user.encode("utf-8"), provided_user.encode("utf-8")
        )
        password_ok = hmac.compare_digest(
            expected_password.encode("utf-8"), provided_password.encode("utf-8")
        )

        if not (user_ok and password_ok):
            return _unauthorized_response()

        return self.get_response(request)


# EOF
