#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The freshness sweep, reproduced OFFLINE against a disposable fake store.

WHY THIS FILE EXISTS ALONGSIDE ``test__freshness_gc.py``
--------------------------------------------------------
That file's store-backed tests need ``$SCITEX_STORE_DSN`` and ERROR without it.
That is the right posture — a skipped gate and a passing one render identically
— but it has a consequence worth naming: on a compute node with no cluster, the
sweep's central claims are *unverified*, and that is exactly the node a reviewer
of this change is handed. This file drives the SAME primitive
(``_store_freshness_gc.freshness_gc``) through an in-process fake, so the
contract can be reproduced with no server, no DSN, and no write anywhere.

It also does one thing the store-backed suite structurally cannot: it can place
a WRITER between the sweep's SELECT and its UPDATE. A quiet store never has a
card leave the forgettable set inside that window, so no amount of seeding makes
that window reachable; a fake can open it on demand.

THIS FILE NEVER OPENS A STORE
-----------------------------
``freshness_gc`` is only ever called with ``conn=<the fake>``. The sweep's other
mode — no connection, so it opens the guarded connection itself — is deliberately
NOT exercised here, because reaching it means resolving a real store target. That
mode is covered by CI's postgres leg; this file must stay runnable on a node
where the store does not exist.

WHAT THE FAKE PROVES, AND WHAT IT DOES NOT
------------------------------------------
It proves the sweep's PROTOCOL and PARAMETER CONTRACT and models its SELECTION:

  * the lock is taken before the first read, and it is the sweep's own key;
  * the whole target set is flipped in ONE statement (no per-card write);
  * no DELETE, no INSERT, no per-card comment;
  * the readback runs AFTER the flip, from the same transaction;
  * the cutoff travels as a parameter and the row clock is cast on both sides,
    with the ``created_at`` fallback rather than an assumed ``last_activity``;
  * the predicate IT DEPENDS ON, evaluated by the same rule the SQL states;
  * a caller-supplied connection is neither committed nor closed by the sweep.

It does NOT prove PostgreSQL's SQL semantics — whether ``jsonb_set`` really
mirrors, whether ``::timestamptz`` really compares instants, whether ``ANY(?)``
really adapts to an array. Those are the CI postgres leg's job, against a real
server, and nothing here substitutes for it.

WHAT THE FAKE REFUSES
---------------------
It models the sweep's four statements and raises ``_UnmodelledStatement`` on
anything else. A statement whose WHERE clause it cannot classify also raises
rather than being guessed at, so a rewrite of the flip cannot silently stop
being covered by these tests.

RUNNING IT WITHOUT PYTEST
-------------------------
``python tests/scitex_cards/test__freshness_gc_offline.py`` runs every test in
this module and exits non-zero on failure — for a node whose interpreter has no
pytest and therefore cannot collect the rest of the suite.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from scitex_cards._store_freshness_gc import (
    FORGETTABLE_STATUSES,
    FRESHNESS_GC_LOCK_KEY,
    cutoff_from_days,
    freshness_gc,
)

#: Fixed instant for the deterministic clock tests — 2026-09-17T12:00:00Z, the
#: same pin the store-backed suite uses so both files describe one world.
_NOW = 1_789_646_400.0

#: The cutoff every sweep below runs with: after every "old" card, before
#: "fresh". Spelled with the ``Z`` suffix, the way the store's rows spell it.
_CUTOFF = "2026-06-01T00:00:00Z"

