#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every word the CURRENCY gate emits, and the scrubber that keeps it safe.

Split out of ``_currency.py`` (which crossed the repo's file-size limit once the
overlay rail and the Python-rail sibling both landed on it). The split line is
STATE, not topic: everything here is a constant or a pure function over text,
and NOTHING here is ever ``monkeypatch.setattr``-ed. Everything with module
state or a test patch point — ``_running_over_overlay``, the warn-once
sentinels, ``check_currency``, ``warn_if_stale_once`` — stays in ``_currency``,
because patching a re-exported name does not change what the DEFINING module
reads, and a split that moved one would silently neuter the tests that patch it.

THE GATE WAS MANUFACTURING THE FAULT IT DETECTS (measured, scitex-ui 2026-07-29)
-------------------------------------------------------------------------------
The chain, reproduced live inside a container:

    1. the base image ships scitex-cards N; PyPI moves to N+1
    2. the gate REFUSES to run the CLI and prints an in-place upgrade command
    3. the agent runs it — inside a container that installs into the AGENT'S
       OVERLAY, not into the read-only base
    4. overlay N+1 alongside base N = TWO dist-info directories
    5. which is PRECISELY the ambiguous-metadata integrity failure this gate
       exists to detect

The remedy was the disease's vector. So the overlay message does two things,
and the second is not optional:

    (a) the gate WARNS instead of raising (that half lives in ``_currency``), and
    (b) the emitted text is SCRUBBED of every in-place install command —
        INCLUDING scitex-dev's verbatim message, which is where the harmful
        command actually comes from.

(b) is the correction of an earlier, insufficient fix. 0.17.11 appended a
"do NOT run this" block AFTER scitex-dev's verbatim message and assumed that was
enough. It is not: an agent scanning for an actionable command takes the FIRST
one, and the first one harms. A warning that must be READ IN FULL to be safe is
not a barrier. Preserve the INFORMATION (which version is installed, which is
current); remove the ACTIONABLE HARM.

WHY AN IN-PLACE INSTALL IN AN OVERLAY BREAKS LATER, NOT NOW
------------------------------------------------------------
Measured by scitex-storage 2026-07-28 with a discriminating control:

    agent            overlay   whiteouts masked      dist-info at next boot
    grant            0.17.10   0.17.5 + 0.17.7       2   -> RAIL DEAD AT BOOT
    scitex-storage   0.17.10   0.17.7 + 0.17.9       1   -> fine

Same version, same base, both healthy at the time of measurement, OPPOSITE
restart-safety. The only difference is WHEN each ran the upgrade — i.e. which
base copy was underneath at that moment.

The mechanism: an in-place upgrade inside an apptainer overlay writes the new
distribution into the WRITABLE layer and leaves a whiteout masking the copy in
the base underneath. An overlayfs whiteout masks exactly ONE NAME. When the base
image is next rebuilt, that whiteout covers a name that no longer exists while
the NEW base copy is masked by nothing — so two dist-info directories become
visible, metadata turns ambiguous, and the rail dies AT BOOT.

Neither agent could have seen this from inside their own container: whiteout
names are invisible in the merged view. Which is why the remedy is qualified at
the point of prescription rather than left to the reader.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# The escape hatch — real env var names, read off the installed scitex-dev      #
# --------------------------------------------------------------------------- #
#: READ OFF THE REAL scitex-dev RATHER THAN INVENTED. ``scitex_dev/staleness.py``
#: defines ``_ENV_SEVERITY`` with exactly this name and the severity ladder
#: ``("silent", "warn", "error")``; ``silent`` raises nothing and prints nothing.
#: It is named IN the overlay message so nobody has to guess it.
CURRENCY_SEVERITY_ENV = "SCITEX_DEV_CURRENCY_SEVERITY"

#: scitex-dev's other knob (``_ENV_BYPASS``). Named in the message only to say
#: PREFER THE OTHER ONE: it prints a "CURRENCY GATE BYPASSED" banner on stdout
#: regardless of severity, which corrupts this CLI's ``--json`` output — the
#: measured reason ``tests/conftest.py`` uses the severity knob instead.
CURRENCY_BYPASS_ENV = "SCITEX_DEV_NO_CURRENCY_GATE"


# --------------------------------------------------------------------------- #
# Scrubbing third-party text — the mechanical half of decision (b)             #
# --------------------------------------------------------------------------- #
#: What replaces a removed command. Deliberately NOT a command, and deliberately
#: not a paraphrase of one either ("upgrade the package" is still an instruction
#: an agent can act on wrongly).
INSTALL_COMMAND_REDACTION = "[in-place install command REMOVED - see REMEDY below]"

