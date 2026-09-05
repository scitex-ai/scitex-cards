#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE TEST SUITE CANNOT REACH THE LIVE STORE. Enforced here, not by discipline.

On 2026-07-19 this suite rebuilt the fleet's production database from its own
fixtures THREE TIMES in one session:

    2,136 cards -> 21     (mirror write path)
    2,138 cards -> 1      (canonical write path)
    2,138 cards -> 3      (canonical read path, via one `comment_task`)

All three were recovered from the snapshot repo's git history. All three had
the same enabling condition, and it is not any of the three bugs that were
fixed afterwards: **a test that never sets ``$SCITEX_CARDS_DB``**. With the
variable unset, ``resolve_db_path(None)`` walks its precedence chain to the
user-canonical path — which IS the real board — and every in-code ownership
guard then sees a perfectly legitimate write to the store it was told to use.
No guard can refuse that, because from inside the code there is nothing wrong
with it.

So the barrier belongs HERE, in the harness, above the code under test. A rule
enforced inside the thing being tested cannot bound the damage that thing can
do; a rule in the harness cannot be reached by any future change to resolution
order, precedence, backend selection, or env compat.

WHY ``autouse`` + ``session`` + ``os.environ`` RATHER THAN ``monkeypatch``:
the pinning must be in place before the first test imports ``scitex_cards``
(``_env_compat.mirror_env()`` runs at import time and reads the environment),
and it must also be inherited by SUBPROCESSES — the concurrency tests pass
``env=os.environ.copy()`` to real child processes, which is precisely how the
first wipe happened. ``monkeypatch`` is per-test and would leave the gap open
during collection and in any test that forgets it.

Per-test overrides still work exactly as before: a test that sets ``ENV_DB``
via ``monkeypatch.setenv`` shadows this for its own duration. This fixture only
supplies a SAFE DEFAULT where there previously was a dangerous one.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

#: Every env name that can point the package at a store. All are pinned, so a
#: half-applied rename cannot leave one of them aimed at the live board.
_STORE_ENV_VARS = (
    "SCITEX_CARDS_DB",
    "SCITEX_CARDS_TASKS_YAML_SHARED",
    "SCITEX_STORE_DSN",
)

#: Env names that select WHICH BACKEND is canonical. These are CLEARED, not
#: pinned. Pinning the store path while inheriting the backend selector makes
#: the suite's behaviour depend on the developer's shell: a maintainer who has
#: exported a backend selector by hand (as anyone working the cutover
#: does) flips every test into DB-canonical mode against a scratch DB that was
#: never created, and they all fail with "canonical store ... does not exist".
#: A test that WANTS canonical mode sets this itself; the default must be the
#: same everywhere.
_BACKEND_ENV_VARS = (
    "SCITEX_CARDS_STORE_BACKEND",
    "SCITEX_CARDS_READ_BACKEND",
)

#: ``$SCITEX_STORE_DSN`` names the PostgreSQL the storage primitive
#: (``scitex_dev.store``) opens. IT IS THE LIVE FLEET BOARD on every machine
#: this suite runs on -- measured 2026-08-30, injected into every sac-managed
#: agent container alongside ``$SCITEX_CARDS_DB``.
#:
#: The four variables above are pinned because a test that resolves the real
#: board can rewrite it, which this suite did three times in 2026-07. That
#: reasoning does not stop at this package's own resolver: the moment one test
#: reaches the store through the PRIMITIVE instead of through
#: ``scitex_cards``, an unpinned ``$SCITEX_STORE_DSN`` hands it the same live
#: board by a route no ``scitex_cards`` guard sits on. Nothing in
#: ``src/scitex_cards`` reads this variable TODAY, so the hole is not currently
#: reachable -- it is pinned now because it is cheap now, and because the
#: postgres port that is landing will make it reachable.
_STORE_DSN_ENV = "SCITEX_STORE_DSN"


