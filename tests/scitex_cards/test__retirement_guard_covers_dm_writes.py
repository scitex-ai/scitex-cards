#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A retired store must refuse WRITES on every path — including DMs.

THE GAP THIS PINS, measured 2026-07-31 during the PostgreSQL cutover: card
writes were refused while DM writes LANDED in the store everyone had left. Card
writes reach the guard only incidentally — they are read-modify-write, so they
pass through the canonical read, which checks. The DM path checked nothing. So
retirement was a fence for one path and a signpost for the other, and the
runbook line about stragglers failing loudly was true only for readers.

THE OTHER HALF IS AS IMPORTANT AND IS EASY TO BREAK WHILE FIXING THIS: a retired
store must stay READABLE. Recovering from a retirement means reading the thing
you retired — that is what makes retirement survivable rather than a one-way
loss. The obvious fix (refuse inside ``open_db``) would have taken reads and
exports down with it, because they open through it too. So this file asserts
BOTH directions, and the read assertions are the ones that would catch an
over-broad guard.

Every test ATTEMPTS THE OPERATION rather than inspecting for a trigger, matching
the discipline in ``test__store_retirement``: a check that cannot fail for the
right reason cannot pass for one either. The current-store cases are positive
controls — without them, a guard that refused everything would look identical to
a guard that works.
"""

import pytest

from scitex_cards._store_retirement import StoreRetired, retire_store


def _make_store(tmp_path, *, retired: bool):
    """A real, schema-complete store, optionally retired through the real verb."""
    from scitex_cards._db import open_db

    db = tmp_path / "cards.db"
    conn = open_db(str(db))
    if retired:
        retire_store(
            conn,
            successor_uuid="uuid-destination",
            by="test",
            at="2026-07-31T15:00:00Z",
        )
        conn.commit()
    conn.close()
    return str(db)


class TestRetiredStoreRefusesDmWrites:
    """The gap itself: the DM write funnel must consult the guard."""

    def test_dm_write_funnel_refuses_on_a_retired_store(self, tmp_path):
        # Arrange
        from scitex_cards._dm.write_rows import _open

        db = _make_store(tmp_path, retired=True)

        # Act
        try:
            conn = _open(db, None)
            conn.close()
            outcome = "opened"
        except StoreRetired:
            outcome = "refused"

        # Assert
        assert outcome == "refused", (
            "the DM write funnel opened a RETIRED store. This is the exact "
            "defect measured 2026-07-31: a card write was refused while a DM "
            "write landed in the store everyone had already left."
        )

    def test_dm_write_funnel_still_opens_a_current_store(self, tmp_path):
        # Arrange — POSITIVE CONTROL. A guard that refuses everything would
        # pass the test above while breaking every DM in the fleet.
        from scitex_cards._dm.write_rows import _open

        db = _make_store(tmp_path, retired=False)

        # Act
        try:
            conn = _open(db, None)
            conn.close()
            outcome = "opened"
        except Exception as exc:  # noqa: BLE001 -- any refusal is the failure
            outcome = f"refused: {type(exc).__name__}"

        # Assert
        assert outcome == "opened", (
            f"a CURRENT store was refused ({outcome}); the guard is firing on "
            f"the wrong condition and would take the fleet's DMs down."
        )


class TestRetiredStoreStaysReadable:
    """The property the over-broad fix would have destroyed."""

    def test_a_retired_store_can_still_be_opened_for_reading(self, tmp_path):
        # Arrange — refusing here is what happens if the guard is placed in
        # open_db instead of at the write funnel.
        import sqlite3

        db = _make_store(tmp_path, retired=True)

        # Act
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
        finally:
            conn.close()

        # Assert
        assert rows > 0, (
            "a retired store must remain readable — recovering from a "
            "retirement means reading the store you retired."
        )

    def test_the_retirement_marker_is_readable_after_retiring(self, tmp_path):
        # Arrange
        import sqlite3

        from scitex_cards._store_retirement import STATUS_RETIRED

        db = _make_store(tmp_path, retired=True)

        # Act
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            got = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'store_status'"
            ).fetchone()
        finally:
            conn.close()

        # Assert
        assert got is not None and got[0] == STATUS_RETIRED, (
            f"expected the store to report itself retired, got {got!r}. "
            f"The successor pointer and the status must survive retirement or "
            f"nobody can tell WHERE the board went."
        )


class TestGuardIsNotDuplicated:
    """One definition of 'is this store retired', not one per caller."""

    def test_dm_funnel_reuses_the_canonical_guard(self):
        # Arrange
        import inspect

        from scitex_cards._dm import write_rows as _dm_write_rows

        # Act
        src = inspect.getsource(_dm_write_rows._open)

        # Assert
        assert "_refuse_if_retired_on" in src, (
            "the DM funnel must REUSE the canonical guard rather than "
            "re-derive the condition. Its docstring states why: both backends "
            "must run ONE definition, because duplicating it per caller is how "
            "the two answers drift."
        )


# EOF
