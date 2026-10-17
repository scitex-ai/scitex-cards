#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deprecated import alias: ``scitex_cards._dm_exchange`` -> :mod:`scitex_cards._dm.exchange`.

Grouped into the ``_dm/`` subpackage 2026-10-16 (the responder-exchange rail
landed flat after PR #785 grouped the family). This shim keeps the old
top-level name importable for external callers, and aliases it to the VERY SAME
module object rather than re-exporting its names -- a second module execution
would fork module-level state, which is the hazard :mod:`scitex_cards`'s
package-level shim documents and avoids the same way.

In-repo callers already use the new path; this exists for anything outside the
repo that does not. Deleting it is a separate, deliberate decision.

THE REASSIGNMENT MUST BE THE LAST STATEMENT THIS MODULE EXECUTES. Once
``sys.modules[__name__]`` points elsewhere, the original module object is
unreferenced and may be collected, so anything running after the swap does so
against globals that can already be gone.
"""

from __future__ import annotations

import sys

from ._dm import exchange as _canonical

sys.modules[__name__] = _canonical  # MUST be last — see the docstring.
