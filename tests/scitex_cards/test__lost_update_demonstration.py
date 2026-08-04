#!/usr/bin/env python3
"""An executable demonstration of the P0 lost-update defect.

WHY THIS FILE EXISTS. cards-row-level-writes-with-revision-lock-20260730 asserts
that two concurrent writers to DIFFERENT cards lose one of the two updates,
because every CRUD verb reads the WHOLE store, modifies one card, and writes the
whole thing back. That claim has been argued in prose across a dozen card
comments. It has never been executed. Until it is, "we fixed it" cannot be
distinguished from "we changed something and the prose still sounds right".

So this is the acceptance test for that card, written BEFORE the fix, and it is
expected to FAIL on today's code. A test that passes now would be testing the
wrong thing.

=========================================================================
READ THIS BEFORE ADDING A TEST THAT TOUCHES THE STORE
=========================================================================
`_read_canonical_db_or_raise` and `write_doc_to_db` take NO path argument. They
resolve the store from the ambient environment (`resolve_db_path(None)`, i.e.
$SCITEX_CARDS_DB). So a test that forgets to redirect that variable does not
fail -- it reads and rewrites THE LIVE FLEET STORE, which currently holds ~2,900
cards written by ~90 agents.

That is not hypothetical. `write_doc_to_db`'s own docstring records it happening
TWICE on 2026-07-19: "the mirror path let a pytest fixture rebuild the live DB
(2,136 cards -> 21), and after that path was guarded, the canonical path --
unguarded -- let the same suite do it again, harder (2,138 -> 1)."

Hence `isolated_store` below asserts the redirect TOOK EFFECT before yielding,
by asking the production resolver where it would write. An assertion that runs
before any write is the only kind that helps here; discovering afterwards that
the environment was wrong is discovering it too late.
"""

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def isolated_store(tmp_path):
    """Point the ambient store at ``tmp_path`` and PROVE it before yielding.

    Not ``monkeypatch``: the code under test reads the real ``os.environ`` via
    its own resolver, so the real environment is the thing that must change.
    """
    from scitex_cards._db import init_schema, resolve_db_path

    db_path = tmp_path / "cards.db"
    saved = os.environ.get("SCITEX_CARDS_DB")
    os.environ["SCITEX_CARDS_DB"] = str(db_path)

    try:
        # THE GUARD. Ask production where it would write, and refuse to proceed
        # unless that is inside tmp_path. Without this the test body would
        # happily rewrite the live fleet store.
        resolved = Path(resolve_db_path(None)).resolve()
        if tmp_path.resolve() not in resolved.parents:
            raise AssertionError(
                f"REFUSING TO RUN: the store resolved to {resolved}, which is "
                f"outside {tmp_path}. A test that writes there would rewrite the "
                f"live fleet store, which is how 2,138 cards became 1 on "
                f"2026-07-19. Fix the redirect before running this test."
            )
        conn = sqlite3.connect(db_path)
        try:
            init_schema(conn)
        finally:
            conn.close()
        yield db_path
    finally:
        if saved is None:
            os.environ.pop("SCITEX_CARDS_DB", None)
        else:
            os.environ["SCITEX_CARDS_DB"] = saved


def _card(card_id: str, title: str) -> dict:
    return {"id": card_id, "title": title, "status": "deferred"}


def _titles(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, title FROM tasks").fetchall()
    finally:
        conn.close()
    return dict(rows)


# === the guard itself, tested before it is trusted =========================


def test_the_isolation_guard_points_at_the_temp_store(isolated_store):
    """If this fails, every other test in this file is writing somewhere real."""
    # Arrange
    from scitex_cards._db import resolve_db_path

    # Act
    resolved = Path(resolve_db_path(None)).resolve()

    # Assert
    assert resolved == isolated_store.resolve()


# === the demonstration ====================================================


@pytest.mark.xfail(
    strict=True,
    reason=(
        "THE P0 IS REAL AND UNFIXED. Demonstrated 2026-07-30: writer one's edit "
        "is reverted to 'A original'. strict=True on purpose -- when 1b/2b lands "
        "and this starts passing, pytest reports XPASS as a FAILURE, which forces "
        "whoever fixed it to delete this marker. An xfail that silently starts "
        "passing is how a fixed defect keeps being described as open. Card: "
        "cards-row-level-writes-with-revision-lock-20260730"
    ),
)
def test_two_writers_to_different_cards_must_not_lose_an_update(isolated_store):
    """THE P0, executed rather than argued. EXPECTED TO FAIL before the fix.

    Both writers read the whole store, each edits a DIFFERENT card, and both
    write the whole store back. Nothing about that is a conflict -- they touched
    disjoint rows -- so a correct store keeps both edits.

    Today the second write carries the second writer's ENTIRE VIEW, which still
    contains the first writer's card as it was BEFORE the edit. The incremental
    mirror compares hashes, sees that row differs, and writes the stale copy over
    the fresh one. The first writer's update is gone, with no error anywhere.
    """
    # Arrange
    from scitex_cards._store_backend import write_doc_to_db
    from scitex_cards._store_canonical_read import _read_canonical_db_or_raise

    seed = {"tasks": [_card("card-a", "A original"), _card("card-b", "B original")]}
    write_doc_to_db(seed, isolated_store)

    doc_writer_one = _read_canonical_db_or_raise()
    doc_writer_two = _read_canonical_db_or_raise()

    for task in doc_writer_one["tasks"]:
        if task["id"] == "card-a":
            task["title"] = "A EDITED BY WRITER ONE"
    for task in doc_writer_two["tasks"]:
        if task["id"] == "card-b":
            task["title"] = "B EDITED BY WRITER TWO"

    # Act
    write_doc_to_db(doc_writer_one, isolated_store)
    write_doc_to_db(doc_writer_two, isolated_store)

    # Assert
    stored = _titles(isolated_store)
    assert stored["card-a"] == "A EDITED BY WRITER ONE", (
        "LOST UPDATE: writer two's whole-document write reverted card-a to the "
        f"copy it had read before writer one edited it. Stored: {stored!r}. "
        "This is the P0 on cards-row-level-writes-with-revision-lock-20260730."
    )


def test_the_second_writers_own_edit_does_survive(isolated_store):
    """The control. Without this, a failure above could mean 'writes are broken'.

    Writer two's edit landing proves the write path works and that the defect is
    specifically the LAST writer overwriting the other's row -- not writes
    failing in general.
    """
    # Arrange
    from scitex_cards._store_backend import write_doc_to_db
    from scitex_cards._store_canonical_read import _read_canonical_db_or_raise

    seed = {"tasks": [_card("card-a", "A original"), _card("card-b", "B original")]}
    write_doc_to_db(seed, isolated_store)

    doc_writer_one = _read_canonical_db_or_raise()
    doc_writer_two = _read_canonical_db_or_raise()
    for task in doc_writer_one["tasks"]:
        if task["id"] == "card-a":
            task["title"] = "A EDITED BY WRITER ONE"
    for task in doc_writer_two["tasks"]:
        if task["id"] == "card-b":
            task["title"] = "B EDITED BY WRITER TWO"

    # Act
    write_doc_to_db(doc_writer_one, isolated_store)
    write_doc_to_db(doc_writer_two, isolated_store)

    # Assert
    assert _titles(isolated_store)["card-b"] == "B EDITED BY WRITER TWO"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# EOF
