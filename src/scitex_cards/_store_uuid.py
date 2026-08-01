#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE IDENTITY IS A UUID, NOT A PATH.

Design: ``docs/design/store-identity-is-a-uuid.md``. Card:
``scitex-cards-resolver-never-default-yaml-20260727`` (P0). Contract accepted
verbatim by scitex-dev 2026-07-28 and adopted ecosystem-wide.

THE DEFECT THIS ENDS
--------------------
Store ownership used to be a PATH, and one file here has three names::

    /home/agent/.scitex/cards/cards.db                    (container only)
    /home/ywatanabe/.scitex/cards/cards.db                (host + container)
    /home/ywatanabe/.dotfiles/src/.scitex/cards/cards.db  (the realpath)

ONE inode, three spellings. ``_db_freshness.stamp_store_provenance`` rewrote the
stamp with the WRITER'S OWN spelling on every write, so the name FLIPPED each
time a different mount namespace wrote a card. Whenever it landed on the
container-only name the host could not ``stat`` it, ``_dual_write._same_file``
fell through to a realpath STRING compare that can never match across that
boundary, the ownership guard said "different store", and the operator's board
answered ``GET /tasks`` with HTTP 500. It was repaired three times on
2026-07-28 and broken three times again by nothing more sinister than writing
cards from the other side.

scitex-storage's formulation is the whole of it: *a path is not identity when
more than one view or code path can produce it.*

THE REPAIR
----------
The database carries its own opaque identity in ``schema_meta.store_uuid``, and
:func:`identity_verdict` — a PURE function of two optional strings — decides
ownership from that alone. It takes no path, no connection and no environment,
so no mount namespace, working directory or bind mount can change its answer.

IDENTITY AND RESOLUTION STAY TWO SEPARATE RULES (design §6). "No expectation is
not evidence of a foreign store" is about IDENTITY and lives here. "Never
auto-create a store at an ambient default" is about RESOLUTION and lives in
:func:`scitex_cards._paths.refuse_ambient_store_creation`. NOTHING in this
module is an input to that guard, and nothing here may be used to bypass it —
merge the two and rule 4b silently becomes "use whatever you were pointed at".
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid as _uuid_module
from pathlib import Path
from typing import Final

from ._store_tx import begin_write_transaction

logger = logging.getLogger(__name__)

#: ``schema_meta`` key holding this database's own identity.
KEY_STORE_UUID: Final[str] = "store_uuid"

#: libpq's own connect-timeout variable. Used rather than rewriting the DSN:
#: appending a parameter means parsing and re-emitting a connection string in
#: two syntaxes (URL and keyword/value), and a reporting primitive should not be
#: in the business of editing the target it was asked to describe.
_PGCONNECT_TIMEOUT_ENV: Final[str] = "PGCONNECT_TIMEOUT"

#: Seconds :func:`store_uuid_at` will wait for a server before answering None.
#: Deliberately short: this backs the "which store am I on?" diagnostic, whose
#: whole value is being fast when everything else is broken. A store that cannot
#: answer within this window is not one whose identity is worth blocking on.
_REPORTING_CONNECT_TIMEOUT_S: Final[str] = "3"

#: Environment variable carrying the caller's EXPECTATION of which store it
#: should find. The expectation is INJECTED — an explicit argument, else this
#: variable, else absent. It is NEVER read out of the database and NEVER
#: computed from a path: either would make the check circular and re-introduce
#: the view-dependence being removed (design §4).
ENV_EXPECTED_STORE_UUID: Final[str] = "SCITEX_CARDS_STORE_UUID"

#: The three verdicts. Plain strings so a report, a log line and a test can all
#: name them without importing an enum.
ACCEPT: Final[str] = "ACCEPT"
ADOPT: Final[str] = "ADOPT"
REFUSE: Final[str] = "REFUSE"

#: Form validated at the STAMP boundary only, so the corpus stays canonical
#: while the COMPARISON stays dumb (design §3). Deliberately accepts any
#: conforming bare lowercase ``8-4-4-4-12`` rather than uuid4 specifically: an
#: identity minted by another scitex package must never be rejected over a
#: version nibble.
_STORE_UUID_FORM: Final[re.Pattern[str]] = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)


