"""Classify native database failures without reflecting exception text."""

from scitex_dev.store import StoreProvisionError


def ledger_failure_kind(exc: Exception) -> str:
    """Retain typed SQLSTATE through wrappers, including suppressed context."""
    seen = set()
    current = exc
    schema_access = False
    for _ in range(16):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        state = getattr(current, "sqlstate", None)
        if state == "42501":
            return "privilege"
        if isinstance(state, str) and state.startswith("28"):
            return "authentication"
        if isinstance(current, StoreProvisionError):
            schema_access = True
        current = current.__cause__ or current.__context__
    if schema_access:
        return "schema_access"
    # libpq handshake failures sometimes omit SQLSTATE. Match specific native
    # failures, never the substring 'auth' in 'authorized migration identity'.
    text = str(exc).lower()
    if any(s in text for s in (
        "password authentication failed", "no password supplied",
        "no pg_hba.conf entry", "authentication failed",
    )):
        return "authentication"
    return "unavailable"
