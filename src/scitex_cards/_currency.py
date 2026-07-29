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
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _running_over_overlay() -> bool:
    """True when this interpreter's site-packages sits on a layered filesystem.

    Deliberately conservative: this only ever DOWNGRADES a remedy from
    "run pip install -U" to "ask for a rebake", so a false positive costs a
    cautious message while a false negative restores the trap. When the answer
    cannot be determined, say NO and leave the original remedy alone — claiming
    a container we are not in would misdirect a standalone user.
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


#: Appended to the gate's own error when we are demonstrably layered. Kept as a
#: module constant so a test can assert the wording without provoking the gate.
OVERLAY_REMEDY = (
    "\n\n"
    "!! DO NOT RUN `pip install -U` HERE. This process's site-packages is a "
    "LAYERED (overlay) filesystem, so a local install is not a fix — it is a "
    "deferred break.\n"
    "   An overlayfs whiteout masks exactly ONE NAME: the base copy that "
    "happens to be underneath right now. When the base image is next rebuilt, "
    "that whiteout covers a name that no longer exists, the NEW base copy is "
    "masked by nothing, and TWO dist-info directories become visible — "
    "ambiguous metadata, and this rail dies AT BOOT rather than now.\n"
    "   Measured 2026-07-28: two agents on the same version and the same base, "
    "both healthy, had OPPOSITE restart-safety purely because they upgraded at "
    "different times.\n"
    "   CORRECT REMEDY: ask for a BASE REBAKE (sac), then restart onto the new "
    "image. Fleet-managed packages arrive by rebake; they are not pip-installed "
    "into overlays.\n"
    "   If you must unblock yourself RIGHT NOW and accept that the next restart "
    "will need a rebake anyway, say so explicitly when you report it — do not "
    "leave the mortgage undocumented for whoever boots this container next."
)


def check_currency() -> None:
    """Raise if this install is stale or its payload is broken (CURRENCY gate).

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).

    When the gate fires INSIDE an overlay, its own remedy (`pip install -U`) is
    re-raised with :data:`OVERLAY_REMEDY` appended. We do not own that message —
    it is scitex-dev's — so we qualify it rather than rewrite it, and the
    original text is preserved verbatim above the addition.
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return
    try:
        ensure_current("scitex-cards")
    except Exception as exc:  # noqa: BLE001 - re-raised below, never swallowed
        if not _running_over_overlay():
            raise
        raise type(exc)(f"{exc}{OVERLAY_REMEDY}") from exc


__all__ = ["check_currency", "OVERLAY_REMEDY"]

# EOF
