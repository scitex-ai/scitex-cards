#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The gate was MANUFACTURING the fault it detects. These tests are the barrier.

THE CHAIN, reproduced live inside a container (scitex-ui, 2026-07-29):

    1. the base image ships scitex-cards N; PyPI moves to N+1
    2. the gate REFUSES to run the CLI and prints an in-place upgrade command
    3. the agent runs exactly that — inside a container it installs into the
       AGENT'S OVERLAY, not into the read-only base
    4. overlay N+1 alongside base N = TWO dist-info directories
    5. which is PRECISELY the ambiguous-metadata integrity failure the gate
       exists to detect

The remedy was the disease's vector. Two things follow, and the second is the
one this file is really for.

(A) BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT. On a bare host
    an in-place upgrade genuinely repairs, so refusing is right and the gate
    still RAISES there. In an overlay the actor cannot repair — the package
    comes from a read-only base they do not control and the only real fix is an
    operator rebake — so refusing leaves them with no working rail AND a
    harmful instruction. A gate that cannot be satisfied is a trap, not a gate.

(B) NO IN-PLACE INSTALL COMMAND IN THE OVERLAY OUTPUT, FROM ANY SOURCE. 0.17.11
    appended a do-NOT block AFTER scitex-dev's verbatim message and assumed
    that was enough. It was not, and assuming it was is the mistake being
    corrected here: an agent scanning for an actionable command takes the FIRST
    one, and the first one harms. So the verbatim passthrough is SCRUBBED — the
    INFORMATION (installed version, latest version) is preserved, the ACTIONABLE
    HARM is removed.

WHY THE BARRIER IS MECHANICAL. A rule that must be REMEMBERED is forgotten
exactly when it matters — the 0.17.11 fix is the proof. So
``test_the_overlay_text_carries_no_in_place_install_command_from_any_source``
scans the ENTIRE emitted text against an INDEPENDENTLY AUTHORED pattern list,
and its positive control proves that list can actually see a command. Neither
the docstring above nor the module's own regex is load-bearing; that pair is.

