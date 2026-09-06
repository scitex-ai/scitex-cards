#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-cards update` must be able to WRITE `scheduled` / `deadline`, not only read them.

THE LIVE FAILURE, reported by scitex-agent-container 2026-08-20. A stop hook
tells the agent, every turn::

    "scheduled time reached — start it, or re-schedule with a reason"

and the second half had no mechanism from the CLI. `--scheduled` did not exist;
neither did `--deadline`. They commented a concrete reason on all six due cards
and parked the three that should stand, and the hook kept firing, because it
keys on `scheduled` being in the past. THE ONLY EXIT IT ACCEPTED WAS A FIELD NO
CLI VERB COULD SET.

The asymmetry is what makes it worth a test rather than a patch: the cheapest
way to silence that hook was to flip `status` to `in_progress` and misstate the
work. A nudge whose only affordable silencer is a false claim trains the honest
operator out of honesty.

WHY THESE ASSERT REFLECTION AND NOT ACCEPTANCE — the point sac made, from their
own `--exit-zero` incident the same night: the flag was implemented in the CLI
and the JobSpec never passed it, so every "does the option exist" check was
green while the behaviour was absent. ACCEPTANCE AND APPLICATION ARE DIFFERENT
PROPERTIES. A mutant that parses `--scheduled` and silently drops it satisfies
`--help` and a non-zero exit code and every parse-level assertion; only a
round-trip through the store catches it. So each test below writes through the
real CLI and re-reads THE STORE.

No mocks (STX-NM002): the verb is driven end-to-end and the store is the
conftest's fresh canonical DB.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards import _store
from scitex_cards._cli import main

SCHEDULED = "2026-08-21T09:00"
DEADLINE = "2026-08-25"


def _card(task_id):
    """Insert a real deferred card carrying neither date field."""
    return _store.add_task(
        id=task_id,
        title="A card with no dates yet",
        status="deferred",
        assignee="agent:test-suite",
    )


def _reload(task_id):
    """Re-read FROM THE STORE — what got PERSISTED is the whole question."""
    return _store.get_task(task_id=task_id)


def _update(task_id, *args):
    return CliRunner().invoke(main, ["update", task_id, *args])


class TestScheduledIsWritable:
    def test_the_value_reaches_the_store(self):
        # Arrange
        _card("sched-1")

        # Act
        _update("sched-1", "--scheduled", SCHEDULED)

        # Assert
        assert _reload("sched-1")["scheduled"] == SCHEDULED

    def test_an_empty_value_clears_it(self):
        # Arrange
        _card("sched-2")
        _update("sched-2", "--scheduled", SCHEDULED)

        # Act
        _update("sched-2", "--scheduled", "")

        # Assert
        assert _reload("sched-2").get("scheduled") is None


class TestDeadlineIsWritable:
    def test_the_value_reaches_the_store(self):
        # Arrange
        _card("dl-1")

        # Act
        _update("dl-1", "--deadline", DEADLINE)

        # Assert
        assert _reload("dl-1")["deadline"] == DEADLINE

    def test_an_empty_value_clears_it(self):
        # Arrange
        _card("dl-2")
        _update("dl-2", "--deadline", DEADLINE)

        # Act
        _update("dl-2", "--deadline", "")

        # Assert
        assert _reload("dl-2").get("deadline") is None


class TestDeadlinesListIsWritable:
    def test_repeated_flags_reach_the_store_as_a_list(self):
        # Arrange
        _card("dls-1")

        # Act
        _update("dls-1", "--deadlines", "2026-09-01", "--deadlines", "2026-09-08")

        # Assert
        assert _reload("dls-1")["deadlines"] == ["2026-09-01", "2026-09-08"]

    def test_one_empty_value_clears_the_list(self):
        # Arrange
        _card("dls-2")
        _update("dls-2", "--deadlines", "2026-09-01")

        # Act
        _update("dls-2", "--deadlines", "")

        # Assert
        assert _reload("dls-2").get("deadlines") is None


class TestTheVerbStillRefusesAnEmptyUpdate:
    """CONTROL: these flags must not make a no-arg `update` look like work.

    `update` raises when NO field flag is passed. If a new option defaulted to
    something truthy instead of None it would silently satisfy that check, and
    every test above would still pass — so the guard is asserted separately
    rather than assumed.
    """

    def test_passing_no_field_flags_is_still_an_error(self):
        # Arrange
        _card("noop-1")

        # Act
        result = _update("noop-1")

        # Assert
        assert result.exit_code != 0


# EOF
