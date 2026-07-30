#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the systemd unit TEMPLATE + operator-gated install helper (slice 2).

The install helper must WRITE the unit file to ``$XDG_CONFIG_HOME/systemd/user``
and NEVER invoke systemctl (host-enablement is operator-gated). We assert by
pointing ``$XDG_CONFIG_HOME`` at a tmp dir + verifying the file content, and by
confirming no ``systemctl`` subprocess is ever spawned (a real
``subprocess.run`` spy installed as a sentinel, NOT a mock of our code under
test — the helper simply has no subprocess call to intercept).

REGRESSION (203/EXEC): the shipped template used a BARE
``ExecStart=scitex-todo notifyd``. systemd does not use the user's login PATH,
and the console script lives in a venv, so the unit died at ``status=203/EXEC``
and had to be hand-patched before it would start. ExecStart must be an
ABSOLUTE, EXISTING, EXECUTABLE path — asserted through the MECHANISM (resolve
from the running interpreter / $PATH), never against a hard-coded machine path.

One assertion per test (STX-TQ007); the shared environment setup lives in the
``xdg_home`` / ``broken_console_script_env`` fixtures below.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_cards._delivery import _systemd


def _exec_start_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart=") :]
    raise AssertionError(f"no ExecStart= line in unit:\n{text}")


@pytest.fixture
def xdg_home(tmp_path, env):
    """Point ``$XDG_CONFIG_HOME`` at a real tmp dir for this test."""
    root = tmp_path / "cfg"
    env.set("XDG_CONFIG_HOME", str(root))
    return root