SCOPE, STATED RATHER THAN IMPLIED: these assert the layered case is DETECTED,
WARNED rather than refused, and emitted CLEAN. They do not simulate an overlay
rebuild — that needs a container and a base bump, which this suite cannot do.
The restart-safety evidence is scitex-storage's 2026-07-28 control (two agents,
same version, same base, both healthy, OPPOSITE restart-safety, differing only
in WHEN they upgraded), cited here rather than claimed as covered.
"""

from __future__ import annotations

import logging
import re

import pytest

from scitex_cards import _currency

_CURRENCY_LOGGER = "scitex_cards._currency"

#: The REAL freshness message, copied verbatim out of the installed
#: ``scitex_dev/staleness.py`` (``_freshness_message`` + ``_SUPPRESS_HINT``).
#: Copied rather than imported so a scitex-dev that changes its wording cannot
#: silently change what this suite believes it is defending against.
_UPSTREAM_STALE_MESSAGE = (
    "scitex-cards 0.17.11 is behind latest 0.17.12 - run: "
    "pip install -U scitex-cards  (suppress: SCITEX_DEV_NO_CURRENCY_GATE=1 "
    "severity: currency_severity knob)"
)

#: The REAL ambiguous-dist-info message — the integrity half, whose remedy is a
#: FORCED REINSTALL rather than a plain upgrade. Included because it is the
#: exact failure the overlay upgrade CREATES, so it is the message an agent is
#: most likely to be staring at when they reach for the command that caused it.
_UPSTREAM_AMBIGUOUS_MESSAGE = (
    "scitex-cards: 2 dist-info directories claim it in /opt/venv/site-packages "
    "(scitex_cards-0.17.11.dist-info, scitex_cards-0.17.12.dist-info) - run: "
    "pip install -U --force-reinstall scitex-cards  (suppress: "
    "SCITEX_DEV_NO_CURRENCY_GATE=1 severity: currency_severity knob)"
)

#: INDEPENDENTLY AUTHORED, and deliberately NOT imported from ``_currency``.
#: A barrier that reuses the implementation's own pattern goes green the moment
#: that pattern is weakened — which is the precise failure mode it exists to
#: catch. These are written from the outside, from what an agent scanning for
#: something to run would actually recognise as runnable.
_FORBIDDEN_COMMAND_PATTERNS = (
    r"\bpip\s+install\b",
    r"\bpip3\s+install\b",
    r"\bpython[\d.]*\s+-m\s+pip\b",
    r"\buv\s+pip\s+install\b",
    r"\buv\s+add\b",
    r"\bpipx\s+install\b",
    r"\bconda\s+install\b",
    r"\bmamba\s+install\b",
    r"\bpoetry\s+add\b",
    r"\beasy_install\b",
    r"--force-reinstall\b",
    r"--upgrade\b",
)


def _forbidden_hits(text: str) -> list[str]:
    """Every forbidden command pattern that matches ``text``."""
    return [
        pattern
        for pattern in _FORBIDDEN_COMMAND_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    ]


def _gate_args(message: str, *, overlay: bool) -> dict:
    """``check_currency`` arguments for ONE rail with ONE upstream message.

    Both collaborators are supplied as ARGUMENTS through the seams the verb
    obtains them from — no ``sys.modules`` publishing and no module attribute
    rewriting, so nothing here depends on a real scitex-dev install, on the
    network, or on the filesystem the runner sits on (PA-306 §3).

    ``overlay`` is the ONLY variable between the two rails, which is what lets
    the bare-host tests below claim the branch is the thing under test.
    """

    class _StalenessError(RuntimeError):
        pass

    def _ensure_current(dist_name):
        raise _StalenessError(message)

    return {
        "is_overlay": lambda: overlay,
        "load_ensure_current": lambda: _ensure_current,
    }


def _emitted_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _CURRENCY_LOGGER and record.levelno == logging.WARNING
    ]


@pytest.fixture
def overlay_warning(caplog) -> str:
    """The text an agent INSIDE a container actually sees when the gate fires."""
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)
    _currency.check_currency(**_gate_args(_UPSTREAM_STALE_MESSAGE, overlay=True))
    return "\n".join(_emitted_warnings(caplog))


@pytest.fixture
def overlay_warning_for_ambiguous_dist_info(caplog) -> str:
    """Same, for the integrity half — the ``--force-reinstall`` remedy."""
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)
    _currency.check_currency(**_gate_args(_UPSTREAM_AMBIGUOUS_MESSAGE, overlay=True))
    return "\n".join(_emitted_warnings(caplog))


# --------------------------------------------------------------------------- #
# (1) THE LOAD-BEARING BARRIER — no runnable install command, from any source  #
# --------------------------------------------------------------------------- #
def test_the_overlay_text_carries_no_in_place_install_command_from_any_source(
    overlay_warning: str,
):
    """THE ONE THAT MATTERS. Scans the WHOLE emitted text, including the
    verbatim scitex-dev passthrough, which is where the harmful command
    actually comes from. Appending "do not run the above" (0.17.11) does not
    satisfy this and is not meant to: an agent takes the FIRST command it
    finds."""
    # Arrange
    text = overlay_warning

    # Act
    hits = _forbidden_hits(text)

    # Assert
    assert hits == []


def test_the_forbidden_patterns_detect_a_command_in_the_real_upstream_message():
    """POSITIVE CONTROL for the barrier above. "No command found" and "the
    detector is broken" produce identical output, so the detector is pointed at
    an unscrubbed upstream message that is known to contain one."""
    # Arrange
    text = _UPSTREAM_STALE_MESSAGE

    # Act
    hits = _forbidden_hits(text)

    # Assert
    assert hits != []


def test_the_overlay_text_for_ambiguous_dist_info_carries_no_install_command(
    overlay_warning_for_ambiguous_dist_info: str,
):
    """The integrity half prescribes a FORCED REINSTALL, and that is the message
    an agent sees once the overlay upgrade has already broken them — the worst
    possible moment to hand them the command that did it."""
    # Arrange
    text = overlay_warning_for_ambiguous_dist_info

    # Act
    hits = _forbidden_hits(text)

    # Assert
    assert hits == []


def test_the_forbidden_patterns_detect_the_forced_reinstall_remedy():
    """POSITIVE CONTROL for the integrity-half barrier."""
    # Arrange
    text = _UPSTREAM_AMBIGUOUS_MESSAGE

    # Act
    hits = _forbidden_hits(text)

    # Assert
    assert hits != []


# --------------------------------------------------------------------------- #
# (2) IN AN OVERLAY THE GATE WARNS — it does not block                        #
# --------------------------------------------------------------------------- #
def test_the_overlay_case_does_not_raise():
    """A gate that cannot be satisfied is a trap, not a gate. Blocking here
    leaves the agent with no working rail AND a harmful instruction; reaching
    the assert at all is the did-not-raise evidence."""
    # Arrange
    gate = _gate_args(_UPSTREAM_STALE_MESSAGE, overlay=True)

    # Act
    returned = _currency.check_currency(**gate)

    # Assert
    assert returned is None


def test_the_overlay_case_emits_exactly_one_warning(caplog):
    """Warning is not the same as staying quiet: the agent still has to learn
    their install is stale, they just must not be told to "fix" it here."""
    # Arrange
    gate = _gate_args(_UPSTREAM_STALE_MESSAGE, overlay=True)
    caplog.set_level(logging.WARNING, logger=_CURRENCY_LOGGER)

    # Act
    _currency.check_currency(**gate)

    # Assert
    assert len(_emitted_warnings(caplog)) == 1


# --------------------------------------------------------------------------- #
# (3) THE OVERLAY MESSAGE SAYS WHAT IS TRUE AND WHAT TO DO INSTEAD            #
# --------------------------------------------------------------------------- #
def test_the_overlay_message_names_the_read_only_base_image(overlay_warning: str):
    """Without WHERE the package comes from, "do not install" reads as pedantry
    and the reader proceeds anyway."""
    # Arrange
    needle = "READ-ONLY BASE IMAGE"

    # Act
    present = needle in overlay_warning

    # Assert
    assert present


def test_the_overlay_message_says_an_install_here_duplicates_the_package(
    overlay_warning: str,
):
    # Arrange
    needle = "DUPLICATE INSTALL"

    # Act
    present = needle in overlay_warning

    # Assert
    assert present


def test_the_overlay_message_says_the_duplicate_breaks_metadata_resolution(
    overlay_warning: str,
):
    """This is the causal link between the "remedy" and the fault the gate is
    detecting; without it the two read as unrelated facts."""
    # Arrange
    needle = "BREAKS METADATA RESOLUTION"

    # Act
    present = needle in overlay_warning

    # Assert
    assert present


def test_the_overlay_message_names_the_operator_rebake_as_the_repair(
    overlay_warning: str,
):
    """Naming no fix is better than naming a wrong one; naming the right one is
    better still — and the right one is not something this agent can run."""
    # Arrange
    needle = "REBAKE THE BASE IMAGE"

    # Act
    present = needle in overlay_warning

    # Assert
    assert present


def test_the_overlay_message_names_the_escape_hatch_env_var(overlay_warning: str):
    """Spelled out so nobody has to guess it. The name is read off the INSTALLED
    scitex-dev (``staleness._ENV_SEVERITY``), not invented."""
    # Arrange
    needle = "SCITEX_DEV_CURRENCY_SEVERITY=silent"

    # Act
    present = needle in overlay_warning

    # Assert
    assert present


def test_the_overlay_message_explains_why_one_whiteout_is_not_enough(
    overlay_warning: str,
):
    """Without the mechanism a reader treats the warning as pedantry and
    proceeds."""
    # Arrange
    needle = "exactly ONE NAME"

    # Act
    present = needle in overlay_warning

    # Assert
    assert present


def test_the_overlay_message_preserves_the_installed_and_latest_versions(
    overlay_warning: str,
):
    """Scrubbing removes the ACTIONABLE HARM, not the INFORMATION. The operator
    asked for a rebake needs to know which version is in the base and which is
    current, and that fact lives only in scitex-dev's message."""
    # Arrange
    facts = "0.17.11 is behind latest 0.17.12"

    # Act
    present = facts in overlay_warning

    # Assert
    assert present


