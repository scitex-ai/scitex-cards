#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A disposal must carry a reason — including through the generic update verb.

THE DEFECT (measured on the shared store 2026-09-17, while triaging
hub-224-cards-cancelled-in-one-sweep-with-no-recorded-reason-20260822):

    cancelled cards                     2,308
    ... with NO reason-ish comment        993

`close` enforces a reason — `_close.py` raises when `--reason` is empty, and
that is the doctrine ("exactly `done` for success and `close --reason` for
everything else"). But `update --status cancelled` went straight to
`update_task(status=...)`: no comment, no `_log_meta.closed_by`, nothing. A
second door beside the one with the lock, and the 224-card sweep of 2026-08-19
(26 of them SECURITY, the operator's stated #1 priority) is what walking
through it looks like at scale.

WHY REFUSE RATHER THAN WARN: a warning leaves the disposal possible and the
reason absent, which is the state being measured above. The card was disposed
of either way; the only question is whether the next reader can find out why.

WHAT IS DELIBERATELY ALLOWED: every other status transition through this verb,
and `failed` in particular — machine paths (CI sync, sweeps) mark failures
through the store API, and this verb is not where those run. The guard is the
narrowest one that closes the measured hole.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main


def test_update_cannot_dispose_of_a_card_without_a_reason():
    """The refusal is the fix: no reason, no cancellation."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["update", "any-card", "--status", "cancelled"])
    # Assert
    assert (result.exit_code != 0, "close" in result.output) == (True, True), (
        "`update --status cancelled` must refuse and name the verb that records "
        f"a reason; got exit {result.exit_code}: {result.output!r}"
    )


def test_the_refusal_says_why_the_reason_matters():
    """A refusal that does not say what to do instead just looks broken."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["update", "any-card", "--status", "cancelled"])
    # Assert
    assert "reason" in result.output.lower()


def test_ordinary_status_transitions_are_not_caught_by_the_guard():
    """The guard is narrow on purpose — it must not block normal updates."""
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["update", "any-card", "--status", "in_progress"])
    # Assert
    assert "DISPOSAL" not in result.output, (
        "a plain status transition was refused as a disposal; the guard must key "
        "on cancellation only"
    )


# EOF
