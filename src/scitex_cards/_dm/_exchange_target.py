"""Bind trusted credentials without changing the centrally resolved ledger target."""

from psycopg import connect
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from psycopg.conninfo import conninfo_to_dict
from scitex_dev.store import StoreTarget, StoreTargetError


def credential_target(canonical: StoreTarget, store) -> StoreTarget:
    """Overlay only explicit authentication for the identical canonical endpoint."""
    if store is None:
        return canonical
    try:
        original = conninfo_to_dict(canonical.dsn)
        supplied = conninfo_to_dict(str(store))
        original_route = {k: v for k, v in original.items() if k not in ("user", "password")}
        supplied_route = {k: v for k, v in supplied.items() if k not in ("user", "password")}
        original_route.setdefault("port", "5432")
        supplied_route.setdefault("port", "5432")
        if original_route != supplied_route or not supplied.get("user"):
            raise ValueError("credential route mismatch")
        uri = urlsplit(canonical.dsn)
        authority = uri.netloc.rsplit("@", 1)[-1]
        credentials = quote(supplied["user"], safe="") + ":" + quote(
            supplied.get("password", ""), safe=""
        )
        query = urlencode([(k, v) for k, v in parse_qsl(uri.query, keep_blank_values=True)
                           if k not in ("user", "password")])
        dsn = urlunsplit((uri.scheme, credentials + "@" + authority,
                          uri.path, query, uri.fragment))
    except Exception:
        raise StoreTargetError(
            "The scoped DM credential must address the canonical exchange "
            "database with identical connection options and an explicit role."
        ) from None
    target = StoreTarget.postgres(dsn, pkg=canonical.pkg, name=canonical.name)
    # Observe the actual authenticated server before opening the Store or
    # persisting a DM. Proxy login rewrites must not silently change identity.
    with connect(target.dsn, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        role, database = connection.execute(
            "SELECT session_user, current_database()"
        ).fetchone()
        if role != supplied["user"] or database != original.get("dbname"):
            raise StoreTargetError(
                "The scoped exchange connection did not establish the declared "
                "database and session role."
            )
    return target
