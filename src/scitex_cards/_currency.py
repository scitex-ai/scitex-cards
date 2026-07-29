#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CURRENCY gate — a stale or broken install ERRORS on a bare host, WARNS in an overlay.

Companion to the store-local MIN-CLIENT-VERSION FLOOR (``_min_client_version.py``,
"FLOOR #548") — that gate is the OFFLINE backstop: it enforces THIS process's
version against a floor stamped INTO the store, on every DB connection, with
zero network. THIS gate is the freshness+integrity check: it compares the
INSTALLED distribution against the latest release AND validates its payload
(ambiguous dist-info / missing RECORD files — the incident class this closes),
via scitex-dev's dedicated staleness module. Operator directive: outdated or
broken invocations must ERROR, not warn — same ruling as FLOOR #548, applied
at the two process ENTRY points (CLI, MCP server) rather than at DB-open.

DECOUPLING. scitex-dev is an OPTIONAL dependency (the ``currency`` extra) —
a standalone scitex-cards install without scitex-dev keeps working exactly as
before; this gate is then simply a no-op. Never promote it to a hard
dependency.

BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT
-----------------------------------------------------------
That is the rule this module now implements, and it is the whole shape of the
gate. On a BARE HOST an in-place upgrade genuinely repairs the install, so the
actor CAN remediate and refusing to run is correct — that path is unchanged and
still RAISES. IN AN OVERLAY the actor CANNOT remediate: the package comes from
a READ-ONLY BASE IMAGE they do not control, the only real repair is an operator
REBAKE of that base, and the printed remedy ACTIVELY CREATES the very fault the
gate detects. Blocking there leaves an agent with no working rail AND a harmful
instruction. A gate that cannot be satisfied is a trap, not a gate.

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

The remedy was the disease's vector. So in the overlay case this module does
two things, and the second is not optional:

    (a) it WARNS instead of raising, and
    (b) it SCRUBS every in-place install command out of the emitted text —
        INCLUDING scitex-dev's verbatim message, which is where the harmful
        command actually comes from.

(b) is the correction of an earlier, insufficient fix. 0.17.11 appended a
"do NOT run this" block AFTER scitex-dev's verbatim message and assumed that
was enough. It is not: an agent scanning for an actionable command takes the
FIRST one, and the first one harms. A warning that must be READ IN FULL to be
safe is not a barrier. Preserve the INFORMATION (which version is installed,
which is current); remove the ACTIONABLE HARM.

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
names are invisible in the merged view. Which is why the remedy has to be
qualified HERE, at the point of prescription, rather than left to the reader.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

#: The supported escape hatch, READ OFF THE REAL scitex-dev RATHER THAN INVENTED.
#: ``scitex_dev/staleness.py`` defines ``_ENV_SEVERITY`` with exactly this name
#: and the severity ladder ``("silent", "warn", "error")``; ``silent`` runs
#: nothing and prints nothing. It is named IN the overlay message so nobody has
#: to guess it.
CURRENCY_SEVERITY_ENV = "SCITEX_DEV_CURRENCY_SEVERITY"

#: scitex-dev's other knob (``_ENV_BYPASS``). Named in the message only to say
#: PREFER THE OTHER ONE: it prints a "CURRENCY GATE BYPASSED" banner on stdout
#: regardless of severity, which corrupts this CLI's ``--json`` output — the
#: measured reason ``tests/conftest.py`` uses the severity knob instead.
CURRENCY_BYPASS_ENV = "SCITEX_DEV_NO_CURRENCY_GATE"


def _running_over_overlay() -> bool:
    """True when this interpreter's site-packages sits on a layered filesystem.

    Deliberately conservative: a false positive costs a cautious message and a
    warn-instead-of-raise, while a false negative restores the trap. When the
    answer cannot be determined, say NO and leave the bare-host behaviour alone
    — claiming a container we are not in would misdirect a standalone user AND
    would silently downgrade a gate that should be refusing.
    """
    if os.environ.get("APPTAINER_CONTAINER") or os.environ.get("SINGULARITY_CONTAINER"):
        return True
    try:
        target = str(Path(next(p for p in sys.path if "site-packages" in p)).resolve())
    except (StopIteration, OSError):
        return False
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            mounts = [line.split() for line in fh]
    except OSError:
        return False
    # Longest matching mountpoint wins — "/" would otherwise match everything.
    best_fstype = ""
    best_len = -1
    for parts in mounts:
        if len(parts) < 3:
            continue
        mountpoint, fstype = parts[1], parts[2]
        if target.startswith(mountpoint) and len(mountpoint) > best_len:
            best_len, best_fstype = len(mountpoint), fstype
    return best_fstype == "overlay"


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
# The overlay message                                                          #
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
    testable end-to-end rather than assembled at the raise site.
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


def check_currency() -> None:
    """Raise (bare host) or warn (overlay) when this install is stale or broken.

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).

    BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT:

    * BARE HOST — unchanged, and deliberately so. scitex-dev's exception
      propagates verbatim, install command and all, because there that command
      is a real repair and the actor can run it.
    * OVERLAY — a ``logging.WARNING`` carrying :func:`overlay_warning_text`,
      and NO raise. The actor cannot repair a read-only base; refusing would
      leave them with no working rail and an instruction that harms.
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return
    try:
        ensure_current("scitex-cards")
    except Exception as exc:  # noqa: BLE001 - re-raised or warned below
        if not _running_over_overlay():
            raise
        _LOGGER.warning("%s", overlay_warning_text(str(exc)))


__all__ = [
    "CURRENCY_BYPASS_ENV",
    "CURRENCY_SEVERITY_ENV",
    "INSTALL_COMMAND_REDACTION",
    "OVERLAY_HEADER",
    "OVERLAY_REMEDY",
    "OVERLAY_UPSTREAM_LEAD",
    "UNSCRUBBABLE_NOTICE",
    "check_currency",
    "overlay_warning_text",
    "scrub_install_commands",
]

# EOF
