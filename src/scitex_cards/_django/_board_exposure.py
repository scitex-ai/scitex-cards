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

__all__ = ["ExposureWithoutPasswordError", "assert_exposure_is_authenticated"]


class ExposureWithoutPasswordError(RuntimeError):
    """Raised when the board would answer off-box requests with no password."""


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


# EOF
