#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A remedy that clears the condition now and re-creates it later is not a remedy.

MEASURED by scitex-storage 2026-07-28 with a control that discriminates:

    agent            overlay   whiteouts masked      dist-info at next boot
    grant            0.17.10   0.17.5 + 0.17.7       2   -> RAIL DEAD AT BOOT
    scitex-storage   0.17.10   0.17.7 + 0.17.9       1   -> fine

Same version, same base, both healthy when measured, OPPOSITE restart-safety.
The only difference is WHEN each ran `pip install -U`, i.e. which base copy was
underneath at that moment.

Mechanism: inside an apptainer overlay, `pip install -U` writes into the
writable layer and leaves a whiteout masking the base copy beneath it. A
whiteout masks exactly ONE NAME. On the next base rebuild that whiteout covers a
name that no longer exists while the NEW base copy is masked by nothing — two
dist-info become visible, metadata is ambiguous, and the rail dies AT BOOT.

So the currency gate cleared an immediate failure and ARMED a latent one, and
nothing reported it until a base bump. Every agent it nudged into `pip install
-U` became restart-unsafe — which is very likely the source of the
duplicate-dist-info incidents the gate exists to catch.

Neither agent could see it from inside: whiteout names are invisible in the
merged view. That is why the remedy is qualified at the point of prescription.

SCOPE, STATED RATHER THAN IMPLIED: these assert that we DETECT the layered case
and REFUSE to let the unqualified remedy through. They do not simulate an
overlay rebuild — that needs a container and a base bump, which this suite
cannot do. The restart-safety evidence is scitex-storage's control, cited here
rather than claimed as covered.
"""

from __future__ import annotations

import pytest

from scitex_cards import _currency


@pytest.fixture
def gate_error(monkeypatch) -> str:
    """The message a caller actually sees when the gate fires over an overlay."""
    monkeypatch.setattr(_currency, "_running_over_overlay", lambda: True)

    def _boom(_dist):
        raise RuntimeError(
            "scitex-cards 0.17.7 is behind latest 0.17.9 - run: pip install -U scitex-cards"
        )

    import types

    fake = types.ModuleType("scitex_dev.staleness")
    fake.ensure_current = _boom
    monkeypatch.setitem(__import__("sys").modules, "scitex_dev.staleness", fake)

    with pytest.raises(RuntimeError) as excinfo:
        _currency.check_currency()
    return str(excinfo.value)


def test_the_original_remedy_is_preserved_verbatim(gate_error: str):
    """We qualify scitex-dev's message; we do not rewrite or hide it.

    Swallowing another package's diagnostic would make THEIR bug reports
    unreproducible, which is a worse trade than a longer error.
    """
    # Arrange
    original = "is behind latest 0.17.9"

    # Act
    preserved = original in gate_error

    # Assert
    assert preserved


def test_the_layered_case_warns_against_pip_install_dash_u(gate_error: str):
    """The prescribed action is what mortgages the next restart."""
    # Arrange
    needle = "DO NOT RUN `pip install -U` HERE"

    # Act
    present = needle in gate_error

    # Assert
    assert present


def test_the_layered_case_names_the_correct_remedy(gate_error: str):
    """Naming no fix is better than naming a wrong one; naming the right one is better still."""
    # Arrange
    needle = "BASE REBAKE"

    # Act
    present = needle in gate_error

    # Assert
    assert present


def test_the_message_explains_why_one_whiteout_is_not_enough(gate_error: str):
    """Without the mechanism a reader treats the warning as pedantry and proceeds."""
    # Arrange
    needle = "exactly ONE NAME"

    # Act
    present = needle in gate_error

    # Assert
    assert present


@pytest.fixture
def standalone_stale_install(monkeypatch) -> None:
    """A stale install where detection says "this is NOT a layered filesystem".

    Same arrangement as ``gate_error`` with the one difference that decides the
    behaviour under test — the overlay probe answers False.
    """
    monkeypatch.setattr(_currency, "_running_over_overlay", lambda: False)

    def _boom(_dist):
        raise RuntimeError("stale - run: pip install -U scitex-cards")

    import sys as _sys
    import types

    fake = types.ModuleType("scitex_dev.staleness")
    fake.ensure_current = _boom
    monkeypatch.setitem(_sys.modules, "scitex_dev.staleness", fake)


@pytest.fixture
def standalone_gate_error(standalone_stale_install) -> str:
    """The message a caller sees when the gate fires OUTSIDE a container."""
    with pytest.raises(RuntimeError) as excinfo:
        _currency.check_currency()
    return str(excinfo.value)


def test_a_standalone_install_still_fails_the_currency_gate(
    standalone_stale_install,
):
    """Qualifying the remedy must not have turned the gate off where it applies.

    A gate that stops firing outside containers would be the quiet way to make
    this whole check disappear on exactly the installs where `pip install -U`
    is the right answer.
    """
    # Arrange (fixture)
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        _currency.check_currency()


def test_a_standalone_install_is_left_alone(standalone_gate_error: str):
    """A false positive would misdirect a user who is not in a container at all.

    Detection is deliberately conservative — when it cannot tell, it says no and
    leaves scitex-dev's remedy untouched, because `pip install -U` IS correct
    outside a layered filesystem.
    """
    # Arrange (fixture)
    # Act (fixture)
    # Assert
    assert "BASE REBAKE" not in standalone_gate_error


# EOF