#: Two of everything that matters: old/forgettable (must go), old/terminal
#: (must stay), fresh (must stay), and one whose ``last_activity`` is EMPTY so
#: the ``created_at`` fallback is exercised rather than assumed. ``blocker`` is
#: present on the blocked card because clearing it is half of the flip.
_DOC: list[dict] = [
    {
        "id": "old-goal",
        "title": "An old goal",
        "status": "goal",
        "assignee": "agent:test-suite",
        "last_activity": "2026-01-01T00:00:00Z",
    },
    {
        "id": "old-blocked",
        "title": "An old blocked card",
        "status": "blocked",
        "blocker": "dependency",
        "assignee": "agent:test-suite",
        "last_activity": "2026-02-01T00:00:00Z",
    },
    {
        "id": "old-done",
        "title": "An old card that is DONE",
        "status": "done",
        "assignee": "agent:test-suite",
        "last_activity": "2026-01-01T00:00:00Z",
    },
    {
        "id": "fresh",
        "title": "Touched today",
        "status": "in_progress",
        "assignee": "agent:test-suite",
        "last_activity": "2026-09-17T00:30:00Z",
    },
    {
        "id": "no-last-activity",
        "title": "Only ever created",
        "status": "deferred",
        "assignee": "agent:test-suite",
        "last_activity": "",
        "created_at": "2026-03-01T00:00:00Z",
    },
]

#: The ids a sweep at ``_CUTOFF`` must forget, and the ones it must not.
_MUST_FORGET = ("old-goal", "old-blocked", "no-last-activity")
_MUST_KEEP = ("old-done", "fresh")


# ── the fake store ──────────────────────────────────────────────────────────


class _UnmodelledStatement(Exception):
    """The fake was handed a statement it does not model. NEVER swallowed."""


