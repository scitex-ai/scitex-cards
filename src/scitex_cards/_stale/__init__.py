#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The stale-active sweep: the fleet's "this card has gone quiet" alarm.

Grouped out of the package top level 2026-08-15 (operator: the flat layout
was "shockingly dirty" at 144 top-level modules), following the pattern
PR #785 set for ``_dm/``. Layout only -- no module gained or lost a public
name in the move.

    _stale_active.py            -> _stale/active.py
    _stale_active_clocks.py     -> _stale/active_clocks.py
    _stale_active_lines.py      -> _stale/active_lines.py
    _stale_active_nudge.py      -> _stale/active_nudge.py
    _stale_active_thresholds.py -> _stale/active_thresholds.py

The old top-level names still import, and import the SAME module objects --
see the shims beside this package and the identity test that pins them.
"""

# EOF