#: Matches an in-place install command, plus the "- run:" lead-in scitex-dev
#: puts in front of it, so the redaction reads as a sentence rather than as a
#: dangling imperative. Stops at a double space or an opening paren so the
#: trailing "(suppress: ...)" hint — INFORMATION, not a command — survives.
_IN_PLACE_INSTALL_RE = re.compile(
    r"(?:[-–—]\s*)?"
    r"(?:\brun\b\s*:?\s*)?"
    r"(?:\bsudo\s+)?"
    r"(?:\bpython[\d.]*\s+-m\s+)?"
    r"\b(?:uv\s+pip|uv|pip3|pip|pipx|conda|mamba|poetry|easy_install)\s+"
    r"(?:install|add|upgrade|sync|reinstall)"
    r"[^\n;]*?(?=\s{2,}|\s*\(|$)",
    re.IGNORECASE,
)

#: A flag can outlive its verb across a line wrap or a reworded upstream
#: message; on its own it is still an actionable instruction to an agent.
_ORPHAN_INSTALL_FLAG_RE = re.compile(r"\s*--force-reinstall\b", re.IGNORECASE)

#: The FAIL-SAFE detector, deliberately BROADER than the remover: it answers
#: "does anything install-shaped survive?", not "where exactly is it?". If this
#: still fires after scrubbing we do not emit the text at all — an upstream
#: message we cannot make safe is withheld, never printed and apologised for.
_INSTALL_COMMAND_SMELL_RE = re.compile(
    r"--force-reinstall"
    r"|\b(?:uv\s+pip|pip3?|pipx|conda|mamba|poetry|easy_install)\b"
    r"[^\n]{0,32}?\b(?:install|add|upgrade|reinstall|sync)\b",
    re.IGNORECASE,
)

#: Used when scrubbing cannot be verified safe. Losing the message is a real
#: cost; printing a command that breaks the container is a larger one.
UNSCRUBBABLE_NOTICE = (
    "(scitex-dev's message contained an install command that could not be "
    "safely removed, so the message is withheld rather than printed here. "
    "Read it in full on a bare host, where acting on it is safe.)"
)


def scrub_install_commands(text: str) -> str:
    """Return ``text`` with every in-place install command removed.

    THIS IS THE POINT OF THE WHOLE CHANGE, so it is a function and not a
    docstring warning: the harmful command originates in scitex-dev's message,
    which we pass through verbatim. Appending "do not run the above" after it
    was tried (0.17.11) and is insufficient — an agent scanning for something
    actionable takes the FIRST command it finds.
    """
    scrubbed = _IN_PLACE_INSTALL_RE.sub(INSTALL_COMMAND_REDACTION, text)
    scrubbed = _ORPHAN_INSTALL_FLAG_RE.sub(" " + INSTALL_COMMAND_REDACTION, scrubbed)
    if _INSTALL_COMMAND_SMELL_RE.search(scrubbed):
        return UNSCRUBBABLE_NOTICE
    return scrubbed


# --------------------------------------------------------------------------- #
# The overlay message — what the CLI/MCP gate emits inside a container         #
# --------------------------------------------------------------------------- #
#: Kept as module constants so tests can assert the wording without provoking
#: the gate, and so a reviewer can read the emitted text in one place.
OVERLAY_HEADER = (
    "scitex-cards CURRENCY: this install is not current (or its payload is "
    "broken), AND this process runs over a LAYERED (overlay) filesystem. The "
    "gate WARNS here rather than refusing, and NO INSTALL COMMAND APPEARS "
    "ANYWHERE BELOW - including in the quoted upstream message. That is "
    "deliberate: in this container the obvious command is the one that breaks "
    "you."
)

OVERLAY_UPSTREAM_LEAD = (
    "WHAT scitex-dev MEASURED (quoted, with in-place install commands removed):"
)

OVERLAY_REMEDY = (
    "WHY NOTHING HERE TELLS YOU TO INSTALL ANYTHING:\n"
    "   WHERE THIS PACKAGE COMES FROM: a READ-ONLY BASE IMAGE that this agent "
    "does not control. Nothing you install here can replace it. An install "
    "here only stacks a SECOND copy on top of it in the writable overlay "
    "layer.\n"
    "   WHAT THAT ACTUALLY DOES: it writes a second distribution into the "
    "writable layer and records a whiteout masking the base copy underneath. "
    "An overlayfs whiteout masks exactly ONE NAME. When the base image is next "
    "rebuilt, that whiteout covers a name that no longer exists while the NEW "
    "base copy is masked by nothing, so TWO dist-info directories become "
    "visible at once. That is a DUPLICATE INSTALL, it BREAKS METADATA "
    "RESOLUTION, and it is the same integrity failure this very gate exists to "
    "detect. The obvious remedy manufactures the fault.\n"
    "   Measured 2026-07-28: two agents on the same version and the same base, "
    "both healthy at the time, had OPPOSITE restart-safety purely because they "
    "upgraded at different moments.\n"
    "   THE REPAIR IS AN OPERATOR REBAKE: ask the operator to REBAKE THE BASE "
    "IMAGE with the current scitex-cards, then restart this agent onto the new "
    "base. Report the versions quoted above when you ask. Fleet-managed "
    "packages arrive by rebake; they do not arrive by installing into an "
    "overlay.\n"
    f"   ESCAPE HATCH, while you wait for that rebake: set "
    f"{CURRENCY_SEVERITY_ENV}=silent in this container's environment. It "
    f"changes nothing on disk and it is the knob scitex-dev itself documents. "
    f"({CURRENCY_BYPASS_ENV}=1 also exists, but it prints a bypass banner on "
    f"stdout regardless of severity, which corrupts --json output - prefer the "
    f"severity knob.)\n"
    "   WHY THIS WARNED INSTEAD OF REFUSING: BLOCK WHERE THE ACTOR CAN "
    "REMEDIATE, WARN WHERE THEY CANNOT. On a bare host an in-place upgrade "
    "genuinely repairs the install, so the gate still REFUSES there. Here it "
    "cannot be satisfied by anything you can do, and a gate that cannot be "
    "satisfied is a trap rather than a gate."
)