# --------------------------------------------------------------------------- #
# A throwaway PostgreSQL for the whole session.                                #
# --------------------------------------------------------------------------- #
#
# NOT hand-rolled. ``scitex_dev.store.testing`` is the primitive's own test
# affordance: ``writable_dsn()`` finds a cluster that ACCEPTS WRITES (it runs
# ``pg_is_in_recovery()`` rather than assuming -- every host's own loopback
# 55432 is a READ-ONLY STANDBY, which accepts the connection and refuses the
# DDL) and ``ephemeral_schema()`` carves a uniquely named schema out of it.
#
# The yielded DSN carries ``options=-csearch_path=<schema>``, and that is what
# makes it SAFE against the live cluster rather than merely polite: ``public``
# is not on the path, so an unqualified ``SELECT ... FROM tasks`` inside a test
# does not silently read the fleet's 6,399 cards -- it fails to resolve the
# relation at all. The schema is dropped CASCADE when the session ends.
_dsn_stack = contextlib.ExitStack()
atexit.register(_dsn_stack.close)


def _open_throwaway_postgres() -> "tuple[str | None, str | None, str]":
    """The CLUSTER, a schema-scoped DSN on it, and the reason if there is none.

    Returns the reason rather than a bare ``None`` because the fixture below
    turns it into a FAILURE MESSAGE. A PostgreSQL test that cannot reach a
    server must not skip: a skipped test and a passing test render identically
    in a green summary, which is exactly how a suite reports success while
    running nothing.

    THE CLUSTER IS RETURNED TOO, and that is what makes the PER-TEST store
    below possible. ``writable_dsn()`` is the expensive half -- it probes
    routes and may START A CLUSTER -- so it is entered ONCE for the session;
    ``ephemeral_schema()`` is the cheap half and is entered again per test.
    Handing back only the scoped DSN, as this did when only the session-wide
    pin existed, would leave a per-test caller with nowhere to carve from.
    """
    try:
        from scitex_dev.store.testing import ephemeral_schema, writable_dsn
    except ImportError as exc:  # scitex-dev predating the affordance
        return None, None, (
            "scitex_dev.store.testing is not importable "
            f"({type(exc).__name__}: {exc}). It ships the ephemeral-store "
            "affordance this suite needs; upgrade scitex-dev to >=0.57.0."
        )
    try:
        cluster = _dsn_stack.enter_context(writable_dsn())
        scoped = _dsn_stack.enter_context(ephemeral_schema(cluster, prefix="cards_tests"))
        _assert_scope_is_applied_by_the_server(scoped)
    except Exception as exc:  # noqa: BLE001 - report it, do not guess at it
        _dsn_stack.close()
        return None, None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
    return cluster, scoped, "ok"


def _search_path_libpq_will_apply(dsn: str) -> str:
    """The schema the SERVER will be asked for, read the way libpq reads it.

    A DSN can carry ``options`` more than once -- an xdist worker inherits the
    controller's already-scoped ``$SCITEX_STORE_DSN`` and ``ephemeral_schema``
    appends a second one -- and libpq honours the LAST occurrence of a repeated
    URI parameter, discarding the rest. Inside that value the last ``-c
    search_path=`` wins likewise. Reading the FIRST occurrence (the substring
    search this replaced) compared the controller's schema against the worker's
    session and refused every worker on PR #962's first run, while the
    controller, with a single ``options``, passed and printed a clean header.
    """
    from urllib.parse import parse_qsl, urlsplit

    values = [v for k, v in parse_qsl(urlsplit(dsn).query, keep_blank_values=True) if k == "options"]
    if not values:
        return ""
    want = ""
    for token in values[-1].replace("-c ", "-c").split():
        if token.startswith("-csearch_path="):
            want = token[len("-csearch_path="):]
    return want.split(",", 1)[0].strip().strip('"')