# --------------------------------------------------------------------------- #
# (4) ON A BARE HOST THE GATE STILL RAISES — that path is NOT weakened        #
# --------------------------------------------------------------------------- #
@pytest.fixture
def bare_host_gate() -> dict:
    """A stale install where the overlay probe answers False.

    Identical arrangement to ``overlay_warning`` with the single difference
    that decides the behaviour under test, so the branch is the only variable.
    """
    return _gate_args(_UPSTREAM_STALE_MESSAGE, overlay=False)


@pytest.fixture
def bare_host_error(bare_host_gate: dict) -> str:
    """The message a caller sees when the gate fires OUTSIDE a container."""
    with pytest.raises(RuntimeError) as excinfo:
        _currency.check_currency(**bare_host_gate)
    return str(excinfo.value)


def test_a_bare_host_install_still_fails_the_currency_gate(bare_host_gate: dict):
    """Warning in overlays must not have turned the gate off where it applies.
    A gate that stops firing on bare hosts would be the quiet way to delete
    this whole check on exactly the installs where an upgrade IS the repair."""
    # Arrange (fixture)
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        _currency.check_currency(**bare_host_gate)


def test_a_bare_host_error_still_carries_the_upstream_upgrade_command(
    bare_host_error: str,
):
    """The scrub is scoped to the overlay rail ON PURPOSE. On a bare host that
    command IS the repair, so removing it there would replace one broken remedy
    with another."""
    # Arrange
    command = "pip install -U scitex-cards"

    # Act
    present = command in bare_host_error

    # Assert
    assert present


def test_a_bare_host_error_is_not_reframed_as_a_container_problem(
    bare_host_error: str,
):
    """A false positive would misdirect a user who is not in a container at
    all — telling them to ask for a rebake of an image that does not exist."""
    # Arrange
    needle = "REBAKE THE BASE IMAGE"

    # Act
    present = needle in bare_host_error

    # Assert
    assert not present


# --------------------------------------------------------------------------- #
# (5) THE SCRUBBER FAILS SAFE — an unscrubbable message is WITHHELD           #
# --------------------------------------------------------------------------- #
def test_an_install_command_the_remover_misses_suppresses_the_whole_quote():
    """The remover is narrow and the detector is broad, so they CAN disagree —
    a reworded upstream message is exactly when they would. When they do, the
    text is withheld rather than printed: losing an upstream message costs a
    reader some context, printing a command that breaks their container costs
    them the container.

    The input below is a real disagreement, not a contrived one: an option
    between the tool and its verb defeats the remover's adjacency rule while
    the detector's window still spans it."""
    # Arrange
    misses_the_remover = "pip --no-cache-dir install scitex-cards"

    # Act
    scrubbed = _currency.scrub_install_commands(misses_the_remover)

    # Assert
    assert scrubbed == _currency.UNSCRUBBABLE_NOTICE


# EOF
