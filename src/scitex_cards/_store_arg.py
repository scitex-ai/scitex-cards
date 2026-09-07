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

import os
import warnings

__all__ = [
    "StoreArgumentError",
    "refuse_ineffective_store",
    "store_argument_refusal",
    "normalise_store_target",
]


class StoreArgumentError(ValueError):
    """An explicit ``store=`` cannot select where this write goes.

    A ``ValueError`` because it is a bad ARGUMENT, not a store outage —
    :class:`StoreUnavailableError` means the store could not be reached, and a
    caller retrying on that would retry forever on this one.
    """

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
        f"A write cannot be isolated by this argument.\n"
        f"BUT IT IS NOT INERT, SO DO NOT SIMPLY DELETE IT: the same value\n"
        f"also selects the file LOCK (_store_lock) and the destination of\n"
        f"the card-event / inbox rail (_emit_card_event(store=...)).\n"
        f"Dropping it MOVES WHERE NOTIFICATIONS LAND -- measured\n"
        f"2026-09-07, dropping it stopped a `commented` event reaching\n"
        f"the card owner entirely.\n"
        f"To isolate a write, set SCITEX_CARDS_DB to the store you mean."
    )


#: Opt-in HARD refusal. Default is a DeprecationWarning, because a raise here
#: breaks 576 tests on the real-PostgreSQL job (measured 2026-09-07, job
#: 101605547958: 357 failed + 219 errors, 2238 StoreArgumentError). That is not
#: a sloppy rule -- passing a store label is this tree's dominant calling
#: convention, so refusing it outright is an API MIGRATION, not a wiring change.
#: Strict mode exists so a downstream suite that WANTS the hard answer today can
#: have it without waiting for that migration.
_STRICT_ENV = "SCITEX_CARDS_STRICT_STORE_ARG"


def strict_store_arg() -> bool:
    """True when an ineffective ``store=`` should RAISE rather than warn."""
    return os.environ.get(_STRICT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def deliver_refusal(message: str) -> None:
    """Raise under strict mode, otherwise warn. THE STAGING DECISION, ALONE.

    Split out from :func:`refuse_ineffective_store` so it can be tested without
    a store and without a mock: the wrapper needs ``resolve_store()`` to answer,
    this needs nothing but the environment. Same reason
    :func:`store_argument_refusal` is a pure rule -- the parts that can be
    decided without a server are kept where a test can reach them honestly.

    `-W error::DeprecationWarning` promotes every one of these to the hard
    failure, which is how a caller finds their own sites without the whole
    fleet's suite going red first.
    """
    if strict_store_arg():
        raise StoreArgumentError(message)
    warnings.warn(message, DeprecationWarning, stacklevel=4)


def refuse_ineffective_store(explicit: object | None, *, verb: str) -> None:
    """Raise :class:`StoreArgumentError` when ``explicit`` cannot take effect.

    THE ENFORCING WRAPPER. :func:`store_argument_refusal` is the rule and stays
    pure so it can be tested without a server; this resolves the store and
    raises, and is what the public write verbs call.

    Deliberately NOT called from :func:`~scitex_cards._paths.local_store_path`.
    That resolver has callers who legitimately mean the local file — the
    delivery daemon, the recipients sidecar, the notifyd log line — and refusing
    there would break them for doing the right thing. The refusal belongs at the
    verbs whose ``store=`` a caller believes selects where DATA goes.

    Fail-open on a resolution error, and that is a considered choice: if
    ``resolve_store()`` itself cannot answer, the caller is about to hit a real
    store failure with a real message, and masking it with an argument
    complaint would send them to the wrong problem.

    WARNS BY DEFAULT, RAISES UNDER ``$SCITEX_CARDS_STRICT_STORE_ARG``. The first
    cut of this raised unconditionally, and the real-PostgreSQL job answered:
    357 failed + 219 errors. Every green pytest-matrix run had been blind to it,
    because that job's target does not resolve to a server, so the guard never
    armed there. The lesson is in the staging: a refusal whose precondition is
    the DEPLOYMENT (a server target) cannot be validated on a job that does not
    deploy that way.
    """
    if explicit is None:
        return
    try:
        from ._store import resolve_store

        info = resolve_store()
        target = str(info.get("resolved") or "")
        backend = info.get("backend")
    except Exception:  # noqa: BLE001 — see the fail-open note above
        return
    if not target:
        return
    message = store_argument_refusal(
        explicit, resolved_target=target, backend=backend, verb=verb
    )
    if not message:
        return
    deliver_refusal(message)


# EOF