class _Cursor:
    """The slice of a driver cursor the sweep uses."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return list(self._rows)

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


def _instant(text: str) -> datetime | None:
    """The instant a stored clock spelling names, or ``None`` if it is empty.

    BOTH SPELLINGS ARE ACCEPTED ON PURPOSE. Rows carry ``2026-01-01T00:00:00Z``
    while a cutoff built by ``cutoff_from_days`` carries ``+00:00`` and
    microseconds. Comparing those SPELLINGS is right in the middle and wrong at
    every boundary, so this PARSES — which is what the SQL's ``::timestamptz``
    cast does, and the only reason the model below agrees with the engine.
    """
    # Anything that is not a clock spelling names no instant — an empty column,
    # or a statement that carried no cutoff because it never read the clock.
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        # A CLOCK THE FAKE CANNOT READ IS NOT A CLOCK IT WILL GUESS AT: the
        # model would otherwise compare nothing to a cutoff and quietly agree
        # with whatever the sweep did.
        raise _UnmodelledStatement(
            f"this fake cannot read {text!r} as an instant"
        ) from exc


def _row(card: dict) -> dict:
    """One ``tasks`` row: the indexed columns AND the verbatim payload together."""
    return {
        "id": card["id"],
        "status": card.get("status"),
        "blocker": card.get("blocker"),
        "last_activity": card.get("last_activity", ""),
        "created_at": card.get("created_at", ""),
        "card_json": json.dumps(card, sort_keys=True),
    }


class _FakeStore:
    """A disposable store: dicts, a statement log, and no way to reach a server.

    IT IS NOT A DATABASE AND DOES NOT PRETEND TO BE ONE. It holds rows, answers
    the four statements the sweep issues, and records what it was asked to do.
    Everything it can do is visible in this file, and deleting it deletes the
    store — which is the property that made it the right instrument for a verb
    whose purpose is to dispose of cards.
    """

    def __init__(
        self,
        cards: list[dict],
        *,
        on_select: Callable[[], None] | None = None,
    ) -> None:
        self.tasks = [_row(card) for card in cards]
        self.comments: list[str] = []
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0
        self.closed = False
        #: Fired once, after the SELECT has read: this is where a test puts a
        #: DIFFERENT writer's committed change into the sweep's window.
        self.on_select = on_select

    # --- the connection surface the sweep uses ---------------------------- #

    def execute(self, sql: str, params: tuple = ()) -> _Cursor:
        flat = " ".join(sql.split())
        self.statements.append((flat, tuple(params)))
        if flat.startswith("SELECT pg_advisory_xact_lock"):
            return _Cursor([])
        if flat.startswith("SELECT id FROM tasks WHERE"):
            selected = self._selected(params)
            if self.on_select is not None:
                self.on_select()
            return _Cursor([{"id": row["id"]} for row in selected])
        if flat.startswith("UPDATE tasks SET"):
            return self._flip(flat, params)
        if flat.startswith("SELECT count(*) AS n FROM tasks WHERE"):
            return _Cursor([{"n": len(self._selected(params))}])
        raise _UnmodelledStatement(
            "this fake models the four statements the sweep issues and nothing "
            f"else; it was handed: {flat!r}"
        )

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True

    # --- the model of the two clauses that select and flip ----------------- #

    def _selected(self, params: tuple) -> list[dict]:
        cutoff = _instant(params[-1])
        return [row for row in self.tasks if self._matches(row, cutoff)]

    def _matches(self, row: dict, cutoff: datetime | None) -> bool:
        """The predicate, evaluated by the rule the SQL states.

        ``status IN (<the forgettable set>)`` AND the row clock — ``COALESCE(
        NULLIF(last_activity, ''), created_at)`` — older than the cutoff.
        """
        if cutoff is None or row["status"] not in FORGETTABLE_STATUSES:
            return False
        clock = _instant(row["last_activity"]) or _instant(row["created_at"])
        return clock is not None and clock < cutoff

    def _flip(self, flat: str, params: tuple) -> _Cursor:
        ids = set(params[0])
        # THE SHAPE IS CLASSIFIED FIRST: a flip this fake cannot read must be
        # refused by name, not raise something unrelated while it is parsed.
        guarded = self._reapplies_the_predicate(flat)
        cutoff = _instant(params[-1])
        rows = [row for row in self.tasks if row["id"] in ids]
        # THE MODEL OF THE WHERE CLAUSE, stated so it cannot be mistaken for a
        # convenience: a GUARDED flip re-checks the predicate, so a row that
        # stopped matching is skipped; an UNGUARDED flip checks only the id
        # list, so every id the SELECT handed over is written whatever happened
        # to it since. The second is not a punishment — it is what such a
        # statement does.
        affected = [
            row for row in rows if not guarded or self._matches(row, cutoff)
        ]
        for row in affected:
            self._forget(row)
        if "RETURNING" in flat:
            return _Cursor([{"id": row["id"]} for row in affected])
        return _Cursor([])

    @staticmethod
    def _reapplies_the_predicate(flat: str) -> bool:
        """Whether the flip's WHERE clause re-checks the predicate.

        TWO SHAPES ARE MODELLED AND A HALF-GUARD IS REFUSED, so a rewrite of
        the statement fails loudly here instead of being covered by a model
        that no longer describes it.
        """
        reads_the_clock = "last_activity" in flat and "created_at" in flat
        casts_it = "timestamptz" in flat
        if "status IN" in flat:
            if not (reads_the_clock and casts_it):
                raise _UnmodelledStatement(
                    "the flip states a status guard but not the clock guard, "
                    f"which this fake cannot model: {flat!r}"
                )
            return True
        return False

    def _forget(self, row: dict) -> None:
        """Flip the COLUMN and the PAYLOAD together — never one of the two."""
        row["status"] = "cancelled"
        row["blocker"] = None
        payload = json.loads(row["card_json"])
        # Both keys already exist on every seeded card, and the SQL's
        # `jsonb_set` does not create a missing one — so neither does this.
        for key in ("status", "blocker"):
            if key in payload:
                payload[key] = "cancelled" if key == "status" else None
        row["card_json"] = json.dumps(payload, sort_keys=True)

    # --- what a test reads back -------------------------------------------- #

    def statuses(self) -> dict[str, str]:
        """The COLUMN a query filters on."""
        return {row["id"]: row["status"] for row in self.tasks}

    def cards(self) -> dict[str, dict]:
        """The PAYLOAD a reader reconstructs from."""
        return {row["id"]: json.loads(row["card_json"]) for row in self.tasks}

    def set_status(self, card_id: str, status: str) -> None:
        """A DIFFERENT writer's committed change, applied by a test.

        The column and the payload move together here too, because a real
        writer of this store keeps them consistent — and because a model that
        let them diverge would manufacture a disagreement the sweep is not
        responsible for.
        """
        for row in self.tasks:
            if row["id"] == card_id:
                row["status"] = status
                payload = json.loads(row["card_json"])
                payload["status"] = status
                row["card_json"] = json.dumps(payload, sort_keys=True)

    # --- the statement log -------------------------------------------------- #

    def statements_starting_with(self, prefix: str) -> list[tuple[str, tuple]]:
        return [entry for entry in self.statements if entry[0].startswith(prefix)]

    def index_of(self, prefix: str) -> int:
        return next(
            i for i, (sql, _) in enumerate(self.statements) if sql.startswith(prefix)
        )


def _seeded(**kwargs) -> _FakeStore:
    """A fresh fake holding ``_DOC`` — never shared between tests."""
    return _FakeStore([dict(card) for card in _DOC], **kwargs)


# ── the clock, deterministically ────────────────────────────────────────────


def test_the_cutoff_is_exactly_n_days_before_a_pinned_now():
    """The org-wide default is a NUMBER; this is where it becomes an instant."""
    # Arrange
    expected = "2026-08-18T12:00:00+00:00"
    # Act
    got = cutoff_from_days(30, now=_NOW)
    # Assert
    assert got == expected


def test_zero_days_means_the_pinned_now():
    """A boundary the sweep's own predicate turns on."""
    # Arrange
    expected = "2026-09-17T12:00:00+00:00"
    # Act
    got = cutoff_from_days(0, now=_NOW)
    # Assert
    assert got == expected


