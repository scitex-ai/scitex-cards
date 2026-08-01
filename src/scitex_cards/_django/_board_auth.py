#!/usr/bin/env python3
"""A password on the board, so it can leave loopback without leaving it open.

WHY THIS EXISTS. The standalone board had no authentication of any kind: no
``django.contrib.auth``, no sessions, no login. That was survivable only because
it bound 127.0.0.1, where the operating system is the access control. The
operator wants it from their phone, which means binding the LAN, which means the
OS stops being the gate and there is nothing behind it.

WHAT IT IS AND IS NOT. HTTP Basic over the LAN. Basic sends the password
base64-encoded, which is encoding, NOT encryption -- anyone who can watch the
network sees it. On a LAN with one human that is a real and accepted risk; over
the internet it would not be, which is why exposure to a public hostname keeps
requiring the tunnel's authenticator in front and does not treat this password
as sufficient.

THE PART THAT MATTERS MORE THAN THE PASSWORD ITSELF is in settings.py: opening
the board beyond loopback without setting a password RAISES AT IMPORT. The
unsafe combination is unreachable rather than discouraged. A comment saying "only
open this on a trusted network" is advice, and advice is what you write when the
code will let you do the wrong thing.

CONSTANT-TIME COMPARISON is used because a byte-by-byte early return leaks the
password's prefix to anyone who can time the responses. That attack is
impractical over a LAN and costs one function call to remove, so there is no
reason to leave it.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponse

__all__ = [
    "REALM",
    "BoardPasswordMiddleware",
    "challenge",
    "is_authorised",
    "resolve_password",
]

REALM = "SciTeX Cards"

_ENV_VAR = "SCITEX_CARDS_PASSWORD"


def resolve_password() -> str:
    """The configured password, or ``""`` when the board is unprotected."""
    return os.environ.get(_ENV_VAR, "").strip()


def is_authorised(header: str | None, password: str) -> bool:
    """Whether ``header`` (a raw ``Authorization`` value) carries ``password``.

    An empty ``password`` means no gate is configured, so everything is
    authorised -- the loopback default. Callers that must not be open rely on
    settings.py refusing to start, not on this returning False.
    """
    if not password:
        return True
    if not header:
        return False

    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False

    # Everything after the FIRST colon is the password: a username cannot
    # contain one, a password can, and splitting on the last colon would
    # silently reject any password containing ":".
    _, separator, supplied = decoded.partition(":")
    if not separator:
        return False

    return secrets.compare_digest(supplied, password)


def challenge() -> HttpResponse:
    """The 401 that makes a browser show its password prompt."""
    response = HttpResponse(
        "This board is password protected.",
        content_type="text/plain",
        status=401,
    )
    response["WWW-Authenticate"] = f'Basic realm="{REALM}", charset="UTF-8"'
    return response


class BoardPasswordMiddleware:
    """Reject every request that does not carry the configured password.

    Nothing is exempt -- not static files, not the health endpoint. An exemption
    list is a second place for the gate's shape to be wrong, and the board has
    no endpoint whose contents are less sensitive than the rest.
    """

    def __init__(self, get_response, password: str | None = None) -> None:
        self.get_response = get_response
        # Resolved ONCE at startup, not per request: a password that can be
        # changed under a running server is a password whose current value
        # nobody can state.
        self.password = resolve_password() if password is None else password
        if not self.password:
            # Django drops the middleware entirely, so the loopback default
            # pays nothing for a feature it is not using.
            raise MiddlewareNotUsed

    def __call__(self, request):
        header = request.META.get("HTTP_AUTHORIZATION")
        if not is_authorised(header, self.password):
            return challenge()
        return self.get_response(request)


# EOF
