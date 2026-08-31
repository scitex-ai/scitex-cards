#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE ONE FAIL-LOUD READER of the canonical store. Every door shares it.

Extracted verbatim from :mod:`scitex_cards._store` (which is a thin
orchestrator over focused siblings) so the guard, the incident history that
justifies each of its checks, and its tests sit in one place instead of being
buried among the mutation helpers. ``_store`` re-exports it, so every historical
import — ``from ._store import _read_canonical_db_or_raise`` — is unchanged.

Three callers, ONE policy, deliberately:

    _store._read_write_doc      the read-modify-write cycle behind every CRUD verb
    _model.load_doc             the pure-read path (``load_tasks`` → ``list_tasks``)
    _store_backend.write_doc_to_db   by inheritance

That single chokepoint is the point. On 2026-07-19 the write door refused a
foreign store correctly all day while the read door happily returned its rows,
and a packaged fixture was read AS THE BOARD for hours because of it. Do NOT
split this into a lenient read variant and a strict write variant — that
recreates exactly the asymmetry that outage was made of.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ._store_errors import StoreNotProvisionedError, StoreUnavailableError


def _read_canonical_db_or_raise() -> dict:
    """Read the whole store from SQLite for a read-modify-write. FAILS LOUD.

    THE BUG THIS REPLACES turned a READ error into TOTAL DATA LOSS, three times
    on 2026-07-19. The old line was::

        doc = export_doc(None)[0] or {}

    Read-modify-write means whatever this returns is what gets WRITTEN BACK as
    the canonical store. So ``or {}`` does not mean "no cards found" — it means
    "delete every card", and it says so to nobody. Any reason the export came
    back empty (a stamp naming another store, an unreadable DB, a resolution
    that landed on the wrong path) is silently promoted from a failed read into
    an authoritative empty board. Measured: 2,138 cards -> 3, from one
    ``comment_task`` call.

    #507's own commit message predicted this exact shape ("2065 cards down to
    1") for ``load_doc`` and guarded that one. The identical hazard sat in the
    sibling expression and was not — which is the same lesson as the two write
    doors: fixing one instance of a pattern is not fixing the pattern.

    A store with genuinely zero cards is legitimate ONLY when the DB has no
    tasks table content to begin with; that case returns an empty doc honestly.
    Every other emptiness is a failed read and raises, because refusing to
    write is always recoverable and writing nothing over everything is not.
    """
    from ._db import resolve_db_path
    from ._db_export import export_doc  # noqa: F401 -- re-exported for callers
    from ._store_target import resolve_store_target
    from ._store_url import is_postgres_url

    # POSTGRESQL TAKES ITS OWN BRANCH, AND THE SQLITE PATH BELOW IS UNTOUCHED.
    # SQLite is the default and PostgreSQL is opt-in, so the default backend
    # keeps byte-identical behaviour -- existence as a stat() call, the
    # read-only URI connection, all of it. A store this function has already
    # emptied once is not the place to refactor a working path in order to
    # share code with a new one.
    target = resolve_store_target(None)
    if is_postgres_url(target):
        return _read_canonical_postgres(target)

    db_path = Path(resolve_db_path(None))

    # A MISSING DB IS NOT AN EMPTY STORE. `export_doc` answers a nonexistent
    # file with a perfectly well-formed ``{"tasks": []}``, which is why merely
    # type-checking the result does not help — that value is indistinguishable
    # from a real empty board and is exactly what got written back over 2,138
    # cards. Ask the file system, not the exporter.
    if not db_path.exists():
        # StoreUnavailableError, not a bare RuntimeError: this message names an
        # absolute path and is rendered into a PAGE by the Django layer, which
        # scitex-hub found being served verbatim to anonymous visitors of
        # /apps/cards/. A named type lets that layer pick the audience-appropriate
        # form instead of string-matching prose. Still a RuntimeError subclass, so
        # every existing `except Exception` keeps working.
        raise StoreNotProvisionedError(
            f"canonical store {db_path} does not exist. REFUSING to continue: "
            f"the exporter answers a missing database with an empty document, "
            f"and this value is written back as the WHOLE store — every card "
            f"replaced by nothing. Point $SCITEX_CARDS_DB at the real database "
            f"(there is no CLI verb that bootstraps one — a database is written "
            f"by normal operation once the pointer is correct)."
        )

    # OWNERSHIP IS CHECKED HERE TOO, NOT ONLY ON WRITE. This is a read-MODIFY-
    # write helper, so what the write door would refuse must fail at the read
    # door: same verdict, several steps earlier. It was the missing half on
    # 2026-07-19 — the write guard refused correctly all day while reads against
    # a foreign-stamped DB kept succeeding, so the disagreement only surfaced
    # once someone tried to write, long after a packaged fixture had been read
    # AS the board. Reusing the write door's own predicate keeps one definition
    # of "owns"; an UNSTAMPED DB is adoptable there and stays adoptable here.
    from ._dual_write import _db_mirrors_this_store

    if not _db_mirrors_this_store(db_path, db_path):
        raise RuntimeError(
            f"REFUSING TO READ {db_path} as the store: that database is "
            f"stamped for a DIFFERENT store than this process resolved. "
            f"Reading it would treat another board's rows as yours, and the "
            f"write-back would then replace that board. Run `scitex-cards "
            f"health` to see both paths, then point $SCITEX_CARDS_DB at this "
            f"store's own database."
        )

    # A RETIRED STORE IS NOT THE STORE, and this is the same shape as the
    # ownership check above: reading it would serve yesterday's board while
    # looking perfectly healthy. After a verified copy there are TWO stores with
    # the same identity, so identity alone cannot say which one is authoritative
    # -- only a statement of which is CURRENT can, and the cutover is the act of
    # moving that statement into the old store precisely so a straggler holding
    # the old path fails loudly here rather than quietly serving stale rows.
    #
    # Checked at the READ door deliberately. The 2026-07-19 outage was the write
    # door refusing correctly all day while reads kept succeeding, and this
    # docstring already says not to recreate that asymmetry.
    _refuse_if_retired(db_path)

    doc = _export_and_count_in_one_snapshot(db_path)
    return doc


