#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deprecated import alias: ``scitex_cards._stale_active`` -> :mod:`scitex_cards._stale.active`.

Grouped into the ``_stale/`` subpackage 2026-08-15. This shim keeps the old
top-level name importable for external callers, and aliases it to the VERY SAME
module object rather than re-exporting its names -- a second module execution
would fork module-level state (caches, thresholds read once from the
environment), which is the hazard :mod:`scitex_todo`'s package-level shim
documents and avoids the same way.

In-repo callers already use the new path; this exists for anything outside the
repo that does not. Deleting it is a separate, deliberate decision.

THE ONE RULE THE OTHER FOUR SHIMS COPY, and the reason so it survives copying:
THE REASSIGNMENT MUST BE THE LAST STATEMENT THIS MODULE EXECUTES. Once
``sys.modules[__name__]`` points elsewhere, the original module object is
unreferenced and may be collected, so anything running after the swap does so
against globals that can already be gone. ``scitex_todo``'s package shim obeys
the same constraint. If a future shim needs to do work, it does it BEFORE.
"""

from __future__ import annotations

import sys

from ._stale import active as _canonical

sys.modules[__name__] = _canonical  # MUST be last — see the docstring.

# EOF
