#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the board GUI's systemd unit — the resident-board declaration.

WHAT THESE PROTECT (2026-08-14). The operator's browser got a bare
``ERR_CONNECTION_REFUSED`` because nothing had ever been responsible for
starting the board except a human. The unit is the fix, so the properties that
make it actually start — and actually come back — are asserted here rather
than left to whoever next reads the file.

The install helper must WRITE the unit to ``$XDG_CONFIG_HOME/systemd/user``
and NEVER invoke systemctl (host-enablement is operator-gated). Everything
here is real: a real tmp ``$XDG_CONFIG_HOME``, real file content, and — for
the "never runs systemctl" claim — a real executable named ``systemctl``
placed first on ``$PATH`` that records its own invocation. Nothing about the
code under test is patched.

One assertion per test (STX-TQ007).
"""

from __future__ import annotations

import os

import pytest

from scitex_cards import _systemd_gui


def _exec_start_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart=") :]
    raise AssertionError(f"no ExecStart= line in unit:\n{text}")


def _unit_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line[len(key) + 1 :]
    raise AssertionError(f"no {key}= line in unit:\n{text}")


@pytest.fixture
def xdg_home(tmp_path, env):
    """Point ``$XDG_CONFIG_HOME`` at a real tmp dir for this test."""
    root = tmp_path / "cfg"
    env.set("XDG_CONFIG_HOME", str(root))
    return root


@pytest.fixture
def systemctl_tripwire(tmp_path, env):
    """A REAL ``systemctl`` first on ``$PATH`` that records being called.

    The claim under test is behavioural — "this helper never shells out to
    systemctl" — so the sentinel is a real executable, not a patched module.
    Yields the marker path; the marker exists iff systemctl was invoked.
    """
    bin_dir = tmp_path / "tripwire-bin"
    bin_dir.mkdir()
    marker = tmp_path / "systemctl-was-called"
    fake = bin_dir / "systemctl"
    fake.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n', encoding="utf-8")
    fake.chmod(0o755)
    env.set("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return marker


# --------------------------------------------------------------------------- #
# the unit must come back — the whole reason it exists                        #
# --------------------------------------------------------------------------- #
def test_unit_restarts_always_not_only_on_failure():
    # Arrange
    # A board that exits 0 (OOM sweep, stray `gui stop`, closed parent
    # terminal) leaves the operator at ERR_CONNECTION_REFUSED. Its ABSENCE is
    # the fault, however it went away — so `on-failure` is not enough.
    # Act
    unit = _systemd_gui.render_gui_unit()
    # Assert
    assert _unit_field(unit, "Restart") == "always"


def test_unit_is_wanted_by_default_target():
    # Arrange
    # Without this the unit exists but never starts at login — a declaration
    # nobody honours, which is the state that produced the incident.
    # Act
    unit = _systemd_gui.render_gui_unit()
    # Assert
    assert "WantedBy=default.target" in unit


def test_unit_declares_type_simple():
    # Arrange
    # Act
    unit = _systemd_gui.render_gui_unit()
    # Assert
    assert _unit_field(unit, "Type") == "simple"


# --------------------------------------------------------------------------- #
# ExecStart: absolute, and carrying the arguments that make it work           #
# --------------------------------------------------------------------------- #
def test_exec_start_program_path_is_absolute():
    # Arrange
    # systemd does not use the login PATH; a bare command dies at 203/EXEC.
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert os.path.isabs(exec_start.split()[0])


def test_exec_start_program_exists_on_disk():
    # Arrange
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert os.path.isfile(exec_start.split()[0])


def test_exec_start_program_is_executable():
    # Arrange
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert os.access(exec_start.split()[0], os.X_OK)


def test_exec_start_runs_the_gui_serve_verb():
    # Arrange
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert exec_start.split()[1:3] == ["gui", "serve"]


def test_exec_start_binds_loopback_explicitly():
    # Arrange
    # The operator RULED that the board is served per-host on loopback and the
    # DATA travels via the per-host Postgres sync — one VPN-reachable board
    # would be a single point of failure and would vanish with the network.
    # The unit must never widen the bind.
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert "--host 127.0.0.1" in exec_start


def test_exec_start_names_the_port_explicitly():
    # Arrange
    # The unit file is where an operator reads what this host serves; `gui
    # serve` alone answers that with "whatever the installed version defaults
    # to".
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert "--port 8051" in exec_start


def test_exec_start_takes_over_a_stale_board():
    # Arrange
    # `gui serve` refuses to start when the pidfile names a live process. For a
    # human that refusal is right; for the resident service it is a trap — one
    # hand-started board or one leftover pidfile and the unit fails on every
    # restart forever, reproducing the outage with extra steps.
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit())
    # Assert
    assert "--force" in exec_start


def test_a_custom_port_reaches_exec_start():
    # Arrange
    # Act
    exec_start = _exec_start_line(_systemd_gui.render_gui_unit(port=9051))
    # Assert
    assert "--port 9051" in exec_start


def test_a_custom_port_reaches_the_description():
    # Arrange
    # `systemctl --user status` is where an operator reads what this host
    # serves; a description naming the wrong port is worse than none.
    # Act
    unit = _systemd_gui.render_gui_unit(port=9051)
    # Assert
    assert "9051" in _unit_field(unit, "Description")


def test_the_unit_carries_no_environment_line():
    # Arrange
    # systemd does not source the login shell, so a unit depending on
    # $SCITEX_CARDS_DB from ~/.bashrc would start, refuse the unconfigured
    # store and crash-loop. The store resolves from ~/.scitex/cards/config.json
    # with no environment at all — and the store has exactly ONE identity, so a
    # unit file must not become a second place it is declared.
    # Act
    unit = _systemd_gui.render_gui_unit()
    # Assert
    assert "Environment=" not in unit


def test_the_unit_launches_the_cards_console_script():
    # Arrange
    # Act
    spec = _systemd_gui.gui_unit_spec()
    # Assert
    assert spec.console_script == "scitex-cards"


# --------------------------------------------------------------------------- #
# install: writes the file, never runs systemctl                              #
# --------------------------------------------------------------------------- #
def test_install_writes_the_unit_file_on_disk(xdg_home):
    # Arrange
    # Act
    result = _systemd_gui.install_gui_unit()
    # Assert
    assert os.path.isfile(result["path"])


def test_installed_unit_lands_under_the_xdg_config_home(xdg_home):
    # Arrange
    # Act
    result = _systemd_gui.install_gui_unit()
    # Assert
    assert str(xdg_home) in result["path"]


def test_installed_unit_is_named_for_the_package(xdg_home):
    # Arrange
    # `systemctl --user list-units 'scitex-cards*'` must show the store, the
    # peer sync and the board together.
    # Act
    result = _systemd_gui.install_gui_unit()
    # Assert
    assert result["path"].endswith("scitex-cards-gui.service")


def test_installed_unit_body_carries_the_resolved_exec_start(xdg_home):
    # Arrange
    # Act
    result = _systemd_gui.install_gui_unit()
    written = _systemd_gui.gui_unit_path().read_text(encoding="utf-8")
    # Assert
    assert _exec_start_line(written) == result["exec_start"]


def test_install_returns_the_enable_now_command(xdg_home):
    # Arrange
    # Enabling is the OPERATOR's call; the tool's job is to print the command.
    # Act
    result = _systemd_gui.install_gui_unit()
    # Assert
    assert (
        "systemctl --user enable --now scitex-cards-gui.service"
        in result["enable_commands"]
    )


def test_install_never_invokes_systemctl(xdg_home, systemctl_tripwire):
    # Arrange
    # Host-enablement is a deliberate human gate.
    # Act
    _systemd_gui.install_gui_unit()
    # Assert
    assert not systemctl_tripwire.exists()


def test_second_install_without_force_keeps_a_hand_edited_unit(xdg_home):
    # Arrange
    marker = "# hand-edited by the operator\n"
    _systemd_gui.install_gui_unit()
    _systemd_gui.gui_unit_path().write_text(marker, encoding="utf-8")
    # Act
    _systemd_gui.install_gui_unit()
    # Assert
    assert _systemd_gui.gui_unit_path().read_text(encoding="utf-8") == marker


def test_second_install_without_force_reports_nothing_written(xdg_home):
    # Arrange
    _systemd_gui.install_gui_unit()
    # Act
    result = _systemd_gui.install_gui_unit()
    # Assert
    assert result["written"] is False


def test_forced_install_reports_the_unit_was_rewritten(xdg_home):
    # Arrange
    _systemd_gui.install_gui_unit()
    _systemd_gui.gui_unit_path().write_text("# stale\n", encoding="utf-8")
    # Act
    result = _systemd_gui.install_gui_unit(force=True)
    # Assert
    assert result["written"] is True


def test_forced_install_restores_the_exec_start_line(xdg_home):
    # Arrange
    _systemd_gui.install_gui_unit()
    _systemd_gui.gui_unit_path().write_text("# stale\n", encoding="utf-8")
    # Act
    _systemd_gui.install_gui_unit(force=True)
    written = _systemd_gui.gui_unit_path().read_text(encoding="utf-8")
    # Assert
    assert "gui serve" in _exec_start_line(written)


# --------------------------------------------------------------------------- #
# the declaration predicate the health check reads                            #
# --------------------------------------------------------------------------- #
def test_no_unit_means_this_host_never_declared_a_board(xdg_home):
    # Arrange
    # Act
    # Assert
    assert _systemd_gui.gui_unit_is_installed() is False


def test_an_installed_unit_is_a_declaration(xdg_home):
    # Arrange
    _systemd_gui.install_gui_unit()
    # Act
    # Assert
    assert _systemd_gui.gui_unit_is_installed() is True


# --------------------------------------------------------------------------- #
# reading the declared bind back — so the probe asks about the right port     #
# --------------------------------------------------------------------------- #
def test_no_unit_declares_no_bind(xdg_home):
    # Arrange
    # Act
    # Assert
    # `None`, never a guess: an absent unit is not a claim about a port.
    assert _systemd_gui.installed_gui_bind() is None


def test_the_declared_bind_is_read_back_from_the_unit(xdg_home):
    # Arrange
    # A host installed on a custom port must be PROBED on that port. Assuming
    # the default would manufacture an outage on a healthy host.
    _systemd_gui.install_gui_unit(port=9051)
    # Act
    bind = _systemd_gui.installed_gui_bind()
    # Assert
    assert bind == ("127.0.0.1", 9051)


def test_a_hand_edited_unit_with_a_bad_port_does_not_crash_the_read(xdg_home):
    # Arrange
    # The health report's job is to stay answerable. A unit with a non-numeric
    # port will not start either — that is the health check's finding to make,
    # not a traceback's.
    _systemd_gui.install_gui_unit()
    path = _systemd_gui.gui_unit_path()
    path.write_text(
        path.read_text(encoding="utf-8").replace("--port 8051", "--port eight"),
        encoding="utf-8",
    )
    # Act
    bind = _systemd_gui.installed_gui_bind()
    # Assert
    assert bind == ("127.0.0.1", 8051)

# EOF
