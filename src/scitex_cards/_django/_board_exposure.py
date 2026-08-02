#!/usr/bin/env python3
"""The rule that a board reachable from off-box must have a password.

WHY THIS IS A SEPARATE MODULE FROM ``_board_auth``, and why it imports nothing:
settings.py has to call this while Django is still loading its settings, so
anything imported here is imported at that moment. ``_board_auth`` needs
``django.http``, which reads settings, and importing it from settings.py is a
cycle waiting for the right conditions. A pure module has no such hazard.

The second reason is testability, and it is the one that produced this file. The
guard first lived inline in settings.py, so testing it meant reloading the
settings module with a mutated environment. That worked in isolation and threw an
order-dependent teardown error in the full file: reloading a module Django holds a
live reference to is fragile, and the fragility belonged to the TEST HARNESS, not
to the thing being tested. A pure function needs no reload, no environment, and
no ordering -- so the test measures the rule instead of measuring Django's import
machinery.
"""

from __future__ import annotations

__all__ = [
    "ExposureWithoutPasswordError",
    "PublicExposureWithoutAuthError",
    "assert_exposure_is_authenticated",
    "assert_public_exposure_is_authenticated",
]


class ExposureWithoutPasswordError(RuntimeError):
    """Raised when the board would answer off-box requests with no password."""


class PublicExposureWithoutAuthError(RuntimeError):
    """Raised when a public hostname would be bound with no way to authenticate."""


_MESSAGE = (
    "SCITEX_CARDS_ALLOWED_HOSTS is set ({hosts!r}), so this board would answer "
    "requests from outside this machine -- but SCITEX_CARDS_PASSWORD is not "
    "set, and the board has no login of its own. Every DM and every card would "
    "be readable AND writable by anyone on that network. Set a password:\n"
    "  export SCITEX_CARDS_PASSWORD=\"$(python -c 'import secrets; "
    "print(secrets.token_urlsafe(12))')\""
)


def assert_exposure_is_authenticated(extra_hosts: str, password: str) -> None:
    """Raise unless off-box reachability comes with a password.

    ``extra_hosts`` is the raw ``SCITEX_CARDS_ALLOWED_HOSTS`` value and
    ``password`` the raw ``SCITEX_CARDS_PASSWORD``. Empty (or whitespace-only)
    hosts means loopback-only, which needs no password -- the operating system
    is the access control there.

    Whitespace is stripped on BOTH sides deliberately: a password of " " is not a
    password, and treating it as one would let a stray space in a shell export
    silently open the board.
    """
    if not extra_hosts.strip():
        return
    if password.strip():
        return
    raise ExposureWithoutPasswordError(_MESSAGE.format(hosts=extra_hosts))


_PUBLIC_MESSAGE = (
    "SCITEX_CARDS_PUBLIC_HOST is set ({host!r}), so this board would answer "
    "requests from the internet -- but it has no way to authenticate them.\n\n"
    "DJANGO_SECRET_KEY is already required on this path and is NOT "
    "authentication: it makes session and CSRF signatures unforgeable, which is "
    "a different property. A board with a perfect secret key and no login is a "
    "board anyone can read and write.\n\n"
    "The board authenticates its own callers, the way sshd does -- a key or a "
    "password, never neither. Today that means:\n"
    "  export SCITEX_CARDS_PASSWORD=\"$(python -c 'import secrets; "
    "print(secrets.token_urlsafe(12))')\"\n\n"
    "A proxy in front (Cloudflare Access, the hub's own login) is a SECOND "
    "layer, never the only one. This process cannot see whether such a proxy is "
    "enforcing, so a board that relied on it alone would be indistinguishable "
    "from a board with nothing in front at all."
)


def assert_public_exposure_is_authenticated(public_host: str, password: str) -> None:
    """Raise unless a board bound to a PUBLIC hostname can authenticate callers.

    THE FAILURE THIS EXISTS FOR: ``SCITEX_CARDS_PUBLIC_HOST`` used to bind a
    public hostname while asserting only that ``DJANGO_SECRET_KEY`` was set.
    That check measures a DIFFERENT property -- signature integrity -- so a
    deployment behind an enforcing Cloudflare Access policy and a deployment
    behind nothing at all produced byte-identical settings. Two states, one
    representation, and the unsafe one rendered as the safe one at exactly the
    moment it mattered. Worse, a security-shaped check sitting on that branch
    reads to the next maintainer as "this path is guarded", which ends the
    question instead of raising it.

    THE BOARD ALWAYS AUTHENTICATES. That is the operator's ruling and it is the
    simpler rule: a key or a password, chosen the way sshd chooses, never
    neither. An earlier version of this gate also accepted "something in front
    authenticates for me" as a written claim. That was a third concept nobody
    asked for, and it was the only way to reach a naked origin -- so it is gone.
    A proxy is a second layer now, never the boundary.

    Which matters most for the deployment that is coming: a board reached
    through a Cloudflare Tunnel is protected by Access AND by its own login, and
    a misconfigured Access policy stops being a breach. It also keeps the
    standalone case honest, since standalone is the same code path with no proxy
    at all.

    ``public_host`` empty (or whitespace-only) means no public binding, which
    needs nothing -- the loopback default is the access control there. Whitespace
    is stripped on every input for the reason the LAN twin documents: a value of
    " " is not a value, and treating it as one would let a stray space in a shell
    export silently open the board.
    """
    if not public_host.strip():
        return
    if password.strip():
        return
    raise PublicExposureWithoutAuthError(
        _PUBLIC_MESSAGE.format(host=public_host.strip())
    )


# EOF
