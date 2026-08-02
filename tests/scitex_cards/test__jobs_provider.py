#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pin the shape of scitex-todo's ``scitex_dev.jobs`` leaf entry point.

The provider is a one-line contract — but it is the single declaration
``scitex-dev ecosystem up`` reads to know that scitex-todo's board
needs to be a long-running ``--user`` systemd unit on TCP 8051. A
typo in the schedule / kind / command / port silently breaks the
operator's primary daily UI surface, so we pin every field.

Real fakes only (PA-306 / STX-NM*) — no ``unittest.mock``,
no ``monkeypatch``.
"""

from __future__ import annotations

from scitex_cards._jobs_provider import provide_jobs


def test_provide_jobs_returns_at_least_one_jobspec():
    # Arrange
    # Act
    jobs = provide_jobs()
    # Assert
    assert len(jobs) >= 1


def test_provide_jobs_includes_the_board_dashboard():
    # Arrange
    # Act
    names = {j.name for j in provide_jobs()}
    # Assert
    assert "scitex-todo.dashboard" in names


def _board() -> object:
    return next(j for j in provide_jobs() if j.name == "scitex-todo.dashboard")


def _snapshot() -> object:
    return next(j for j in provide_jobs() if j.name == "scitex-cards.snapshot")


def test_board_kind_is_service():
    # Arrange
    # Act
    job = _board()
    # Assert — service kind = long-running unit, NOT a periodic timer.
    assert job.kind == "service"


def test_board_command_invokes_scitex_cards_board():
    # Arrange
    # Act
    job = _board()
    # Assert
    assert "scitex-todo board" in job.command


def test_board_command_pins_port_8051():
    # Arrange
    # Act
    job = _board()
    # Assert — the operator's primary UI is documented at
    # http://127.0.0.1:8051/; the canonical entry point in
    # _cli/_main.py also defaults to 8051.
    assert "--port 8051" in job.command


def test_board_schedule_is_empty():
    # Arrange
    # Act
    job = _board()
    # Assert — services aren't scheduled; JobSpec.validate() requires
    # schedule="" for kind="service".
    assert job.schedule == ""


def test_board_restart_policy_is_on_failure():
    # Arrange
    # Act
    job = _board()
    # Assert — board is the operator's daily UI; lost availability is
    # paged via the restart cycle (and the master reconcile unit on
    # boot).
    assert job.restart_policy == "on-failure"


def test_board_on_boot_sec_is_set():
    # Arrange
    # Act
    job = _board()
    # Assert — a non-None OnBootSec lets systemd defer startup until
    # network-online.target settles.
    assert job.on_boot_sec is not None


def test_board_description_mentions_the_canonical_url():
    # Arrange
    # Act
    job = _board()
    # Assert — `scitex-dev ecosystem cron list` / `systemd list` show
    # the description; mentioning 127.0.0.1:8051 makes the unit's
    # purpose obvious from a one-line summary.
    assert "8051" in job.description


#: WHY the three `snapshot` tests below are split but share one rationale:
#: the ADR-0010 backup rail runs on a TIMER, not on somebody remembering. A
#: typo in the kind or the command silently disarms the whole backup rail, so
#: the kind and the exact command are each pinned on their own.
#:
#: THE THIRD TEST INVERTED, 2026-07-20. It used to assert that `--refresh` was
#: PRESENT, describing it as "load-bearing pre-cutover: import IS the freshness
#: step". That was true while YAML was canonical and the database was a mirror
#: of it. The cutover made the database the store, and the same flag inverted
#: into a data-loss engine on a timer: it rebuilds cards.db FROM a YAML file, so
#: every hour it would overwrite the DB — including every card written in the
#: preceding hour — while logging a successful refresh and a successful push.
#:
#: The test now asserts the flag is ABSENT. Note the old assertion was not
#: wrong when written and did not become wrong quietly: it stated its own
#: expiry condition ("pre-cutover") in the comment beside it, which is why this
#: change is a two-line inversion rather than an archaeology exercise.


def test_provide_jobs_includes_the_snapshot_cadence():
    # Arrange
    # Act
    job = _snapshot()
    # Assert — cron kind = a timer, not a service somebody has to start.
    assert job.kind == "cron"


def test_snapshot_command_is_the_db_snapshot_verb():
    # Arrange
    # Act
    job = _snapshot()
    # Assert
    assert job.command == "scitex-cards db snapshot --push"


def test_snapshot_command_does_not_import_yaml_over_the_database():
    # Arrange
    # Act
    job = _snapshot()
    # Assert — `--refresh` imports a YAML document over the store. On an
    # unattended hourly timer that is a wipe with a success log. On
    # 2026-07-20 the live board went 2,165 cards -> 5 by exactly this route.
    assert "--refresh" not in job.command


def test_snapshot_command_still_pushes_off_site():
    # Arrange
    # Act
    job = _snapshot()
    # Assert — removing the import must not quietly remove the BACKUP too.
    # The export is the operator's stated fallback, and dropping it while the
    # database becomes the only store would delete the safety net at the
    # moment it matters most.
    assert "--push" in job.command


# EOF
