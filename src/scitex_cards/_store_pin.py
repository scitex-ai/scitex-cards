#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE GUARD THAT CONSUMES THE INSTRUMENT — resolution, checked against a pin.

:mod:`._store_instance` landed on 2026-08-10 with an unusually honest closing
paragraph in its own test module::

    They do NOT claim any guard consumes it yet; the guard is the next step and
    owns its own tests, because having the instrument is exactly what makes the
    missing guard LOOK done.

The guard was never built, and the prediction came true on 2026-08-12. THREE
PostgreSQL databases were live, all three answering ``store_uuid =
1d55dd6e-3d2a-4c24-a429-a78835ab988f``, holding different fragments of one
board::

    ywata-note-win   :55432   3843 tasks   most of that night's cards
    scitex-compute-04:55432   3743 tasks   exactly one of them
    scitex-compute-04: 5442   3422 tasks   none of them

Two of those are one ``lsof`` apart on the SAME MACHINE and are genuinely
different clusters — ``system_identifier`` 7672112238472680366 (a native server
at ``/home/ywatanabe/.scitex/pg/18/main``) versus 7671108644284358700 (a Docker
server at ``/var/lib/postgresql/18/docker``). ``store_uuid`` could not tell them
apart because it is a ``schema_meta`` ROW and a dump/restore carries rows.

WHY THE INSTRUMENT ALONE CHANGED NOTHING. ``check_store_identity`` takes a
``conn`` and an ``expected``. Every caller in the package was in the ``health``
doctor — a verb a human runs deliberately, after already suspecting something.
Nothing consulted it on the path that actually chooses a database, so the
resolution stayed unchecked and the divergence stayed silent. An instrument
wired to nothing measures nothing.

WHAT THIS MODULE ADDS. ``_store_instance`` compares an OPEN CONNECTION to an
expectation. This module supplies the two halves that were missing on the
resolution path:

1. :func:`instance_at` — probe a TARGET (a DSN or a path) rather than a
   connection, under the same never-raises/time-bounded reporting contract
   ``_store_uuid.store_uuid_at`` already established, so a diagnostic run
   against a store that is down still answers.
2. :func:`pinned_instance` — the expectation, INJECTED from
   ``$SCITEX_CARDS_STORE_INSTANCE``, never computed and never read out of the
   database being judged. Reading the expectation from the store under test is
   the circularity ``_store_uuid``'s design §4 already forbids for uuids, and
   it is no less circular here.

TWO PINS, TWO DIFFERENT QUESTIONS, AND THE OLD ONE IS NOT RETIRED.
``$SCITEX_CARDS_STORE_UUID`` pins the LOGICAL board — "these rows are the fleet
board", a property a replica legitimately shares with its primary.
``$SCITEX_CARDS_STORE_INSTANCE`` pins the PHYSICAL server — "and it is THIS
copy of it". The 2026-08-12 split is invisible to the first and obvious to the
second, which is the whole argument for adding one rather than tightening the
other. A deployment may set either, both, or neither.

WHY THIS DOES NOT RAISE INSIDE ``resolve_store``. ``resolve_store`` is the verb
an operator runs WHEN THINGS ARE ALREADY BROKEN, and its own docstring records
what happens when a diagnostic dies on the case being diagnosed: mid-cutover on
2026-07-31 it raised ``StoreTargetIsNotAPath`` against PostgreSQL while
``list-tasks`` was happily serving 2973 cards, and it read as "the store is
broken". So the REPORT carries the verdict and the REFUSAL lives in
:func:`require_pinned_store`, at the doors where proceeding does damage — the
same one-door-at-a-time rule ``_store_target.require_configured_store_target``
states for the unconfigured-store case.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Optional

from ._store_instance import (
    Certainty,
    IdentityCheck,
    IdentityVerdict,
    StoreInstance,
    check_store_identity,
)

#: Environment variable carrying the caller's EXPECTATION of WHICH SERVER it
#: should find. Deliberately parallel in shape and precedence to
#: ``_store_uuid.ENV_EXPECTED_STORE_UUID`` so an operator who has met one has
#: met both, and deliberately a SECOND variable rather than a stricter reading
#: of the first — see the module docstring on the two questions.
ENV_PINNED_INSTANCE: Final[str] = "SCITEX_CARDS_STORE_INSTANCE"

#: libpq's own connect-timeout variable, set the same way and for the same
#: reason as in ``_store_uuid.store_uuid_at``: a reporting primitive must not
#: rewrite the DSN it was asked to describe, and libpq applies no connect
#: timeout by default (measured >40s against a dead port).
_PGCONNECT_TIMEOUT_ENV: Final[str] = "PGCONNECT_TIMEOUT"

