#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_cards/_inbox_backend.py
"""Which inbox backend is in force — one question, one place to answer it.

Extracted from ``_inbox.py`` rather than added to it: the selection is a
policy consulted by three delegation sites and by tests, and ``_inbox.py``
was already at its size budget. Keeping it here also makes the policy
testable without importing the whole inbox surface.

The default is the fix
----------------------
The inbox follows the primitive-owned shared store. PostgreSQL is the only
backend; historical selector values are ignored and cannot reopen a file rail.

That is deliberate, not a convenience. Measured 2026-08-09, the inbox was a
per-host file while the cards lived in a shared database::

    laptop      4901 rows, 1981 unseen, 130 recipients
    compute-04   162 rows,   87 unseen,  12 recipients

Two files that never meet, so the operator's notifications to agents on
compute-04 reached nobody. Had the shared inbox required each of twelve
agents to set a variable, it would have stayed broken for every agent that
did not — which is indistinguishable from not fixing it. A fix that only
works where someone remembered to enable it is not a fix.

No backend is ever selected as a FALLBACK. If Postgres is chosen and
unreachable, the error propagates; quietly writing to a local file is what
let this hide for weeks.
"""

from __future__ import annotations

from typing import Final

__all__ = ["POSTGRES", "YAML", "backend", "store_is_shared"]

POSTGRES: Final[str] = "postgres"
YAML: Final[str] = "yaml"

def store_is_shared() -> bool:
    """True: ambient Cards state is always the shared PostgreSQL store."""
    return True


def backend() -> str:
    """Return the only inbox backend: the shared PostgreSQL store."""
    return POSTGRES


# EOF
