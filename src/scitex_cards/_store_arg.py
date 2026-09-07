#!/usr/bin/env python3
"""Decide whether an explicit ``store=`` argument can do what its caller thinks.

WHY THIS IS A SEPARATE, PURE MODULE. The decision is the whole fix; the wiring
is mechanical. Keeping the decision here — taking its inputs as ARGUMENTS rather
than reading the environment or opening the store — means it can be tested
without a PostgreSQL server, and means wiring it into the write verbs is a
reviewable change of its own that cannot alter the rule.

THE DEFECT IT REPORTS
---------------------
``store=`` is resolved by :func:`scitex_cards._paths.local_store_path`, whose
own docstring records that its former name ``_resolved_store`` "was a lie on
every PostgreSQL deployment"::

    resolve_store().resolved   postgresql://scitex-primary:55432/scitex
    local_store_path(None)     /home/agent/.scitex/cards/tasks.yaml

On a PostgreSQL deployment that value serves the file lock and the sidecar
paths. It does NOT select where card data goes: ``_read_write_doc(path)``
discards its argument and calls ``_read_canonical_db_or_raise()``, which takes
no argument at all. So an explicit ``store=`` naming a directory is not
overridden downstream — there is no point at which it could take effect.

The visible symptom is worse than silence. Both the write and the read ignore
the argument and meet at the same real store, so a round-trip SUCCEEDS and an
isolation test written against it PASSES while every row lands on the live
board. That is how it went unreported for weeks: the reassuring result and the
broken behaviour are the same observation.

WHY REFUSE RATHER THAN HONOUR
-----------------------------
Honouring the argument means giving the canonical reader a parameter it has
never had, and auditing ~27 call sites of the local-path resolver, several of
which (the delivery daemon, the recipients sidecar, the notifyd log line)
legitimately mean the local file and must keep their current behaviour. That is
a per-call-site decision, not a substitution.

A refusal is small and, per the consumer who lost three hours to the silent
version, worth more than the isolation would be: an honoured argument gives one
caller a working test, while a loud refusal protects everyone who tries the same
obvious thing in the meantime — and "pass a temp path to isolate a write" is the
obvious thing to try.

WHAT IT MUST NOT DO
-------------------
Refuse an argument that is merely redundant. The test suite pins
``$SCITEX_CARDS_DB`` to a schema-scoped throwaway DSN and hands tests that same
DSN, so ``store=<that DSN>`` names exactly the store the data is in. Refusing
there would break the suite while reporting no real defect. So the mere PRESENCE
of an argument is never the discriminator.

Nor is text equality with the resolved target. Two spellings can name one store
— a host alias against its address, the same DSN with or without a user, an
``sslmode`` parameter or an ``options=-csearch_path=`` suffix — and comparing
strings would refuse callers who did nothing wrong. That is the exact mirror of
the defect above: a LOUD wrong answer in place of a quiet one, and it is the
harder one to argue with, because the refusal looks authoritative.

THE RULE THEREFORE FIRES ONLY ON THE UNAMBIGUOUS CASE: the argument is not a
server target at all — a filesystem path — while the data lives on a server.
A DSN argument is left alone until store identity can be compared properly
rather than spelled. That is a deliberate gap: a DSN naming a genuinely
different database is NOT caught here, and closing it needs identity
resolution, not a better string comparison.
"""

from __future__ import annotations

__all__ = ["store_argument_refusal", "normalise_store_target"]

#: Backends whose data location an explicit ``store=`` cannot select. A
#: file-backed deployment CAN honour a path, so the rule is backend-specific
#: rather than universal — the refusal must not fire where the argument works.
_SERVER_BACKENDS = frozenset({"postgresql", "postgres"})

#: Scheme prefixes that identify a server target when the backend name is not
#: available to the caller. Kept beside the set above so the two cannot drift.
_SERVER_SCHEMES = ("postgresql://", "postgres://")


def normalise_store_target(value: object) -> str:
    """Return ``value`` as a comparable target string.

    Only whitespace and a single trailing ``/`` are stripped. This is NOT a DSN
    parser and must not be used to decide that two DSNs name the same store —
    see :func:`store_argument_refusal`, which deliberately never compares one
    DSN against another.
    """
    text = str(value).strip()
    return text[:-1] if len(text) > 1 and text.endswith("/") else text


def _is_server_target(target: str, backend: str | None) -> bool:
    if backend and backend.strip().lower() in _SERVER_BACKENDS:
        return True
    return target.lower().startswith(_SERVER_SCHEMES)


def store_argument_refusal(
    explicit: object | None,
    *,
    resolved_target: str,
    backend: str | None = None,
    verb: str = "this verb",
) -> str | None:
    """Return the refusal message for ``explicit``, or ``None`` if it is fine.

    Parameters
    ----------
    explicit
        The caller's ``store=`` argument, exactly as passed. ``None`` means the
        caller asked for the ambient store and there is nothing to refuse.
    resolved_target
        What ``resolve_store()["resolved"]`` reports — the store the data is
        actually in.
    backend
        ``resolve_store()["backend"]`` when the caller has it. Optional: the
        scheme of ``resolved_target`` is used when it is absent, so a caller
        that only has the target is not forced to invent one.
    verb
        Name used in the message, e.g. ``"add_task"``. Cosmetic only.

    Returns
    -------
    str or None
        ``None`` when the argument is absent, when it names the resolved store,
        or when the backend can honour a path. Otherwise a message naming BOTH
        resolutions, so the reader can check the claim rather than trust it.
    """
    if explicit is None:
        return None

    target = normalise_store_target(resolved_target)
    passed = normalise_store_target(explicit)

    if not _is_server_target(target, backend):
        return None

    # NEVER compare one DSN against another. Two spellings can name one store
    # -- `postgresql://scitex-primary:55432/scitex` and
    # `postgresql://100.64.0.5:55432/scitex` are the same database and unequal
    # as strings; so are the same DSN with and without a user, an sslmode
    # parameter, or an `options=-csearch_path=` suffix. String equality would
    # therefore REFUSE callers who did nothing wrong, which is the exact mirror
    # of the silent wrong answer this module exists to stop -- a loud wrong
    # answer instead of a quiet one. (Raised by scitex-writer, who lost time in
    # August to two clusters that a string comparison could not tell apart.)
    #
    # So the refusal fires ONLY on the case that is unambiguous without
    # resolving identity: the caller passed something that is not a server
    # target at all -- a filesystem path -- while the data lives on a server.
    # A DSN argument is left alone until identity can be compared properly.
    if _is_server_target(passed, None):
        return None

    return (
        f"{verb}(store={passed!r}): `store=` selects a LOCAL FILE PATH, "
        f"not a store target.\n"
        f"  you passed        {passed}\n"
        f"  data would go to  {target}   (resolve_store())\n"
        f"A write cannot be isolated by this argument. Unset it, or set "
        f"SCITEX_CARDS_DB to the store you mean."
    )


# EOF
