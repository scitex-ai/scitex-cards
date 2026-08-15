#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WHO IS LOOKING AT THIS PAGE — the one place the board answers that.

WHY THIS EXISTS. The operator wants his cards from his phone, via scitex.ai
(2026-08-14: 「scitex.ai がどこかでケータイからカード使えるようになりますか？」→
「それが1番綺麗」). "His cards" needs a "his", and the board has never had one.
The authentication that exists today is ONE SHARED PASSWORD whose username is
discarded on purpose -- :mod:`._board_login` says so outright: "It is not a user
system." Its signed cookie payload is literally ``{"v": 1}``. So the gate knows
that SOMEONE knocked and nothing about WHO.

WHAT THIS IS. A thin adapter over that seam, and deliberately nothing more. It
does not authenticate, does not issue credentials and does not decide what a
viewer may see. It answers exactly one question -- "which board identity is
making this request?" -- so that the ``/mine`` endpoint has a subject to filter
on and every future caller asks the question in ONE place instead of each
growing its own guess.

THE PRECEDENCE CHAIN, and why it is ordered this way:

1. AN AUTHENTICATED DJANGO USER (``request.user``), read for its verified
   email. This is scitex-hub's seam. The board is already mounted INSIDE the
   hub's Django project under ``/apps/cards/`` (see ``views._include_root``),
   so when hub's django-allauth login runs, ``request.user`` is simply
   populated by the auth middleware and this branch starts working with NO
   code change here and no bespoke token, header or shared secret between us.
   Standalone, no auth middleware is installed, ``request.user`` is absent,
   and the branch is skipped -- not an error, just a deployment without hub.

2. AN IDENTITY CLAIM IN THE BOARD'S OWN SIGNED COOKIE. The escape hatch for a
   deployment that would rather hand the board a subject than a session. The
   cookie is already signed by :mod:`._board_login`; this reads the optional
   ``sub`` key out of the payload. Old ``{"v": 1}`` cookies carry no ``sub``,
   fall through to the next rung, and keep validating exactly as before --
   nobody is logged out by this module existing.

3. A CONFIGURED IDENTITY (``SCITEX_CARDS_IDENTITY``). Today's real deployment
   is one human behind one shared password, and for him the honest answer to
   "who are you?" is a value the operator sets once on the server. This is
   what makes the phone view work NOW, before hub's half lands.

4. NOBODY. Returns an anonymous viewer.

RUNG 4 IS THE IMPORTANT ONE. An unidentified viewer resolves to NOBODY, and
callers must render that as "we do not know who you are". It must never widen
to "show everything": on a public, multi-user scitex.ai that hands one visitor
another person's board, and it fails SILENTLY because a full board looks
exactly like a working feature. The repo's own no-silent-fallback rule, applied
where getting it wrong is a data leak rather than an inconvenience.

EMAIL -> BOARD IDENTITY, and the honest limit of it today. Cards are owned by
NAME (``assignee`` / ``agent``), so an email must be resolved to a registered
user. :func:`scitex_cards._users.resolve_user` matches a string EXACTLY against
each user's ``names`` alias list (and ``host_at_name``), so hub can link an
account today, with no schema change at all, by adding the verified email to
the user's aliases via the already-exported ``add_alias`` -- ``names`` is
explicitly an alias list, and an email is a legitimate alias.

What this deliberately does NOT do is guess. It will not match an email's
local part against a username: ``alice@example.com`` and ``alice@other.org``
have the same local part and are different people, so that guess silently
serves one person another's cards -- rung 4's failure mode wearing a
plausible-looking answer. An email that resolves to nobody is reported as
``unlinked``, which is a state a page can explain and a user can act on, and
which is exactly what a first-ever scitex.ai login will hit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "ENV_IDENTITY",
    "Viewer",
    "resolve_viewer",
]

#: Names the board identity for a deployment that has no per-user login --
#: i.e. today's single-shared-password board. Mirrors the naming of
#: ``SCITEX_CARDS_PASSWORD`` in :mod:`._board_auth`, which is the sibling
#: knob on the same deployment.
ENV_IDENTITY = "SCITEX_CARDS_IDENTITY"

#: Payload key carrying the subject in a signed board cookie (see rung 2).
COOKIE_SUBJECT_KEY = "sub"


@dataclass(frozen=True, slots=True)
class Viewer:
    """The resolved answer to "whose board is this request asking for?".

    Attributes
    ----------
    name : str | None
        The board identity -- the string that matches a card's ``assignee`` /
        ``agent``. ``None`` means the viewer is not identified, which is a
        legitimate answer and never a stand-in for "everyone".
    source : str
        Which rung of the precedence chain answered. Reported to callers so a
        page can explain itself and so a misconfiguration is diagnosable from
        the response instead of by reading this file. One of
        ``"session-user"`` / ``"cookie"`` / ``"configured"`` /
        ``"unlinked-email"`` / ``"anonymous"``.
    email : str | None
        The verified email the identity came from, when it came from one.
        Carried even in the ``unlinked-email`` case -- that is the whole
        value of that state: the page can name the address that needs
        linking rather than saying "unknown".
    """

    name: str | None = None
    source: str = "anonymous"
    email: str | None = None

    @property
    def is_known(self) -> bool:
        """Whether this viewer resolved to a board identity.

        The single predicate callers gate on, so "am I identified?" cannot
        drift into several subtly different truth tests across the codebase.
        """
        return bool(self.name)