#: Seconds :func:`instance_at` will wait for a server before answering UNKNOWN.
#: Same value and same argument as the uuid probe's bound: this backs the
#: "which store am I on?" diagnostic, whose whole value is being fast when
#: everything else is broken.
_REPORTING_CONNECT_TIMEOUT_S: Final[str] = "3"

#: Why an unreachable target cannot answer. A refusal a caller cannot print is
#: a refusal nobody can act on, so the reason is built from the real exception.
_UNREACHABLE = (
    "the store target could not be opened, so the instance it names is unknown"
)


class StoreIdentityRefused(RuntimeError):
    """The resolved store is not the one that was pinned, or cannot be told.

    ONE exception for both refusals, carrying the verdict, because the two
    call for the same action from a program (stop) and different actions from
    a human (re-point vs. pin) — and the human reads ``reason``, which
    ``_store_instance`` already writes differently for each.
    """

    def __init__(self, check: IdentityCheck, target: str) -> None:
        self.check = check
        self.target = target
        super().__init__(
            f"REFUSING to use {target!r}: {check.reason}\n"
            # NAMES BOTH HALVES, because as of 2026-08-19 pinning one is not
            # enough and a hint that names one sends the reader to do half the
            # work and hit this same refusal again. An error message outlives
            # the contract it describes unless it is changed with the contract.
            f"Pin the store you trust with ${ENV_PINNED_INSTANCE}=<instance id> "
            f"AND ${_ENV_EXPECTED_STORE_UUID}=<store uuid> — BOTH are required: "
            f"the instance says which server, the uuid says which board. Read "
            f"both for any store with `scitex-cards resolve-store --json` "
            f"(fields `instance_id` and `store_uuid`)."
        )


#: The uuid half's env name, imported rather than re-spelled so the two
#: modules cannot drift on the string an operator has to type.
from ._store_uuid import ENV_EXPECTED_STORE_UUID as _ENV_EXPECTED_STORE_UUID


