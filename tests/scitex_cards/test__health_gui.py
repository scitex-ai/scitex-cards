#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``gui_resident`` health check — is the board actually up?

WHAT THIS PROTECTS (2026-08-14). Every existing check was green while the
operator's browser showed ``ERR_CONNECTION_REFUSED``: the store was fine, the
notification rail was fine, the agent maintaining the GUI was alive. Nothing
asked the only question that mattered — is anything serving the board? — and
so the outage was invisible until a human noticed.

FOUR STATES, and the reason the check is three-valued: "nothing is listening"
is a FAULT on a host that declared it serves a board (an installed unit) and
merely NOT APPLICABLE on a host that never did. A check that failed on every
container in the fleet would be switched off within a day, which is how a real
alarm gets lost.

Everything here is real: a real socket bound to a real loopback port for
"listening", and a real unit file written to a real tmp ``$XDG_CONFIG_HOME``
for "declared".

One assertion per test (STX-TQ007).
"""

from __future__ import annotations

import socket

import pytest

from scitex_cards._health_gui import check_gui_resident
from scitex_cards._systemd_gui import install_gui_unit


@pytest.fixture
def xdg_home(tmp_path, env):
    """A tmp ``$XDG_CONFIG_HOME``: this host has declared NOTHING yet."""
    root = tmp_path / "cfg"
    env.set("XDG_CONFIG_HOME", str(root))
    return root


@pytest.fixture
def listening_port():
    """A real port with a real listening socket on it."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


@pytest.fixture
def silent_port():
    """A real loopback port with NOTHING listening — a refused connection.

    Yields rather than returns (STX-TQ005): a fixture that opens a socket owns
    a resource for the test's lifetime, and the teardown half of that ownership
    is only expressible after a ``yield``. The socket is closed before the test
    runs — that is the point, the port must be silent — but the fixture still
    holds the port reservation conceptually, so the shape has to be honest.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    yield port


@pytest.fixture
def declared_and_up(xdg_home, listening_port):
    """Unit installed AND something serving: the healthy host."""
    install_gui_unit(port=listening_port)
    return check_gui_resident(port=listening_port)


@pytest.fixture
def declared_and_down(xdg_home, silent_port):
    """Unit installed and NOTHING serving: the 2026-08-14 fault."""
    install_gui_unit(port=silent_port)
    return check_gui_resident(port=silent_port)


@pytest.fixture
def undeclared_but_up(xdg_home, listening_port):
    """Something serving with no unit: a hand-started board, dies at reboot."""
    return check_gui_resident(port=listening_port)


@pytest.fixture
def undeclared_and_down(xdg_home, silent_port):
    """No unit and nothing serving: this host never promised a board."""
    return check_gui_resident(port=silent_port)


# --------------------------------------------------------------------------- #
# declared + up — the healthy host                                            #
# --------------------------------------------------------------------------- #
def test_a_declared_and_serving_board_passes(declared_and_up):
    # Arrange
    # Act
    # Assert
    assert declared_and_up["ok"] is True


def test_a_healthy_board_needs_no_hint(declared_and_up):
    # Arrange
    # Act
    # Assert
    assert declared_and_up["hint"] is None


# --------------------------------------------------------------------------- #
# declared + down — THE FAULT this check exists for                           #
# --------------------------------------------------------------------------- #
def test_a_declared_board_that_is_not_serving_fails(declared_and_down):
    # Arrange
    # A host that promised a board and has none. This is the state that was
    # invisible for hours.
    # Act
    # Assert
    assert declared_and_down["ok"] is False


def test_the_failure_names_what_the_operator_would_see(declared_and_down):
    # Arrange
    # The report has to connect the machine fact to the human symptom, or the
    # person reading it does not know it is about their browser.
    # Act
    # Assert
    assert "ERR_CONNECTION_REFUSED" in declared_and_down["detail"]


def test_the_failure_hint_is_the_command_that_fixes_it(declared_and_down):
    # Arrange
    # Act
    # Assert
    assert "systemctl --user restart scitex-cards-gui.service" in (
        declared_and_down["hint"]
    )


# --------------------------------------------------------------------------- #
# undeclared + up — serving, but only until something goes wrong              #
# --------------------------------------------------------------------------- #
def test_a_hand_started_board_still_passes(undeclared_but_up):
    # Arrange
    # It IS serving. Failing here would be a false alarm.
    # Act
    # Assert
    assert undeclared_but_up["ok"] is True


def test_a_hand_started_board_is_reported_as_fragile(undeclared_but_up):
    # Arrange
    # ...but this is exactly the configuration that produced the outage: the
    # board existed only because a human had started it. A passing check may
    # carry a hint, and this one must.
    # Act
    # Assert
    assert "will not survive" in undeclared_but_up["detail"]


def test_a_hand_started_board_is_told_how_to_become_resident(undeclared_but_up):
    # Arrange
    # Act
    # Assert
    assert "board install-service" in undeclared_but_up["hint"]


# --------------------------------------------------------------------------- #
# undeclared + down — UNKNOWN, and neither a pass nor a fault                 #
# --------------------------------------------------------------------------- #
def test_an_undeclared_absent_board_is_unknown_not_failed(undeclared_and_down):
    # Arrange
    # Most hosts in this fleet run no board. Reporting each of them as broken
    # would train everyone to ignore the check — the opposite of loud.
    # Act
    # Assert
    assert undeclared_and_down["ok"] is None


def test_an_undeclared_absent_board_is_not_silently_fine(undeclared_and_down):
    # Arrange
    # UNKNOWN does not fail the run, but the health contract NAMES every
    # unknown in the summary, so this can never read as a silent pass. What it
    # must not do is claim a measurement it did not make.
    # Act
    # Assert
    assert "has not declared" in undeclared_and_down["detail"]


def test_an_undeclared_absent_board_still_carries_an_actionable_hint(
    undeclared_and_down,
):
    # Arrange
    # The health contract: every failing AND every unknown check carries a hint
    # that says what to do — for an unknown, how to make it measurable.
    # Act
    # Assert
    assert "board install-service" in undeclared_and_down["hint"]


# --------------------------------------------------------------------------- #
# the probe follows the DECLARED bind, not a hardcoded default                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def declared_on_a_custom_port(xdg_home, silent_port):
    """A unit installed on a non-default port, probed with NO explicit port."""
    install_gui_unit(port=silent_port)
    return check_gui_resident()


def test_the_check_probes_the_port_the_unit_declares(
    declared_on_a_custom_port, silent_port
):
    # Arrange
    # A host installed on --port 9051 must not be told about :8051. Reporting a
    # healthy host as down costs exactly as much trust as missing a real
    # outage — and this check exists because trust in the instruments failed.
    # Act
    # Assert
    assert f":{silent_port}" in declared_on_a_custom_port["detail"]


def test_the_declared_custom_port_is_still_judged_a_fault_when_silent(
    declared_on_a_custom_port,
):
    # Arrange
    # Following the declared bind must not soften the verdict: a declared board
    # that is not listening is the fault, whichever port it declared.
    # Act
    # Assert
    assert declared_on_a_custom_port["ok"] is False

# EOF
