"""Keep the test suite's throwaway schemas off the live board's cluster.

A MODULE RATHER THAN A FEW LINES IN ``conftest.py``, for one reason: importing
``conftest`` executes it, and its import opens a PostgreSQL cluster. A guard
whose only test cannot run without starting a database is a guard that ends up
untested, so the decision lives here where a test can call it directly with a
dict of environment variables and no side effects at all.

See ``conftest._open_throwaway_postgres`` for the caller.
"""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlparse

#: The variable ``scitex_dev.store.testing.writable_dsn()`` consults first.
CLUSTER_ENV = "SCITEX_STORE_DSN"
#: The variable naming the board this agent actually reads and writes.
BOARD_ENV = "SCITEX_CARDS_DB"


def server_of(dsn: str | None) -> tuple[str, str, str] | None:
    """``(host, port, dbname)`` of a DSN -- the SERVER, not the schema.

    Credentials and query string are dropped deliberately. Two DSNs naming one
    server through different users, or through different ``search_path``
    options, are still ONE SERVER, and it is the server whose catalogue a test
    run writes to. Comparing whole DSN strings would answer "different" for the
    schema-scoped form of the very same primary.
    """
    if not dsn:
        return None
    try:
        parsed = urlparse(dsn)
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    try:
        port = str(parsed.port or 5432)
    except ValueError:  # malformed port; not a DSN we can reason about
        return None
    return parsed.hostname, port, (parsed.path or "").lstrip("/")


def fleet_store_declined(env: Mapping[str, str]) -> str | None:
    """The reason to stop using ``$SCITEX_STORE_DSN``, or ``None`` to keep it.

    PURE: reads the mapping, mutates nothing, so the caller decides what to do
    with the verdict. Returns the operator-facing explanation when the
    configured cluster is the same server as the live board.

    WHY THIS EXISTS. ``writable_dsn()``'s first route is "whatever the caller
    configured in ``SCITEX_STORE_DSN``", and its docstring states the fleet
    store is not among its routes. True of the FUNCTION, false of this
    DEPLOYMENT: in a sac agent container ``SCITEX_STORE_DSN`` IS the board --
    that is how the container points every tool at it -- so route one succeeds,
    hands back the production primary, and the private-throwaway route is never
    reached. Measured 2026-09-06: three leaked ``cards_test*`` schemas were on
    the primary, and killing a run skips ``ephemeral_schema``'s ``finally``, so
    nothing drops them.

    WHAT THIS REVISES, stated plainly because it is a deliberate choice and not
    an oversight. ``conftest`` already argues that the schema-scoped DSN makes
    carving here "SAFE against the live cluster rather than merely polite":
    ``public`` is off the search_path, so a test cannot read the fleet's cards.
    That reasoning is sound AND IT ONLY COVERS THE DATA. It says nothing about
    the CATALOGUE, which is what a test store actually writes -- every fresh
    schema runs the full DDL, nine trigger functions plus their triggers -- so
    a parallel run is a DDL storm beside the operator's own writes and every
    abandoned run leaves a schema behind. Data isolation was never the whole
    exposure.

    A NO-OP IN CI, BY CONSTRUCTION: each pytest-matrix leg sets
    ``SCITEX_STORE_DSN`` to its own ``postgres:16`` service and does NOT set
    ``SCITEX_CARDS_DB``, so there is no board to match and this returns
    ``None``. The guard fires exactly where the exposure is.
    """
    here = server_of(env.get(CLUSTER_ENV))
    live = server_of(env.get(BOARD_ENV))
    if here is None or live is None or here != live:
        return None
    host, port, dbname = here
    return (
        f"{CLUSTER_ENV} named the live board's server ({host}:{port}/{dbname}), "
        "so it was cleared for this run and a private throwaway cluster used "
        "instead. Tests carve schemas, and a schema on the primary is a DDL "
        "storm beside the operator's writes plus a leak when the run is killed."
    )
