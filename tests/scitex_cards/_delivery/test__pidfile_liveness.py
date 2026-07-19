#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests: notifyd liveness ACROSS a PID-namespace boundary.

THE BUG (observed live, 2026-07-13). notifyd runs on the BARE HOST under
systemd; fleet agents run in CONTAINERS that share the task store — and hence
the pidfile — through a bind-mount, but in a DIFFERENT PID namespace. The health
check read the host's pid out of the shared pidfile and probed it with
``os.kill(pid, 0)``. The host's pid does not exist in the container's ``/proc``,
so the probe raised ``ProcessLookupError`` and health reported::

    stale notifyd pidfile: pid 3285778 is not running

while that very pid was alive and ticking on the host. **A pid is only
meaningful inside the PID namespace that issued it.** A check that reports
FAILED on a healthy system, permanently, trains its reader to ignore the
channel — so when the daemon really dies, nobody looks.

Note the hostname is NOT a usable discriminator on this fleet: apptainer shares
the UTS namespace, so ``socket.gethostname()`` inside the container returns the
SAME name as the bare host. The namespace inode (``/proc/self/ns/pid``) is the
identity that actually issues pids, and it is what the fix compares.

NO mocks (STX-NM / PA-306): real pidfiles on a real ``tmp_path``, a real dead
pid obtained by really running and reaping a real subprocess, our own real live
pid, and real identity dicts.

One assertion per test (STX-TQ007): each split test re-arranges its own
pidfile through the ``_write_pidfile`` helper.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys

from scitex_cards._delivery import _pidfile
from scitex_cards._delivery._pidfile import (
    assess_liveness,
    local_identity,
    parse,
    render,
    writer_is_local,
)

NOW = _dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=_dt.timezone.utc)


