#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which store is CANONICAL — the YAML file, or the SQLite DB?

THE ORDER (operator, 2026-07-18): 「db にハードに切り替えてください、ヤムル
ファイルは全てアーカイブしてください」「書き出ししなくていいですよ！！ぜんぶ
db で！」 — switch hard to the DB, archive the YAML, and do NOT keep writing an
export. The DB is the store, not a mirror of one.

WHY THIS IS THE RIGHT END STATE and not merely an instruction followed. While
YAML was canonical and the DB a mirror, the SQLite read path was gated on
``_db_freshness.check_fresh``, which demands EXACT (mtime_ns, size) equality
between the DB's stamp and the YAML on disk. The fleet rewrites that YAML every
few seconds, so the gate could pass only in the gaps between writes — measured
alternating fresh/stale under live load. That race exists BY CONSTRUCTION in a
mirror design; no tuning removes it, because tightening the gate keeps the fast
path off and loosening it risks serving stale cards. Making the DB canonical
deletes the entire failure class: there is nothing left for it to be stale
against.

THE ONE-WAY DOOR, stated plainly. In canonical mode a card that fails to reach
SQLite is GONE — there is no YAML behind it to fall back on. So the write path
must NOT keep the mirror's best-effort, never-raise posture: under
:func:`db_is_canonical` a failed DB write MUST raise and the caller MUST see
it. Swallowing an exception here would silently drop the operator's cards,
which is the single worst thing this package can do. See ``_store_write``.

THERE IS NO DEFAULT, BECAUSE THERE IS NO CHOICE. This module once exposed
``SCITEX_CARDS_STORE_BACKEND`` and defaulted to OFF. The flag is gone (operator,
2026-07-19): 「db_canonical とかいうのを置くな、DB 以外ありえない」. The cutover ran
on 2026-07-19 — both YAML stores archived to ``~/.scitex/cards/.old/<stamp>/``,
a snapshot pushed to the ``scitex-cards-cards`` backup repo, and the DB left as
the only store on disk.

Keeping the switch would have kept YAML mode alive as a supported way to run,
and a mode that can be entered by OMISSION is entered eventually. It was, within
the hour: an unset flag sent ``resolve_tasks_path(None)`` down its precedence
chain to the BUNDLED EXAMPLE store in site-packages, and the DB's provenance
stamp followed it there. YAML survives only as import/export/snapshot — a
backup and interchange format, never a store.
"""

from __future__ import annotations


def db_is_canonical() -> bool:
    """SQLite IS the store. Always. Retained as a shim, about to be deleted.

    THERE IS NO LONGER A FLAG (operator, 2026-07-19): 「db_canonical とかいうの
    を置くな、DB 以外ありえない」 — do not keep a "db canonical" switch; nothing
    but the DB is possible. This function returns ``True`` unconditionally and
    exists only so the remaining call sites keep importing something while they
    are unwound; it takes no argument, reads no environment, and has no other
    branch to take.

    WHY THE FLAG HAD TO GO, and it is not merely tidiness. A flag advertises
    that the other setting is a supported way to run, so every reader must
    still reason about YAML mode and every deployment can still land in it by
    omission. That is not hypothetical: on 2026-07-19, with the flag unset in
    one shell, ``resolve_tasks_path(None)`` walked its precedence chain past
    the (now archived) canonical YAML and settled on the BUNDLED EXAMPLE store
    inside site-packages — and the DB's provenance stamp was rewritten to point
    at it. A configuration that can silently designate a packaged fixture as
    the fleet's board is not a configuration, it is a trap. Deleting the choice
    deletes the trap.

    The YAML machinery that remains is import/export/snapshot only — a backup
    and interchange format, never a store. See ``_db_export`` / ``db snapshot``.
    """
    return True


def write_doc_to_db(doc: dict, store_path) -> dict:
    """Commit ``doc`` to SQLite as the CANONICAL store. RAISES on failure.

    THE INVERSE POSTURE of :func:`_dual_write.mirror_after_save`, and the
    inversion is deliberate. That one swallows every exception because the
    YAML already holds the card, so a mirror hiccup must never turn a
    successful write into a failed one. Here SQLite is the ONLY copy: a
    swallowed exception is a card that vanished while the caller was told it
    saved. So this propagates, and callers must not wrap it in a bare
    ``except``.

    ``store_path`` still identifies WHICH logical store is addressed (and
    therefore stamps provenance), even though nothing is written to that file
    in this mode.
    """
    from ._db import resolve_db_path
    from ._db_mirror import mirror_doc_incremental

    # `mirror_doc_incremental` already raises on failure — no try/except here
    # ON PURPOSE. Adding one could only make this quieter, which is the one
    # direction this function must never move.
    return mirror_doc_incremental(doc, resolve_db_path(None), store_path=store_path)


__all__ = [
    "db_is_canonical",
    "write_doc_to_db",
]

# EOF
