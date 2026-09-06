#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The identity of the DATABASE INSTANCE a connection actually reached.

WHY ``store_uuid`` IS NOT THIS, measured 2026-08-10 on two live servers.
Two PostgreSQL clusters, 127.0.0.1:5432 and 127.0.0.1:5442, reported

    store_uuid                1d55dd6e-3d2a-4c24-a429-a78835ab988f   IDENTICAL
    schema_version            9                                     identical
    created_at                2026-07-16T19:24:40Z                  identical
    downgrades_refused        4769                                  identical
    ---------------------------------------------------------------------
    tasks                     3602            vs   3422             DIVERGED
    max(last_activity)        2026-08-10      vs   2026-08-07       DIVERGED

Every identity field a client can read was byte-identical while the DATA was
180 cards and three days apart. ``store_uuid`` lives in ``schema_meta``, so it
is a ROW; a dump/restore into a second cluster carries rows, which is exactly
how these two came to share one. A value that survives being copied cannot
answer "am I connected to the store I think I am".

WHAT THIS MODULE READS INSTEAD. PostgreSQL's ``system_identifier`` is generated
by ``initdb`` for the CLUSTER and is not in any dump, so a restore cannot carry
it — it belongs to the machine that received the data. The two clusters above
differ on it (7668165447904178049 vs 7671108644284358700) precisely because
they are two machines, which is the question being asked. The unprivileged
``scitex_cards`` role can read it on both; no grant change is needed.

Same argument as the schema LADDER in :mod:`._schema_shape` and as the
row-level-write marker ruling: trust an artifact the copying operation cannot
fake, never a stamp the operation happens to write.

A TARGET THAT IS NOT THE STORE HAS NO ANALOGUE AND THIS MODULE SAYS SO. There
is no cluster to ask, so there is no instance identity to read — which is the
honest answer, not a gap to paper over. So that answer is ``UNKNOWN`` with a
stated reason, and a caller
that needs certainty must refuse rather than proceed. Returning a
made-up-but-stable value here (a hash of the path, say) would be worse than
useless: it would look like an instance identity and would follow a copy.

THE THREE-VALUEDNESS IS THE POINT. ``UNKNOWN`` is not a failure and must not
collapse into either pole. The defect this module exists to fix is precisely
a collapse of that kind: ``expected_uuid`` reads ``None`` when unset, and a
comparison with no right-hand side cannot fail, so the mismatch guard passed
on every store including the wrong one. A gate that cannot fail is not a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

#: Read on PostgreSQL. ``pg_control_system()`` is readable by an unprivileged
#: role — verified 2026-08-10 against both clusters as ``scitex_cards`` with
#: ``usesuper = false`` on one of them — unlike ``current_setting
#: ('data_directory')``, which raises InsufficientPrivilege for the same role
#: and would therefore have made this probe fail exactly where it is needed.
_POSTGRES_INSTANCE_SQL = "select system_identifier::text from pg_control_system()"

#: Why a target that is not the store cannot answer. Stated once, returned
#: verbatim, so the reason a caller reports is the reason this module has.
_NO_INSTANCE_ID_OFF_SERVER = (
    "unsupported: this target does not name the store, so there is no cluster "
    "to ask for an instance identity. The store is a PostgreSQL DSN; check "
    "$SCITEX_CARDS_DB."
)


