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
When ``SCITEX_CARDS_INBOX_BACKEND`` is unset, the inbox FOLLOWS THE STORE:
a Postgres DSN selects the shared inbox, anything else selects SQLite.

That is deliberate, not a convenience. Measured 2026-08-09, the inbox was a
per-host SQLite file while the cards lived in a shared database::

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

import os
from typing import Final

__all__ = ["POSTGRES", "SQLITE", "YAML", "backend", "store_is_shared"]

POSTGRES: Final[str] = "postgres"
SQLITE: Final[str] = "sqlite"
YAML: Final[str] = "yaml"

#: The explicit override. Any of the three names selects that backend
#: outright; anything else falls through to the store-following default.
ENV_INBOX_BACKEND: Final[str] = "SCITEX_CARDS_INBOX_BACKEND"

#: Store settings consulted when the backend is not named. A Postgres store
#: means the CARDS are shared, and an inbox that is not shared alongside
#: them is exactly the defect above.
ENV_STORE_SETTINGS: Final[tuple[str, ...]] = (
    "SCITEX_CARDS_INBOX_DSN",
    "SCITEX_CARDS_DB",
)

_DSN_PREFIXES: Final[tuple[str, ...]] = ("postgres://", "postgresql://")

#: Spellings accepted for the Postgres backend. `pg` is included because it
#: is what people type, and a config that silently means "sqlite" because
#: the spelling was not recognised would reproduce the original defect.
_POSTGRES_ALIASES: Final[frozenset[str]] = frozenset({"postgres", "postgresql", "pg"})


def store_is_shared() -> bool:
    """True when the configured store is a Postgres DSN rather than a file."""
    for name in ENV_STORE_SETTINGS:
        if (os.environ.get(name) or "").strip().startswith(_DSN_PREFIXES):
            return True
    return False


def backend() -> str:
    """``postgres`` | ``sqlite`` | ``yaml`` — the backend in force.

    An explicit ``SCITEX_CARDS_INBOX_BACKEND`` always wins; otherwise the
    inbox follows the store. See the module docstring for why the default
    is not "sqlite unless told otherwise".
    """
    explicit = (os.environ.get(ENV_INBOX_BACKEND) or "").strip().lower()
    if explicit in _POSTGRES_ALIASES:
        return POSTGRES
    if explicit == YAML:
        return YAML
    if explicit == SQLITE:
        return SQLITE
    return POSTGRES if store_is_shared() else SQLITE


# EOF
