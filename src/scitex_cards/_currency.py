#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CURRENCY gate — an outdated or broken install ERRORS, never warns.

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

THE GATE'S OWN REMEDY IS UNSAFE INSIDE A CONTAINER, AND THIS MODULE SAYS SO.
Measured by scitex-storage 2026-07-28 with a discriminating control:

    agent            overlay   whiteouts masked      dist-info at next boot
    grant            0.17.10   0.17.5 + 0.17.7       2   -> RAIL DEAD AT BOOT
    scitex-storage   0.17.10   0.17.7 + 0.17.9       1   -> fine

Same version, same base, both healthy at the time of measurement, OPPOSITE
restart-safety. The only difference is WHEN each ran the upgrade — i.e. which
base copy was underneath at that moment.

The mechanism: `pip install -U` inside an apptainer overlay writes the new
distribution into the WRITABLE layer and leaves a whiteout masking the copy in
the base underneath. An overlayfs whiteout masks exactly ONE NAME. When the base
image is next rebuilt, that whiteout covers a name that no longer exists while
the NEW base copy is masked by nothing — so two dist-info directories become
visible, metadata turns ambiguous, and the rail dies AT BOOT.

So the gate CLEARS the immediate condition and ARMS a latent one, and nothing
reports it until a base bump. Every agent it nudged into `pip install -U` became
restart-unsafe. That is very likely the source of the duplicate-dist-info
incidents this gate exists to catch — the control above is what makes that a
finding rather than a suspicion.

Neither agent could have seen this from inside their own container: whiteout
names are invisible in the merged view. Which is why the remedy has to be
qualified HERE, at the point of prescription, rather than left to the reader.

BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT.
------------------------------------------------------------
That is the rule this module now implements, and it is the reason the overlay
path WARNS while the bare-host path still RAISES.

On a BARE HOST the gate's own remedy genuinely repairs the condition: the
actor owns the site-packages the gate is complaining about, one upgrade fixes
it, and refusing to run until they do it is a correct, satisfiable demand.
That path is unchanged — it still ERRORS, exactly as the operator directed.

IN AN OVERLAY THE ACTOR CANNOT REPAIR ANYTHING. The distribution comes from a
READ-ONLY BASE IMAGE they do not control; the only real repair is an operator
REBAKE of that base. Blocking there left an agent with no working rail AND an
instruction that, if followed, MANUFACTURES the very fault this gate exists to
detect. Measured chain (scitex-ui, reproduced live 2026-07-29):

    1. base image ships scitex-cards N, PyPI moves to N+1
    2. this gate REFUSES and prints "run: pip install -U scitex-cards"
    3. the agent complies -> the install lands in their OVERLAY, not the base
    4. overlay N+1 alongside base N = TWO dist-info directories
    5. ambiguous metadata = precisely the integrity failure this gate detects

The remedy was the disease's vector. A gate that cannot be satisfied is a
trap, not a gate.

NO IN-PLACE INSTALL COMMAND SURVIVES THE OVERLAY PATH, FROM ANY SOURCE.
-----------------------------------------------------------------------
0.17.11 tried appending a do-NOT block after scitex-dev's verbatim message.
That was NOT enough, and assuming it was is the mistake being corrected here:
an agent scanning for an actionable command takes the FIRST one, and the first
one harms. So on the overlay path the passthrough is SCRUBBED
(:func:`_scrub_install_commands`) and, if scrubbing cannot be shown to have
worked, the message is WITHHELD and distilled to its version facts
(:func:`_withheld_text`). The INFORMATION survives; the ACTIONABLE HARM does
not. A test asserts this against a literal blocklist of its own, because a
rule that must be remembered is forgotten exactly when it matters.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

#: The escape hatches THAT ACTUALLY EXIST. Both names were grepped out of
#: scitex-dev's own ``staleness.py`` (``_ENV_SEVERITY`` / ``_ENV_BYPASS``)
#: rather than invented, because a message that names a knob which does not
#: exist is worse than a message that names none: the reader spends their
#: time proving OUR text wrong instead of getting unblocked.
#:
#: ``severity=silent`` is the one this module recommends. ``tests/conftest.py``
#: already relies on it for the same reason: it runs the check, raises nothing
#: and prints nothing. The harder ``NO_CURRENCY_GATE`` bypass is named too but
#: NOT recommended — it prints a "CURRENCY GATE BYPASSED" banner to STDOUT
#: regardless of severity (a known scitex-dev bug), which corrupts ``--json``.
_ENV_SEVERITY = "SCITEX_DEV_CURRENCY_SEVERITY"
_ENV_BYPASS = "SCITEX_DEV_NO_CURRENCY_GATE"


