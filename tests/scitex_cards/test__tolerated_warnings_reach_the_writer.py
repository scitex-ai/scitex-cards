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

THE STORE STILL WARNS, NEVER REFUSES — the ruling holds where it applies
(reads tolerate legacy rows; ``save_tasks`` never destroys another writer's
row). The CREATE verb refuses ABOLISHED values only (``pending`` carries
the abolition hint): a caller minting a known-dead status gets
``TaskValidationError`` BEFORE the store is touched — refusing a create
costs nobody their card, no card exists yet. A merely-UNKNOWN value still
warns at the save path (forward-compat — it may be a newer agent's
status), and the tests below pin both halves; an ordinary write stays
byte-identical.

Real round-trips against the conftest-isolated store, no mocks (STX-NM).

Store-touching tests request the ``postgres_dsn`` fail-LOUD fixture
(bare-param pattern): the ambient-store checks below target the ambient
per-test store, which is refused without a writable cluster. The refusal
pins take no fixture — the gate fires before any store touch, so they run
anywhere. The collector no-op test stays unguarded by design (no store
touch).
"""

from __future__ import annotations

import pytest

from scitex_cards._store import add_task, get_task, update_task
from scitex_cards._task import TaskValidationError


@pytest.fixture()
def seeded(postgres_dsn):
    """A clean card to mutate, created with a status this build knows.

    ``postgres_dsn`` is requested but is not the seed target. With no
    writable PostgreSQL the harness pins the refused placeholder, so the
    seeding below would raise — the fixture fails first, LOUDLY, with the
    reason and the remedy. (Same guard as the ``store`` fixtures in _django/.)
    """
    add_task(id="w1", title="probe", status="deferred", assignee="tester",
             created_by="tester")
    return "w1"


# --------------------------------------------------------------------------
# add_task -- the verb all three `pending` cards came through
# --------------------------------------------------------------------------


def test_creating_a_card_with_an_unknown_status_warns_the_caller(postgres_dsn):
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


def test_the_returned_warning_names_the_offending_value(postgres_dsn):
    # Arrange
    unknown = "archived"
    # Act
    result = add_task(id="w3", title="probe", status=unknown, assignee="t",
                      created_by="t")
    # Assert
    assert "archived" in " ".join(result["warnings"])


def test_creating_a_card_with_an_abolished_status_is_refused_with_the_hint():
    """`pending` was abolished 2026-07-10 and three cards still acquired it."""
    # Arrange
    abolished = "pending"
    # Act
    def act():
        add_task(id="w4", title="probe", status=abolished, assignee="t",
                 created_by="t")
    # Assert
    with pytest.raises(TaskValidationError, match="abolished"):
        act()


def test_the_refused_create_costs_no_card_case1(seeded, postgres_dsn):
    """OPERATOR RULING 2026-07-10 -- a status value must never cost a card.

    The refusal lands BEFORE the store guard, so the seeded card stands
    untouched (that a rejected id is never minted is pinned by the
    gate-before-guard order test beside the verb).
    """
    # Arrange
    # Act
    # Assert
    stored = get_task(None, "w1")
    with pytest.raises(TaskValidationError):
        add_task(id="w5", title="probe", status="pending", assignee="t",
                 created_by="t")


def test_the_refused_create_costs_no_card_case2(seeded, postgres_dsn):
    """OPERATOR RULING 2026-07-10 -- a status value must never cost a card.

    The refusal lands BEFORE the store guard, so the seeded card stands
    untouched (that a rejected id is never minted is pinned by the
    gate-before-guard order test beside the verb).
    """
    # Arrange
    # Act
    stored = get_task(None, "w1")
    # Assert
    assert stored["status"] == "deferred"


def test_an_ordinary_create_carries_no_warnings_key(postgres_dsn):
    """A clean write must be byte-identical to before this change."""
    # Arrange
    known = "deferred"
    # Act
    result = add_task(id="w6", title="probe", status=known, assignee="t",
                      created_by="t")
    # Assert
    assert "warnings" not in result


def test_an_add_of_an_unknown_status_still_keeps_the_card(postgres_dsn):
    """Verb-level mirror of the save-layer keeps-the-card pin.

    The collector tests below seed their off-enum row through ``add_task``,
    so this pins the composition they rely on: a warn-accepted unknown
    status is WRITTEN, not dropped. Without this, those tests would pass
    vacuously (no off-enum row held, trivially nothing to inherit).
    """
    # Arrange
    unknown = "archived"
    # Act
    add_task(id="w7", title="probe", status=unknown, assignee="t",
             created_by="t")
    stored = get_task(None, "w7")
    # Assert
    assert stored["status"] == unknown


# --------------------------------------------------------------------------
# update_task
# --------------------------------------------------------------------------


def test_updating_to_an_unknown_status_is_refused():
    # Arrange
    unknown = "archived"
    # Act
    def act():
        update_task(None, "w1", status=unknown)
    # Assert
    with pytest.raises(TaskValidationError, match="archived"):
        act()


def test_the_refused_update_keeps_the_prior_status_case1(seeded, postgres_dsn):
    """Warn, never refuse, was the old mutate-path contract -- the same ruling,
    on the same path. The refusal keeps the row's prior (good) status, so
    nothing is lost; the caller retries with a legal value."""
    # Arrange
    # Act
    # Assert
    stored = get_task(None, "w1")
    with pytest.raises(TaskValidationError):
        update_task(None, "w1", status="archived")


def test_the_refused_update_keeps_the_prior_status_case2(seeded, postgres_dsn):
    """Warn, never refuse, was the old mutate-path contract -- the same ruling,
    on the same path. The refusal keeps the row's prior (good) status, so
    nothing is lost; the caller retries with a legal value."""
    # Arrange
    # Act
    stored = get_task(None, "w1")
    # Assert
    assert stored["status"] == "deferred"


def test_an_ordinary_update_carries_no_warnings_key(seeded, postgres_dsn):
    # Arrange
    known = "in_progress"
    # Act
    result = update_task(None, "w1", status=known)
    # Assert
    assert "warnings" not in result


def test_a_blocked_card_with_no_blocker_also_reaches_the_caller(seeded, postgres_dsn):
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


def test_a_clean_write_to_ANOTHER_card_inherits_nothing(postgres_dsn):
    """A module-level list would carry the first write's warning into the
    second's result. The collector is per-context precisely to stop that.

    NOTE THE SECOND CARD. An earlier version of this test wrote a bad status to
    `w1` and then made a title-only edit to `w1`, expecting no warning -- and it
    failed, correctly. `w1` still HELD `archived`, and `save_tasks` validates the
    whole card, so the warning fired again on its own merits. That is the
    intended behaviour (it keeps telling you until the value is fixed), not a
    leak, and the test was measuring the wrong thing.

    NOTE THE SEEDING. The off-enum row used to be seeded with
    ``update_task(None, "w1", status="archived")``. The update verb is now
    source-strict -- a caller-supplied status outside ``VALID_STATUSES``
    raises before the store is touched (pinned beside the verb, and by
    ``test_updating_to_an_unknown_status_is_refused`` above) -- so that
    seeding raises instead of writing. The row is seeded through ``add_task``
    instead, which warn-accepts merely-unknown values per the 2026-07-10
    ruling (persistence pinned by
    ``test_an_add_of_an_unknown_status_still_keeps_the_card`` above). The
    store still HOLDS the off-enum row, which is all the Act/Assert below
    needs -- the seeding route is not what is under test.
    """
    # Arrange
    add_task(id="w8", title="off-enum", status="archived", assignee="t",
             created_by="t")
    add_task(id="w9", title="clean", status="deferred", assignee="t", created_by="t")
    # Act
    clean = update_task(None, "w9", title="renamed")
    # Assert
    assert "warnings" not in clean


def test_the_writer_is_not_told_about_other_cards_bad_values(postgres_dsn):
    """SCOPED TO THE CALLER'S CARD. `save_tasks` validates the WHOLE task list,
    so an unscoped collector would hand this writer a warning for every off-enum
    row in the document -- eleven of them on the live board today, about cards
    they never touched. Silence is a check nobody reads; noise is a check
    everybody learns to ignore.

    Seeded through ``add_task`` (warn-accepting) rather than ``update_task``
    (source-strict for caller-supplied statuses) -- see the NOTE in
    ``test_a_clean_write_to_ANOTHER_card_inherits_nothing`` above.
    """
    # Arrange
    add_task(id="w8", title="off-enum", status="archived", assignee="t",
             created_by="t")
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