@pytest.fixture
def broken_console_script_env(tmp_path, env, monkeypatch):
    """A real interpreter path with an EMPTY bin dir and an empty ``$PATH``.

    Nothing to find beside the interpreter, and nothing on $PATH either, so
    the console script is genuinely unresolvable.
    """
    empty_bin = tmp_path / "empty-venv" / "bin"
    empty_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(empty_bin / "python"))
    env.set("PATH", str(tmp_path / "nowhere"))
    env.set("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return empty_bin


@pytest.fixture
def subprocess_run_calls(monkeypatch):
    """Record every ``subprocess.run`` call so we can PROVE none was made."""
    calls: list = []
    real_run = subprocess.run

    def _spy_run(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)
    return calls


def _resolve_exec_start_error():
    """Return the ``ExecStartUnresolved`` raised by ``resolve_exec_start``."""
    try:
        _systemd.resolve_exec_start()
    except _systemd.ExecStartUnresolved as exc:
        return exc
    raise AssertionError("resolve_exec_start() did not fail")


def _install_then_hand_edit(marker: str):
    """Install the unit, hand-edit it, install again WITHOUT force.

    Returns ``(first_result, second_result, target_path)``.
    """
    first = _systemd.install_unit()
    target = _systemd.unit_path()
    target.write_text(marker, encoding="utf-8")
    second = _systemd.install_unit()
    return first, second, target


# --------------------------------------------------------------------------- #
# the rendered unit's required conventions                                    #
# --------------------------------------------------------------------------- #
def test_rendered_unit_declares_type_simple():
    # Arrange
    # Act
    text = _systemd.render_unit()
    # Assert
    assert "Type=simple" in text


def test_rendered_unit_restarts_on_failure():
    # Arrange
    # Act
    text = _systemd.render_unit()
    # Assert
    assert "Restart=on-failure" in text


def test_rendered_unit_is_wanted_by_default_target():
    # Arrange
    # Act
    text = _systemd.render_unit()
    # Assert
    assert "WantedBy=default.target" in text


def test_rendered_unit_exec_start_runs_the_notifyd_verb():
    # Arrange
    text = _systemd.render_unit()
    # Act
    exec_start = _exec_start_line(text)
    # Assert
    assert exec_start.endswith(" notifyd")


# --------------------------------------------------------------------------- #
# 203/EXEC regression — ExecStart must be an ABSOLUTE, EXISTING path          #
# --------------------------------------------------------------------------- #
def test_exec_start_program_path_is_absolute():
    # Arrange
    # the exact defect was a bare command systemd cannot find.
    exec_start = _systemd.resolve_exec_start()
    # Act
    program = exec_start.split()[0]
    # Assert
    assert Path(program).is_absolute(), f"ExecStart program {program!r} is relative"


def test_exec_start_program_exists_on_disk():
    # Arrange
    exec_start = _systemd.resolve_exec_start()
    # Act
    program = exec_start.split()[0]
    # Assert
    assert Path(program).is_file(), f"ExecStart program {program!r} does not exist"


def test_exec_start_program_is_executable():
    # Arrange
    exec_start = _systemd.resolve_exec_start()
    # Act
    program = exec_start.split()[0]
    # Assert
    assert os.access(Path(program), os.X_OK), f"{program!r} is not executable"


def test_exec_start_program_is_the_console_script():
    # Arrange
    exec_start = _systemd.resolve_exec_start()
    # Act
    program = exec_start.split()[0]
    # Assert
    assert Path(program).name == "scitex-todo"


def test_exec_start_arguments_are_only_the_notifyd_verb():
    # Arrange
    exec_start = _systemd.resolve_exec_start()
    # Act
    arguments = exec_start.split()[1:]
    # Assert
    assert arguments == ["notifyd"]


def test_rendered_unit_exec_start_program_is_absolute():
    # Arrange
    # the absoluteness must survive into the FILE, not just the helper.
    text = _systemd.render_unit()
    # Act
    program = _exec_start_line(text).split()[0]
    # Assert
    assert Path(program).is_absolute()


def test_rendered_unit_exec_start_program_exists_on_disk():
    # Arrange
    text = _systemd.render_unit()
    # Act
    program = _exec_start_line(text).split()[0]
    # Assert
    assert Path(program).is_file()


def test_console_script_prefers_the_running_interpreters_bin_dir():
    # Arrange
    # a venv install must point the unit at THAT venv.
    candidate = Path(sys.executable).parent / "scitex-todo"
    if not (candidate.is_file() and os.access(candidate, os.X_OK)):
        pytest.skip("no console script beside this interpreter to prefer")
    # Act
    resolved = _systemd.console_script_path()
    # Assert
    assert resolved == candidate


def test_unresolvable_console_script_raises_exec_start_unresolved(
    broken_console_script_env,
):
    # Arrange
    # never write a unit that is GUARANTEED to fail at 203/EXEC.
    # Act
    # Assert
    with pytest.raises(_systemd.ExecStartUnresolved):
        _systemd.resolve_exec_start()


def test_unresolvable_error_names_the_absolute_exec_start_requirement(
    broken_console_script_env,
):
    # Arrange
    # Act
    error = _resolve_exec_start_error()
    # Assert
    # the message must name the problem.
    assert "ABSOLUTE ExecStart" in str(error)


def test_unresolvable_error_names_the_pip_install_remedy(
    broken_console_script_env,
):
    # Arrange
    # Act
    error = _resolve_exec_start_error()
    # Assert
    # ...and the remedy.
    assert "pip install" in str(error)


def test_install_unit_aborts_when_exec_start_is_unresolvable(
    broken_console_script_env,
):
    # Arrange
    # Act
    # Assert
    with pytest.raises(_systemd.ExecStartUnresolved):
        _systemd.install_unit()


def test_aborted_install_leaves_no_half_written_unit(broken_console_script_env):
    # Arrange
    with contextlib.suppress(_systemd.ExecStartUnresolved):
        _systemd.install_unit()
    # Act
    exists = _systemd.unit_path().exists()
    # Assert
    # no half-written, unstartable unit is left behind.
    assert not exists


# --------------------------------------------------------------------------- #
# install helper — operator-gated, never shells out                          #
# --------------------------------------------------------------------------- #
def test_install_unit_reports_the_unit_was_written(xdg_home):
    # Arrange
    # Act
    result = _systemd.install_unit()
    # Assert
    assert result["written"] is True


def test_install_unit_reports_the_xdg_config_home_path(xdg_home):
    # Arrange
    target = xdg_home / "systemd" / "user" / "scitex-todo-notifyd.service"
    # Act
    result = _systemd.install_unit()
    # Assert
    assert result["path"] == str(target)


def test_install_unit_creates_the_unit_file_on_disk(xdg_home):
    # Arrange
    target = xdg_home / "systemd" / "user" / "scitex-todo-notifyd.service"
    # Act
    _systemd.install_unit()
    # Assert
    assert target.exists()


def test_installed_unit_body_carries_the_resolved_exec_start(xdg_home):
    # Arrange
    result = _systemd.install_unit()
    # Act
    body = Path(result["path"]).read_text(encoding="utf-8")
    # Assert
    assert f"ExecStart={result['exec_start']}" in body


def test_installed_unit_exec_start_program_is_absolute(xdg_home):
    # Arrange
    result = _systemd.install_unit()
    # Act
    program = result["exec_start"].split()[0]
    # Assert
    assert Path(program).is_absolute()


def test_installed_unit_body_sets_restart_on_failure(xdg_home):
    # Arrange
    result = _systemd.install_unit()
    # Act
    body = Path(result["path"]).read_text(encoding="utf-8")
    # Assert
    assert "Restart=on-failure" in body


def test_install_unit_returns_the_daemon_reload_command(xdg_home):
    # Arrange
    # Act
    result = _systemd.install_unit()
    # Assert
    # the enable commands are RETURNED for the operator.
    assert "systemctl --user daemon-reload" in result["enable_commands"]


def test_install_unit_returns_the_enable_now_command(xdg_home):
    # Arrange
    # Act
    result = _systemd.install_unit()
    # Assert
    assert "enable --now scitex-todo-notifyd.service" in result["enable_commands"]


def test_install_unit_never_spawns_a_systemctl_subprocess(
    xdg_home, subprocess_run_calls
):
    # Arrange
    # Act
    _systemd.install_unit()
    # Assert
    # host-enablement is operator-gated, never executed here.
    assert subprocess_run_calls == []


def test_first_install_without_force_writes_the_unit(xdg_home):
    # Arrange
    first, _second, _target = _install_then_hand_edit("MANUALLY EDITED — keep me\n")
    # Act
    written = first["written"]
    # Assert
    assert written is True


def test_second_install_without_force_writes_nothing(xdg_home):
    # Arrange
    _first, second, _target = _install_then_hand_edit("MANUALLY EDITED — keep me\n")
    # Act
    written = second["written"]
    # Assert
    assert written is False


def test_second_install_reports_the_unit_already_existed(xdg_home):
    # Arrange
    _first, second, _target = _install_then_hand_edit("MANUALLY EDITED — keep me\n")
    # Act
    existed = second["existed"]
    # Assert
    assert existed is True


def test_install_without_force_keeps_hand_edited_content(xdg_home):
    # Arrange
    _first, _second, target = _install_then_hand_edit("MANUALLY EDITED — keep me\n")
    # Act
    body = target.read_text(encoding="utf-8")
    # Assert
    # we did not clobber the operator's edit.
    assert "MANUALLY EDITED" in body


def test_forced_install_reports_the_unit_was_rewritten(xdg_home):
    # Arrange
    _systemd.install_unit()
    _systemd.unit_path().write_text("stale\n", encoding="utf-8")
    # Act
    result = _systemd.install_unit(force=True)
    # Assert
    assert result["written"] is True


def test_forced_install_rewrites_the_exec_start_line(xdg_home):
    # Arrange
    _systemd.install_unit()
    target = _systemd.unit_path()
    target.write_text("stale\n", encoding="utf-8")
    result = _systemd.install_unit(force=True)
    # Act
    body = target.read_text(encoding="utf-8")
    # Assert
    assert _exec_start_line(body) == result["exec_start"]


def test_forced_install_exec_start_program_exists_on_disk(xdg_home):
    # Arrange
    _systemd.install_unit()
    target = _systemd.unit_path()
    target.write_text("stale\n", encoding="utf-8")
    _systemd.install_unit(force=True)
    # Act
    program = _exec_start_line(target.read_text(encoding="utf-8")).split()[0]
    # Assert
    assert Path(program).is_file()


def test_explicit_exec_start_is_echoed_in_the_result(xdg_home):
    # Arrange
    # an explicit override still works (the operator stays in control).
    override = "/opt/venv/bin/scitex-todo notifyd"
    # Act
    result = _systemd.install_unit(exec_start=override)
    # Assert
    assert result["exec_start"] == override


def test_explicit_exec_start_is_written_into_the_unit(xdg_home):
    # Arrange
    override = "/opt/venv/bin/scitex-todo notifyd"
    result = _systemd.install_unit(exec_start=override)
    # Act
    body = Path(result["path"]).read_text(encoding="utf-8")
    # Assert
    assert f"ExecStart={override}" in body


# EOF