def test_a_negative_number_of_days_is_refused():
    """A negative horizon would sweep the FUTURE, which is never intended."""
    # Arrange
    refused = False
    # Act
    try:
        cutoff_from_days(-1, now=_NOW)
    except ValueError:
        refused = True
    # Assert
    assert refused


def test_every_forgettable_status_is_the_documented_set():
    """The set is an interface (scitex-dev passes a cutoff against it)."""
    # Arrange
    expected = ("goal", "in_progress", "blocked", "deferred")
    # Act
    observed = tuple(FORGETTABLE_STATUSES)
    # Assert
    assert observed == expected


# ── the sweep: what it takes, and what it leaves ─────────────────────────────


def test_the_sweep_forgets_exactly_the_stale_forgettable_cards():
    """Counts first: matched == cancelled == the cards that SHOULD go."""
    # Arrange
    store = _seeded()
    # Act
    result = freshness_gc(cutoff=_CUTOFF, conn=store)
    # Assert
    assert (result.matched, result.cancelled, result.remaining) == (3, 3, 0)


def test_the_sweep_leaves_terminal_and_fresh_cards_alone():
    """The half that matters more: what a bulk flip must NOT reach."""
    # Arrange
    store = _seeded()
    before = {card_id: store.statuses()[card_id] for card_id in _MUST_KEEP}
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    # Assert
    assert {card_id: store.statuses()[card_id] for card_id in _MUST_KEEP} == before


def test_a_forgotten_card_is_cancelled_and_its_blocker_is_cleared():
    """`blocked` is forgettable only because the flip clears the blocker."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    blocked = store.cards()["old-blocked"]
    # Assert — the payload AND the column, so neither half can flip alone.
    assert (blocked["status"], blocked["blocker"], store.statuses()["old-blocked"]) == (
        "cancelled",
        None,
        "cancelled",
    )


def test_the_payload_and_the_status_column_agree_after_a_flip():
    """A store that filters on the column and reads the payload cannot disagree."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    columns = store.statuses()
    payloads = store.cards()
    # Assert
    assert all(
        (columns[card_id], payloads[card_id]["status"]) == ("cancelled", "cancelled")
        for card_id in _MUST_FORGET
    )