def overlay_warning_text(detail: str) -> str:
    """Compose the full text emitted when the gate fires INSIDE an overlay.

    The upstream message is quoted for its INFORMATION (installed version,
    latest version, which integrity half failed) and scrubbed of its ACTIONABLE
    HARM. Composition lives here, in one function, so the emitted text is
    testable end-to-end rather than assembled at the call site.
    """
    return "\n".join(
        (
            OVERLAY_HEADER,
            "",
            OVERLAY_UPSTREAM_LEAD,
            f"   {scrub_install_commands(detail)}",
            "",
            OVERLAY_REMEDY,
        )
    )


# --------------------------------------------------------------------------- #
# The Python-rail message — what warn_if_stale_once() logs                     #
# --------------------------------------------------------------------------- #
#: The remedy WE author, and it is deliberately NOT an in-place pip upgrade.
#: Inside an apptainer overlay an in-place upgrade leaves a whiteout masking
#: exactly ONE dist-info name; on the next base rebake that whiteout covers a
#: name that no longer exists, the new base copy is masked by nothing, TWO
#: dist-info directories appear, and the rail is dead AT BOOT — before any
#: command runs. (Measured: two agents, same version, same base, both healthy,
#: OPPOSITE restart-safety, differing only in WHEN they upgraded.) The text
#: below therefore never spells an in-place upgrade command, not even to
#: forbid one: this constant is asserted free of it, so a later edit cannot
#: smuggle the bad remedy back in as an aside.
STALE_REMEDY = (
    "REMEDY - REBAKE THE CONTAINER BASE IMAGE with the current scitex-cards, "
    "then restart this agent onto the new base. Do NOT upgrade this package "
    "in place inside a running apptainer overlay: the overlay records a "
    "whiteout masking exactly ONE dist-info name, and on the next base rebake "
    "that whiteout covers a name that no longer exists - the new base copy is "
    "masked by nothing, TWO dist-info directories appear, and the rail is "
    "dead AT BOOT. (Measured: two agents, same version, same base, both "
    "healthy, OPPOSITE restart-safety, differing only in WHEN they upgraded.) "
    "Any in-place upgrade command in the scitex-dev message above is a "
    "bare-host remedy and does not apply inside a container overlay."
)

#: Names the SIBLING rail EXPLICITLY. The reader is an agent whose Python call
#: just succeeded and who therefore has no reason to suspect anything is
#: wrong — the whole job of this text is to tell them WHICH rail is down and
#: that it will stay silent about it.
#:
#: BOTH console-script names are spelled out, and that is not redundancy. The
#: command that actually refused in the 2026-07-29 incident was ``scitex-cards
#: list-tasks`` — the LEGACY script, which ``pyproject.toml`` still installs
#: alongside ``scitex-cards`` (both resolve to ``scitex_cards._cli:main``) and
#: which much of the fleet still types. A reader who types ``scitex-cards`` may
#: not recognise a warning phrased only in terms of ``scitex-cards``, which
#: would defeat the single purpose of this text. Name whichever form they use.
_STALE_HEADER = (
    "scitex-cards CURRENCY: this Python call SUCCEEDED, but the CLI/MCP rail "
    "for this same package is currently REFUSING. Both console scripts are "
    "affected - 'scitex-cards list-tasks' AND its still-installed legacy "
    "alias 'scitex-cards list-tasks' are the same program and will BOTH FAIL "
    "until this install is fixed, as will every other scitex-cards CLI "
    "command and the scitex-cards MCP server, while Python calls such as "
    "dm_send keep working. Nothing on this rail will error, so this warning "
    "is the only signal you get."
)


def stale_warning_text(detail: str | None) -> str:
    """Compose the warn-once text: which rail is down, why, and the remedy."""
    return "\n".join(
        (
            _STALE_HEADER,
            "",
            f"scitex-dev reports: {detail}",
            "",
            STALE_REMEDY,
        )
    )


__all__ = [
    "CURRENCY_BYPASS_ENV",
    "CURRENCY_SEVERITY_ENV",
    "INSTALL_COMMAND_REDACTION",
    "OVERLAY_HEADER",
    "OVERLAY_REMEDY",
    "OVERLAY_UPSTREAM_LEAD",
    "STALE_REMEDY",
    "UNSCRUBBABLE_NOTICE",
    "overlay_warning_text",
    "scrub_install_commands",
    "stale_warning_text",
]

# EOF
