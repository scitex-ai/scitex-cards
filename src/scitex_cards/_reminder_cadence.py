#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deprecated import alias: ``scitex_cards._reminder_cadence`` -> :mod:`scitex_cards._reminder.cadence`.

Grouped into the ``_reminder/`` subpackage 2026-09-06, the second family after
``_stale/`` (#856). This shim keeps the old top-level name importable for
external callers, and aliases it to the VERY SAME module object rather than
re-exporting its names -- a second module execution would fork module-level
state (caches, thresholds read once from the environment), so two live copies
could disagree about the digest floor with nothing to notice it.

THE CONSTRAINT THIS SHAPE CARRIES: the ``sys.modules[__name__]`` reassignment
must be the LAST statement this module executes. After the swap the original
module object is unreferenced and may be collected, so anything running past it
would read globals that can already be gone.

In-repo callers already use the new path; this exists for anything outside the
repo that does not. Deleting it is a separate, deliberate decision.
"""

from __future__ import annotations

import sys

from ._reminder import cadence as _canonical

sys.modules[__name__] = _canonical

# EOF
