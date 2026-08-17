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


class IdentityVerdict(str, Enum):
    """Whether the connected store is the one the caller expected.

    THREE-VALUED, and the third value is the reason this enum exists rather
    than a bool. An unset expectation reads ``None``, so the comparison it feeds
    has no right-hand side and cannot fail — a gate that cannot fail is not a
    gate. ``CANNOT_TELL`` gives that state a name so a caller must handle it
    rather than inherit it as a pass.

    THIS DOCSTRING USED TO SAY ``expected_uuid``, IN THE MODULE THAT COMPARES
    THE INSTANCE, and that lie did real damage. It read as though the uuid were
    the value being checked here; it never was. dotfiles found the consequence
    on 2026-08-17 by mutation-testing rather than reading — a garbage uuid with
    a correct instance returned ``MATCHES``, with observed and expected printed
    different on adjacent lines. Two readers (its author and them) had read this
    file closely and both missed it, because the words agreed with the intent
    instead of the code. Constitution §3: a name that lies becomes architecture.
    """

    MATCHES = "matches"
    DIFFERS = "differs"
    CANNOT_TELL = "cannot-tell"


@dataclass(frozen=True)
class IdentityCheck:
    """The answer to "am I connected to the store I think I am".

    Attributes
    ----------
    verdict : IdentityVerdict
    observed : StoreInstance
        What the connection actually reached.
    expected : str or None
        The pinned INSTANCE, verbatim, so an error message can print both sides
        rather than asserting a mismatch the reader cannot check.
    expected_uuid : str or None
        The pinned STORE UUID, verbatim. A SEPARATE FIELD ON PURPOSE — see
        below.
    observed_uuid : str or None
        The uuid this database actually carries, or ``None`` if it carries none.
    reason : str or None
        Why the answer is not ``MATCHES``. ``None`` only on ``MATCHES``.

    WHY TWO EXPECTATION FIELDS AND NOT ONE
    --------------------------------------
    They answer DIFFERENT questions and catch DIFFERENT failures:

        instance   WHICH SERVER am I talking to (PostgreSQL system_identifier)
        uuid       WHICH DATABASE on it (schema_meta.store_uuid)

    A restored or frozen database on the SAME physical server keeps its
    ``system_identifier`` and gets a NEW ``store_uuid`` — the instance check
    waves it through and only the uuid catches it. That is the 2026-08-09
    incident this pin exists to prevent. Conversely ``_store_pin`` records three
    databases once answering the same ``store_uuid``, which is the argument for
    the instance check. Neither substitutes for the other.

    Before 2026-08-17 both were funnelled through ONE ``expected`` field, every
    caller passed the instance, and the uuid was read, reported, and never
    compared. Keeping them as separate fields is what stops that collapse from
    being re-created: a future edit cannot "add the uuid check" to a field that
    is already carrying something else.
    """

    verdict: IdentityVerdict
    observed: StoreInstance
    expected: Optional[str] = None
    reason: Optional[str] = None
    expected_uuid: Optional[str] = None
    observed_uuid: Optional[str] = None

    def __post_init__(self) -> None:
        """A non-matching verdict must say why; a matching one must not."""
        if self.verdict is IdentityVerdict.MATCHES:
            if self.reason is not None:
                raise ValueError(
                    f"IdentityCheck(MATCHES) carries a reason — a reason "
                    f"explains a refusal: {self.reason!r}"
                )
            return
        if not self.reason:
            raise ValueError(
                f"IdentityCheck({self.verdict.value}) with no reason — a "
                "refusal a caller cannot print is a refusal nobody can act on"
            )

    @property
    def may_proceed(self) -> bool:
        """Only ``MATCHES`` proceeds. ``CANNOT_TELL`` refuses like ``DIFFERS``.

        Named as a question about permission rather than exposed as the raw
        verdict, so no call site can accidentally treat "cannot tell" as a
        pass by testing ``verdict is not DIFFERS``.
        """
        return self.verdict is IdentityVerdict.MATCHES


def _read_observed_uuid(conn) -> Optional[str]:
    """This database's own ``schema_meta.store_uuid``, or ``None``.

    Delegated to :func:`scitex_cards._store_uuid.read_store_uuid`, which already
    handles the psycopg dict-row case and never raises. Imported lazily for the
    same import-cycle reason every other cross-module read here is.
    """
    from ._store_uuid import read_store_uuid  # noqa: PLC0415 -- import cycle

    try:
        return read_store_uuid(conn)
    except Exception:  # noqa: BLE001 — an unreadable uuid is "none", never a raise
        return None