def pinned_instance(explicit: str | None = None) -> Optional[str]:
    """The caller's EXPECTATION: explicit argument, else the env var, else None.

    Byte-for-byte the same precedence, blank-handling and no-normalising rule
    as ``_store_uuid.expected_store_uuid``. A blank/whitespace-only value means
    "no expectation declared", not "expect the empty identity" — a variable
    that exists but was never filled in must not refuse every store on the
    machine. A non-blank value is returned VERBATIM: normalising the
    expectation is normalising the comparison, which is the class of bug the
    identity work exists to remove.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(ENV_PINNED_INSTANCE)
    if raw is None or not raw.strip():
        return None
    return raw


def instance_at(target: str | Path) -> StoreInstance:
    """The instance identity of a TARGET, read-only, never raising.

    The resolution-side twin of ``_store_instance.store_instance``, which needs
    a connection the caller has already opened. On the resolution path there is
    no connection yet — that is the point at which the wrong database gets
    chosen — so this opens one, reads, and closes it.

    NEVER RAISES, for the same reason ``store_uuid_at`` never raises: this backs
    a diagnostic, and a diagnostic that throws where the connection is already
    suspect turns "I cannot tell" into a traceback at the call site least able
    to handle it. Every failure becomes an ``UNKNOWN`` carrying its cause.
    """
    from ._store_url import BACKEND_SQLITE, is_postgres_url

    if not is_postgres_url(target):
        # Deferred to the probe rather than answered here, so the SQLite reason
        # is written in exactly one place (``_store_instance``) and a caller
        # comparing reasons across the two entry points sees one string.
        from ._store_instance import _SQLITE_HAS_NO_INSTANCE_ID

        return StoreInstance(
            backend=BACKEND_SQLITE,
            certainty=Certainty.UNKNOWN,
            reason=_SQLITE_HAS_NO_INSTANCE_ID,
        )

    from ._store_instance import store_instance
    from ._store_url import BACKEND_POSTGRES

    prior_timeout = os.environ.get(_PGCONNECT_TIMEOUT_ENV)
    if prior_timeout is None:
        os.environ[_PGCONNECT_TIMEOUT_ENV] = _REPORTING_CONNECT_TIMEOUT_S
    try:
        from ._db import connect

        conn = connect(str(target))
    except Exception as exc:  # noqa: BLE001 — every failure is an UNKNOWN
        return StoreInstance(
            backend=BACKEND_POSTGRES,
            certainty=Certainty.UNKNOWN,
            reason=f"{_UNREACHABLE} ({type(exc).__name__}: {exc})",
        )
    finally:
        if prior_timeout is None:
            os.environ.pop(_PGCONNECT_TIMEOUT_ENV, None)
    try:
        return store_instance(conn)
    finally:
        conn.close()


def check_resolution(
    store: str | Path | None = None,
    expected: str | None = None,
) -> IdentityCheck:
    """Resolve the store the way every write does, then judge what it reached.

    THE ORDER IS THE POINT. The target is resolved through the SAME
    ``resolve_store_target`` precedence a write uses (explicit, then
    ``$SCITEX_CARDS_DB``, then config, then the packaged default), and the
    identity is read from whatever that resolution actually landed on. A guard
    that consults a target the writes do not use is a guard that agrees with
    itself and nothing else.

    ``expected=None`` falls through to :func:`pinned_instance`, so the default
    behaviour is "whatever this deployment pinned" rather than "nothing", and
    passing an explicit value stays available for tests and for a caller
    checking a store it does not run on.
    """
    from ._store_target import resolve_store_target

    from ._store_uuid import expected_store_uuid, store_uuid_at

    _arg = store if isinstance(store, (str, type(None))) else str(store)
    target = resolve_store_target(_arg)
    observed = instance_at(target)
    # BOTH HALVES, for the reason on `decide_identity`: the instance answers
    # which SERVER and the uuid answers which BOARD, and a caller that supplies
    # one gets `CANNOT_TELL` rather than a half-checked pass. `store_uuid_at`
    # never raises, same as `instance_at`, so this stays a reporting primitive.
    return _check_against(
        observed,
        pinned_instance(expected),
        observed_uuid=store_uuid_at(target),
        expected_uuid=expected_store_uuid(),
    )


def _check_against(
    observed: StoreInstance,
    expected: Optional[str],
    *,
    observed_uuid: Optional[str] = None,
    expected_uuid: Optional[str] = None,
) -> IdentityCheck:
    """Compare an ALREADY-PROBED store against an expectation.

    Split out so :func:`check_resolution` and ``resolve_store`` can share one
    probe instead of opening the store twice. ``check_store_identity`` takes a
    live connection, which is exactly what this layer no longer has by the time
    it needs the verdict, so the values arrive already probed.

    THE OUTCOMES ARE NO LONGER RE-EXPRESSED HERE. This function used to carry
    its own copy of the comparison, "kept IDENTICAL to ``check_store_identity``'s"
    by discipline — and the two drifted: THIS one never compared the uuid at
    all, so ``resolve-store`` answered "matches" to a mismatched uuid while
    printing both values on adjacent lines (found by dotfiles 2026-08-17 by
    mutation-testing the gate). Both bodies now delegate to
    :func:`._store_identity_decision.decide_identity`, which is the only way two
    guards cannot disagree.

    THE UUID MUST BE PASSED IN. This layer has no connection and deliberately
    does not open one — ``resolve_store`` already probes it via ``store_uuid_at``
    on the line above the call, which is precisely why the omission was
    invisible: the value was in scope and simply never handed over.
    """
    from ._store_identity_decision import decide_identity

    return decide_identity(
        observed,
        expected,
        observed_uuid=observed_uuid,
        expected_uuid=expected_uuid,
        subject="this resolution",
    )


def require_pinned_store(
    store: str | Path | None = None,
    expected: str | None = None,
) -> str:
    """The resolved target, but ONLY if it is the store that was pinned.

    THE REFUSAL DOOR. For SERVERS and for any writer that must not guess —
    the same population ``require_configured_store_target`` serves, and the
    same reasoning: a one-shot CLI landing on an unpinned store is a fresh
    install behaving correctly, while a BOARD or a long-lived agent landing on
    the wrong one writes into it for days and looks healthy the whole time.

    ``CANNOT_TELL`` REFUSES ALONGSIDE ``DIFFERS``, via ``may_proceed`` rather
    than by testing the verdict, because a call site written as
    ``verdict is not DIFFERS`` treats "I cannot tell which store this is" as a
    pass — which is the exact collapse that let three databases share one
    identity unnoticed. ``_store_instance`` names the permission question for
    precisely this reason; using the raw verdict here would relocate the bug
    one level up rather than fix it.

    Raises
    ------
    StoreIdentityRefused
        On ``DIFFERS`` and on ``CANNOT_TELL``, carrying the check so a caller
        can print both sides rather than assert a mismatch nobody can verify.
    """
    from ._store_target import resolve_store_target
    from ._store_uuid import expected_store_uuid, store_uuid_at

    _arg = store if isinstance(store, (str, type(None))) else str(store)
    target = resolve_store_target(_arg)
    # BOTH HALVES — see `decide_identity`. This is the REFUSING door, so a
    # half-checked pass here is the one that would let a write reach the wrong
    # store.
    check = _check_against(
        instance_at(target),
        pinned_instance(expected),
        observed_uuid=store_uuid_at(target),
        expected_uuid=expected_store_uuid(),
    )
    if not check.may_proceed:
        raise StoreIdentityRefused(check, target)
    return target


__all__ = [
    "ENV_PINNED_INSTANCE",
    "StoreIdentityRefused",
    "check_resolution",
    "instance_at",
    "pinned_instance",
    "require_pinned_store",
]

# EOF