def _running_over_overlay() -> bool:
    """True when this interpreter's site-packages sits on a layered filesystem.

    Deliberately conservative: this only ever DOWNGRADES a refusal to a
    warning and an in-place upgrade to "ask for a rebake", so a false positive
    costs a cautious message while a false negative restores the trap. When
    the answer cannot be determined, say NO and leave the original behaviour
    alone — claiming a container we are not in would both misdirect a
    standalone user and silently disarm a gate that is correct for them.
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
# Scrubbing the passthrough — no in-place install command, from any source     #
# --------------------------------------------------------------------------- #
#: A package-manager invocation, matched TOKEN BY TOKEN rather than
#: to-end-of-line. Stopping at the first token that is neither a flag nor a
#: requirement specifier is what lets the redaction remove the COMMAND while
#: leaving everything around it — scitex-dev appends its own suppress hint on
#: the same line, and eating that would destroy information for no safety gain.
_PACKAGE_MANAGER = (
    r"(?:python[0-9.]*\s+-m\s+pip[0-9.]*|pip[0-9.]*|uv\s+pip|uv|pipx|poetry"
    r"|conda|mamba|easy_install)"
)
_INSTALL_VERB = r"(?:install|reinstall|upgrade|update|add|sync)"
#: An argument to such a command: a FLAG, or a requirement specifier. The
#: lookahead is what stops the match running off into the prose that follows —
#: a requirement token must contain a hyphen, dot, underscore or digit
#: somewhere, so ``scitex-cards`` is consumed while ``or``, ``ask`` and ``then``
#: are not. Over-redaction would destroy the version facts we are trying to
#: preserve, and the manager+verb are already gone by then anyway.
_COMMAND_TOKEN = (
    r"(?:-{1,2}[A-Za-z][\w\-.]*"  # flags: -U, --upgrade, --force-reinstall
    r"|[A-Za-z](?=[\w.\-]*[-._\d])[\w.\-]*"  # requirements: scitex-cards, foo2
    r"(?:\[[\w,\-]+\])?(?:[=<>!~]=?[\w.*]+)?)"  # extras and version pins
)
_INSTALL_COMMAND_RE = re.compile(
    rf"(?i)\b{_PACKAGE_MANAGER}\s+{_INSTALL_VERB}(?:\s+{_COMMAND_TOKEN})*"
)

#: A bare reinstall/upgrade flag that survived because its verb was phrased in
#: prose ("... then re-run it with --force-reinstall").
_INSTALL_FLAG_RE = re.compile(
    r"(?i)(?<![\w-])--(?:force-reinstall|upgrade|no-deps|ignore-installed)(?![\w-])"
)

#: THE INDEPENDENT SECOND OPINION, and deliberately dumber than the scrubber.
#: If the NAME of any package manager still appears after redaction, we do not
#: reason about whether it is a command — we withhold the whole message. The
#: scrubber can be out-thought by a phrasing nobody anticipated; this cannot,
#: because it does not try to be clever. Over-triggering costs a distilled
#: message; under-triggering costs an agent's container.
_PACKAGE_MANAGER_MENTION_RE = re.compile(
    r"(?i)(?<![\w-])(?:pip[0-9.]*|uv|pipx|poetry|conda|mamba|easy_install)(?![\w-])"
)

_VERSION_RE = re.compile(r"\b\d+\.\d+(?:\.\w+)*\b")

_REDACTED = "<in-place install command REDACTED - unsafe here, see below>"


def _withheld_text(original: str) -> str:
    """Distil a message we could not redact down to its version facts.

    Reached only when :data:`_PACKAGE_MANAGER_MENTION_RE` still fires after
    scrubbing, i.e. when we cannot PROVE the emitted text is command-free.
    Passing it through anyway would be gambling an agent's container on our
    own regex; dropping it entirely would lose the one thing the reader needs.
    So we keep the versions and say plainly what was removed and why.
    """
    versions = _VERSION_RE.findall(original)
    named = ", ".join(versions) if versions else "(none it stated numerically)"
    return (
        "scitex-dev's message is WITHHELD here. It prescribes an in-place "
        "install in a form this module could not redact with certainty, and "
        "an in-place install is exactly what breaks this container (below). "
        f"Versions it named, in the order it named them: {named} - for a "
        "stale install scitex-dev states the INSTALLED version first and the "
        "LATEST second. To read it in full, run the check on a BARE HOST, "
        "where its remedy is the correct one."
    )


def _scrub_install_commands(message: str) -> str:
    """Return ``message`` with every in-place install command removed.

    TWO DETECTORS, ON PURPOSE, and they are not the same detector twice. The
    first (:data:`_INSTALL_COMMAND_RE`) removes what it recognises. The second
    (:data:`_PACKAGE_MANAGER_MENTION_RE`) then asks a much weaker question —
    "is any package manager still NAMED?" — and any yes routes to
    :func:`_withheld_text` instead of to the reader. A scrubber that silently
    passes through what it failed to match is the same class of bug as the one
    this module exists to fix: a safety step that reports success by default.
    """
    scrubbed = _INSTALL_COMMAND_RE.sub(_REDACTED, message)
    scrubbed = _INSTALL_FLAG_RE.sub(_REDACTED, scrubbed)
    if _PACKAGE_MANAGER_MENTION_RE.search(scrubbed):
        return _withheld_text(message)
    return scrubbed


# --------------------------------------------------------------------------- #
# The overlay message                                                         #
# --------------------------------------------------------------------------- #
#: The body of the overlay WARNING. Kept as a module constant so a test can
#: assert its wording — and its FREEDOM from install commands — without
#: provoking the gate. It names no command, not even to forbid one: a reader
#: scanning for something actionable takes the first command-shaped string
#: they find, and "DO NOT RUN <command>" is command-shaped.
OVERLAY_REMEDY = (
    "WHY THIS IS A WARNING AND NOT A REFUSAL: this container's scitex-cards "
    "comes from a READ-ONLY BASE IMAGE that you do not control. You cannot "
    "repair it from in here, so refusing to run would leave you with no "
    "working rail and nothing you could do about it.\n"
    "\n"
    "DO NOT UPGRADE THIS PACKAGE IN PLACE. An in-place upgrade lands in your "
    "writable OVERLAY, on top of the read-only base copy, which leaves TWO "
    "dist-info directories for one distribution. That is ambiguous metadata - "
    "the precise integrity failure this gate exists to detect - so the "
    "'fix' MANUFACTURES the fault. It also breaks later rather than now: the "
    "overlay records a whiteout masking exactly ONE dist-info NAME, and when "
    "the base is next rebaked that whiteout covers a name that no longer "
    "exists while the NEW base copy is masked by nothing, and this rail dies "
    "AT BOOT. (Measured 2026-07-28: two agents, same version, same base, both "
    "healthy, OPPOSITE restart-safety, differing only in WHEN they upgraded.)\n"
    "\n"
    "THE REPAIR IS AN OPERATOR REBAKE OF THE BASE IMAGE with a current "
    "scitex-cards, followed by restarting this agent onto the new base. "
    "Fleet-managed packages arrive by rebake. Report the staleness; do not "
    "try to fix it from inside the container.\n"
    "\n"
    "MEANWHILE, EVERYTHING STILL WORKS - this warning does not stop the "
    f"command you ran. To silence it until the rebake lands, set "
    f"{_ENV_SEVERITY}=silent in this agent's environment; that runs the check "
    f"and reports nothing. ({_ENV_BYPASS}=1 also exists but is NOT "
    "recommended: it prints a bypass banner on STDOUT even at silent "
    "severity, which corrupts --json output.)"
)

_OVERLAY_HEADER = (
    "scitex-cards CURRENCY WARNING (not fatal): this install is stale or its "
    "payload is broken, and this process is running over a LAYERED (overlay) "
    "filesystem."
)


def overlay_warning_text(detail: str) -> str:
    """Compose the full text emitted on the overlay path.

    Exposed so a test can assert on exactly what a reader sees, including the
    scrubbed passthrough, rather than on the pieces separately.
    """
    return "\n\n".join(
        (
            _OVERLAY_HEADER,
            f"scitex-dev reports: {_scrub_install_commands(detail)}",
            OVERLAY_REMEDY,
        )
    )


def _emit_overlay_warning(detail: str) -> None:
    """Write the overlay warning to STDERR.

    STDERR, not stdout, and not the logging module. Not stdout because the
    CLI's ``--json`` output must stay parseable. Not logging because whether a
    ``logging.WARNING`` is ever SEEN depends on a configuration this module
    does not own — and an unseen warning on a path that no longer blocks is
    the same as no warning at all, which would turn "warn instead of block"
    into "silently do nothing".
    """
    print(overlay_warning_text(detail), file=sys.stderr, flush=True)


def check_currency() -> None:
    """Raise if this install is stale or broken AND the caller can fix it.

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).

    BLOCK WHERE THE ACTOR CAN REMEDIATE, WARN WHERE THEY CANNOT:

    * BARE HOST -> RAISES, and scitex-dev's message propagates VERBATIM. The
      actor owns this site-packages, its remedy genuinely repairs, and the
      refusal is satisfiable. Unchanged; do not weaken this path.
    * OVERLAY -> WARNS on stderr and RETURNS. The package comes from a
      read-only base the actor cannot write, so no local action satisfies the
      gate; the only repair is an operator rebake. Blocking here would leave
      an agent with no rail AND an instruction that creates the duplicate
      dist-info this gate detects. The emitted text is scrubbed free of
      in-place install commands from EVERY source, including scitex-dev's own
      passthrough — see :func:`_scrub_install_commands`.
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return
    try:
        ensure_current("scitex-cards")
    except Exception as exc:  # noqa: BLE001 - re-raised or reported, never swallowed
        if not _running_over_overlay():
            raise
        _emit_overlay_warning(str(exc))


__all__ = ["check_currency", "OVERLAY_REMEDY", "overlay_warning_text"]

# EOF