def _assert_scope_is_applied_by_the_server(scoped: str) -> None:
    """The scoped DSN is only safe if the SERVER actually applies its search_path.

    MEASURED 2026-09-05: a transaction-mode pooler (pgbouncer 1.25 in front of
    scitex-primary:55432) accepts a DSN carrying ``options=-csearch_path=...``
    and silently DROPS the startup parameter, so the session has the default
    ``"$user", public`` and every unqualified ``tasks`` is the live board. That
    day only the store-identity stamp refused; this check names the cause
    instead of leaving it to the next guard. The DSN saying so is not the
    scope being in force.
    """
    import psycopg

    want = _search_path_libpq_will_apply(scoped)
    if not want:
        raise RuntimeError(f"the scoped DSN carries no search_path to verify: {scoped!r}")
    with psycopg.connect(scoped) as conn:
        got = conn.execute("SHOW search_path").fetchone()[0]
    on_path = {p.strip().strip('"') for p in str(got).split(",")}
    if want not in on_path:
        raise RuntimeError(
            f"the server did not apply the scoped DSN's search_path: asked for {want!r}, "
            f"the session has {got!r}. A pooler between the client and PostgreSQL "
            "(transaction-mode pgbouncer) drops the `options` startup parameter; point "
            "SCITEX_STORE_DSN at the PostgreSQL port itself (55433 on scitex-primary), "
            "never at the pooler, for tests."
        )


#: Resolved at IMPORT, before collection -- same reasoning as ``_SCRATCH``
#: below: the pin has to be in place before the first test module imports
#: anything that reads the environment.
_CLUSTER_DSN, _EPHEMERAL_DSN, _EPHEMERAL_DSN_REASON = _open_throwaway_postgres()


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """A DSN for a REAL, throwaway PostgreSQL schema. FAILS if there is none.

    The asymmetry with ``pytest.skip`` is the whole point and it is not
    stylistic. Every host in this fleet answers on 55432, and every one of
    those is a read-only STANDBY: a fixture that skipped on "cannot write"
    would skip on every developer machine and in CI, and the suite would go
    green having exercised no storage at all.
    """
    if _EPHEMERAL_DSN is None:
        pytest.fail(
            "No writable PostgreSQL is available, so this test cannot run "
            "against the engine it ships on. This is a FAILURE and not a skip: "
            "a skipped storage test is indistinguishable from a passing one.\n"
            f"  reason: {_EPHEMERAL_DSN_REASON}\n"
            "  a writable cluster is e.g. "
            "postgresql://ywatanabe__scitex-dev@scitex-primary:55432/scitex\n"
            "  (this host's own loopback 55432 is a READ-ONLY STANDBY)",
            pytrace=False,
        )
    return _EPHEMERAL_DSN


def pytest_report_header() -> str:
    """Say, in the header of EVERY run, whether a store was opened and why not.

    WITHOUT THIS THE REASON IS UNREACHABLE IN CI. ``_EPHEMERAL_DSN_REASON`` is
    only rendered by the ``postgres_dsn`` / ``postgres_cluster_dsn`` fixtures,
    and this repo runs pytest with ``-x``: the session stops at the first
    store-touching failure, which is reached long before any test requests
    those fixtures. So a run with no cluster produced a hundred identical
    "target does not name the store" refusals and NOWHERE said that the harness
    had failed to open a cluster at all -- measured on the pytest-matrix leg
    2026-08-30, where the cause had to be deduced from the fact that the
    refused path was the harness's own ``store0/cards.db`` placeholder.

    The header runs before collection and is printed even on a green run, so
    the answer is in the log whether or not anything failed.
    """
    if _EPHEMERAL_DSN is not None:
        return "scitex-cards store: a throwaway PostgreSQL schema was opened"
    return (
        "scitex-cards store: NO WRITABLE POSTGRESQL WAS OPENED -- every "
        "store-touching test will refuse.\n"
        f"  reason: {_EPHEMERAL_DSN_REASON}"
    )


