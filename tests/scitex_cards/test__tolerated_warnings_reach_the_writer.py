#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A tolerated-value warning must reach the caller who caused it.

`_warn_tolerated` shouts and keeps going -- correctly, by operator ruling
2026-07-10 (「カードが書けないということはなしで大丈夫です、warning で十分です」):
a status value must never cost someone their card, and refusing would make one
legacy row fail every OTHER agent's write.

But it shouts to the MCP SERVER process's stderr and to `warnings`. An agent
calling `add_task` over MCP receives the tool result and nothing else, so the
one mechanism that notices is invisible to the only party who can act on it.

WHAT THAT PERMITTED, measured on the live board 2026-08-16:

  * three cards carrying the ABOLISHED status `pending` were CREATED after its
    abolition -- all three by the maintainer of the package that abolished it,
    inside 36 hours;
  * a several-hundred-card sweep set `archived`, a status no build has ever
    known, and it stood about six hours.

Every one fired the warning. Nobody saw one. Constitution s2: a check whose
failure nothing reads.

THE FIX RETURNS THE WARNING, IT DOES NOT REFUSE THE WRITE. Refusing is ruled out
above; returning costs nothing and stops a sweep at card ONE rather than card
293. Two tests below assert the ruling still holds -- the card is written and the
value survives.

Real round-trips against the conftest-isolated store, no mocks (STX-NM).
"""

from __future__ import annotations

import pytest

from scitex_cards._store import add_task, get_task, update_task


@pytest.fixture()
def seeded():
    """A clean card to mutate, created with a status this build knows."""
    add_task(id="w1", title="probe", status="deferred", assignee="tester",
             created_by="tester")
    return "w1"


# --------------------------------------------------------------------------
# add_task -- the verb all three `pending` cards came through
# --------------------------------------------------------------------------


def test_creating_a_card_with_an_unknown_status_warns_the_caller():
    # Arrange
    unknown = "archived"
    # Act
    result = add_task(id="w2", title="probe", status=unknown, assignee="t",
                      created_by="t")
    # Assert
    assert result.get("warnings"), (
        "add_task accepted an unknown status and told the caller nothing. This "
        "is how a several-hundred-card sweep set `archived` unnoticed."
    )


def test_the_returned_warning_names_the_offending_value():
    # Arrange
    unknown = "archived"
    # Act
    result = add_task(id="w3", title="probe", status=unknown, assignee="t",
                      created_by="t")
    # Assert
    assert "archived" in " ".join(result["warnings"])


def test_creating_a_card_with_an_abolished_status_warns_the_caller():
    """`pending` was abolished 2026-07-10 and three cards still acquired it."""
    # Arrange
    abolished = "pending"
    # Act
    result = add_task(id="w4", title="probe", status=abolished, assignee="t",
                      created_by="t")
    # Assert
    assert result.get("warnings")


def test_the_card_is_still_created(seeded):
    """OPERATOR RULING 2026-07-10 -- a status value must never cost a card."""
    # Arrange
    add_task(id="w5", title="probe", status="archived", assignee="t",
             created_by="t")
    # Act
    stored = get_task(None, "w5")
    # Assert
    assert stored["status"] == "archived"


def test_an_ordinary_create_carries_no_warnings_key():
    """A clean write must be byte-identical to before this change."""
    # Arrange
    known = "deferred"
    # Act
    result = add_task(id="w6", title="probe", status=known, assignee="t",
                      created_by="t")
    # Assert
    assert "warnings" not in result


# --------------------------------------------------------------------------
# update_task
# --------------------------------------------------------------------------


def test_updating_to_an_unknown_status_warns_the_caller(seeded):
    # Arrange
    unknown = "archived"
    # Act
    result = update_task(None, "w1", status=unknown)
    # Assert
    assert result.get("warnings")


def test_the_update_still_applies(seeded):
    """Warn, never refuse -- the same ruling, on the mutate path."""
    # Arrange
    unknown = "archived"
    # Act
    result = update_task(None, "w1", status=unknown)
    # Assert
    assert result["status"] == "archived"


def test_an_ordinary_update_carries_no_warnings_key(seeded):
    # Arrange
    known = "in_progress"
    # Act
    result = update_task(None, "w1", status=known)
    # Assert
    assert "warnings" not in result


def test_a_blocked_card_with_no_blocker_also_reaches_the_caller(seeded):
    """The OTHER `_warn_tolerated` call site, so the fix is not half-applied."""
    # Arrange
    gateless = "blocked"
    # Act
    result = update_task(None, "w1", status=gateless)
    # Assert
    assert any("names no blocker" in w for w in result.get("warnings", []))


# --------------------------------------------------------------------------
# the collector must not leak, which is why it is a ContextVar
# --------------------------------------------------------------------------


def test_a_clean_write_to_ANOTHER_card_inherits_nothing(seeded):
    """A module-level list would carry the first write's warning into the
    second's result. The collector is per-context precisely to stop that.

    NOTE THE SECOND CARD. An earlier version of this test wrote a bad status to
    `w1` and then made a title-only edit to `w1`, expecting no warning -- and it
    failed, correctly. `w1` still HELD `archived`, and `save_tasks` validates the
    whole card, so the warning fired again on its own merits. That is the
    intended behaviour (it keeps telling you until the value is fixed), not a
    leak, and the test was measuring the wrong thing.
    """
    # Arrange
    update_task(None, "w1", status="archived")
    add_task(id="w9", title="clean", status="deferred", assignee="t", created_by="t")
    # Act
    clean = update_task(None, "w9", title="renamed")
    # Assert
    assert "warnings" not in clean


def test_the_writer_is_not_told_about_other_cards_bad_values(seeded):
    """SCOPED TO THE CALLER'S CARD. `save_tasks` validates the WHOLE task list,
    so an unscoped collector would hand this writer a warning for every off-enum
    row in the document -- eleven of them on the live board today, about cards
    they never touched. Silence is a check nobody reads; noise is a check
    everybody learns to ignore."""
    # Arrange
    update_task(None, "w1", status="archived")
    add_task(id="w10", title="clean", status="deferred", assignee="t", created_by="t")
    # Act
    result = update_task(None, "w10", status="in_progress")
    # Assert
    assert "warnings" not in result


def test_the_collector_is_inactive_outside_a_write():
    """With nobody collecting, `record` must be a no-op -- the READ path fires
    this warning on every load of a store holding legacy rows, and must pay
    nothing for it."""
    # Arrange
    from scitex_cards._tolerated import record

    # Act
    outcome = record("a warning with no collector open")
    # Assert
    assert outcome is None