def mint_store_uuid() -> str:
    """Mint a NEW store identity: a bare lowercase uuid4, ``8-4-4-4-12``.

    NEVER DERIVE THIS FROM A PATH, A HOSTNAME, A TIMESTAMP, OR ANY HASH OF
    THEM. This paragraph is the requirement, not a note about it.

    Someone will eventually look at this function, see an unpredictable value
    where a reproducible one would be tidier, and "improve" it into a
    deterministic hash of the store path (or the hostname, or the inode, or the
    creation time). That change reintroduces EXACTLY the defect this module
    exists to remove: a value computed from a path is view-dependent, so the
    same file reached under two names yields two identities and the guard is
    back to refusing a database its own. A hostname makes the identity change
    when the file is served from elsewhere; a timestamp makes two processes
    minting concurrently disagree about a store they both created.

    The identity's ONLY job is to be the same string on both sides of a mount
    namespace and a different string for a different store. Randomness is what
    delivers that with no input at all — and taking no input is the property
    being protected. ``test_minting_is_never_derived_so_two_mints_in_one_process_differ``
    enforces it: two mints from ONE process, ONE host and ONE working directory
    must differ, which no path/host/second-resolution derivation can satisfy.
    """
    return str(_uuid_module.uuid4())


def is_store_uuid(value: str | None) -> bool:
    """Is ``value`` a well-formed store identity (bare lowercase 8-4-4-4-12)?"""
    return bool(value) and bool(_STORE_UUID_FORM.match(str(value)))


def identity_verdict(db_uuid: str | None, expected: str | None) -> str:
    """Decide store ownership from two optional strings. PURE, by construction.

    No path, no connection, no environment: everything the verdict depends on
    is in its two arguments, so no view or mount namespace can change it. That
    is the entire repair — the host and the container reach the same answer
    because neither one looks at a name.

    ==========  ==========  =========  =====================================
    ``db_uuid`` ``expected`` verdict   why
    ==========  ==========  =========  =====================================
    ``None``    ``None``    ``ADOPT``  legacy/fresh database, nothing claims it
    ``None``    ``X``       ``REFUSE`` an expectation was DECLARED and the
                                       database cannot show it (see below)
    ``X``       ``None``    ``ACCEPT`` absence of an expectation is not
                                       evidence of a foreign store (rule 4b)
    ``X``       ``X``       ``ACCEPT`` same store, whatever it is called here
    ``X``       ``Y``       ``REFUSE`` a declared expectation, contradicted
    ==========  ==========  =========  =====================================

    ROW 1 MUST STAY ``ADOPT``. Every database in existence today carries no
    identity, including the live fleet board. Refusing them here would brick
    the board on deploy — read-only, with no YAML behind it, which is the
    outage this work exists to end. Unstamped means "not yet claimed", never
    "wrong".

    WHY ROW 2 REFUSES (design §5.1). Under ``ADOPT`` the guard would not merely
    proceed, it would MINT — writing the expected uuid into a database that
    never demonstrated it deserved that identity. A misresolution then becomes
    permanent, SELF-CERTIFYING identity that every later check agrees with,
    including the checks built to catch it. Refusing is recoverable; adopting
    manufactures the evidence. Measured corroboration: on 2026-07-28 a board
    served HTTP 200 with ZERO cards while the store held 2647. Row 1 must stay
    ``ADOPT`` for legacy databases, so row 2 = ``REFUSE`` is the ONLY rule that
    closes misresolution-to-an-empty-database. The sequencing that keeps it
    safe is design §9 constraint 1: stamp the store FIRST, declare the
    expectation SECOND.

    Rows 2 and 5 are the only refusals, and both are rows where an expectation
    was DECLARED and the database did not meet it. Where no expectation was
    declared this function never refuses: a guard that refuses in the cases it
    cannot judge denies service to the process that was explicitly pointed at
    the store, which is what took the board down.

    THE COMPARISON IS EXACT STRING EQUALITY. No ``uuid.UUID()`` parse, no case
    folding, no stripping of ``{}`` or a ``urn:uuid:`` prefix. A comparison
    that normalises is a comparison with a second spelling, which is the class
    of bug being removed. The identity is OPAQUE: never parsed for a version
    nibble, never sorted, never used as a filename.
    """
    if db_uuid is None:
        return ADOPT if expected is None else REFUSE
    if expected is None:
        return ACCEPT
    return ACCEPT if db_uuid == expected else REFUSE


