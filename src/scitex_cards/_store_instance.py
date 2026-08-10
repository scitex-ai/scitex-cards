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

SQLITE HAS NO ANALOGUE AND THIS MODULE SAYS SO. A SQLite store is a file, and
a byte copy of that file is indistinguishable from the original by anything
inside it — which is the honest answer, not a gap to paper over. The file's
path IS its identity (ADR-0010), and that is the caller's to compare, not
ours. So the SQLite answer is ``UNKNOWN`` with a stated reason, and a caller
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

#: Why a SQLite store cannot answer. Stated once, returned verbatim, so the
#: reason a caller reports is the reason this module actually has.
_SQLITE_HAS_NO_INSTANCE_ID = (
    "sqlite: a database file carries no instance identity — a byte copy is "
    "indistinguishable from the original from inside it. The file path is the "
    "store identity (ADR-0010); compare that instead."
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
        ``_store_url.BACKEND_POSTGRES`` or ``BACKEND_SQLITE``.
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
    from ._store_url import BACKEND_POSTGRES, BACKEND_SQLITE

    # The backend comes from the LIVE connection, never from a caller's belief
    # about what it opened — the same rule `_inbox_shape.shape_for` states:
    # reading it from the connection is what stops a call site being correct in
    # tests and wrong in production. On this module it matters twice over,
    # since being wrong about the backend here means answering a question about
    # the wrong instance.
    if not _is_postgres(conn):
        return StoreInstance(
            backend=BACKEND_SQLITE,
            certainty=Certainty.UNKNOWN,
            reason=_SQLITE_HAS_NO_INSTANCE_ID,
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


__all__ = [
    "Certainty",
    "StoreInstance",
    "store_instance",
]

# EOF
