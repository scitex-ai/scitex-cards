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
    "EXTERNAL_ENFORCERS",
    "ExposureWithoutPasswordError",
    "PublicExposureWithoutAuthError",
    "assert_exposure_is_authenticated",
    "assert_public_exposure_is_authenticated",
]


class ExposureWithoutPasswordError(RuntimeError):
    """Raised when the board would answer off-box requests with no password."""


class PublicExposureWithoutAuthError(RuntimeError):
    """Raised when a public hostname would be bound with nothing asserting auth."""


#: External systems this board will accept AS its authentication boundary.
#:
#: A CLOSED SET on purpose. If any non-empty string counted, then a typo, a
#: leftover export, or a value copied from an unrelated example would disarm the
#: refusal silently -- and the whole point of this gate is that the unsafe state
#: cannot be reached by accident. A member of this set is a claim someone wrote
#: down, in a name that means something, and can be held to.
EXTERNAL_ENFORCERS = frozenset({"cloudflare-access"})


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
    "requests from the internet -- but nothing here asserts that anything "
    "authenticates them.\n\n"
    "DJANGO_SECRET_KEY is already required on this path and is NOT that "
    "assertion: it makes session and CSRF signatures unforgeable, which is a "
    "different property. A board with a perfect secret key and no login is a "
    "board anyone can read and write.\n\n"
    "Choose one, explicitly:\n"
    "  export SCITEX_CARDS_PASSWORD=\"$(python -c 'import secrets; "
    "print(secrets.token_urlsafe(12))')\"\n"
    "    -- the board authenticates its own callers, and a proxy in front "
    "becomes defence in depth.\n"
    "  export SCITEX_CARDS_EXTERNAL_AUTH=cloudflare-access\n"
    "    -- you are stating that something in front authenticates every "
    "request before it arrives. This process CANNOT verify that, so it is "
    "recorded as your claim. Verify it yourself: an unauthenticated GET to "
    "https://{host} must return a challenge, never a 200.\n\n"
    "Known external enforcers: {known}"
)

_UNKNOWN_ENFORCER_MESSAGE = (
    "SCITEX_CARDS_EXTERNAL_AUTH is {value!r}, which names no enforcer this "
    "board knows about, so it asserts nothing. Known values: {known}. If a new "
    "one belongs here, add it to EXTERNAL_ENFORCERS deliberately -- an "
    "unrecognised value must not be able to open the board by looking like it "
    "meant something."
)


def assert_public_exposure_is_authenticated(
    public_host: str, password: str, external_auth: str
) -> None:
    """Raise unless binding a PUBLIC hostname comes with a named auth boundary.

    THE FAILURE THIS EXISTS FOR: ``SCITEX_CARDS_PUBLIC_HOST`` used to bind a
    public hostname while asserting only that ``DJANGO_SECRET_KEY`` was set.
    That check measures a DIFFERENT property -- signature integrity -- so a
    deployment behind an enforcing Cloudflare Access policy and a deployment
    behind nothing at all produced byte-identical settings. Two states, one
    representation, and the unsafe one rendered as the safe one at exactly the
    moment it mattered. Worse, a security-shaped check sitting on that branch
    reads to the next maintainer as "this path is guarded", which ends the
    question instead of raising it.

    So the rule is not "a public board must have a password" -- Access-only IS a
    legitimate deployment, and the process genuinely cannot see whether Access
    is enforcing. The rule is that SILENCE IS NOT THE PERMISSIVE CASE. Either
    the board authenticates its own callers, or someone names the system that
    does, in a value that is auditable and that they can be held to. Omission
    reaches neither, so omission refuses.

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
    claimed = external_auth.strip()
    known = ", ".join(sorted(EXTERNAL_ENFORCERS))
    if claimed and claimed not in EXTERNAL_ENFORCERS:
        raise PublicExposureWithoutAuthError(
            _UNKNOWN_ENFORCER_MESSAGE.format(value=external_auth, known=known)
        )
    if claimed:
        return
    raise PublicExposureWithoutAuthError(
        _PUBLIC_MESSAGE.format(host=public_host.strip(), known=known)
    )


# EOF
