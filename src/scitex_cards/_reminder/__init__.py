#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The reminder engine's helper slices: bodies, cadence, enqueue, liveness.

Grouped out of the package top level (operator: the flat layout was
"shockingly dirty" at 144 top-level modules), the second family after
``_stale/`` (#856) and following the pattern PR #785 set for ``_dm/``.
Layout only -- no module gained or lost a public name in the move.

    _reminder_bodies.py   -> _reminder/bodies.py
    _reminder_cadence.py  -> _reminder/cadence.py
    _reminder_enqueue.py  -> _reminder/enqueue.py
    _reminder_liveness.py -> _reminder/liveness.py

``_reminders.py`` -- the sweep state machine these four were extracted FROM --
deliberately stays at the top level: it is the engine, not a slice of it, and
it is what the rest of the package imports.

The old top-level names still import, and import the SAME module objects --
see the shims beside this package and the identity test that pins them.
"""

# EOF
