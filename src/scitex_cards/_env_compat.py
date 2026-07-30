#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``SCITEX_CARDS_*`` / ``SCITEX_TODO_*`` environment dual-read (transition).

The package rename (scitex-todo -> scitex-cards, 2026-07-16) renames every
environment-variable prefix from ``SCITEX_TODO_`` to ``SCITEX_CARDS_``. The
codebase still READS the old names at ~50 sites; rewriting them all inside
the mechanical rename PR would tangle two risky changes into one diff. So
this module — imported FIRST by ``scitex_cards.__init__`` — MIRRORS every
``SCITEX_CARDS_<X>`` value onto ``SCITEX_TODO_<X>`` in this process's
environment:

- a shell that already exports the NEW names (the operator's ``.envrc``)
  works today, and child processes inherit the mirrored pair;
- the un-cutover fleet exporting only OLD names keeps working, with ONE
  deprecation warning per process (operator-requested, 2026-07-16);
- when BOTH names are set to different values, the NEW name wins, loudly —
  EXCEPT in the two cases below, where the new value is not trustworthy.

The read sites migrate to the new names in the store-engine stage; this
mirror then inverts and finally dies together with the ``scitex_todo`` shim.

WHY THIS MODULE NOW REFUSES SOME OVERRIDES (incident 2026-07-19)
---------------------------------------------------------------
"The new name wins" is the right policy for a DELIBERATE migration. It was
implemented as an UNCONDITIONAL overwrite, and that is a different thing: it
means a malformed new-prefix value silently defeats a working old-prefix one.
Measured on the live fleet, from the MCP server's own ``/proc/<pid>/environ``:

    SCITEX_TODO_DB        = ~/.scitex/todo/cards.db    (2117 cards)
    SCITEX_CARDS_DB       = ~/.scitex/cards/cards.db   (5 cards)
    SCITEX_TODO_AGENT_ID  = scitex-cards
    SCITEX_CARDS_AGENT_ID = ${SCITEX_CARDS_AGENT_ID}   <- literal

Both overrides applied. The store silently FORKED: agents wrote into an empty
store while the fleet's history sat untouched in the other one, and every card
written carried ``created_by: ${SCITEX_CARDS_AGENT_ID}``. A hard SQLite cutover
seeded from "the resolved store" would have built the database from 5 cards and
destroyed 2117 — the fork was the only thing between a correct-sounding
instruction and total loss.

The upstream defect (a spec template that did not expand) is not ours. Turning
somebody else's config bug into silent data loss IS ours. Hence two refusals:

1. An UNEXPANDED value is not a value. ``${FOO}`` reaching a process means an
   expansion failed; treating it as data propagates the failure into the store.
2. A rename must not silently RELOCATE a mutable data store. If the two names
   disagree and the OLD path exists with content, that is a fork, not a
   migration.

Both refuse LOUDLY and keep the old value. Refusing is safe (the pre-rename
configuration keeps working); accepting is not (it strands writes).

RETIRED VARIABLES ARE ANNOUNCED, NOT HONOURED (2026-07-29)
-----------------------------------------------------------
This module also owns the other half of "what the environment says about the
store": :func:`warn_retired_vars` logs one ERROR per variable a live config
still exports that NOTHING in the package reads. See ``_RETIRED_VARS`` for why
an inert setting is worse than a missing one, and why the answer is never to
make the flag work again.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import MutableMapping

logger = logging.getLogger(__name__)

NEW_PREFIX = "SCITEX_CARDS_"
OLD_PREFIX = "SCITEX_TODO_"

#: A value that is *entirely* an unexpanded shell/template placeholder —
#: ``${FOO}``, ``$FOO``, ``{{ foo }}``. Anchored on purpose: a legitimate path
#: may CONTAIN a ``$`` (rare, but legal in POSIX filenames), and only a value
#: that is nothing but a placeholder is unambiguously a failed expansion.
_UNEXPANDED = re.compile(
    r"^\s*(\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\{\{[^}]*\}\})\s*$"
)

#: Suffixes of vars that name a MUTABLE DATA STORE. Relocating one of these
#: does not change behaviour — it changes which data you are looking at, and a
#: wrong answer here is indistinguishable from an empty board. ``DB`` is the
#: store identity ($SCITEX_CARDS_DB); the legacy ``…_TASKS_YAML_SHARED`` var was
#: deleted with the SQLite cutover.
_DATA_STORE_SUFFIXES = ("DB", "TASKS_DB", "DB_PATH")


#: RETIRED variables: names a live config may still export that NOTHING in
#: ``src/`` reads. Each maps to the sentence explaining what replaced it.
#:
#: A dead flag is not harmless, and this is the lesson of 2026-07-29: the
#: board's systemd unit carried ``SCITEX_CARDS_READ_BACKEND=sqlite`` while the
#: board served 0 cards from a database holding 2,654. The flag has ZERO
#: references in the package. It did nothing — but it STATED a read policy, so
#: everyone diagnosing the outage read that line, concluded the read target was
#: configured and correct, and looked elsewhere. An inert setting that appears
#: to answer the question is worse than no setting at all: it spends the
#: diagnostician's trust.
#:
#: The repair is never to make the flag work again. A flag that can be flipped
#: is a second target that merely happens to be switched one way today — the
#: reasoning that DELETED the dual-write toggle on 2026-07-21 rather than
#: defaulting it off. So these stay deleted, and a config that still names one
#: says so out loud.
_RETIRED_VARS: dict[str, str] = {
    "READ_BACKEND": (
        "there is no read-backend choice: the canonical SQLite database is the "
        "ONLY read target, unconditionally "
        "(scitex_cards._store._read_canonical_db_or_raise)"
    ),
    "DUAL_WRITE": (
        "the dual-write mirror was DELETED 2026-07-21, not defaulted off: "
        "SQLite is the ONLY write target "
        "(scitex_cards._store_backend.write_doc_to_db)"
    ),
    "STORE_BACKEND": (
        "there is no backend choice left to make: SQLite is the store "
        "(the YAML store was retired at the cutover)"
    ),
}


def warn_retired_vars(environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Log one ERROR per RETIRED variable still set. Returns the names found.

    Deliberately a LOG and not a raise. This runs at import time, so raising
    would make ``import scitex_cards`` fail fleet-wide over a setting that, by
    construction, changes nothing — trading a cosmetic config problem for a
    total outage. ERROR is the right volume: it reaches the board unit's
    journal, which is exactly where someone restarting the board is looking.
    """
    found: list[str] = []
    for prefix in (NEW_PREFIX, OLD_PREFIX):
        for suffix, why in _RETIRED_VARS.items():
            name = prefix + suffix
            if not environ.get(name):
                continue
            found.append(name)
            logger.error(
                "%s=%r is set, but NOTHING READS IT — %s. This setting does not "
                "change behaviour; it only makes a stale config LOOK like it "
                "states a policy, which is how the last outage stayed "
                "misdiagnosed. Unset it.",
                name,
                environ[name],
                why,
            )
    return found


def _is_unexpanded(value: str) -> bool:
    """True when ``value`` is nothing but an unexpanded placeholder."""
    return bool(_UNEXPANDED.match(value))


def _names_a_data_store(suffix: str) -> bool:
    return suffix.endswith(_DATA_STORE_SUFFIXES)


def _would_relocate_populated_store(
    suffix: str, old_value: str, new_value: str
) -> bool:
    """True when honouring ``new_value`` would move us off a POPULATED store.

    Deliberately conservative: we only refuse when the OLD path exists and is
    non-empty. A first-time setup (old path absent) is a real migration and is
    allowed through — refusing there would break every fresh install.
    """
    if not _names_a_data_store(suffix):
        return False
    try:
        old_path = Path(old_value)
        if not old_path.is_file() or old_path.stat().st_size == 0:
            return False
        return Path(new_value) != old_path
    except OSError:
        # Unreadable/odd path: not evidence of a populated store, so do not
        # refuse on it. NEVER let this helper raise — it runs at import time,
        # and an exception here would make `import scitex_cards` fail outright.
        return False


def mirror_env(environ: MutableMapping[str, str] = os.environ) -> None:
    """Mirror ``SCITEX_CARDS_*`` onto ``SCITEX_TODO_*`` (new names win).

    Also emits one aggregate deprecation warning when old-prefix names are
    in use without a new-prefix twin — the signal the operator asked for to
    find un-migrated exports during the transition window.

    Two classes of new-prefix value are REFUSED rather than mirrored; see the
    module docstring for the incident that added them. A refusal keeps the old
    value, logs at ERROR, and never raises: this runs at import time, so a
    raise here would make ``import scitex_cards`` fail for every consumer.
    """
    for new in [k for k in environ if k.startswith(NEW_PREFIX)]:
        suffix = new[len(NEW_PREFIX) :]
        old = OLD_PREFIX + suffix
        new_value = environ[new]
        old_value = environ.get(old)

        # REFUSAL 1 — an unexpanded placeholder is a failed expansion, not a
        # value. Mirroring it writes the literal `${...}` into whatever the var
        # feeds; for AGENT_ID that lands in every card's `created_by`.
        if _is_unexpanded(new_value):
            logger.error(
                "%s=%r is an UNEXPANDED placeholder, not a value — refusing to "
                "mirror it onto %s (keeping %r). Fix the spec/template that "
                "should have expanded it; this process continues on the old name.",
                new,
                new_value,
                old,
                old_value,
            )
            continue

        # REFUSAL 2 — a rename must not silently relocate a populated data
        # store. Honouring this would fork the store: new writes land in one
        # file while the existing history sits in another, with nothing to
        # reconcile them.
        if old_value is not None and _would_relocate_populated_store(
            suffix, old_value, new_value
        ):
            logger.error(
                "%s=%r would relocate a POPULATED data store away from %s=%r — "
                "refusing. This is a fork, not a migration: writes would land in "
                "the new file while the existing records stay in the old one. "
                "Migrate the data first, then unset %s.",
                new,
                new_value,
                old,
                old_value,
                old,
            )
            continue

        if old_value not in (None, new_value):
            logger.warning(
                "%s=%r overrides %s=%r (the SCITEX_CARDS_* name wins)",
                new,
                new_value,
                old,
                old_value,
            )
        environ[old] = new_value

    old_only = sorted(
        k
        for k in environ
        if k.startswith(OLD_PREFIX) and NEW_PREFIX + k[len(OLD_PREFIX) :] not in environ
    )
    if old_only:
        logger.warning(
            "deprecated SCITEX_TODO_* environment names in use (%s); "
            "rename them to SCITEX_CARDS_* — the old prefix is honoured "
            "for one transition window only",
            ", ".join(old_only),
        )


mirror_env()
warn_retired_vars()

# EOF