def test_the_whole_target_set_is_flipped_in_one_statement():
    """Cost tracks MATCHES, not cards: one UPDATE, and no second write at all."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    writes = [
        entry
        for entry in store.statements
        if entry[0].startswith(("UPDATE", "INSERT", "DELETE"))
    ]
    # Assert — ONE write, it is the bulk UPDATE, and the sweep's whole
    # transaction is four statements: lock, select, that UPDATE, readback.
    assert (
        len(writes),
        [sql.split(" SET")[0] for sql in (entry[0] for entry in writes)],
        len(store.statements),
    ) == (1, ["UPDATE tasks"], 4)


def test_a_forgotten_card_is_still_a_row_with_its_history():
    """Forgetting removes work, not records — and adds no per-card comment."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    payloads = store.cards()
    # Assert — no comment was appended, and every forgotten card is STILL a row
    # that can be read back with its history.
    assert (
        store.comments,
        [
            card_id
            for card_id in _MUST_FORGET
            if not payloads.get(card_id, {}).get("title")
        ],
    ) == ([], [])


# ── the protocol: order, parameters, ownership ───────────────────────────────


def test_the_lock_is_taken_before_the_first_read():
    """Two sweeps must not both see the same rows; a late lock serialises nothing."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    flipped_after_selecting = store.index_of("UPDATE tasks SET") > store.index_of(
        "SELECT id FROM tasks"
    )
    # Assert — the FIRST statement is the lock, with the sweep's own key, and
    # the flip does not run before the read it is based on.
    assert (store.statements[0], flipped_after_selecting) == (
        ("SELECT pg_advisory_xact_lock(?)", (FRESHNESS_GC_LOCK_KEY,)),
        True,
    )


def test_the_cutoff_travels_as_a_parameter_and_the_clock_is_cast():
    """Both sides of the clock are cast, and the fallback is stated, not assumed."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    selections = [
        (sql, params)
        for sql, params in store.statements
        if sql.startswith(("SELECT id FROM tasks", "SELECT count(*) AS n FROM tasks"))
    ]
    # Assert — TWO statements read the clock; both cast it on both sides, both
    # name the created_at fallback rather than assuming last_activity, and both
    # carry the cutoff as their LAST parameter.
    assert (
        len(selections),
        all("::timestamptz" in sql for sql, _ in selections),
        all(
            "COALESCE(NULLIF(last_activity, ''), created_at)" in sql
            for sql, _ in selections
        ),
        [params for _, params in selections],
    ) == (2, True, True, [(*FORGETTABLE_STATUSES, _CUTOFF)] * 2)


def test_the_readback_runs_after_the_flip_and_comes_from_the_same_transaction():
    """`remaining` is the only statement that can prove the predicate emptied."""
    # Arrange
    store = _seeded()
    # Act
    result = freshness_gc(cutoff=_CUTOFF, conn=store)
    readback = store.index_of("SELECT count(*) AS n FROM tasks")
    # Assert — the readback is the LAST statement and comes after the flip; its
    # count is what the sweep reports; and the caller-owned connection is left
    # uncommitted, because the sweep does not manage a transaction it did not
    # open.
    assert (
        readback > store.index_of("UPDATE tasks SET"),
        readback == len(store.statements) - 1,
        result.remaining,
        store.commits,
    ) == (True, True, 0, 0)


def test_a_dry_run_reports_the_same_count_and_changes_nothing():
    """The rehearsal must be the real run's count, or it is a guess."""
    # Arrange
    store = _seeded()
    before = store.statuses()
    # Act
    result = freshness_gc(cutoff=_CUTOFF, conn=store, dry_run=True)
    # Assert — the same count as the real run, nothing written, and three
    # statements rather than four: lock, select, readback, no UPDATE.
    assert (
        result.matched,
        result.cancelled,
        store.statuses() == before,
        store.statements_starting_with("UPDATE tasks SET") == [],
        len(store.statements),
    ) == (3, 0, True, True, 3)


def test_a_second_sweep_is_a_no_op():
    """`remaining` is what makes the first sweep's claim checkable."""
    # Arrange
    store = _seeded()
    freshness_gc(cutoff=_CUTOFF, conn=store)
    # Act
    second = freshness_gc(cutoff=_CUTOFF, conn=store)
    # Assert — the second sweep selected nothing, wrote nothing, and did not
    # issue a second UPDATE.
    assert (
        second.matched,
        second.cancelled,
        second.remaining,
        len(store.statements_starting_with("UPDATE tasks SET")),
    ) == (0, 0, 0, 1)