class Certainty(str, Enum):
    """Whether an instance identity could be established at all.

    Separate from the identity itself so a caller cannot read a missing value
    as a value. ``str``-valued so it survives logs and JSON legibly.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StoreInstance:
    """What instance a connection reached — always this shape, never a bare str.

    Attributes
    ----------
    backend : str
        ``_store_url.BACKEND_POSTGRES`` or ``BACKEND_UNSUPPORTED``.
    certainty : Certainty
        ``KNOWN`` iff ``instance_id`` is a real identity read from the server.
    instance_id : str or None
        The cluster's own identifier. ``None`` whenever ``certainty`` is
        ``UNKNOWN``, and never a placeholder — a caller must not be able to
        compare two unknowns and find them equal.
    reason : str or None
        Why the identity is unknown, in words a caller can print. ``None``
        when it is known.
    """

    backend: str
    certainty: Certainty
    instance_id: Optional[str] = None
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        """Fail where the answer is BUILT, not three layers downstream.

        The two invariants are the ones that make the type safe to compare:
        a KNOWN answer must carry an identity, and an UNKNOWN one must carry
        no identity and a stated reason. Without the second, two UNKNOWNs
        could compare equal on a shared placeholder — which is the collapse
        this whole module exists to prevent, reintroduced at the type level.
        """
        if self.certainty is Certainty.KNOWN:
            if not self.instance_id:
                raise ValueError(
                    "StoreInstance(certainty=KNOWN) with no instance_id — a "
                    "known identity must carry the value it claims to know"
                )
            if self.reason is not None:
                raise ValueError(
                    "StoreInstance(certainty=KNOWN) carries a reason — a "
                    f"reason explains an UNKNOWN, not a known value: "
                    f"{self.reason!r}"
                )
            return
        if self.instance_id is not None:
            raise ValueError(
                "StoreInstance(certainty=UNKNOWN) carries an instance_id "
                f"({self.instance_id!r}) — an unknown identity must be None so "
                "two unknowns can never compare equal"
            )
        if not self.reason:
            raise ValueError(
                "StoreInstance(certainty=UNKNOWN) with no reason — an "
                "unanswerable probe must say why, or its caller cannot tell a "
                "missing capability from a broken connection"
            )


def store_instance(conn) -> StoreInstance:
    """The identity of the instance ``conn`` is connected to.

    Never raises. A probe that throws where the connection is already suspect
    turns "I cannot tell" into a traceback at the call site least able to
    handle it, so every failure becomes an ``UNKNOWN`` carrying its cause.

    Parameters
    ----------
    conn
        An open connection from :func:`scitex_cards._db.connect`.

    Returns
    -------
    StoreInstance
        Always. See the class for the invariants it guarantees.
    """
    from ._schema_probe import _is_postgres, _sole_value
    from ._store_url import BACKEND_POSTGRES, BACKEND_UNSUPPORTED

    # The backend comes from the LIVE connection, never from a caller's belief
    # about what it opened — the same rule `_inbox_shape.shape_for` states:
    # reading it from the connection is what stops a call site being correct in
    # tests and wrong in production. On this module it matters twice over,
    # since being wrong about the backend here means answering a question about
    # the wrong instance.
    if not _is_postgres(conn):
        return StoreInstance(
            backend=BACKEND_UNSUPPORTED,
            certainty=Certainty.UNKNOWN,
            reason=_NO_INSTANCE_ID_OFF_SERVER,
        )
    backend = BACKEND_POSTGRES

    try:
        row = conn.execute(_POSTGRES_INSTANCE_SQL).fetchone()
    except Exception as exc:  # noqa: BLE001 — every failure is an UNKNOWN
        return StoreInstance(
            backend=backend,
            certainty=Certainty.UNKNOWN,
            reason=(
                f"postgresql: could not read system_identifier "
                f"({type(exc).__name__}: {exc})"
            ),
        )
    raw = _sole_value(row) if row is not None else None
    if raw is None:
        return StoreInstance(
            backend=backend,
            certainty=Certainty.UNKNOWN,
            reason=(
                "postgresql: pg_control_system() returned no row — this "
                "server did not answer the identity query it accepted"
            ),
        )
    return StoreInstance(
        backend=backend,
        certainty=Certainty.KNOWN,
        instance_id=str(raw),
    )


# THE IDENTITY TYPES AND THE DECISION NOW LIVE IN ONE PLACE.
# `IdentityVerdict`, `IdentityCheck` and the comparison itself moved to
# `_store_identity_decision` on 2026-08-19, and are re-exported here so every
# existing `from ._store_instance import IdentityCheck` keeps working.
#
# WHY THEY MOVED: this module and `_store_pin` each carried a comparison body
# with the same four outcomes, kept identical BY DISCIPLINE — and they drifted
# apart exactly as that arrangement invites. One of the two omitted the uuid
# comparison entirely, so `resolve-store` answered "matches" to a mismatched
# uuid. Two guards that disagree is the collapse this family exists to prevent,
# so the fix was not to write the comparison twice but to stop there being a
# both.
from ._store_identity_decision import (  # noqa: F401  (re-exported)
    IdentityCheck,
    IdentityVerdict,
    decide_identity,
)


def check_store_identity(
    conn,
    expected: Optional[str],
    *,
    observed_uuid: Optional[str] = None,
    expected_uuid: Optional[str] = None,
) -> IdentityCheck:
    """Compare the connected store against the identity the caller pinned.

    PROBES, THEN DELEGATES. The outcomes live in
    :func:`._store_identity_decision.decide_identity` — this function's only job
    is obtaining the observed values from a live connection. It used to carry
    its own copy of the comparison, and its twin in ``_store_pin`` carried
    another; the two drifted and one of them stopped checking the uuid.

    BOTH HALVES ARE REQUIRED FOR A PASS, which is a deliberate change from the
    behaviour this function had until 2026-08-19. An instance-only pin now
    answers ``CANNOT_TELL`` rather than ``MATCHES``: the instance identifies the
    SERVER, and a database restored onto that same server keeps it while
    getting a new uuid — the 2026-08-09 frozen-store incident this pin exists to
    catch. See ``decide_identity``'s table for all nine rows.

    ``expected`` is the value an operator recorded from a store they trust.
    ``None`` means nothing was pinned, which is ``CANNOT_TELL`` and NOT a pass:
    an unpinned client is exactly the one that reads a three-day-stale replica
    and reports a confident match.

    DIFFERS AND CANNOT_TELL BOTH REFUSE, WITH DIFFERENT REASONS. "You are
    pointed at the wrong store" and "I cannot tell which store this is" call
    for different actions from whoever reads the message, so collapsing them
    would throw away the only part a human acts on.
    """
    return decide_identity(
        store_instance(conn),
        expected,
        observed_uuid=observed_uuid,
        expected_uuid=expected_uuid,
        subject="this connection",
    )


__all__ = [
    "Certainty",
    "IdentityCheck",
    "IdentityVerdict",
    "StoreInstance",
    "check_store_identity",
    "store_instance",
]

# EOF