@pytest.fixture(scope="session")
def postgres_cluster_dsn() -> str:
    """The CLUSTER, unscoped — for a test that carves its own schema. NEVER SKIPS.

    ``postgres_dsn`` above hands out ONE schema, shared for the session. A test
    that needs to ``CREATE SCHEMA`` itself (the foreign-key rung builds a
    miniature store and runs ``ALTER TABLE`` over it) needs the cluster the
    schema was carved from, not the scoped DSN — a ``search_path`` already
    pinned to somebody else's schema is the wrong starting point for making a
    new one.

    Same refusal contract as ``postgres_dsn``, and for the same reason: the
    fixtures this replaces skipped on "no server declared", which is how
    seventeen foreign-key tests reported green for months without opening a
    connection.
    """
    if _CLUSTER_DSN is None:
        pytest.fail(
            "No writable PostgreSQL is available, so this test cannot run "
            "against the engine it ships on. This is a FAILURE and not a skip: "
            "a skipped storage test is indistinguishable from a passing one.\n"
            f"  reason: {_EPHEMERAL_DSN_REASON}\n"
            "  (this host's own loopback 55432 is a READ-ONLY STANDBY)",
            pytrace=False,
        )
    return _CLUSTER_DSN


#: ``$SCITEX_DIR`` is the BASE DIRECTORY under ``resolve_db_path``'s tier-4
#: fallback (``scitex_config._ecosystem.local_state.user_path``), which reads
#: ``os.environ.get("SCITEX_DIR", str(Path.home() / ".scitex"))`` on EVERY
#: call — not just at import. It is pinned for the same reason the four vars
#: above are: a test that legitimately clears BOTH ``SCITEX_CARDS_DB`` and
#: ``SCITEX_CARDS_DB`` to exercise that fallback (see
#: ``tests/scitex_cards/test__paths.py``'s ``clean_store_env`` fixture, which
#: pops only the two DB vars) falls straight through to ``Path.home()`` — the
#: REAL home — unless something ALSO names ``$SCITEX_DIR``. Every test that
#: deliberately wants the fallback today happens to set ``$SCITEX_DIR``
#: itself too; this pin exists so that stays true by construction rather than
#: by every future test remembering it independently.
_STORE_ENV_VARS = _STORE_ENV_VARS + ("SCITEX_DIR",)

# CURRENCY gate suppression (scitex_cards._currency.check_currency, wired at
# the CLI group callback + MCP server import). The test suite needs it:
# `pytest-matrix` CI checks out a PR merge ref into an EDITABLE install, so
# scitex-dev's `ensure_current` sees this checkout as "N commits behind its
# own remote" (true, and harmless — the runner just hasn't fast-forwarded a
# ref it will never push to) and, without suppression, raises. Every test
# that invokes the CLI (`CliRunner` -> `main()`) or imports `_mcp_server`
# would otherwise fail on a condition that has nothing to do with the code
# under test — CI incident 2026-07-21, PR #550.
#
# ONLY `SCITEX_DEV_CURRENCY_SEVERITY=silent` — NOT `SCITEX_DEV_NO_CURRENCY_
# GATE=1`. The bypass var was tried first and made things WORSE: it wins
# over the severity knob in scitex-dev's own ladder, and it unconditionally
# prints a "CURRENCY GATE BYPASSED" warning to stdout regardless of severity
# (a known scitex-dev bug, not ours to patch) — which then broke a SECOND
# test asserting the CLI's `--json` output is pure JSON (the warn landed on
# stdout ahead of the JSON payload). `silent` alone runs the check, raises
# nothing, and prints nothing, so stdout stays clean. Unset (as in any real
# invocation outside this harness), the gate still errors loudly on a
# stale/broken install exactly as designed.
os.environ["SCITEX_DEV_CURRENCY_SEVERITY"] = "silent"


def _pin_to_scratch() -> Path:
    """Point every store-selecting variable at a throwaway directory."""
    scratch = Path(tempfile.mkdtemp(prefix="scitex-cards-tests-"))
    _point_env_at(scratch, _EPHEMERAL_DSN)
    for name in _BACKEND_ENV_VARS:
        os.environ.pop(name, None)
    return scratch


def _refused_placeholder(scratch: Path) -> str:
    """A store target the source doors REFUSE, for when no server was opened.

    Not "unset", which is the one value that must never be reached: with
    ``$SCITEX_CARDS_DB`` absent, ``resolve_db_path(None)`` walks its precedence
    chain to the user-canonical target -- the live board -- and that is the
    exact enabling condition of all three 2026-07 wipes this file exists to
    prevent. A filename is refused by ``reject_non_postgres_target`` before the
    filesystem is touched, so the suite fails LOUDLY on configuration instead
    of quietly succeeding against production.
    """
    return str(scratch / "cards.db")