def _session_email(request) -> str | None:
    """The verified email of an authenticated Django user, if there is one.

    ``getattr`` throughout because standalone there is no auth middleware, so
    ``request.user`` does not exist at all -- an absent user is "nobody is
    logged in", which is an ANSWER, not a failure. Anonymous users carry
    ``is_authenticated is False`` and are rejected by the same check.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    email = getattr(user, "email", None)
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _cookie_subject(request) -> str | None:
    """The ``sub`` claim from the board's signed cookie, when present.

    Verifies the signature via the same salt/max-age :mod:`._board_login`
    issues with, so an attacker cannot mint an identity by editing a cookie in
    devtools. A cookie that fails verification -- tampered or expired -- is
    reported as no subject rather than raising: the gate in
    :mod:`._board_auth` is what decides whether a bad cookie may proceed at
    all, and duplicating that decision here would create a second, divergent
    opinion about the same cookie.
    """
    from django.core import signing

    from ._board_login import COOKIE_MAX_AGE, COOKIE_NAME, SIGNING_SALT

    raw = (getattr(request, "COOKIES", None) or {}).get(COOKIE_NAME)
    if not raw:
        return None
    try:
        payload = signing.loads(raw, salt=SIGNING_SALT, max_age=COOKIE_MAX_AGE)
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    subject = payload.get(COOKIE_SUBJECT_KEY)
    if isinstance(subject, str) and subject.strip():
        return subject.strip()
    return None


def _configured_identity() -> str | None:
    """The operator-configured identity for a board with no per-user login."""
    return os.environ.get(ENV_IDENTITY, "").strip() or None


def _registered_name(candidate: str) -> str | None:
    """Resolve a string to a REGISTERED user's canonical name, else ``None``.

    Delegates to :func:`scitex_cards._users.resolve_user`, which is the
    registry's own resolution order (exact alias, ``host_at_name``, then a
    canonicalised retry). Reused rather than reimplemented so a rename or an
    alias that already works everywhere else works here too.

    Returns the user's FIRST alias -- the registry's canonical display name --
    so that two aliases of one person do not produce two different-looking
    viewers of the same board.

    THE USER REGISTRY RESOLVES ITS OWN STORE, and this deliberately does NOT
    hand it the caller's. The two live on DIFFERENT SUBSTRATES: cards are in
    the SQLite/Postgres database, while ``users:`` is a YAML section read with
    ``safe_load``. So a cards-store value forwarded here is fed to a YAML
    parser -- and on the hub that value is a real database path, supplied as a
    trusted request attribute by their tenancy middleware, which makes
    ``load_users`` decode a SQLite header as UTF-8 and raise. Caught exactly
    that way while writing the registry tests, which passed the scratch
    ``SCITEX_CARDS_DB`` in and got ``UnicodeDecodeError: 'utf-8' codec can't
    decode byte 0x89``, with ``b"SQLite format 3\\x00"`` in the traceback.

    It never fired locally because ``read_store`` returns ``None`` on a board
    with no tenancy middleware, so the parameter was always the one value that
    happened to be safe -- a defect reachable only on the deployment this
    feature exists for.
    """
    from .._users import resolve_user

    user = resolve_user(candidate)
    if user is None or not user.names:
        return None
    return user.names[0]


def resolve_viewer(request) -> Viewer:
    """Resolve the board identity behind ``request``. Never raises.

    Walks the precedence chain documented in the module docstring and returns
    the first rung that answers. See that docstring for why the order is what
    it is, and why rung 4 must not widen into "show everything".

    TAKES NO STORE, on purpose -- see :func:`_registered_name`. The user
    registry resolves its own, and handing it a cards store crashes it.

    Parameters
    ----------
    request
        Any object with the Django request surface this reads: ``.user`` and
        ``.COOKIES``. Both are read defensively, so a minimal test double
        works without being taught fields it does not use.

    Returns
    -------
    Viewer
        Always a ``Viewer``; an unidentified request yields the anonymous one
        rather than ``None``, so callers cannot forget the case exists.
    """
    email = _session_email(request)
    if email:
        # An authenticated session is the STRONGEST claim available, so it is
        # never silently downgraded to a weaker rung. If its email is not
        # linked to a board identity, that is reported AS SUCH -- falling
        # through to the configured identity here would serve a logged-in
        # stranger the operator's own cards, which is precisely the leak this
        # module is ordered to prevent.
        name = _registered_name(email)
        if name:
            return Viewer(name=name, source="session-user", email=email)
        return Viewer(name=None, source="unlinked-email", email=email)

    subject = _cookie_subject(request)
    if subject:
        name = _registered_name(subject)
        # An unregistered cookie subject is used VERBATIM: card owners predate
        # the user registry and are still free-form strings, so refusing an
        # unregistered name would lock out exactly the boards that never
        # registered anybody. The registry, when it knows the name, only
        # canonicalises it.
        return Viewer(name=name or subject, source="cookie")

    configured = _configured_identity()
    if configured:
        name = _registered_name(configured)
        return Viewer(name=name or configured, source="configured")

    return Viewer()


# EOF
