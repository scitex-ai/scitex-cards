#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The inventory must never answer the venv's version as the process's version.

An editable checkout on a process's sys.path shadows the installed wheel, so
the venv's version is not what the process imports. Measured 2026-08-23 on two
independent installs: a client reporting 0.48.0 in its dist-info while
importing a source tree, in both directions within half an hour. A fleet-wide
claim built on the venv answer was falsified within four minutes of being made.

These use a REAL directory tree under tmp_path as ``proc_root`` rather than a
mocked filesystem (PA-306 / STX-NM002).
"""

from pathlib import Path

from scitex_cards._process_inventory import (
    LONG_LIVED,
    PER_INVOCATION,
    UNREADABLE,
    UNRESOLVED,
    describe_self,
    scan,
)


def _proc(root: Path, pid: int, cmdline: str) -> Path:
    """Build one real /proc/<pid>/cmdline entry on disk."""
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(cmdline.encode() + b"\x00")
    return d


def test_a_process_matching_the_package_is_listed(tmp_path):
    # Arrange
    _proc(tmp_path, 4242, "/opt/venv/bin/python -m scitex_cards notifyd")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert [r.pid for r in inv.rows] == [4242]


def test_a_process_not_importing_the_package_is_excluded(tmp_path):
    # Arrange
    _proc(tmp_path, 4243, "/usr/bin/vim notes.txt")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows == []


def test_the_enumeration_size_counts_every_process_not_only_the_matches(tmp_path):
    # A zero-row result against a zero-sized enumeration means the scan could
    # not run; against a large one it is evidence. Reporting only matches makes
    # those two indistinguishable, which is the defect this whole card is about.
    # Arrange
    _proc(tmp_path, 5001, "/usr/bin/vim a")
    _proc(tmp_path, 5002, "/usr/bin/less b")
    _proc(tmp_path, 5003, "/opt/venv/bin/scitex-cards notifyd")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.enumerated == 3


def test_an_empty_proc_root_reports_zero_enumerated_rather_than_silence(tmp_path):
    # Arrange
    empty = tmp_path / "nothing"
    empty.mkdir()

    # Act
    inv = scan(proc_root=empty, self_pid=1, parent_pid=2)

    # Assert
    assert inv.enumerated == 0


def test_the_scanner_excludes_its_own_pid(tmp_path):
    # A previous hand-rolled version of this scan matched its own shell,
    # because the probe text sat in bash's cmdline. The instrument must not
    # appear in the population it measures.
    # Arrange
    _proc(tmp_path, 7777, "/opt/venv/bin/python -m scitex_cards list-tasks")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=7777, parent_pid=2)

    # Assert
    assert inv.rows == []


def test_the_scanner_excludes_its_parent_pid(tmp_path):
    # Arrange
    _proc(tmp_path, 8888, "/bin/bash -c 'scitex-cards resolve-store'")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=8888)

    # Assert
    assert inv.rows == []


def test_the_resolved_version_is_never_guessed_from_the_venv(tmp_path):
    # THE CONTRACT THIS MODULE EXISTS FOR. A process's own sys.path is not
    # readable from outside, so this field must say so rather than substitute
    # the venv's answer -- which would be confidently wrong, not merely absent.
    # Arrange
    _proc(tmp_path, 9001, "/opt/venv/bin/python -m scitex_cards notifyd")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].resolved_version == UNRESOLVED


def test_an_unreadable_venv_reports_unreadable_rather_than_omitting_the_row(tmp_path):
    # Arrange
    _proc(tmp_path, 9002, "/opt/venv/bin/python -m scitex_cards notifyd")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].venv_version == UNREADABLE


def test_a_daemon_is_classified_long_lived(tmp_path):
    # Arrange
    _proc(tmp_path, 9100, "/opt/venv/bin/scitex-cards notifyd --interval 120")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].lifetime_class == LONG_LIVED


def test_a_one_shot_cli_call_is_classified_per_invocation(tmp_path):
    # Arrange
    _proc(tmp_path, 9101, "/opt/venv/bin/scitex-cards list-tasks")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].lifetime_class == PER_INVOCATION


def test_a_per_invocation_process_is_never_stale(tmp_path):
    # It re-execs each call, so it picks up whatever the venv holds. Calling it
    # stale is what produced the advice to restart things that never needed one.
    # Arrange
    _proc(tmp_path, 9102, "/opt/venv/bin/scitex-cards list-tasks")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].staleness == "N/A"


def test_a_long_lived_process_with_an_unreadable_version_reports_unknown(tmp_path):
    # Arrange
    _proc(tmp_path, 9103, "/opt/venv/bin/scitex-cards notifyd")

    # Act
    inv = scan(proc_root=tmp_path, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].staleness == "UNKNOWN"


def test_the_venv_is_found_from_argv0_not_from_the_resolved_interpreter(tmp_path):
    # realpath(/proc/<pid>/exe) FOLLOWS a venv's bin/python symlink all the way
    # out to the system interpreter, whose parents hold no pyvenv.cfg. Measured
    # on this module's own first live run: three real processes, three venvs
    # lost, each reported UNREADABLE while the venv sat in argv[0].
    # Arrange
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (site / "scitex_cards-9.9.9.dist-info").mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    _proc(proc, 9200, f"{venv}/bin/python -m scitex_cards notifyd")

    # Act
    inv = scan(proc_root=proc, self_pid=1, parent_pid=2)

    # Assert
    assert inv.rows[0].venv_version == "9.9.9"


def test_describe_self_reports_the_version_actually_imported():
    # The one row that IS knowable: ask the process, do not read its venv.
    # Arrange
    import scitex_cards

    # Act
    got = describe_self()

    # Assert
    assert got["resolved_version"] == scitex_cards.__version__


def test_describe_self_reports_the_file_actually_imported():
    # Arrange
    import scitex_cards

    # Act
    got = describe_self()

    # Assert
    assert got["resolved_import_path"] == scitex_cards.__file__


# EOF
