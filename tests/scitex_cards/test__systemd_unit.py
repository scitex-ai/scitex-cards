#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the SHARED systemd unit machinery (:mod:`scitex_cards._systemd_unit`).

The notify daemon and the board GUI both render and install their units
through this module. Its one hard-won rule — never write a unit whose
``ExecStart`` cannot possibly run — is asserted here once, on behalf of both.

REGRESSION (203/EXEC): a unit shipped with a BARE ``ExecStart=scitex-todo
notifyd``. systemd does not use the user's login ``PATH`` and the console
script lives in a venv, so the unit died at ``status=203/EXEC`` and had to be
hand-patched before it would start. Installing a unit that is GUARANTEED not
to start is worse than refusing: it is a service the operator believes exists.

Everything here is real. The unresolvable case is produced by asking for a
console script that genuinely is not installed, with ``$PATH`` genuinely
pointing at an empty directory — no patching of ``sys.executable``, no mocked
``shutil.which``.

One assertion per test (STX-TQ007).
"""

from __future__ import annotations

import pytest

from scitex_cards._systemd_unit import (
    ExecStartUnresolved,
    UnitSpec,
    install_unit,
    render_unit,
    resolve_exec_start,
    unit_path,
)

#: A console script that is not installed anywhere, by construction.
_ABSENT_SCRIPT = "scitex-cards-no-such-console-script"

_ABSENT_SPEC = UnitSpec(
    unit_name="scitex-cards-test-absent.service",
    description="a unit whose program does not exist",
    console_script=_ABSENT_SCRIPT,
    args=("serve",),
)


@pytest.fixture
def nothing_on_path(tmp_path, env):
    """A real, EMPTY ``$PATH`` directory and a tmp ``$XDG_CONFIG_HOME``."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env.set("PATH", str(empty))
    env.set("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return empty


@pytest.fixture
def aborted_install(nothing_on_path):
    """Attempt an install that must abort; yield the path it would have used."""
    with pytest.raises(ExecStartUnresolved):
        install_unit(_ABSENT_SPEC)
    return unit_path(_ABSENT_SPEC)


def _unresolved_error() -> ExecStartUnresolved:
    """Return the error raised for an absent console script."""
    try:
        resolve_exec_start(_ABSENT_SPEC)
    except ExecStartUnresolved as exc:
        return exc
    raise AssertionError("resolve_exec_start() did not fail")


def test_an_absent_console_script_raises(nothing_on_path):
    # Arrange
    # Act
    # Assert
    with pytest.raises(ExecStartUnresolved):
        resolve_exec_start(_ABSENT_SPEC)


def test_the_error_names_the_absolute_exec_start_requirement(nothing_on_path):
    # Arrange
    # Act
    error = _unresolved_error()
    # Assert
    # The message must name the PROBLEM, not just fail.
    assert "ABSOLUTE ExecStart" in str(error)


def test_the_error_names_the_pip_install_remedy(nothing_on_path):
    # Arrange
    # Act
    error = _unresolved_error()
    # Assert
    # ...and the remedy.
    assert "pip install" in str(error)


def test_the_error_names_the_script_it_looked_for(nothing_on_path):
    # Arrange
    # Act
    error = _unresolved_error()
    # Assert
    assert _ABSENT_SCRIPT in str(error)


def test_install_aborts_when_exec_start_is_unresolvable(nothing_on_path):
    # Arrange
    # Act
    # Assert
    with pytest.raises(ExecStartUnresolved):
        install_unit(_ABSENT_SPEC)


def test_an_aborted_install_leaves_no_half_written_unit(aborted_install):
    # Arrange
    # Resolution happens BEFORE the filesystem is touched, precisely so a
    # failure cannot leave a partial unit behind for systemd to find.
    # Act
    # Assert
    assert not aborted_install.exists()


# --------------------------------------------------------------------------- #
# the spec is what varies; assert it actually reaches the rendered unit       #
# --------------------------------------------------------------------------- #
def test_the_spec_restart_policy_reaches_the_unit():
    # Arrange
    # The notify daemon wants `on-failure`, the board wants `always`; a
    # template that ignored the spec would silently give one of them the
    # other's policy.
    spec = UnitSpec(
        unit_name="x.service",
        description="x",
        console_script="x",
        restart="always",
    )
    # Act
    unit = render_unit(spec, exec_start="/bin/true")
    # Assert
    assert "Restart=always" in unit


def test_the_spec_description_reaches_the_unit():
    # Arrange
    spec = UnitSpec(
        unit_name="x.service",
        description="a distinctive description",
        console_script="x",
    )
    # Act
    unit = render_unit(spec, exec_start="/bin/true")
    # Assert
    assert "Description=a distinctive description" in unit


def test_the_spec_args_reach_the_exec_start():
    # Arrange
    spec = UnitSpec(
        unit_name="x.service",
        description="x",
        console_script="x",
        args=("gui", "serve", "--port", "8051"),
    )
    # Act
    unit = render_unit(spec, exec_start="/bin/true gui serve --port 8051")
    # Assert
    assert "ExecStart=/bin/true gui serve --port 8051" in unit


def test_the_spec_unit_name_decides_the_install_path(nothing_on_path):
    # Arrange
    spec = UnitSpec(
        unit_name="scitex-cards-distinctive.service",
        description="x",
        console_script="x",
    )
    # Act
    path = unit_path(spec)
    # Assert
    assert path.name == "scitex-cards-distinctive.service"

# EOF
