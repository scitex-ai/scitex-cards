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

import os
import tempfile
from pathlib import Path

import pytest
from _store_damage import content_or_none, damaged_candidates

#: Every env name that can point the package at a store. All are pinned, so a
#: half-applied rename cannot leave one of them aimed at the live board.
_STORE_ENV_VARS = (
    "SCITEX_CARDS_DB",
    "SCITEX_TODO_DB",
    "SCITEX_CARDS_TASKS_YAML_SHARED",
    "SCITEX_TODO_TASKS_YAML_SHARED",
)

#: Env names that select WHICH BACKEND is canonical. These are CLEARED, not
#: pinned. Pinning the store path while inheriting the backend selector makes
#: the suite's behaviour depend on the developer's shell: a maintainer who has
#: exported SCITEX_CARDS_STORE_BACKEND=sqlite (as anyone working the cutover
#: does) flips every test into DB-canonical mode against a scratch DB that was
#: never created, and they all fail with "canonical store ... does not exist".
#: A test that WANTS canonical mode sets this itself; the default must be the
#: same everywhere.
_BACKEND_ENV_VARS = (
    "SCITEX_CARDS_STORE_BACKEND",
    "SCITEX_TODO_STORE_BACKEND",
    "SCITEX_CARDS_READ_BACKEND",
    "SCITEX_TODO_READ_BACKEND",
)

#: ``$SCITEX_DIR`` is the BASE DIRECTORY under ``resolve_db_path``'s tier-4
#: fallback (``scitex_config._ecosystem.local_state.user_path``), which reads
#: ``os.environ.get("SCITEX_DIR", str(Path.home() / ".scitex"))`` on EVERY
#: call — not just at import. It is pinned for the same reason the four vars
#: above are: a test that legitimately clears BOTH ``SCITEX_CARDS_DB`` and
#: ``SCITEX_TODO_DB`` to exercise that fallback (see
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
    _point_env_at(scratch)
    for name in _BACKEND_ENV_VARS:
        os.environ.pop(name, None)
    return scratch


def _point_env_at(scratch: Path) -> None:
    """Aim every store-selecting variable at ``scratch``."""
    os.environ["SCITEX_CARDS_DB"] = str(scratch / "cards.db")
    os.environ["SCITEX_TODO_DB"] = str(scratch / "cards.db")
    os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")
    os.environ["SCITEX_TODO_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")
    # Same scratch tree, own subdir — no separate tempfile.mkdtemp() call
    # needed, and it means a test's own $SCITEX_DIR override (every one that
    # wants the tier-4 fallback sets this explicitly) still wins for the
    # duration of that test; this only supplies the default.
    os.environ["SCITEX_DIR"] = str(scratch / "scitex-dir-fallback")


def _bootstrap_empty_db(db_path: Path) -> None:
    """Create an EMPTY, schema-complete database at ``db_path``.

    SQLite is the store now, so a test that writes a card needs a database the
    way it used to need a ``tasks.yaml``. Pinning the variable was enough when
    the DB was a mirror that could be absent; against the real store an absent
    file is a hard, correct refusal ("canonical store ... does not exist"), and
    every write test would fail on configuration rather than on behaviour.

    Imported INSIDE the function on purpose: this module is imported before any
    test touches ``scitex_cards``, and importing the package at conftest import
    time would run ``_env_compat.mirror_env()`` before :func:`_pin_to_scratch`
    has aimed the variables — reading the developer's real environment instead
    of the scratch one.
    """
    from scitex_cards._db import connect, init_schema

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        init_schema(conn)
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
_REAL_STORE_CANDIDATES: tuple[Path, ...] = (
    Path("/home/agent/.scitex/cards/cards.db"),
    Path("/home/ywatanabe/.scitex/cards/cards.db"),
    # Pre-rename dirname (package renamed scitex-todo -> scitex-cards,
    # 2026-07-16); this path held 2,117 real cards as recently as the rename
    # itself (see _env_compat.py's incident writeup) and may still exist.
    Path("/home/agent/.scitex/todo/cards.db"),
    Path("/home/ywatanabe/.scitex/todo/cards.db"),
)


def _stat_or_none(path: Path) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` for ``path``, or ``None`` when it doesn't exist.

    Never raises. Diagnostic context only — see :func:`_store_damage.damage`
    for why file stat is NOT the failure criterion.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


# Captured at IMPORT — same reasoning as ``_SCRATCH`` above: nothing this
# suite does can happen before this module finishes importing, so this is the
# earliest possible "before" snapshot.
_REAL_STORE_BEFORE: dict[Path, tuple[int, int] | None] = {
    p: _stat_or_none(p) for p in _REAL_STORE_CANDIDATES
}
_REAL_CONTENT_BEFORE: dict[Path, dict | None] = {
    p: content_or_none(p) for p in _REAL_STORE_CANDIDATES
}


@pytest.fixture(scope="session", autouse=True)
def _assert_real_store_untouched_by_session():
    """FAIL LOUD if any real store candidate was DAMAGED during this session.

    This is a DETECTOR, not a preventer — the prevention is the pinning above
    and in ``tests/scitex_cards/conftest.py``. If this fires, do not go
    hunting for the one leaking test as a condition of fixing the card in
    hand: per the incident runbook, report the failing state (which candidate
    path, and what changed) and treat it as a signal that the pinning
    fixtures need a wider audit — finding the exact leaking test is
    legitimate follow-up work, not a blocker on having this guard at all.
    """
    yield
    damaged = damaged_candidates(_REAL_CONTENT_BEFORE, _REAL_STORE_CANDIDATES)
    if not damaged:
        return
    details = "\n".join(
        f"  {path}\n    {why}\n"
        f"    stat before (mtime_ns, size) = {_REAL_STORE_BEFORE[path]}\n"
        f"    stat after  (mtime_ns, size) = {_stat_or_none(path)}"
        for path, why in damaged
    )
    pytest.fail(
        "REAL TASK STORE DAMAGED DURING THIS TEST SESSION.\n"
        "Every pinning fixture in this file and in "
        "tests/scitex_cards/conftest.py is supposed to make this "
        "impossible; one of them has a hole. Do NOT chase the individual "
        "leaking test as a condition of triage — report this failure "
        "verbatim; finding the exact leak is follow-up work.\n"
        f"{details}",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _store_env_stays_pinned(tmp_path_factory) -> None:
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
    """
    scratch = tmp_path_factory.mktemp("store")
    _point_env_at(scratch)
    _bootstrap_empty_db(scratch / "cards.db")
    # Re-assert the CURRENCY gate suppression too (same "a stray pop/delenv
    # must not leak into the next test" reasoning as the store vars above).
    os.environ["SCITEX_DEV_CURRENCY_SEVERITY"] = "silent"


# EOF