def _read_canonical_postgres(target: str) -> dict:
    """The canonical read against a PostgreSQL store.

    SAME THREE GUARDS AS THE SQLITE PATH, re-expressed for a server rather than
    a file. They are stated here rather than shared because each one asks a
    question whose MEANING differs by backend, and a guard that silently means
    something else is worse than one that is absent:

      exists    -- not a stat() call. A server can be reachable while holding
                   no store at all, so the question is "does this database
                   carry the schema", asked of the catalogue.
      ownership -- the store_uuid comparison, which is already backend-neutral:
                   a database stamped for a DIFFERENT store must not be read as
                   this one, or the write-back replaces that other board.
      retired   -- schema_meta, via the one shared definition in
                   _refuse_if_retired_on.

    Every failure RAISES. Returning an empty document from here would be
    written back as the whole store, which is this module's founding incident.
    """
    # connect(), NOT open_db(). open_db runs init_schema, which CREATES the
    # tables -- so an existence guard placed after it can never fire, and the
    # first version of this function proved exactly that: pointed at a database
    # with no store in it, it created the whole schema and then returned 0 tasks
    # instead of refusing. A guard defeated by the act of opening is not a guard.
    from ._db import connect
    from ._schema_probe import has_table
    from ._store_uuid import expected_store_uuid, read_store_uuid

    try:
        conn = connect(target)
    except _store_errors() as exc:
        # DELIBERATELY THE PARENT TYPE, NOT StoreNotProvisionedError, AND THIS
        # IS THE LOAD-BEARING LINE OF THE WHOLE DISTINCTION. A server that is
        # down, unreachable, out of connections or refusing auth arrives here.
        # That is an OUTAGE: it belongs in 5xx, it belongs in alerting, and the
        # client SHOULD retry. "This tenant has no store yet" is the opposite
        # answer on every one of those axes.
        #
        # Classifying this as not-provisioned would render an onboarding page
        # over a dead database and drop a real outage out of monitoring —
        # silently, which is strictly worse than the noisy misclassification
        # this module's change set exists to remove. If a future edit makes
        # this line raise the subclass, `test_an_unreachable_postgres_is_still
        # _a_server_fault` fails, and it should.
        raise StoreUnavailableError(
            f"cannot open the PostgreSQL store {target!r} ({exc}). REFUSING to "
            f"continue rather than writing an unverified document back over "
            f"the store."
        ) from exc

    try:
        # EXISTENCE, asked of the catalogue rather than the filesystem. An
        # empty-but-reachable server answers export_doc with a well-formed
        # {"tasks": []}, which is indistinguishable from a real empty board and
        # is exactly the value that must never be written back.
        if not has_table(conn, "tasks"):
            raise StoreNotProvisionedError(
                f"the PostgreSQL store {target!r} has no `tasks` table. "
                f"REFUSING to continue: the exporter answers a schemaless "
                f"database with an empty document, and that value is written "
                f"back as the WHOLE store."
            )

        # OWNERSHIP. Same verdict as the write door, several steps earlier. An
        # UNSTAMPED store is adoptable, exactly as on the SQLite path -- only a
        # store stamped for someone ELSE is refused.
        actual = read_store_uuid(conn)
        expected = expected_store_uuid()
        if actual and expected and actual != expected:
            raise RuntimeError(
                f"REFUSING TO READ {target!r} as the store: it is stamped "
                f"{actual}, but this process expects {expected}. Reading it "
                f"would treat another board's rows as yours, and the "
                f"write-back would then replace that board."
            )

        _refuse_if_retired_on(conn)
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    return _export_and_count_in_one_snapshot(target)


