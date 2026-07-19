#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 READ PATH — the two backends must be INDISTINGUISHABLE, or it does not ship.

WHAT THIS FILE IS FOR
---------------------
``list_tasks`` costs ~830 ms on the live 1,452-card / 5.8 MB store, and a FILTERED
call costs the same (~730 ms for 152 of 1,452 cards) because the whole YAML document
is parsed and only THEN filtered in Python. The cost is the PARSE, not the query. The
SQLite mirror already indexes every field ``list_tasks`` filters on, so the fix is an
indexed lookup instead of a 5.8 MB parse.

But a read backend that serves subtly-different cards is FAR worse than a slow one:
slow is visible, wrong is not, and the fleet reads this store to decide what to work
on. So the bar for shipping is not "fast" and not "plausible" — it is IDENTICAL:
same cards, same order, same fields, same values, byte-for-byte through JSON.

These tests are that proof. :func:`assert_identical` compares the two backends' full
result sets — including key ORDER, which plain ``==`` on dicts would silently forgive
— across a matrix of filter combinations, with the tricky ones (``blocker="__none"``,
``blocking_me``, ``overdue`` incl. a recurring deadline, ``statuses`` + ``status``,
``scope=""`` vs ``scope=None``, ``id_prefix`` with an underscore) called out by name.

THE FIXTURE IS ADVERSARIAL ON PURPOSE
-------------------------------------
It is built from what the LIVE store actually contains, not from what the schema
wishes it contained (measured 2026-07-13):

* **Unknown fields.** 22 card keys in the live store map to NO column —
  ``deferred_at`` (20 cards), ``subagent`` (8), ``blocked_by`` (3),
  ``completed_at``, ``tasks_path``, and a family of ad-hoc ``note_*`` fields agents
  invent as they work. A column-based rebuild drops every one of them SILENTLY. The
  fixture carries them, so that bug cannot pass.
* **Key order.** 711 distinct key orders exist across the live cards. The fixture
  cards deliberately disagree about field order.
* **An underscore in an id.** ``_`` is a single-char WILDCARD in SQL ``LIKE``, so a
  ``LIKE 'note_%'`` prefix filter would also match ``note-x``. The fixture has both.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
import os
from pathlib import Path

import pytest

from scitex_cards import _store, _store_read_sqlite
from scitex_cards._db import ENV_DB
from scitex_cards._db_bootstrap import import_from_yaml
from scitex_cards._store_read_sqlite import BACKEND_SQLITE, ENV_READ_BACKEND
from scitex_cards._yaml import safe_dump

_YESTERDAY = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
_TOMORROW = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()


