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
    "CHALLENGE_BODY",
    "challenge",
    "is_authorised",
    "resolve_password",
]

_ENV_VAR = "SCITEX_CARDS_PASSWORD"

#: Shown by the browser INSIDE its own password dialog, so the dialog can say
#: where its answer lives. It previously read "SciTeX Cards" and nothing else.
#:
#: THAT WAS THE BUG, and it is a usability bug with a security consequence.
#: Measured 2026-08-02: the operator opened their own board, met a credential
#: prompt they had not configured, and had no path from the dialog to the
#: secret -- "何のパスワードかってまず心当たりがなくて、ユーザネームもわからない".
#: An anonymous credential prompt is indistinguishable from a phishing one, so a
#: user who cannot tell them apart is being trained to type secrets into
#: whichever dialog appears. Naming the source is what makes the prompt
#: refusable: if the named source is not one you control, do not answer it.
#:
#: Kept short because browsers truncate long realms; the full recovery
#: instructions live in :data:`CHALLENGE_BODY`, which Chrome renders when the
#: dialog is cancelled. Contains no quote or backslash -- a realm is an HTTP
#: quoted-string and neither can be escaped portably.
REALM = f"SciTeX Cards - password is {_ENV_VAR} on the server"

#: The page behind the dialog. Answers the three questions the dialog cannot:
#: what the username is (nothing -- it is discarded), where the password is
#: kept, and what to do if you did not set one.
CHALLENGE_BODY = f"""\
SciTeX Cards is asking for a password because this board is reachable from
somewhere other than loopback.

  Username   ignored entirely. Leave it blank, or type anything.
  Password   the value of {_ENV_VAR} in the environment of the process
             serving this board.

To read it on the machine running the board:

  systemctl --user show scitex-todo.dashboard.service -p Environment
  grep -rh {_ENV_VAR} ~/.config/systemd/user/

IF YOU DID NOT SET THIS PASSWORD, DO NOT TYPE ONE. A credential prompt that
cannot tell you where its answer lives is the same shape as a phishing prompt.
Ask whoever runs this board before answering it.
"""


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
    """The 401 that makes a browser show its password prompt, and NAMES ITS SOURCE.

    Both halves carry the source. The realm is what the browser prints inside
    its own dialog; the body is what it renders when the dialog is cancelled.
    Either route now reaches an answer, which the previous
    "This board is password protected." did not -- it stated the fact the user
    could already see and withheld the only thing they needed.
    """
    response = HttpResponse(
        CHALLENGE_BODY,
        content_type="text/plain; charset=utf-8",
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