def _store_errors() -> tuple[type[BaseException], ...]:
    """Every driver error class that can escape a store read.

    Catching ``sqlite3.Error`` alone was exactly right while SQLite was the only
    backend. A psycopg error is NOT a subclass of it, so on PostgreSQL the
    handlers in this module would let a driver failure escape UNCAUGHT through
    the read path -- and this is a read-MODIFY-write helper, so an escape here
    is not a clean failure, it is an unguarded one.

    Catching by MEANING rather than by driver class. psycopg is an optional
    dependency (it ships in the `postgres` extra, not in a default install), so
    it joins the tuple only when it is importable -- a SQLite-only install must
    not start requiring a PostgreSQL driver to catch a SQLite error.
    """
    errors: list[type[BaseException]] = [sqlite3.Error]
    try:
        import psycopg  # noqa: PLC0415 -- optional, absent on SQLite-only installs
    except ModuleNotFoundError:
        return tuple(errors)
    errors.append(psycopg.Error)
    return tuple(errors)


def _refuse_if_retired(db_path: Path) -> None:
    """Raise :class:`StoreRetired` if this store has been superseded.

    ``unguarded_store=STATUS_CURRENT`` -- the PERMISSIVE era, and the choice is
    stated here rather than defaulted so it is visible at the call site.

    MEASURED 2026-07-30: no live store carries the retirement guards yet. They
    install via ``init_schema`` on a WRITE open, and this path opens read-only,
    where creating a trigger fails outright. Passing ``"refuse"`` today would
    make every reader answer "I cannot prove this store is current" and refuse
    on release day -- the guard causing the very outage it exists to prevent.
    Flip to ``"refuse"`` once a released client has opened the live stores for
    writing; that is a one-line change and it is meant to be visible.

    The retirement branch is NOT deferred: a store that says it is retired is
    refused in either era, which is what the cutover depends on.

    A store too old to have ``schema_meta`` is treated as un-retired rather than
    unreadable -- absence of the table is not a retirement, and refusing here
    would break stores that predate it for no safety gain.
    """
    from ._schema_probe import trigger_names
    from ._store_retirement import STATUS_CURRENT, read_status

    # The SQLite open is UNCHANGED, deliberately: read-only is expressed as a
    # URI query parameter here and has no equivalent on the seam, so the
    # default backend keeps exactly the connection it has always had. The
    # PostgreSQL caller opens its own and calls _refuse_if_retired_on directly.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        _refuse_if_retired_on(conn)
    finally:
        conn.close()