# --------------------------------------------------------------------------- #
# helpers — real processes, real files, no mocks                             #
# --------------------------------------------------------------------------- #
def _really_dead_pid() -> int:
    """Run a real process to completion and REAP it — its pid is truly gone."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _foreign_identity() -> dict[str, str]:
    """Identity of a DIFFERENT PID namespace — same hostname on purpose.

    Same host, same boot id, different namespace: exactly the apptainer shape
    that defeats a hostname-only check.
    """
    mine = local_identity()
    return {
        "host": mine["host"],
        "boot_id": mine["boot_id"],
        "pid_ns": "pid:[4026531836]-not-ours",
        "container": "0",
    }


def _write_pidfile(dirpath, pid, *, identity, heartbeat, interval=120.0):
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / "notifyd.pid"
    body = render(pid, interval=interval, now=heartbeat, identity=identity)
    path.write_text(body, encoding="utf-8")
    return path


def _foreign_fresh_pidfile(tmp_path):
    """A pidfile from another namespace whose daemon is still ticking.

    The pid does NOT exist here (really dead), yet the daemon that wrote it is
    ticking elsewhere. Freshness, not identity, must decide.
    """
    return _write_pidfile(
        tmp_path,
        _really_dead_pid(),  # unresolvable in OUR namespace...
        identity=_foreign_identity(),  # ...because it was never OUR pid.
        heartbeat=NOW - _dt.timedelta(seconds=30),  # ticking (interval 120s)
    )


def _foreign_stale_pidfile(tmp_path):
    """A foreign pidfile that stopped ticking, stamped with a LIVE local pid.

    If the check ever fell back to probing the pid it would wrongly say
    "alive" — the stale heartbeat must decide.
    """
    return _write_pidfile(
        tmp_path,
        os.getpid(),  # alive in OUR namespace — must not be consulted
        identity=_foreign_identity(),
        heartbeat=NOW - _dt.timedelta(hours=2),
        interval=120.0,
    )


def _local_dead_pidfile(tmp_path):
    """OUR namespace, fresh heartbeat, but the process is really gone.

    A daemon that crashed a second after its last stamp leaves a FRESH
    heartbeat behind; freshness must not paper over a corpse we can see.
    """
    return _write_pidfile(
        tmp_path,
        _really_dead_pid(),
        identity=local_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=1),
    )


# --------------------------------------------------------------------------- #
# THE false-negative — a foreign, FRESH pidfile is ALIVE                      #
# --------------------------------------------------------------------------- #
def test_foreign_namespace_with_fresh_heartbeat_is_ok(tmp_path):
    # Arrange
    pidfile = _foreign_fresh_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    # the exact live false-negative; it must never regress.
    assert verdict["ok"] is True


def test_foreign_namespace_with_fresh_heartbeat_reports_alive(tmp_path):
    # Arrange
    pidfile = _foreign_fresh_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert verdict["state"] == "alive"


def test_foreign_namespace_alive_verdict_carries_no_hint(tmp_path):
    # Arrange
    pidfile = _foreign_fresh_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    # a passing check has nothing to remedy.
    assert verdict["hint"] is None


def test_foreign_namespace_verdict_says_the_pid_was_not_probed(tmp_path):
    # Arrange
    pidfile = _foreign_fresh_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    # it says WHY it did not trust the pid.
    assert "NOT probed" in verdict["detail"]


def test_foreign_namespace_alive_verdict_never_mentions_staleness(tmp_path):
    # Arrange
    pidfile = _foreign_fresh_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert "stale" not in verdict["detail"].lower()


def test_foreign_identity_shares_our_hostname_exactly(tmp_path):
    # Arrange
    # apptainer shares the UTS namespace, so the names match.
    foreign = _foreign_identity()
    # Act
    mine = local_identity()
    # Assert
    # hostname alone would have MISSED the boundary.
    assert foreign["host"] == mine["host"]


def test_same_hostname_different_namespace_is_not_local(tmp_path):
    # Arrange
    foreign = _foreign_identity()
    # Act
    record = parse(render(1234, now=NOW, identity=foreign))
    # Assert
    assert writer_is_local(record) is False


# --------------------------------------------------------------------------- #
# still FAIL-LOUD — a foreign daemon that STOPPED ticking is dead            #
# --------------------------------------------------------------------------- #
def test_foreign_namespace_with_stale_heartbeat_is_not_ok(tmp_path):
    # Arrange
    pidfile = _foreign_stale_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    # a real death across the boundary is still caught.
    assert verdict["ok"] is False


def test_foreign_namespace_with_stale_heartbeat_reports_dead(tmp_path):
    # Arrange
    pidfile = _foreign_stale_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert verdict["state"] == "dead"


def test_foreign_stale_verdict_names_staleness_in_the_detail(tmp_path):
    # Arrange
    pidfile = _foreign_stale_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert "STALE" in verdict["detail"]


def test_foreign_stale_verdict_carries_an_actionable_hint(tmp_path):
    # Arrange
    pidfile = _foreign_stale_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert verdict["hint"], "a failing check must carry an actionable hint"


def test_foreign_stale_hint_tells_the_reader_to_restart(tmp_path):
    # Arrange
    pidfile = _foreign_stale_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert "restart" in verdict["hint"]


def test_one_and_a_half_missed_ticks_still_reads_as_alive(tmp_path):
    # Arrange
    # interval=600 => budget 1800s; 900s old is 1.5 ticks.
    fresh = _write_pidfile(
        tmp_path / "a",
        4242,
        identity=_foreign_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=900),
        interval=600.0,
    )
    # Act
    verdict = assess_liveness(fresh, now=NOW)
    # Assert
    # one slow/missed tick is tolerated.
    assert verdict["state"] == "alive"


def test_six_missed_ticks_exceeds_the_staleness_budget(tmp_path):
    # Arrange
    # interval=600 => budget 1800s; 3600s old is far past it.
    stale = _write_pidfile(
        tmp_path / "b",
        4242,
        identity=_foreign_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=3600),
        interval=600.0,
    )
    # Act
    verdict = assess_liveness(stale, now=NOW)
    # Assert
    # a stopped daemon is not tolerated.
    assert verdict["state"] == "dead"


# --------------------------------------------------------------------------- #
# LOCAL namespace — the precise pid probe is preserved                       #
# --------------------------------------------------------------------------- #
def test_local_dead_daemon_with_fresh_heartbeat_is_not_ok(tmp_path):
    # Arrange
    pidfile = _local_dead_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    # in OUR namespace the pid probe is authoritative.
    assert verdict["ok"] is False


def test_local_dead_daemon_with_fresh_heartbeat_reports_dead(tmp_path):
    # Arrange
    pidfile = _local_dead_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert verdict["state"] == "dead"


def test_local_dead_daemon_detail_says_it_is_not_running(tmp_path):
    # Arrange
    pidfile = _local_dead_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert "is not running" in verdict["detail"]


def test_local_dead_daemon_hint_names_the_notifyd_command(tmp_path):
    # Arrange
    pidfile = _local_dead_pidfile(tmp_path)
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert "scitex-todo notifyd" in verdict["hint"]


def test_local_live_daemon_verdict_is_ok(tmp_path):
    # Arrange
    pidfile = _write_pidfile(
        tmp_path,
        os.getpid(),
        identity=local_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=5),
    )
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert verdict["ok"] is True


def test_local_live_daemon_reports_state_alive(tmp_path):
    # Arrange
    pidfile = _write_pidfile(
        tmp_path,
        os.getpid(),
        identity=local_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=5),
    )
    # Act
    verdict = assess_liveness(pidfile, now=NOW)
    # Assert
    assert verdict["state"] == "alive"


def test_missing_pidfile_verdict_is_not_ok(tmp_path):
    # Arrange
    missing = tmp_path / "notifyd.pid"
    # Act
    verdict = assess_liveness(missing, now=NOW)
    # Assert
    assert verdict["ok"] is False


def test_missing_pidfile_reports_state_not_running(tmp_path):
    # Arrange
    missing = tmp_path / "notifyd.pid"
    # Act
    verdict = assess_liveness(missing, now=NOW)
    # Assert
    assert verdict["state"] == "not_running"


def test_missing_pidfile_hint_points_at_install_unit(tmp_path):
    # Arrange
    missing = tmp_path / "notifyd.pid"
    # Act
    verdict = assess_liveness(missing, now=NOW)
    # Assert
    assert "install-unit" in verdict["hint"]


# --------------------------------------------------------------------------- #
# LEGACY pidfiles (bare pid, pre-heartbeat notifyd)                           #
# --------------------------------------------------------------------------- #
def test_legacy_bare_pidfile_with_live_pid_is_ok(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    assert verdict["ok"] is True


def test_legacy_bare_pidfile_with_live_pid_reports_alive(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    assert verdict["state"] == "alive"


def test_legacy_bare_pidfile_with_dead_pid_is_not_ok(tmp_path):
    # Arrange
    # no stamp = no way to know; stay loud.
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{_really_dead_pid()}\n", encoding="utf-8")
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    assert verdict["ok"] is False


def test_legacy_bare_pidfile_detail_names_the_missing_stamp(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{_really_dead_pid()}\n", encoding="utf-8")
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    assert "no host/namespace stamp" in verdict["detail"]


def test_legacy_bare_pidfile_hint_admits_a_possible_false_verdict(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{_really_dead_pid()}\n", encoding="utf-8")
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    # the verdict may be an artefact of the namespace boundary.
    assert "FALSE" in verdict["hint"]


def test_legacy_bare_pidfile_hint_tells_the_reader_to_upgrade(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{_really_dead_pid()}\n", encoding="utf-8")
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    # how to make the check trustworthy.
    assert "Upgrade" in verdict["hint"]


# --------------------------------------------------------------------------- #
# format — backward compatible + round-trips                                  #
# --------------------------------------------------------------------------- #
def test_first_token_is_still_the_bare_pid():
    # Arrange
    pid = 4242
    # Act
    body = render(pid, now=NOW)
    # Assert
    # any old reader doing ``int(text.split()[0])`` keeps working.
    assert int(body.split()[0]) == pid


def test_render_parse_round_trip_keeps_the_pid():
    # Arrange
    ident = local_identity()
    # Act
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Assert
    assert record["pid"] == 99


def test_render_parse_round_trip_keeps_the_host():
    # Arrange
    ident = local_identity()
    # Act
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Assert
    assert record["host"] == ident["host"]


def test_render_parse_round_trip_keeps_the_pid_namespace():
    # Arrange
    ident = local_identity()
    # Act
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Assert
    assert record["pid_ns"] == ident["pid_ns"]


def test_render_parse_round_trip_keeps_the_boot_id():
    # Arrange
    ident = local_identity()
    # Act
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Assert
    assert record["boot_id"] == ident["boot_id"]


def test_render_parse_round_trip_keeps_the_tick_interval():
    # Arrange
    ident = local_identity()
    # Act
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Assert
    assert float(record["interval"]) == 42.0


def test_round_tripped_heartbeat_age_is_zero_at_the_stamp_time():
    # Arrange
    ident = local_identity()
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Act
    age = _pidfile.heartbeat_age_seconds(record, NOW)
    # Assert
    assert age == 0.0


def test_round_tripped_local_identity_is_recognised_as_local():
    # Arrange
    ident = local_identity()
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    # Act
    is_local = writer_is_local(record, ident)
    # Assert
    assert is_local is True


def test_naive_heartbeat_stamp_does_not_explode():
    # Arrange
    # a naive stamp meeting an aware `now` (a known repo trap).
    record = {"heartbeat": "2026-07-13T11:59:00"}
    # Act
    age = _pidfile.heartbeat_age_seconds(record, NOW)
    # Assert
    assert age == 60.0


def test_unparseable_pid_with_fresh_heartbeat_is_ok(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(
        f"garbage\nhost=elsewhere\npid_ns=pid:[1]\nheartbeat={NOW.isoformat()}\n",
        encoding="utf-8",
    )
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    assert verdict["ok"] is True


def test_unparseable_pid_with_fresh_heartbeat_reports_alive(tmp_path):
    # Arrange
    path = tmp_path / "notifyd.pid"
    path.write_text(
        f"garbage\nhost=elsewhere\npid_ns=pid:[1]\nheartbeat={NOW.isoformat()}\n",
        encoding="utf-8",
    )
    # Act
    verdict = assess_liveness(path, now=NOW)
    # Assert
    assert verdict["state"] == "alive"


# EOF