def _point_env_at(scratch: Path, store_dsn: "str | None") -> None:
    """Aim every store-selecting variable at ``scratch`` / ``store_dsn``.

    ``store_dsn`` is a schema-scoped throwaway DSN, or ``None`` when no
    writable PostgreSQL could be opened at all. THE VARIABLE IS SET EITHER
    WAY -- see :func:`_refused_placeholder` for why the absent case is not
    allowed to mean "unset".
    """
    os.environ["SCITEX_CARDS_DB"] = store_dsn or _refused_placeholder(scratch)
    os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")
    # Same scratch tree, own subdir — no separate tempfile.mkdtemp() call
    # needed, and it means a test's own $SCITEX_DIR override (every one that
    # wants the tier-4 fallback sets this explicitly) still wins for the
    # duration of that test; this only supplies the default.
    os.environ["SCITEX_DIR"] = str(scratch / "scitex-dir-fallback")
    # The storage primitive's own variable. SET to the throwaway schema when
    # one could be opened; otherwise REMOVED — never left inherited. Removing
    # it is strictly safer than the value it replaces, because the value it
    # replaces is the live fleet board.
    if _EPHEMERAL_DSN is None:
        os.environ.pop(_STORE_DSN_ENV, None)
    else:
        os.environ[_STORE_DSN_ENV] = _EPHEMERAL_DSN


def _bootstrap_empty_store(store_dsn: str) -> None:
    """Install the schema into the EMPTY throwaway schema named by ``store_dsn``.

    The store is the database now, so a test that writes a card needs the
    tables present the way it used to need a ``tasks.yaml``. Pinning the
    variable was enough when the DB was a mirror that could be absent; against
    the real store an unprovisioned target is a hard, correct refusal
    ("canonical store ... does not exist"), and every write test would fail on
    configuration rather than on behaviour.

    THROUGH THE PACKAGE'S OWN DOORS, not hand-written DDL. ``connect`` +
    ``init_schema`` is what production runs, so a schema this builds is the
    schema that ships -- including the migration ladder and the version stamp.
    A fixture that issued its own ``CREATE TABLE`` would drift from the real
    shape silently, and every test would then be asserting against a store no
    user will ever have.

    Imported INSIDE the function on purpose: this module is imported before any
    test touches ``scitex_cards``, and importing the package at conftest import
    time would run ``_env_compat.mirror_env()`` before :func:`_pin_to_scratch`
    has aimed the variables — reading the developer's real environment instead
    of the scratch one.
    """
    from scitex_cards._db import connect, init_schema
    from scitex_cards._store_uuid import mint_store_uuid, stamp_store_uuid

    conn = connect(store_dsn)
    try:
        init_schema(conn)
        # AN IDENTITY, BECAUSE A REAL STORE HAS ONE. `init_schema` builds the
        # tables and stops; `schema_meta.store_uuid` is written separately, so
        # a store bootstrapped by tables alone answers "no store_uuid" -- which
        # is the shape of a server that has never held a board, not of the
        # provisioned store this fixture is standing in for.
        #
        # IT WAS COSTING COVERAGE, not merely realism. The four both-halves pin
        # tests in test__store_pin.py SKIP on exactly that condition ("names a
        # server with no store on it"), and a skipped test is indistinguishable
        # from a passing one -- the same silent-green this harness is otherwise
        # built to refuse. Minted per test, so the per-test isolation the schema
        # gives is matched by a per-test identity rather than a shared one.
        stamp_store_uuid(conn, mint_store_uuid())
        conn.commit()
    finally:
        conn.close()


# Executed at IMPORT of this conftest — before collection, therefore before any
# test module imports scitex_cards. A fixture would already be too late for the
# import-time env read in _env_compat.
_SCRATCH = _pin_to_scratch()


@pytest.fixture(scope="session")
def scratch_store_root() -> Path:
    """The throwaway store directory this run is pinned to (for assertions)."""
    return _SCRATCH