def _refuse_if_retired_on(conn) -> None:
    """The retirement guard itself, over an ALREADY-OPEN connection.

    Split out so both backends run ONE definition of "is this store retired".
    Duplicating it per backend is how the two answers drift, and the whole
    point of the check is that exactly one store can be current.
    """
    from ._schema_probe import trigger_names
    from ._store_retirement import STATUS_CURRENT, read_status

    try:
        rows = _schema_meta_mapping(conn)
    except _missing_table_errors():
        # A store too old to have schema_meta is un-retired, not unreadable.
        return
    # Via the shared probe: this set feeds read_status, and on a backend
    # whose catalogue this query cannot reach the set comes back EMPTY --
    # which reads as "no guards" and, under unguarded_store=STATUS_CURRENT,
    # reports the store healthy and current. A store that can prove nothing
    # must not answer yes.
    triggers = trigger_names(conn)
    read_status(rows, triggers, unguarded_store=STATUS_CURRENT)


def _schema_meta_mapping(conn) -> dict:
    """schema_meta as {key: value}, whatever row shape the driver returns.

    ``dict(cursor)`` worked because sqlite3 yields 2-tuples. psycopg's
    ``dict_row`` yields ``{"key": ..., "value": ...}`` per row, and ``dict()``
    over those raises rather than building the mapping -- so the retirement
    guard would fail on the backend it is being taught to read.
    """
    rows = conn.execute("SELECT key, value FROM schema_meta").fetchall()
    out = {}
    for row in rows:
        if isinstance(row, dict):
            out[row["key"]] = row["value"]
        else:
            out[row[0]] = row[1]
    return out


def _missing_table_errors() -> tuple[type[BaseException], ...]:
    """The errors that mean "that table is not there", per backend.

    Deliberately NARROWER than :func:`_store_errors`. This one is caught and
    SWALLOWED, so widening it would turn a real read failure into a silent
    "not retired" -- the store would then be read and written back. SQLite says
    OperationalError; PostgreSQL says UndefinedTable.
    """
    errors: list[type[BaseException]] = [sqlite3.OperationalError]
    try:
        from psycopg import errors as _pg_errors  # noqa: PLC0415 -- optional
    except ModuleNotFoundError:
        return tuple(errors)
    errors.append(_pg_errors.UndefinedTable)
    return tuple(errors)


