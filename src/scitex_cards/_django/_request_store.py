#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which store one HTTP request may touch — the single place that decides.

Two channels can name a store on an inbound request, and they do not deserve
the same trust:

``request.scitex_store`` (:data:`STORE_REQUEST_ATTR`)
    A request ATTRIBUTE. Only code already running inside this process can set
    it — a tenancy middleware, typically. A remote caller cannot forge one.

``?store=`` in the query string
    Entirely under the caller's control.

:func:`write_store` accepts only the first. :func:`read_store` prefers the
first and falls back to the second.

THE PREFERENCE IS THE POINT, not the fallback. Before this module, reads
consulted the QUERY ONLY, in two hand-copied lines (``views.py`` and
``handlers/dm.py``). scitex-hub's ``TodoBoardTenancyMiddleware`` therefore had
to keep OVERWRITING ``request.GET["store"]`` with its server-resolved value,
and its own comment (middleware.py:191) names the cost exactly:

    "Injecting tenancy via ?store= put a security-critical value in the exact
     namespace the attacker controls, so downstream ... our injected store and
     a hostile ?store= are byte-identical — indistinguishable by construction."

They are right, and it was our defect: the two values are distinguishable HERE,
where one arrives as an attribute and the other as a query parameter. Once the
attribute WINS, a caller-supplied ``?store=`` is inert on any deployment that
runs a tenancy middleware — defended by construction rather than by a
neighbouring package remembering to overwrite it. That is what lets hub delete
its legacy injection (middleware.py:200-213, "DELETE THIS BLOCK once
scitex-cards ships attribute support").

WHY THE QUERY FALLBACK IS STILL HERE, and what it is waiting for. The
standalone loopback board and the whole Django test suite select a store
through ``?store=``; removing it in the same change would break both, and
removing it BEFORE hub deletes its injection would drop tenancy for a release
window — the upstream would fall back to its ambient canonical store, one store
for every tenant. Alias first, then remove. Tracked on
``cards-read-path-ignores-the-trusted-store-attribute-20260806``; the removal
step has to name what replaces the seam for the standalone board and the tests
before it can run.

WHAT IS DELIBERATELY NOT A CHANNEL: the request BODY. A POST that names its own
store is the same forgeable surface as the query with none of the historical
justification, so no reader here looks at one, and
``tests/.../test_request_store_is_the_sole_store_reader.py`` fails the build if
any handler grows one.
"""

from __future__ import annotations

from typing import Any, Optional

#: Request attribute a TRUSTED middleware sets to select this request's store.
#: An attribute cannot be forged over HTTP; a query parameter can.
STORE_REQUEST_ATTR = "scitex_store"

#: The query parameter the standalone board and the test suite still use.
STORE_QUERY_PARAM = "store"


def _trusted(request: Any) -> Optional[str]:
    """The middleware-supplied store, normalised to ``str``, or ``None``.

    Normalising matters more than it looks. scitex-hub sets the attribute to a
    ``Path`` while it injected the query as ``str(store)``, so honouring the
    attribute without this would hand the resolver a different TYPE than the
    value it has been resolving all along — a silent behaviour change riding
    along with a security fix. ``str`` here keeps the resolved value
    byte-identical across the migration.
    """
    value = getattr(request, STORE_REQUEST_ATTR, None)
    if not value:
        return None
    return str(value)


def write_store(request: Any) -> Optional[str]:
    """The store a WRITE may touch — a TRUSTED ATTRIBUTE or nothing.

    ``None`` means NO trusted scope was supplied, and the caller must fall back
    to its own server-side resolution rather than to anything the request
    carried. Failing to a known store is safe; failing to a caller-named one is
    the defect scitex-hub found in design review on 2026-07-28, when the query
    still reached this path and a request parameter chose the file that got
    written.
    """
    return _trusted(request)


def read_store(request: Any) -> Optional[str]:
    """The store a READ may use — the trusted attribute, else ``?store=``.

    ``None`` means the request named no store at all, and the caller resolves
    its own.
    """
    trusted = _trusted(request)
    if trusted is not None:
        return trusted
    return request.GET.get(STORE_QUERY_PARAM) or None


# EOF