@pytest.fixture
def new_store():
    """Hand out ADDITIONAL throwaway stores, one per call. Returns a DSN string.

    THE REPLACEMENT FOR ``tmp_path / "cards.db"``. That spelling meant "a fresh
    empty store nobody else is using", and for a file store the temp directory
    supplied both halves at once. It cannot mean that any more: a filesystem
    path names no store and is refused at the door
    (``_store_url.reject_non_postgres_target``), so a test that still writes it
    is not testing a store -- it is testing the refusal.

    A FACTORY RATHER THAN A FIXTURE VALUE, because the tests that need this
    mostly need TWO. The store carries an identity and half this suite's
    subjects are about two stores disagreeing -- a peer's database, a byte
    copy, the store a stamp was claimed for versus the one it was opened as.
    One store per test would force those back into sharing, which is the
    collision the per-test pin removes rather than arbitrates.

    CLOSE EVERY CONNECTION YOU OPEN ON ONE OF THESE, AND CLOSE IT FROM A
    FIXTURE OR A ``finally`` -- NEVER ON THE LINE AFTER YOUR ASSERTION. The
    schema is removed with ``DROP SCHEMA ... CASCADE`` when the test ends, and
    that statement BLOCKS while any connection is still holding a transaction
    on it. So a test that opens a connection and then FAILS never reaches its
    own ``close()``, and the failure does not report red -- IT HANGS, taking
    the rest of the session with it, and a hang reads as a slow runner rather
    than as a failure.

    That is the single most expensive difference between this and the scratch
    FILE these replaced: a file store forgave a leaked handle completely.
    Measured twice on 2026-08-30 while converting ``test__schema_shape.py`` and
    ``test__store_retirement.py``, both times as an indefinite wedge with no
    output. The fix in both was the same and is the pattern to copy: a fixture
    that hands out stores AND owns the connections, unwinding them on the way
    out whatever the test did.

    ``bootstrap=True`` (the default) installs the schema through the package's
    own ``connect`` + ``init_schema``, exactly as ``_bootstrap_empty_store``
    does for the pinned per-test store, so a caller that just wants somewhere
    to write cards gets a working store.

    PASS ``bootstrap=False`` WHEN THE SUBJECT IS PROVISIONING ITSELF, and read
    this before deciding you do not need to. The pinned per-test store is
    already schema-complete, so a test that asserts "the verb created the
    table" passes against it WITH THE VERB REMOVED -- the assertion is true
    before the act runs, which makes it a check that cannot fail. An empty
    schema is the only arrangement under which that assertion measures
    anything, and the test should assert the FALSE -> TRUE transition across
    the act rather than the true-at-the-end state.

    Every schema is dropped ``CASCADE`` when the test ends.
    """
    with contextlib.ExitStack() as per_call:

        def make(prefix: str = "cards_extra", *, bootstrap: bool = True) -> str:
            if _CLUSTER_DSN is None:
                pytest.fail(
                    "This test needs a second throwaway store and no writable "
                    "PostgreSQL was opened, so there is nowhere to carve one. "
                    "This is a FAILURE and not a skip: a skipped storage test "
                    "is indistinguishable from a passing one.\n"
                    f"  reason: {_EPHEMERAL_DSN_REASON}",
                    pytrace=False,
                )
            from scitex_dev.store.testing import ephemeral_schema

            dsn = per_call.enter_context(
                ephemeral_schema(_CLUSTER_DSN, prefix=prefix)
            )
            if bootstrap:
                _bootstrap_empty_store(dsn)
            return dsn

        yield make


