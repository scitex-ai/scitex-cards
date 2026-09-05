#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A DSN that ASKS for a search_path is not a session that HAS one.

MEASURED 2026-09-05. The fleet primary went behind PgBouncer 1.25 in
transaction mode on ``scitex-primary:55432`` (PostgreSQL itself moved to
55433). A DSN carrying ``options=-csearch_path=cards_tests_<hex>`` connected
without error, and ``SHOW search_path`` answered ``"$user", public`` — the
pooler's ``ignore_startup_parameters = ...,options`` accepted the startup
parameter and DISCARDED it. Every unqualified ``tasks`` on that connection was
the live board. The only thing that stopped a write was the store-identity
stamp refusing to re-identify a database already bound to the board's uuid — a
guard about a different property, which fired only because the schema it
landed in happened to hold an identity, and said so in words about identities.

Every scoped handle in this package (``_workspace._workspace_dsn``, the test
harness, any consumer that appends ``-csearch_path``) relies on that one
startup parameter for its isolation, and until today every guard asserted what
the DSN SAID — the parameter's presence — never what the server HELD. A request
is not the fact. A middlebox between client and server can honour, drop, or
rewrite any startup parameter, and the handshake does not tell the client
which.

So the check lives at the one door every connection opens through
(:func:`scitex_cards._backend_connect.connect`): if the DSN asks for a
search_path, read it back from the session and refuse on mismatch, naming
asked-for, got, and the remedy. A DSN that asks for nothing pays nothing.
"""

from __future__ import annotations

from typing import Any


class SearchPathNotApplied(RuntimeError):
    """The session does not carry the search_path the DSN asked for."""


def requested_search_path(dsn: str) -> str:
    """The first schema the DSN's ``options`` asks the server to put on the
    search_path, read the way libpq reads it — or ``""`` when it asks for none.

    LIBPQ'S RULES, NOT A SUBSTRING SEARCH. A DSN can carry ``options`` more than
    once (an xdist worker inherits the controller's already-scoped
    ``$SCITEX_STORE_DSN`` and the harness carves a second schema on top); libpq
    honours the LAST occurrence of a repeated URI parameter and discards the
    rest. Inside that value the last ``-c search_path=`` wins likewise. Reading
    the first occurrence compared the controller's schema against the worker's
    session and refused every worker on PR #962's first run. So the parsing is
    delegated to libpq's own reader, which handles both the URI and the
    ``key=value`` conninfo forms.
    """
    from psycopg.conninfo import conninfo_to_dict  # noqa: PLC0415

    try:
        options = str(conninfo_to_dict(dsn).get("options") or "")
    except Exception:  # noqa: BLE001 - an unparseable DSN asks for nothing here
        return ""
    wanted = ""
    tokens = options.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        setting = ""
        if token == "-c" and i + 1 < len(tokens):
            setting = tokens[i + 1]
            i += 1
        elif token.startswith("-c") and len(token) > 2:
            setting = token[2:]
        if setting.startswith("search_path="):
            wanted = setting[len("search_path="):]
        i += 1
    return wanted.split(",", 1)[0].strip().strip('"')


def search_path_in_force(raw: Any) -> list[str]:
    """The schemas the SERVER has on this session's search_path, unquoted."""
    from psycopg.rows import dict_row  # noqa: PLC0415

    # BY NAME, whatever row factory the connection was opened with: a cursor
    # can carry its own factory, and the package reads every fetched column
    # by name (a positional read is refused by a package-wide guard test).
    with raw.cursor(row_factory=dict_row) as cur:
        row = cur.execute("SHOW search_path").fetchone()
    # The read opened psycopg's implicit transaction; end it, so the caller
    # receives the connection in the state a fresh connect() hands out (a
    # caller that then sets `autocommit` would otherwise be refused: psycopg
    # will not change it inside a transaction).
    raw.rollback()
    value = row["search_path"] if row else ""
    return [p.strip().strip('"') for p in str(value).split(",") if p.strip()]


def assert_search_path_applied(raw: Any, dsn: str) -> None:
    """Refuse a connection whose session lacks the schema its DSN asked for.

    One round trip, paid only by a DSN that carries a search_path. The message
    names both sides and the remedy, because the previous guard to fire on this
    defect named neither: it spoke of store identities, and the cause had to be
    deduced from which schema happened to hold one.
    """
    wanted = requested_search_path(dsn)
    if not wanted:
        return
    got = search_path_in_force(raw)
    if wanted in got:
        return
    raise SearchPathNotApplied(
        f"the server did not apply the search_path this DSN asked for: asked "
        f"for {wanted!r}, the session has {got!r}. A pooler between the client "
        "and PostgreSQL (transaction-mode pgbouncer with `options` in "
        "ignore_startup_parameters) accepts the startup parameter and discards "
        "it, so every unqualified statement would land on the DEFAULT schema - "
        "for the fleet primary, the live board. Refusing rather than proceeding "
        "unscoped. Point the DSN at the PostgreSQL port itself (55433 on "
        "scitex-primary), or have the pooler track search_path "
        "(track_extra_parameters), for anything schema-scoped."
    )


# EOF
