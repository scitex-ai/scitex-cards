#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SINGLE-WRITE-TARGET health check — split out of ``_health`` (512-line cap).

Replaces the deleted ``dual_write_mirror`` check (2026-07-21 operator ruling:
「データベースしか書く場所なんてありえない。デュアルライトっていうオプション
があること自体がおかしい」 — there is no such thing as a second place to
write; the mere EXISTENCE of a dual-write option is the bug). That check used
to report whether an env-gated YAML mirror had stayed in sync with the store.
The mirror is gone, not merely off, so there is nothing left to report
sync/divergence for.

What this checks instead is the thing the deleted feature actually put at
risk: is the store still the ONLY write target? The 2026-07-21 incident's root
cause was not a mirror falling out of sync — it was a stale ``schema_meta``
row plus an agent env that still carried the dual-write flag, which together
routed every write to a dead YAML file while every call reported SUCCESS and
`health` stayed green. So this check asks the two questions that would let
that class of bug recur:

1. Is a legacy toggle env var still set in this process's environment?
   Harmless today (nothing reads it), but its presence is exactly the
   footgun that caused the incident, so it is flagged for cleanup rather
   than silently tolerated.
2. Has the toggle been reintroduced as CODE (``_dual_write.enabled`` /
   ``mirror_after_save`` / ``ENV_DUAL_WRITE``)?

Both are checked directly rather than inferred, so a regression on either
axis fails LOUDLY instead of this check silently trusting the deletion held.
"""

from __future__ import annotations

from ._store_url import describe_store_target

import os
from typing import Any

#: Legacy dual-write toggle names. The feature they gated was DELETED
#: entirely — the store is the only write target, unconditionally, with no env
#: var left to read.
_LEGACY_DUAL_WRITE_ENV_VARS = ("SCITEX_CARDS_DUAL_WRITE",)

#: Symbols that made up the deleted toggle. Their reappearance on
#: ``scitex_cards._dual_write`` means the feature was reintroduced.
_REINTRODUCED_TOGGLE_SYMBOLS = ("enabled", "mirror_after_save", "ENV_DUAL_WRITE")


def check_single_write_target() -> dict[str, Any]:
    """The store is the ONLY write target — assert there is no second one, ever."""
    leftover = [v for v in _LEGACY_DUAL_WRITE_ENV_VARS if os.environ.get(v)]
    if leftover:
        return {
            "ok": False,
            "detail": (
                f"legacy dual-write env var(s) still set: {', '.join(leftover)}. "
                "The dual-write mirror was DELETED (2026-07-21) — the store is "
                "the only write target and nothing reads these anymore, but their "
                "presence is exactly the state that let an entire session's "
                "card writes vanish silently."
            ),
            "hint": f"unset {', '.join(leftover)} from this agent's environment",
        }

    import scitex_cards._dual_write as dual_write_mod

    reintroduced = [
        name for name in _REINTRODUCED_TOGGLE_SYMBOLS if hasattr(dual_write_mod, name)
    ]
    if reintroduced:
        return {
            "ok": False,
            "detail": (
                f"the dual-write toggle was reintroduced in code: "
                f"scitex_cards._dual_write now defines {reintroduced}. The store "
                "must be the ONLY write target — there is no sanctioned second "
                "one, not even behind a flag."
            ),
            "hint": (
                "remove the reintroduced toggle; the sole write path is "
                "scitex_cards._store_backend.write_doc_to_db"
            ),
        }
    # NAME THE ENGINE THIS PROCESS IS ACTUALLY ON. This string used to be a
    # hardcoded engine name, which was true when written and became a lie the
    # day a store could be a server: the doctor then named one engine while
    # every card write went to another. An operator asking "which mode am I
    # in?" — the exact question this line looks like it answers — got the wrong
    # answer, confidently. Resolve it instead of asserting it.
    try:
        from ._health_backend_mode import _store_mode

        engine, target = _store_mode(None)
        where = f"{engine} ({describe_store_target(target)})"
    except Exception:  # noqa: BLE001 — a doctor must not crash the caller
        where = "the resolved store (engine undetermined)"
    return {
        "ok": True,
        "detail": (
            f"exactly one write target: {where} via "
            "_store_backend.write_doc_to_db; no dual-write toggle present"
        ),
        "hint": None,
    }


__all__ = ["check_single_write_target"]

# EOF
