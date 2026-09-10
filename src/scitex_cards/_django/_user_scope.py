#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Cards USER-SCOPE boundary — the single point where "who/what is this
request" is decided. Own-ledger #230/#231, and the Cards portions of #48/#239.

WHY THIS MODULE EXISTS
----------------------
#230 "use the SciTeX common User / Project / Permission infrastructure" and
#231 "separate Cards-specific logic from the infrastructure" together mean:
the decision of WHICH user (and therefore which store) a request acts as must
live in ONE named place that consumes the COMMON identity, and the Cards-SPECIFIC
task/board logic (graph, timeline, DM content, matrix, export) must stay a
consumer of that boundary rather than re-deriving it ad hoc in each view.

Before this module, "who is looking" was resolved inline in
``handlers.dm._author_of`` and the store was resolved inline in
``handlers.dm`` / ``views`` — correct, but scattered, so the invariant that
Cards is USER-scoped (not project-scoped) had no single owner and no single
test could pin it.

WHAT "USER-SCOPED" MEANS HERE (and the negative property that follows)
----------------------------------------------------------------------
A user-scoped app's tenancy comes from the authenticated user, not from a
project. So the boundary reads exactly two COMMON inputs:

  * ``request.user``        — the SciTeX common authenticated Django user, set
                              by the hub's auth layer (Cards has no user model
                              of its own — #229, no AUTH_USER_MODEL).
  * ``request.scitex_store`` — the store the hub's tenancy middleware resolved
                              for THIS user (an unforgeable request ATTRIBUTE;
                              see ``_request_store``). The browser cannot set
                              it, and ``?store=`` is inert on any exposed board.

and it reads NO project selector. #48 says project-scope vs user-scope is
"specified by the leaf package side" — so this module DECLARES Cards' scope
(:data:`SCOPE`), which ``manifest.json`` mirrors, and a test pins that the
boundary never consults ``request.project`` / ``request.current_project`` /
``?project=``. That is the "no project selector is injected for user-scoped
Cards" property, made verifiable rather than assumed.

STANDALONE FALLBACK
-------------------
On the loopback standalone board there is no auth layer, so the sole caller IS
the operator at their own keyboard; :func:`current_user` falls back to
``OPERATOR_NAME`` in that (and only that) case. This is documented behaviour,
not a hole: a hub request is always authenticated, so the fallback is
unreachable there.
"""

from __future__ import annotations

from typing import Any, Optional

#: Cards is a USER-SCOPED app. This is the leaf-package-side declaration #48
#: asks for ("this should be specified by the leaf package side"); it is also
#: mirrored in ``manifest.json`` (``"scope": "user"``) so the hub launcher and
#: any scope-aware consumer read it from the canonical source. A user-scoped app
#: takes its tenancy from the authenticated user, so NO project selector is a
#: valid tenancy input here.
SCOPE = "user"

#: The request attribute the hub's tenancy middleware sets (server-side). Cards
#: reads its store scope from this, never from a client-controllable query param.
#: Kept beside SCOPE so the boundary's two common inputs live in one place.
STORE_ATTR = "scitex_store"


def current_user(request: Any) -> str:
    """The authenticated principal this request acts as — the COMMON identity.

    The single "who is looking" answer for a Cards request. Resolved from
    ``request.user`` (the hub's common Django user); falls back to
    ``OPERATOR_NAME`` only when there is no auth layer (standalone loopback).

    DELIBERATELY IGNORES any project selector: it does not read
    ``request.project``, ``request.current_project``, or a ``?project=`` param,
    because a user-scoped app is not re-scoped by a project switch. Pinned by
    ``test_user_scope_boundaries.py``.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        name = (getattr(user, "get_username", lambda: "")() or "").strip()
        if name:
            return name
    from scitex_cards._threads import OPERATOR_NAME

    return OPERATOR_NAME


def user_scoped_store(request: Any) -> Optional[str]:
    """The store this USER request may read/write — the COMMON tenancy input.

    Reads only the trusted ``request.scitex_store`` attribute the hub's
    tenancy middleware set for this user. Returns ``None`` when no trusted
    scope was supplied (the standalone board then resolves its own ambient
    store server-side). A browser-supplied ``?store=`` / ``?project=`` can NOT
    select this: an attribute cannot be forged over HTTP, and a project
    selector is not a tenancy input for a user-scoped app.
    """
    from ._request_store import STORE_REQUEST_ATTR

    return getattr(request, STORE_REQUEST_ATTR, None)


__all__ = ["SCOPE", "STORE_ATTR", "current_user", "user_scoped_store"]