def test_a_caller_owned_connection_is_not_committed_or_closed():
    """The sweep does not manage a transaction it did not open."""
    # Arrange
    store = _seeded()
    # Act
    freshness_gc(cutoff=_CUTOFF, conn=store)
    # Assert
    assert (store.commits, store.closed) == (0, False)


# ── the window a seeded store cannot open ───────────────────────────────────


def test_a_card_completed_between_the_select_and_the_flip_is_not_cancelled():
    """A writer that commits inside the sweep's window must not be overwritten.

    THE WINDOW IS REAL AND NOTHING ELSE CLOSES IT: the sweep's advisory lock is
    its own key, and the package's other locks (the file lock on the CRUD path,
    the store-write advisory lock the DM and store-uuid paths take) are not held
    against it. So the ONLY thing standing between a just-completed card and a
    flip to `cancelled` is the flip's own WHERE clause re-checking the predicate
    — and `done` is excluded from the forgettable set precisely because it is
    already terminal.
    """
    # Arrange
    store = _seeded(on_select=lambda: store.set_status("old-goal", "done"))
    # Act
    result = freshness_gc(cutoff=_CUTOFF, conn=store)
    # Assert — the column, the payload and all three counts AT ONCE. The
    # selection saw the card (matched=3), the flip did not touch it
    # (cancelled=2), it is still `done` in both places, and nothing is left.
    assert (
        store.statuses()["old-goal"],
        store.cards()["old-goal"]["status"],
        result.matched,
        result.cancelled,
        result.remaining,
    ) == ("done", "done", 3, 2, 0), (
        "a card completed inside the sweep's window was flipped: "
        f"column={store.statuses()['old-goal']!r} "
        f"payload={store.cards()['old-goal']['status']!r} "
        f"matched={result.matched} cancelled={result.cancelled} "
        f"remaining={result.remaining}"
    )


def test_the_flip_count_and_sample_name_the_cards_actually_flipped():
    """`cancelled` must not claim a card was forgotten that was left alone."""
    # Arrange
    store = _seeded(on_select=lambda: store.set_status("old-goal", "done"))
    # Act
    result = freshness_gc(cutoff=_CUTOFF, conn=store)
    # Assert — the sample names the two cards the flip WROTE, in store order,
    # and the completed card is neither named nor cancelled.
    assert (
        result.sample_ids,
        sorted(result.sample_ids),
        store.statuses()["old-goal"],
    ) == (
        ("old-blocked", "no-last-activity"),
        ["no-last-activity", "old-blocked"],
        "done",
    )


# ── the instrument itself ────────────────────────────────────────────────────


def test_the_fake_store_refuses_a_statement_it_does_not_model():
    """Without this, every test above could pass by modelling nothing at all."""
    # Arrange
    store = _seeded()
    refused = False
    # Act
    try:
        store.execute("SELECT * FROM cards", ())
    except _UnmodelledStatement:
        refused = True
    # Assert — refused, and the refusal left the store untouched.
    assert (refused, store.statuses()["old-goal"]) == (True, "goal")


def test_the_fake_store_refuses_a_half_guarded_flip():
    """A status guard without the clock guard is not a shape this fake models."""
    # Arrange
    store = _seeded()
    refused = False
    # Act
    try:
        store.execute(
            "UPDATE tasks SET status = 'cancelled' WHERE id = ANY(?) AND status IN (?)",
            (["old-goal"], "goal"),
        )
    except _UnmodelledStatement:
        refused = True
    # Assert
    assert refused


# ── running without pytest ───────────────────────────────────────────────────


def _main() -> int:
    """Run every test in this module and report it. NO PYTEST REQUIRED.

    The rest of this suite cannot be collected on a node whose interpreter has
    no pytest — but this file's subject is the sweep, not the harness, so it
    reports its own results rather than leaving the contract unexercised.
    """
    tests = [
        (name, fn)
        for name, fn in list(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed: list[str] = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- report it, never hide it
            failed.append(name)
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")
    print(
        f"\n{len(tests) - len(failed)} passed, {len(failed)} failed, "
        f"{len(tests)} collected"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())


# EOF