def _export_and_count_in_one_snapshot(db_path: Path) -> dict:
    """Export the store AND count its rows from ONE WAL read snapshot.

    WHY ONE SNAPSHOT, AND WHY THAT IS THE WHOLE FIX. The cross-check at the
    bottom of this function is only evidence if both numbers describe the SAME
    database state. They used to not: the export ran on ``export_doc``'s own
    connection while the verifying ``COUNT(*)`` ran on a second, separately
    opened read-only one. The store is WAL, so those are two INDEPENDENT
    snapshots taken an export-duration apart (~1.25s on the live 2,379-card
    board). Any other agent writing in that window makes ``exported <
    in_table`` with NO card missing and nothing wrong — and the guard then
    refuses a perfectly healthy read.

    Not hypothetical: ``list_tasks`` refused fleet-wide at 2,374-vs-2,375 while
    ``scitex-cards dev db verify`` reported ``quick_check=ok``, and a background
    writer inserting one row every 100ms reproduces it 10/10 (0/10 with the
    writer off). The exported count consistently tracked the table as it had
    been one export-duration earlier — the signature of a stale snapshot, not
    of a lost card.

    AN ALWAYS-REFUSING GUARD IS THE SAME USELESSNESS AS AN ALWAYS-PASSING ONE,
    and this package has already shipped that shape: the S2 read accelerator
    (deleted in 256bc2d1) had a freshness check that could never pass again
    once SQLite became canonical, so it refused unconditionally and fell back
    to serving an empty board. So the fix is emphatically NOT a tolerance
    window, a retry-until-equal loop, a swallowed mismatch, or a flag — every
    one of those silences the gate rather than repairing it. It is to make the
    comparison snapshot-consistent and leave the verdict exactly as strict.

    ``BEGIN DEFERRED`` takes the WAL read snapshot at the first read statement
    and holds it until rollback, so the ``COUNT(*)`` sees precisely the rows the
    export walked. A concurrent writer can no longer come between them, while
    an export that genuinely under-reports the rows it was handed still
    disagrees with the count — the failure this exists to catch, unchanged.

    THE CONNECTION COMES FROM ``open_db``, NOT a hand-rolled ``sqlite3.connect``.
    :func:`scitex_cards._db.connect` is where the min-client-version gate runs,
    and an outdated client must ERROR the moment it opens the store rather than
    warn. Opening a raw connection here to "keep the check independent" would
    silently delete that gate — and independence was never the property that
    made this check real. Asking the TABLE something the EXPORTER cannot fake
    is, and that survives sharing a connection: the exporter assembles ``tasks``
    from the ``card_json`` payloads it walks, while ``COUNT(*)`` asks the table
    how many rows are actually there.
    """
    from ._db import open_db
    from ._db_export import export_doc

    try:
        conn = open_db(None)
    except _store_errors() as exc:
        raise RuntimeError(
            f"cannot open {db_path} to read the canonical store ({exc}). "
            f"REFUSING to continue rather than writing an unverified document "
            f"back over the store."
        ) from exc

    try:
        # Our own read transaction: opened explicitly so the snapshot is ours
        # and pinned, rolled back in `finally` so this never holds a lock and
        # never writes.
        # BEGIN DEFERRED is SQLite's spelling. On PostgreSQL "psycopg opens a
        # transaction implicitly" is TRUE AND NOT SUFFICIENT, which is what an
        # earlier version of this comment got wrong: PostgreSQL's default
        # isolation is READ COMMITTED, and under READ COMMITTED **every
        # statement takes a fresh snapshot even inside a transaction**.
        # Measured on the live server -- `SHOW transaction_isolation` on our own
        # connection returned `read committed`.
        #
        # So being in a transaction was never the property this function needs.
        # It needs ONE snapshot spanning the export and the COUNT(*), which on
        # PostgreSQL is REPEATABLE READ. Without it the cross-check compares two
        # different database states and still passes almost every time -- the
        # same shape as the 2,374-vs-2,375 fleet-wide refusal, only rarer and so
        # harder to attribute.
        #
        # The rollback first is deliberate: SET/BEGIN ISOLATION LEVEL must be
        # the first statement of a transaction, and psycopg may already have
        # opened one on an earlier statement.
        from ._schema_probe import _is_postgres  # noqa: PLC0415

        if _is_postgres(conn):
            conn.rollback()
            conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        else:
            conn.execute("BEGIN DEFERRED")

        doc = export_doc(conn=conn)[0]
        if not isinstance(doc, dict) or not isinstance(doc.get("tasks"), list):
            raise RuntimeError(
                f"canonical read of {db_path} returned no usable document "
                f"(got {type(doc).__name__}). REFUSING to continue: this value "
                f"would be written back as the whole store."
            )

        # CROSS-CHECK the export against the table itself, in the same
        # snapshot. These can now only disagree when the read half failed in a
        # way it did not report — a partial read, a schema the exporter could
        # not walk. An export that silently under-reports is the total-loss
        # case, because the difference is DELETED on write-back. Zero-vs-zero
        # agrees and is allowed through: a genuinely empty database is a
        # legitimate store.
        try:
            # _sole_value, NOT [0]: psycopg's dict_row yields a real dict, which
            # raises KeyError on a positional index. This is the CROSS-CHECK
            # that stops a partial read being written back over the store, so
            # it must not be the thing that breaks on the new backend.
            from ._schema_probe import _sole_value  # noqa: PLC0415

            in_table = _sole_value(
                conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
            )
        except _store_errors() as exc:
            raise RuntimeError(
                f"cannot read {db_path} to verify the canonical read ({exc}). "
                f"REFUSING to continue rather than writing an unverified "
                f"document back over the store."
            ) from exc
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    exported = len(doc["tasks"])
    if exported != in_table:
        raise RuntimeError(
            f"canonical read of {db_path} is INCOMPLETE: the exporter returned "
            f"{exported} cards but the tasks table holds {in_table}. REFUSING "
            f"to continue — this document is written back as the whole store, "
            f"so the {in_table - exported} missing cards would be DELETED. "
            f"Verify with `scitex-cards dev db verify`, then point $SCITEX_CARDS_DB "
            f"at a complete database for this store."
        )
    return doc


__all__ = ["_read_canonical_db_or_raise"]

# EOF