def check_store_identity(
    conn,
    expected: Optional[str],
    expected_uuid: Optional[str] = None,
) -> IdentityCheck:
    """Compare the connected store against BOTH identities the caller pinned.

    ``expected`` is the pinned INSTANCE and ``expected_uuid`` the pinned STORE
    UUID, each a value an operator recorded from a store they trust. ``None``
    means that half was not pinned.

    FAIL-CLOSED ACROSS BOTH AXES, AND A HALF-PIN IS ``CANNOT_TELL``:

        neither pinned                     CANNOT_TELL   nothing to check against
        one pinned, it differs             DIFFERS       wrong store, say so
        one pinned and satisfied, other
          not pinned                       CANNOT_TELL   half a question answered
        both pinned, both satisfied        MATCHES       the only pass
        either pinned but unreadable       CANNOT_TELL   cannot answer

    THE HALF-PIN ROW IS THE DESIGN DECISION AND IT WAS DELIBERATE. ``MATCHES``
    must mean "this is the board you pinned". Satisfying the instance while the
    uuid is undeclared has not answered that — it has answered which SERVER,
    never which DATABASE — so reporting a pass there is a confident half-truth,
    and a restored database on that same server would sail through it.
    ``CANNOT_TELL`` is also the more useful answer because it is actionable: its
    reason names the missing half, so the operator knows what to add. Settled
    with dotfiles 2026-08-17 before either of us wrote a test, precisely so the
    test would not be written against the wrong contract.

    DIFFERS AND CANNOT_TELL BOTH REFUSE, WITH DIFFERENT REASONS. "You are
    pointed at the wrong store" and "I cannot tell which store this is" call
    for different actions from whoever reads the message, so collapsing them
    would throw away the only part a human acts on.
    """
    observed = store_instance(conn)
    observed_uuid = _read_observed_uuid(conn)

    # UUID FIRST, and not for style: it is the half that was inert until
    # 2026-08-17, and the half that catches a restored database on the pinned
    # server. Checking it before the instance means a mismatch here can never
    # again be masked by the instance agreeing.
    if expected_uuid and observed_uuid and observed_uuid != expected_uuid:
        return IdentityCheck(
            verdict=IdentityVerdict.DIFFERS,
            observed=observed,
            expected=expected,
            expected_uuid=expected_uuid,
            observed_uuid=observed_uuid,
            reason=(
                f"this database carries store_uuid {observed_uuid!r}, but "
                f"{expected_uuid!r} was pinned. A database restored or rebuilt "
                "on the SAME server keeps its instance id and takes a NEW "
                "uuid, so the instance agreeing is not evidence. Point the "
                "client at the pinned store, or re-pin deliberately if the "
                "move was intended."
            ),
        )
    if expected_uuid and observed_uuid is None:
        return IdentityCheck(
            verdict=IdentityVerdict.CANNOT_TELL,
            observed=observed,
            expected=expected,
            expected_uuid=expected_uuid,
            observed_uuid=None,
            reason=(
                f"a store_uuid is pinned ({expected_uuid!r}) but this database "
                "carries none, so the pin cannot be checked. An unstamped "
                "store is not a matching store."
            ),
        )
    if expected and expected_uuid is None and observed.certainty is Certainty.KNOWN:
        # HALF-PIN. The instance is checked below on its own merits; this guard
        # exists so a satisfied instance cannot RETURN MATCHES while the uuid
        # half is undeclared. Placed before the instance comparison so the
        # refusal names the gap rather than the agreement.
        if observed.instance_id == expected:
            return IdentityCheck(
                verdict=IdentityVerdict.CANNOT_TELL,
                observed=observed,
                expected=expected,
                expected_uuid=None,
                observed_uuid=observed_uuid,
                reason=(
                    "the pinned instance matches, but no store_uuid is pinned, "
                    "so this is HALF a verification: it confirms which server "
                    "answered and not which database on it. A database "
                    "restored on this same server would pass this check. Pin "
                    f"SCITEX_CARDS_STORE_UUID (this store carries "
                    f"{observed_uuid!r}) to complete it."
                ),
            )
    if not expected:
        # THE OTHER HALF-PIN, and it needs its own sentence. Reaching here with
        # `expected_uuid` set means the uuid was pinned AND satisfied (the guards
        # above would have returned otherwise) — so "nothing is pinned" would be
        # a false statement about a caller who pinned half. Same verdict, honest
        # reason.
        if expected_uuid:
            return IdentityCheck(
                verdict=IdentityVerdict.CANNOT_TELL,
                observed=observed,
                expected=None,
                expected_uuid=expected_uuid,
                observed_uuid=observed_uuid,
                reason=(
                    "the pinned store_uuid matches, but no instance is pinned, "
                    "so this is HALF a verification: it confirms which database "
                    "answered and not which server it lives on. Two servers can "
                    "carry the same store_uuid — measured 2026-08-10, 180 cards "
                    "and three days apart. Pin SCITEX_CARDS_STORE_INSTANCE "
                    f"(this server reports {observed.instance_id!r}) to "
                    "complete it."
                ),
            )
        return IdentityCheck(
            verdict=IdentityVerdict.CANNOT_TELL,
            observed=observed,
            expected=None,
            observed_uuid=observed_uuid,
            reason=(
                "no expected store identity is pinned, so this connection "
                "cannot be checked against anything. Record the identity of "
                "the store you trust and pin it; an unpinned client cannot "
                "tell a stale replica from the store it meant to reach."
            ),
        )
    if observed.certainty is Certainty.UNKNOWN:
        return IdentityCheck(
            verdict=IdentityVerdict.CANNOT_TELL,
            observed=observed,
            expected=expected,
            expected_uuid=expected_uuid,
            observed_uuid=observed_uuid,
            reason=(
                f"an identity is pinned ({expected!r}) but this store cannot "
                f"report one: {observed.reason}"
            ),
        )
    if observed.instance_id != expected:
        return IdentityCheck(
            verdict=IdentityVerdict.DIFFERS,
            observed=observed,
            expected=expected,
            expected_uuid=expected_uuid,
            observed_uuid=observed_uuid,
            reason=(
                f"this connection reached instance "
                f"{observed.instance_id!r}, but {expected!r} was pinned. Two "
                "stores can carry the SAME store_uuid and different data — "
                "measured 2026-08-10, 180 cards and three days apart — so a "
                "matching uuid is not evidence. Point the client at the "
                "pinned store, or re-pin deliberately if the move was "
                "intended."
            ),
        )
    # BOTH PINNED AND BOTH SATISFIED — the only path to MATCHES. Every other
    # combination returned above, which is what makes this fail-closed: a new
    # expectation added later must add its own guard, and forgetting to leaves
    # it falling into a refusal rather than into this pass.
    return IdentityCheck(
        verdict=IdentityVerdict.MATCHES,
        observed=observed,
        expected=expected,
        expected_uuid=expected_uuid,
        observed_uuid=observed_uuid,
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
