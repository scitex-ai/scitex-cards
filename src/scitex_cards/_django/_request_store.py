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

WHY THE QUERY FALLBACK IS STILL HERE, and what finally bounded it. The
standalone loopback board and the whole Django test suite select a store
through ``?store=``; removing it outright breaks both, and removing it BEFORE
hub deletes its injection would drop tenancy for a release window — the
upstream would fall back to its ambient canonical store, one store for every
tenant. Alias first, then remove. Tracked on
``cards-read-path-ignores-the-trusted-store-attribute-20260806``, which
recorded that the removal step "has to name what replaces the seam for the
standalone board and the tests before it can run."

THIS MODULE NOW NAMES IT: exposure. ``settings.PUBLIC_HOST`` is, in its own
words, "the ONE switch that says 'this board is reachable from the internet'",
and every setting that makes exposure safe already keys off it. So the query
channel is admitted on a board that is NOT publicly reachable and refused on
one that is — see :func:`_caller_may_name_the_store`. The standalone board and
the test suite keep the seam they depend on; every deployment where a hostile
caller can reach the door loses it, by construction rather than by a middleware
remembering to overwrite a parameter.

WHY THAT BOUND IS URGENT AND NOT COSMETIC. On the CARD path the fallback is
inert: ``load_tasks`` discards the resolved store and reads the one canonical
DB (``_model.py`` ``_read_canonical_db_or_raise()``, called with no argument),
so no ``?store=`` has ever chosen a card database. On the DM path it is LIVE —
``handlers/dm`` -> ``_dm_read._open`` -> ``_dm_ids`` turns the value into a
``cards.db`` path and ``_db.open_db`` opens it. The one surface that honours
the parameter is therefore the one surface that was unguarded, while the
surface everyone reasons about was safe only by a different defect. ADR-0017
§D0 makes the store handle THE authority boundary; a door that takes its handle
from the caller's own namespace is that boundary unenforced.

WHAT THIS IS NOT: a lenient read policy beside a strict write one.
``_store_canonical_read`` forbids exactly that — "Do NOT split this into a
lenient read variant and a strict write variant — that recreates exactly the
asymmetry that outage was made of." Reads here CONVERGE on the write rule
wherever it matters (any exposed deployment) and keep the legacy seam only
where the deployment has provably one tenant and one caller.

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

#: Distinguishes "settings says the board is not exposed" from "settings does
#: not carry the switch at all". ``None`` and ``""`` are both legitimate values
#: of ``PUBLIC_HOST``, so neither can stand in for "absent" — and conflating
#: absent with not-exposed is the fail-open this sentinel exists to prevent.
_UNCONFIGURED = object()


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


def _caller_may_name_the_store() -> bool:
    """Whether THIS DEPLOYMENT may let a request's own query choose a store.

    Admissible only on a board that is not reachable from the internet.
    ``settings.PUBLIC_HOST`` is that fact — its own block calls it "the ONE
    switch that says 'this board is reachable from the internet'" and forces
    every exposure-critical setting off it — so keying the query channel to the
    same switch adds no second notion of "exposed" to keep in sync.

    THREE-VALUED, AND ONLY ONE OF THE THREE ADMITS THE QUERY:

    ================================  ==========================  ==========
    settings state                    means                       verdict
    ================================  ==========================  ==========
    ``PUBLIC_HOST`` empty             loopback board, tests       ADMIT
    ``PUBLIC_HOST`` set               internet-reachable          REFUSE
    absent / settings unconfigured    cannot tell                 REFUSE
    ================================  ==========================  ==========

    The third row is the one that matters and it is deliberately NOT collapsed
    into the first. A host application that embeds this board brings its OWN
    settings module, which has no ``PUBLIC_HOST`` — scitex-hub is exactly that
    shape — so "the attribute is missing" is the signature of running inside
    someone else's deployment, which is precisely where a caller-named store
    must not be honoured. Reading a missing switch as "not exposed" would admit
    the query on every embedding deployment while looking like a safe default.
    """
    try:
        from django.conf import settings as django_settings

        public_host = getattr(django_settings, "PUBLIC_HOST", _UNCONFIGURED)
    except Exception:  # ImproperlyConfigured, ImportError — cannot tell
        return False
    if public_host is _UNCONFIGURED:
        return False
    return not str(public_host).strip()


def read_store(request: Any) -> Optional[str]:
    """The store a READ may use — the trusted attribute, else ``?store=``.

    ``None`` means no store this request may name, and the caller resolves its
    own server-side. That is the safe direction: failing to a KNOWN store is
    safe, failing to a CALLER-NAMED one is the defect.

    The query fallback applies only where :func:`_caller_may_name_the_store`
    admits it. On an exposed or embedded deployment a caller-supplied
    ``?store=`` is inert — the same guarantee :func:`write_store` has had since
    2026-07-28, arrived at from the other side.
    """
    trusted = _trusted(request)
    if trusted is not None:
        return trusted
    if not _caller_may_name_the_store():
        return None
    return request.GET.get(STORE_QUERY_PARAM) or None


# EOF