def expected_store_uuid(explicit: str | None = None) -> str | None:
    """The caller's EXPECTATION: explicit argument, else the env var, else None.

    A blank/whitespace-only environment value means "no expectation declared",
    not "expect the empty identity" — an env var that exists but was never
    filled in must not refuse every store on the machine. A non-blank value is
    returned VERBATIM and is never trimmed, upper/lower-cased or otherwise
    normalised: normalising the expectation is normalising the comparison.

    A malformed expectation is NOT rejected here. It simply never compares
    equal, so it refuses — which is the fail-closed direction, and the operator
    sees the exact string they configured in the refusal message.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(ENV_EXPECTED_STORE_UUID)
    if raw is None or not raw.strip():
        return None
    return raw


def read_store_uuid(conn: sqlite3.Connection) -> str | None:
    """This database's own identity, or ``None`` when it carries none.

    ``None`` is a LEGAL, load-bearing input to :func:`identity_verdict` — rows
    1 and 2 of the table are both an absent identity — so an unstamped or
    unreadable ``schema_meta`` is reported as "no identity" rather than raised.
    That is the safe direction on both branches: with an expectation declared
    it REFUSES (row 2), and with none declared it falls through to the legacy
    path comparison exactly as an unstamped database always has.
    """
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (KEY_STORE_UUID,)
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    # _sole_value, NOT row[0]: psycopg's dict_row yields a real dict, which
    # raises KeyError on a positional index. THIRD instance of this same bug
    # in one port (after _read_stamps and the canonical read's COUNT), so it
    # is a pattern rather than three accidents -- every one-column fetchone()
    # in this package is a candidate.
    from ._schema_probe import _sole_value  # noqa: PLC0415 -- import cycle

    raw = _sole_value(row)
    if raw is None:
        return None
    value = str(raw)
    return value or None


def store_uuid_at(db_path: str | Path) -> str | None:
    """Read a database's identity by TARGET, read-only, never raising.

    The exposure primitive (design §11 / contract point 8): ``resolve_store``
    and the health doctor use it so the identity can be put into config without
    archaeology. Absent file, unreadable file, no schema — all ``None``.

    ``db_path`` may be a filesystem path OR a PostgreSQL URL. Handling the
    server case is not a nicety: ``Path("postgresql://h/db").exists()`` is
    False, so before this branch existed a PostgreSQL store reported
    ``store_uuid: None`` — indistinguishable from "this store has no identity",
    and reported by the very verb an operator runs to check identity. That is
    the worse failure, because it does not look like one. An identity that
    silently reads None also makes ``expected_uuid`` unfalsifiable, which is
    how a mismatch guard passes on the wrong store.

    The server branch is TIME-BOUNDED, and that is part of the contract rather
    than a tuning detail. libpq applies no connect timeout by default, so a
    server that is down (as opposed to refusing) leaves this blocked
    indefinitely — measured at over 40s against a dead port before the bound
    existed. This primitive backs ``resolve-store``, which is what someone runs
    WHEN THINGS ARE ALREADY BROKEN, so hanging is not a lesser failure than
    answering wrongly: both leave the operator with no answer, and the hang also
    burns the time they are trying to save. "Never raises" is only half a
    reporting contract; "answers promptly" is the other half.

    An explicit ``connect_timeout`` in the DSN or a caller-set
    ``$PGCONNECT_TIMEOUT`` still WINS — the bound is a default for the case
    where nobody expressed an intent, never an override of one.
    """
    from ._store_url import is_postgres_url

    if is_postgres_url(db_path):
        prior_timeout = os.environ.get(_PGCONNECT_TIMEOUT_ENV)
        if prior_timeout is None:
            os.environ[_PGCONNECT_TIMEOUT_ENV] = _REPORTING_CONNECT_TIMEOUT_S
        try:
            from ._db import connect

            conn = connect(str(db_path))
        except Exception:
            # Same contract as the SQLite branch: unreachable or unreadable is
            # None, never an exception. A server adds failure modes a file does
            # not have (down, refused, auth), and a REPORTING call must not
            # raise on any of them.
            return None
        finally:
            if prior_timeout is None:
                os.environ.pop(_PGCONNECT_TIMEOUT_ENV, None)
        try:
            return read_store_uuid(conn)
        except Exception:
            return None
        finally:
            conn.close()

    path = Path(db_path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return read_store_uuid(conn)
    finally:
        conn.close()


def stamp_store_uuid(conn: sqlite3.Connection, identity: str) -> None:
    """Bind this database to ``identity``. ONE ``schema_meta`` row, nothing else.

    Call inside the caller's write transaction. This writes the single
    :data:`KEY_STORE_UUID` row and NOTHING ELSE: it must not touch
    ``store_path``, must not touch ``tasks`` or ``messages``, and must not
    change what any resolver resolves. Re-stamping ``store_path`` was repair
    attempt 3 on 2026-07-28 — the HTTP 500 cleared and the board came back
    EMPTY, which is the 2,138-card-wipe shape. Pinned by
    ``test_binding_an_identity_leaves_every_card_row_untouched``.

    MINTED ONCE, NEVER REWRITTEN. Idempotent for the same value; REFUSES a
    different one. Re-identifying a store is a deliberate operator action with
    its own command, not a side effect of whichever process happened to write
    first — an identity that a write can silently replace is the path stamp
    again, wearing a uuid.

    Raises
    ------
    ValueError
        When ``identity`` is not a bare lowercase ``8-4-4-4-12``, or when the
        database already carries a DIFFERENT identity.
    """
    if not is_store_uuid(identity):
        raise ValueError(
            f"refusing to stamp {identity!r} as a store identity: it is not a "
            f"bare lowercase 8-4-4-4-12 uuid. Form is validated HERE, at the "
            f"stamp boundary, so the corpus stays canonical while the "
            f"comparison stays dumb (an identity that needs normalising before "
            f"it compares equal is two identities)."
        )
    current = read_store_uuid(conn)
    if current == identity:
        return  # idempotent: the same binding, already made
    if current is not None:
        raise ValueError(
            f"refusing to RE-IDENTIFY this database: it is already bound to "
            f"{current!r} and this would bind it to {identity!r}. A store's "
            f"identity is minted once; changing it makes every prior check that "
            f"agreed with the old value retroactively meaningless. If you truly "
            f"mean to re-identify it, clear the {KEY_STORE_UUID!r} schema_meta "
            f"row deliberately and by hand, having first recorded why."
        )
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
        (KEY_STORE_UUID, identity),
    )


def adopt_store_uuid(db_path: str | Path, identity: str | None = None) -> str:
    """Bind ``db_path`` to an identity, once, deliberately. Returns the identity.

    The migration step of design §9, as a function. Mints when ``identity`` is
    ``None``. Idempotent: a database that already carries an identity keeps it
    and that value is returned, so re-running is safe and re-identification
    still requires the deliberate hand edit :func:`stamp_store_uuid` describes.

    An explicit one-time bind is AUDITABLE. The drive-by alternative — the
    first write to an unstamped database claims it — may never stamp a
    CONFIGURED expectation, because that is exactly the mint row 2 of the
    decision table refuses.
    """
    from ._db import connect

    existing = store_uuid_at(db_path)
    if existing is not None:
        return existing
    value = identity if identity is not None else mint_store_uuid()
    conn = connect(str(db_path))
    try:
        begin_write_transaction(conn)
        stamp_store_uuid(conn, value)
        conn.commit()
    finally:
        conn.close()
    return value


__all__ = [
    "ACCEPT",
    "ADOPT",
    "ENV_EXPECTED_STORE_UUID",
    "KEY_STORE_UUID",
    "REFUSE",
    "adopt_store_uuid",
    "expected_store_uuid",
    "identity_verdict",
    "is_store_uuid",
    "mint_store_uuid",
    "read_store_uuid",
    "stamp_store_uuid",
    "store_uuid_at",
]

# EOF