# --------------------------------------------------------------------------- #
# Belt-and-braces: the real store must not move AT ALL, session-wide.        #
# --------------------------------------------------------------------------- #
#
# Everything above this line makes it mechanically hard for a test to RESOLVE
# a real store path. It assumes that guard has a hole somewhere it hasn't been
# found yet — proven true on 2026-07-21 (2,170 cards -> 18; THIRD such wipe,
# two days after the 2026-07-19 fix above), so this layer checks the fact that
# actually matters, regardless of which env var or code path let a write
# through: is the real board still INTACT — same or more cards, same identity
# stamps, structurally sound? See :func:`_store_damage.damage` for why not
# "did the file change", is the criterion on a shared live board.
#
# Both real homes this fleet's agents run under. Checked BY NAME, not by
# reading $HOME/$SCITEX_DIR — the whole point is to catch a leak that reached
# the store via one of those variables, so asking the same variable "were you
# bypassed" would beg the question.
# This listed FOUR paths: these two, plus the same two under the pre-rename
# directory name, because that older location had held 2,117 real cards as
# recently as the 2026-07-16 rename and a leak could still have landed there.
# The rename swept the old dirname to the new one, which turned the extra two
# entries into duplicates of the first two — so the guard silently stopped
# covering the second location while its length still suggested it did.
#
# REMOVED RATHER THAN RE-POINTED, and checked before removing: the pre-rename
# directory still EXISTS on both homes (which are the same bind-mounted path)
# but is EMPTY — measured 2026-08-16, zero files, so the store file this
# guarded is gone. Nothing can recreate it either: the env tier and the compat
# mirror that could resolve to that dirname were deleted with the shim, so no
# code path in this package names it any more.




# Captured at IMPORT — same reasoning as ``_SCRATCH`` above: nothing this
# suite does can happen before this module finishes importing, so this is the
# earliest possible "before" snapshot.




@pytest.fixture(autouse=True)
def _store_env_stays_pinned(tmp_path_factory) -> "Iterator[None]":
    """Give every test its OWN empty database, and re-assert the pin.

    TWO JOBS, both load-bearing.

    (1) RE-ASSERT THE PIN. A test that deletes rather than overrides one of
    these (``monkeypatch.delenv``, or a stray ``os.environ.pop``) would
    silently hand the NEXT test the user-canonical default — the live board.
    Restoring it every test keeps the guarantee for the whole session rather
    than only for the first test.

    (2) A FRESH DATABASE PER TEST, which is new and is what the cutover
    requires. A single session-wide database cannot serve this suite: the store
    carries an identity, and a test that passes its own ``tmp_path`` store is
    refused by a database already stamped for a different one — correctly, since
    writing store A into store B's database replaces B's rows with A's. Sharing
    one database between tests would therefore either break them or force the
    ownership guard off, and the guard is the thing that stopped this suite
    rebuilding the fleet's production database three times on 2026-07-19.

    Per-test isolation removes the collision instead of arbitrating it, and it
    buys real isolation as a side effect: no test can observe another's rows.

    Still ``os.environ`` rather than ``monkeypatch``: the concurrency tests pass
    ``env=os.environ.copy()`` to real child processes, and those children must
    inherit this test's database. That inheritance is precisely how the first
    wipe happened, so it is not incidental.

    A SCHEMA, NOT A FILE. The per-test store used to be a scratch FILENAME;
    there is one storage engine now and a filename names no store, so the
    isolation is a uniquely named PostgreSQL schema carved out of the session
    cluster and dropped ``CASCADE`` when the test ends. The DSN carries
    ``options=-csearch_path=<schema>``, and that is the load-bearing part:
    ``public`` is off the path, so an unqualified read inside a test cannot
    resolve the live board's tables at all -- it fails to find the relation
    rather than quietly returning the fleet's cards. Isolation and the barrier
    are the same mechanism, which is why converting the pin could not be done
    by weakening it.
    """
    scratch = tmp_path_factory.mktemp("store")
    with contextlib.ExitStack() as per_test:
        store_dsn = None
        if _CLUSTER_DSN is not None:
            from scitex_dev.store.testing import ephemeral_schema

            store_dsn = per_test.enter_context(
                ephemeral_schema(_CLUSTER_DSN, prefix="cards_test")
            )
            _bootstrap_empty_store(store_dsn)
        _point_env_at(scratch, store_dsn)
        # Re-assert the CURRENCY gate suppression too (same "a stray pop/delenv
        # must not leak into the next test" reasoning as the store vars above).
        os.environ["SCITEX_DEV_CURRENCY_SEVERITY"] = "silent"
        yield


# EOF