def _cards() -> list[dict]:
    """Cards shaped like the live store's — including the parts no column maps.

    Every value here is one the VALIDATOR accepts, because the YAML backend
    re-validates on read: a fixture the YAML path cannot even load proves nothing.
    (Checked, not assumed — ``blocker=""`` and ``kind=""`` are REJECTED by
    ``_validate``, so they cannot occur in a real store and are not modelled. The
    SQL still folds them, defensively, but no test can reach that fold.)
    """
    return [
        # key order A; a blocker; a scope; comments
        {
            "id": "alpha-1",
            "title": "alpha one",
            "status": "blocked",
            "blocker": "operator-decision",
            "assignee": "agent-a",
            "agent": "agent-a",
            "scope": "agent:agent-a",
            "project": "proj-x",
            "host": "hostA",
            "repo": "own/repo-x",
            "priority": 2,
            "comments": [
                {"author": "agent-a", "ts": "2026-07-01T00:00:00Z", "text": "hi"}
            ],
        },
        # key order B (status FIRST, id second); NO blocker key -> blocker="__none"
        {
            "status": "in_progress",
            "id": "alpha-2",
            "assignee": "agent-b",
            "title": "alpha two",
            "kind": "compute",
            "agent": "agent-b",
            "scope": "agent:agent-b",
            # UNKNOWN FIELDS: no column maps these. A column-based rebuild loses
            # them, silently. All four are real shapes from the live store.
            "deferred_at": "2026-07-02T00:00:00Z",
            "subagent": "sub-7",
            "note_DONE": "the ad-hoc note fields agents really invent",
            "blocked_by": "someone",
        },
        # blocker="none" — a VALID enum member, and TRUTHY. It must NOT match
        # `__none` (which means "no blocker named"). A `blocker IS NULL OR
        # blocker='none'` clause would wrongly swallow this card.
        {
            "id": "beta_1",
            "title": "beta one (underscore id)",
            "status": "blocked",
            "blocker": "none",
            "assignee": "agent-a",
            "kind": "task",  # explicit "task"
            "_log_meta": {"completed_by": "agent-a"},
        },
        # id that LIKE 'beta_%' would wrongly match but startswith('beta_') must not
        {
            "id": "beta-2",
            "title": "beta two (hyphen id)",
            "status": "done",
            "assignee": "agent-b",
            # kind ABSENT -> must behave as "task" (ADR-0002)
        },
        # OVERDUE: a bare past deadline, non-terminal status
        {
            "id": "gamma-1",
            "title": "overdue for real",
            "status": "in_progress",
            "assignee": "agent-a",
            "deadline": _YESTERDAY,
        },
        # NOT overdue: past deadline but a RECURRING repeater rolls it forward
        {
            "id": "gamma-2",
            "title": "recurring is never overdue",
            "status": "in_progress",
            "assignee": "agent-a",
            "deadline": f"{_YESTERDAY} +1w",
        },
        # NOT overdue: past deadline but TERMINAL status
        {
            "id": "gamma-3",
            "title": "past deadline but done",
            "status": "done",
            "assignee": "agent-a",
            "deadline": _YESTERDAY,
        },
        # NOT overdue: future deadlines (the multi form)
        {
            "id": "gamma-4",
            "title": "future deadline",
            "status": "goal",
            "assignee": "agent-b",
            "deadlines": [_TOMORROW, f"{_TOMORROW} +1m"],
        },
        # a plain card with no kind at all, to round out the kind="task" set
        {
            "id": "delta-1",
            "title": "absent kind folds to task",
            "status": "goal",
            "assignee": "agent-a",
        },
    ]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A YAML store + a mirror imported from it, with the caches cleared."""
    monkeypatch.setenv(ENV_DB, str(tmp_path / "todo.db"))
    monkeypatch.delenv("SCITEX_TODO_SCOPE", raising=False)
    path = tmp_path / "tasks.yaml"
    path.write_text(safe_dump({"tasks": _cards()}))
    import_from_yaml(path, tmp_path / "todo.db")
    _store_read_sqlite.reset_cache()
    return path


def _fingerprint(rows: list[dict]) -> str:
    """A digest of the FULL result set — order, fields, values, and KEY ORDER.

    ``json.dumps`` without ``sort_keys`` is the point: two dicts with the same
    content but different key order are ``==`` in Python, so a plain equality check
    would forgive a backend that re-orders fields — and everything that serializes a
    card (the CLI's JSON output, an API response) would change shape underneath us.
    This digest does not forgive it.
    """
    return json.dumps(rows, ensure_ascii=False)


def _both(store_path, **kw) -> tuple[list[dict], list[dict]]:
    os.environ.pop(ENV_READ_BACKEND, None)
    _store_read_sqlite.reset_cache()
    yaml_rows = _store.list_tasks(store_path, **kw)

    os.environ[ENV_READ_BACKEND] = BACKEND_SQLITE
    _store_read_sqlite.reset_cache()
    try:
        # The guard must actually be SATISFIED — otherwise this whole file would
        # silently compare the YAML backend against itself and prove nothing.
        assert _store_read_sqlite.enabled(store_path), "guard refused a healthy mirror"
        sqlite_rows = _store.list_tasks(store_path, **kw)
    finally:
        os.environ.pop(ENV_READ_BACKEND, None)
        _store_read_sqlite.reset_cache()
    return yaml_rows, sqlite_rows


def _divergence(kw, yaml_rows, sqlite_rows) -> str:
    return (
        f"BACKENDS DIVERGED for {kw!r}\n"
        f"  yaml  : {[r.get('id') for r in yaml_rows]}\n"
        f"  sqlite: {[r.get('id') for r in sqlite_rows]}"
    )


def assert_identical(store_path, **kw) -> list[dict]:
    yaml_rows, sqlite_rows = _both(store_path, **kw)
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), _divergence(
        kw, yaml_rows, sqlite_rows
    )
    return yaml_rows


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------
_SINGLE_FILTERS: list[dict] = [
    {},
    {"assignee": "agent-a"},
    {"assignee": "nobody"},
    {"agent": "agent-b"},
    {"status": "blocked"},
    {"status": "done"},
    {"statuses": ["done", "goal"]},
    {"status": "blocked", "statuses": ["done"]},  # OR-combined, not AND
    {"project": "proj-x"},
    {"host": "hostA"},
    {"repo": "own/repo-x"},
    {"scope": "agent:agent-a"},
    {"scope": ""},
    {"blocker": "operator-decision"},
    {"blocker": "none"},  # the enum member — NOT the same as "__none"
    {"blocker": "__none"},
    {"kind": "task"},
    {"kind": "compute"},
    {"id_prefix": "alpha"},
    {"id_prefix": "beta_"},
    {"id_prefix": "beta"},
    {"blocking_me": True},
    {"overdue": True},
]


@pytest.mark.parametrize("kw", _SINGLE_FILTERS, ids=lambda k: str(sorted(k.items())))
def test_each_filter_alone_is_identical(store, kw):
    # Arrange
    filters = dict(kw)
    # Act
    yaml_rows, sqlite_rows = _both(store, **filters)
    # Assert — same cards, same order, same fields, same KEY order.
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), _divergence(
        filters, yaml_rows, sqlite_rows
    )


_PAIRS = list(
    itertools.combinations(
        [
            {"assignee": "agent-a"},
            {"status": "blocked"},
            {"kind": "task"},
            {"blocker": "__none"},
            {"id_prefix": "alpha"},
            {"scope": "agent:agent-a"},
            {"overdue": True},
            {"blocking_me": True},
        ],
        2,
    )
)


@pytest.mark.parametrize("a,b", _PAIRS, ids=lambda k: str(sorted(k.items())))
def test_filter_combinations_are_identical(store, a, b):
    # Arrange
    filters = {**a, **b}
    # Act
    yaml_rows, sqlite_rows = _both(store, **filters)
    # Assert — composing filters must not diverge where each alone agreed.
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), _divergence(
        filters, yaml_rows, sqlite_rows
    )


# --------------------------------------------------------------------------
# The specific traps — each one is a way the SQLite path could be quietly wrong
# --------------------------------------------------------------------------
#: The 22 live keys no column maps. A column-based rebuild drops them SILENTLY,
#: so each is pinned by name rather than in one compound assertion — "an unknown
#: field was lost" is only actionable if it says WHICH, and the four here are
#: four different live shapes (a timestamp, an id, an ad-hoc note, a relation).
def _alpha_2(store) -> dict:
    return assert_identical(store, id_prefix="alpha-2")[0]


def test_unknown_deferred_at_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["deferred_at"]
    # Assert
    assert value == "2026-07-02T00:00:00Z"


def test_unknown_subagent_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["subagent"]
    # Assert
    assert value == "sub-7"


def test_ad_hoc_note_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["note_DONE"]
    # Assert — agents invent these as they work; the mirror must not eat them.
    assert value == "the ad-hoc note fields agents really invent"


def test_unknown_blocked_by_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["blocked_by"]
    # Assert
    assert value == "someone"


def test_key_order_survives_the_round_trip(store):
    """Card 'alpha-2' declares `status` BEFORE `id`. 711 such orders exist live."""
    # Arrange
    yaml_rows, sqlite_rows = _both(store, id_prefix="alpha-2")
    # Act
    orders = (list(sqlite_rows[0].keys()), list(yaml_rows[0].keys()))
    # Assert — plain `==` on dicts would forgive a re-ordering; this does not.
    assert orders[0] == orders[1]


def test_the_fixture_really_declares_status_before_id(store):
    # Arrange
    _yaml_rows, sqlite_rows = _both(store, id_prefix="alpha-2")
    # Act
    first_key = list(sqlite_rows[0].keys())[0]
    # Assert — without this the key-order test above could pass on a tame card.
    assert first_key == "status"


def test_document_order_is_preserved(store):
    """`row_order` exists for exactly this. Not sorted by id, not by insertion."""
    # Arrange
    expected_ids = [c["id"] for c in _cards()]
    # Act
    rows = assert_identical(store)
    # Assert
    assert [r["id"] for r in rows] == expected_ids


#: `__none` means "no blocker NAMED". `none` is a real, TRUTHY enum member.
#: `_match` tests FALSINESS (`if task.get("blocker")`), so only an absent (or
#: empty) blocker matches `__none` — a card explicitly blocked on `none` does
#: not. A naive `blocker IS NULL OR blocker = 'none'` clause would swallow it,
#: which is why the membership of BOTH filters is pinned card by card below.
def _blocker_none_token_ids(store) -> set:
    return {r["id"] for r in assert_identical(store, blocker="__none")}


def test_a_card_with_no_blocker_key_matches_the_none_token(store):
    # Arrange
    ids = _blocker_none_token_ids(store)
    # Act
    matched = "alpha-2" in ids
    # Assert
    assert matched, "a card with NO blocker key must match __none"


def test_blocker___none__is_not_the_enum_none(store):
    # Arrange
    ids = _blocker_none_token_ids(store)
    # Act
    matched = "beta_1" in ids
    # Assert
    assert not matched, "blocker='none' is TRUTHY — must NOT match __none"


def test_a_named_blocker_does_not_match_the_none_token(store):
    # Arrange
    ids = _blocker_none_token_ids(store)
    # Act
    matched = "alpha-1" in ids
    # Assert
    assert not matched


def test_filtering_on_the_none_enum_member_returns_only_that_card(store):
    # Arrange
    expected_ids = ["beta_1"]
    # Act
    rows = assert_identical(store, blocker="none")
    # Assert — `none` is a filterable value in its own right.
    assert [r["id"] for r in rows] == expected_ids


def test_id_prefix_underscore_is_not_a_sql_wildcard(store):
    """`LIKE 'beta_%'` would ALSO match 'beta-2'. `startswith` must not."""
    # Arrange
    expected_ids = ["beta_1"]
    # Act
    rows = assert_identical(store, id_prefix="beta_")
    # Assert
    assert [r["id"] for r in rows] == expected_ids


#: absent ≡ "task" (ADR-0002), so a kind="task" filter must return the explicit
#: and the absent alike — and nothing else. Three tests, because a filter that
#: is too narrow and one that is too wide are opposite bugs.
def _kind_task_ids(store) -> set:
    return {r["id"] for r in assert_identical(store, kind="task")}


def test_explicit_kind_task_matches_the_task_filter(store):
    # Arrange
    ids = _kind_task_ids(store)
    # Act
    matched = "beta_1" in ids
    # Assert
    assert matched, "explicit kind='task'"


def test_kind_task_matches_absent_kinds_too(store):
    # Arrange
    ids = _kind_task_ids(store)
    # Act
    absent_kind_ids = {"beta-2", "delta-1"}
    # Assert
    assert absent_kind_ids <= ids, "absent kind must fold to 'task'"


def test_a_compute_card_is_excluded_from_the_task_filter(store):
    # Arrange
    ids = _kind_task_ids(store)
    # Act
    matched = "alpha-2" in ids
    # Assert — folding absent to "task" must not fold everything to "task".
    assert not matched


def test_overdue_excludes_recurring_and_terminal(store):
    """A RECURRING deadline is NEVER overdue; a terminal card never is either."""
    # Arrange
    expected_ids = ["gamma-1"]
    # Act
    rows = assert_identical(store, overdue=True)
    # Assert
    assert [r["id"] for r in rows] == expected_ids


def test_scope_none_honours_the_env_default(store, monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_TODO_SCOPE", "agent:agent-b")
    # Act
    rows = assert_identical(store)  # scope=None -> env applies
    # Assert — both backends read the same env default.
    assert {r["id"] for r in rows} == {"alpha-2"}


def test_an_explicit_empty_scope_opts_out_of_the_env_default(store, monkeypatch):
    # Arrange
    monkeypatch.setenv("SCITEX_TODO_SCOPE", "agent:agent-b")
    # Act
    rows = assert_identical(store, scope="")  # explicit opt-out
    # Assert — `scope=""` is not the same as unset, on either backend.
    assert len(rows) == len(_cards())


# --------------------------------------------------------------------------
# The guard — the flag alone must never be enough
# --------------------------------------------------------------------------
#: Every refusal below is asserted twice: the guard SAID no, and the caller
#: still got correct cards from YAML. They are split because a guard that
#: refuses and then serves nothing is not fail-safe, it is just broken — and a
#: single test that stopped at `enabled() is False` would never notice.


def test_backend_is_off_by_default(store, monkeypatch):
    # Arrange
    monkeypatch.delenv(ENV_READ_BACKEND, raising=False)
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert — the flag must be opted INTO, never inherited.
    assert on is False


def _store_with_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.setenv(ENV_DB, str(tmp_path / "absent.db"))
    _store_read_sqlite.reset_cache()
    path = tmp_path / "tasks.yaml"
    path.write_text(safe_dump({"tasks": _cards()}))
    return path


def test_guard_refuses_a_missing_db(tmp_path, monkeypatch):
    # Arrange
    path = _store_with_missing_db(tmp_path, monkeypatch)
    # Act
    on = _store_read_sqlite.enabled(path)
    # Assert
    assert on is False


def test_a_missing_db_still_serves_correct_cards(tmp_path, monkeypatch):
    # Arrange
    path = _store_with_missing_db(tmp_path, monkeypatch)
    # Act
    rows = _store.list_tasks(path)
    # Assert — the caller falls back to YAML, not to an empty list.
    assert len(rows) == len(_cards())


#: THE failure this whole guard exists for. The YAML is canonical and ANYTHING
#: may write it — an agent on an older build, a process with the dual-write
#: mirror off (the default!), a hand-edit. None of those touch the mirror. The
#: DB then stays perfectly well-formed and quietly WRONG. Three tests: the guard
#: accepts a FRESH mirror (so the refusal below is not just a guard that always
#: says no), refuses the stale one, and the caller still reads every card.
def _stale_mirror(store):
    cards = _cards() + [
        {"id": "ghost-1", "title": "written without mirroring", "status": "goal"}
    ]
    store.write_text(safe_dump({"tasks": cards}))
    _store_read_sqlite.reset_cache()
    return cards


def test_the_guard_accepts_a_fresh_mirror(store, monkeypatch):
    # Arrange
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    _store_read_sqlite.reset_cache()
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert — without this, the refusals below prove nothing.
    assert on is True


def test_guard_refuses_a_STALE_db(store, monkeypatch):
    # Arrange
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    _store_read_sqlite.reset_cache()
    _stale_mirror(store)
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert
    assert on is False, "a stale mirror must be REFUSED"


def test_a_stale_db_still_serves_correct_cards(store, monkeypatch):
    # Arrange
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    _store_read_sqlite.reset_cache()
    cards = _stale_mirror(store)
    # Act
    rows = _store.list_tasks(store)
    # Assert — including the card the mirror never saw.
    assert [r["id"] for r in rows] == [c["id"] for c in cards], "must fall back to YAML"


#: A v1 DB: right schema, right indexes, `quick_check ok` — and NO payloads.
#: Reconstructing cards from the typed columns alone would drop every field the
#: schema does not name. Refuse, do not improvise.
def _mirror_without_payloads(monkeypatch, tmp_path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "todo.db"))
    try:
        conn.execute("UPDATE tasks SET card_json = NULL WHERE id = 'alpha-2'")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    _store_read_sqlite.reset_cache()


def test_guard_refuses_a_db_with_no_payload_column(store, monkeypatch, tmp_path):
    # Arrange
    _mirror_without_payloads(monkeypatch, tmp_path)
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert
    assert on is False


def test_a_db_with_no_payload_column_still_serves_cards(store, monkeypatch, tmp_path):
    # Arrange
    _mirror_without_payloads(monkeypatch, tmp_path)
    # Act
    rows = _store.list_tasks(store)
    # Assert — refusing to improvise must not mean refusing to answer.
    assert len(rows) == len(_cards())


#: The 135-second lesson: a flag whose safety depends on a code version must
#: VERIFY THAT CODE AT RUNTIME — by SYMBOL, never by version string. Simulates
#: an older build whose mirror writer has no payload column.
def _code_that_cannot_write_payloads(monkeypatch):
    from scitex_cards import _db_bootstrap

    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.setattr(
        _db_bootstrap,
        "TASK_INSERT_COLS",
        tuple(c for c in _db_bootstrap.TASK_INSERT_COLS if c != "card_json"),
    )
    _store_read_sqlite.reset_cache()


def test_guard_refuses_when_the_code_cannot_write_payloads(store, monkeypatch):
    # Arrange
    _code_that_cannot_write_payloads(monkeypatch)
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert — the symbol, not a version string, is what is checked.
    assert on is False


def test_an_older_build_still_serves_correct_cards(store, monkeypatch):
    # Arrange
    _code_that_cannot_write_payloads(monkeypatch)
    # Act
    rows = _store.list_tasks(store)
    # Assert
    assert len(rows) == len(_cards())


#: A duplicate card id means the mirror holds FEWER rows than the doc.
#:
#: And here is the sharp part, which only showed up once this was actually run:
#: the YAML path RAISES on a duplicate id (the validator refuses the store
#: outright), while the mirror silently collapses the two rows into one. So
#: without the card-count guard, switching the backend on would have converted a
#: LOUD, correct failure into a QUIET, wrong answer — the store would simply have
#: come back one card short, forever, and every equality check on the cards that
#: ARE present would have passed.
#:
#: A backend swap must not change WHICH failures are visible. Three tests: the
#: mirror really IS lossy (the premise), the guard refuses it, and the caller
#: still gets the loud failure instead of a quietly short list.
def _lossy_mirror(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DB, str(tmp_path / "todo.db"))
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    path = tmp_path / "tasks.yaml"
    path.write_text(
        safe_dump(
            {
                "tasks": [
                    {"id": "dupe", "title": "first", "status": "goal"},
                    {"id": "dupe", "title": "second", "status": "done"},
                ]
            }
        )
    )
    import_from_yaml(path, tmp_path / "todo.db")
    _store_read_sqlite.reset_cache()
    return path


def _mirror_row_count(tmp_path) -> int:
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "todo.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()


def test_a_duplicate_id_really_collapses_in_the_mirror(tmp_path, monkeypatch):
    # Arrange
    _lossy_mirror(tmp_path, monkeypatch)
    # Act
    rows = _mirror_row_count(tmp_path)
    # Assert — 2 cards in, 1 row out: the premise the guard has to catch.
    assert rows == 1


def test_a_lossy_mirror_is_refused(tmp_path, monkeypatch):
    # Arrange
    path = _lossy_mirror(tmp_path, monkeypatch)
    # Act
    on = _store_read_sqlite.enabled(path)
    # Assert
    assert on is False, "a lossy mirror must be REFUSED"


def test_a_lossy_mirror_still_raises_the_yaml_loud_failure(tmp_path, monkeypatch):
    # Arrange
    from scitex_cards._task import TaskValidationError

    path = _lossy_mirror(tmp_path, monkeypatch)
    # Act
    loud = pytest.raises(TaskValidationError, match="duplicate task id")
    # Assert — a backend swap must not turn a raise into a short list.
    with loud:
        _store.list_tasks(path)


#: A mirror stamped via a RELATIVE path must still be recognised via an absolute
#: one — the two spellings are ONE store.
#:
#: Found by the benchmark, not by me: `db import ./tasks.yaml` stamped a
#: relative `yaml_path`, and the reader (resolving absolutely) then declared
#: "the DB mirrors a DIFFERENT store" and refused a perfectly good mirror. It
#: failed SAFE — fell back to YAML, correct but slow — which is exactly why it
#: would have been easy never to notice. Paths must be compared CANONICALLY,
#: which means BOTH spellings need checking, hence two tests.
def _relative_stamped_mirror(store, monkeypatch):
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.chdir(store.parent)
    import_from_yaml(Path("tasks.yaml"), store.parent / "todo.db")  # RELATIVE stamp
    _store_read_sqlite.reset_cache()


def test_a_relative_store_path_is_still_the_same_store(store, monkeypatch):
    # Arrange
    _relative_stamped_mirror(store, monkeypatch)
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert
    assert on is True, "absolute read of a relative stamp"


def test_a_relative_read_of_a_relative_stamp_is_accepted(store, monkeypatch):
    # Arrange
    _relative_stamped_mirror(store, monkeypatch)
    # Act
    on = _store_read_sqlite.enabled(Path("tasks.yaml"))
    # Assert
    assert on is True, "relative read"


def test_refusal_is_logged_once_not_per_call(store, monkeypatch, caplog):
    """`list_tasks` runs on every poll of every agent. An ERROR per call is noise,
    and noise that fires constantly trains its reader to ignore the channel."""
    # Arrange
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.setenv(ENV_DB, str(store.parent / "absent.db"))
    _store_read_sqlite.reset_cache()
    # Act
    with caplog.at_level("ERROR", logger="scitex_cards._store_read_sqlite"):
        for _ in range(5):
            _store.list_tasks(store)
    # Assert
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, f"expected ONE refusal log, got {len(errors)}"


# EOF
