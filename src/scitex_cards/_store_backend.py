#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite IS the store. There is no other backend and no way to select one.

THE ORDER (operator, 2026-07-20): 「例外を用意しないでください。甘くせずにハード
に切り替えてください。曖昧にするとバグが残ります。他のエージェントも迷ってしまい
ます。唯一の方法だけソースコードに含めてください。」 — provide no exceptions,
switch hard, leave nothing ambiguous, and carry exactly ONE way in the source.

WHAT THIS MODULE USED TO DO, and why that is gone. It exported
``db_is_canonical()``, reading a now-deleted ``*_STORE_BACKEND`` environment
variable to decide whether SQLite was the store or merely a mirror of a YAML
file. That switch — function, env var, and its sibling read-side toggle alike
— is deleted outright, not merely defaulted off, and its exact former name is
deliberately not repeated here: a string a maintainer can copy back into an
``export`` is a string that can be copied back. See ``git log -p`` on this
file / PR #545 for the literal deleted names. Because a selectable backend is
precisely the defect:

    「今の問題はワイエムLとデータベース両方使えてしまってるところで壊れると思う
      んですよ」 — the problem is that BOTH can be used, and THAT is where it breaks.

He is right, and the evidence is on the record rather than in the argument. Every
board wipe in the 2026-07-19/20 sequence needed a SECOND store to be
authoritative-ish. Reconcile means "make identical", and identical includes
deleting rows absent from whatever document is treated as the source — which is
how a 5-row temporary YAML replaced 2,159 live rows. With one store there is no
second document to reconcile TO, so that entire failure class stops being
REACHABLE rather than being guarded against. A guard is a thing that can be
bypassed; an unreachable state cannot.

Flipping the DEFAULT would not have been enough, and this is the part worth
keeping. A default is an opinion about the common case; it leaves the other
world supported, reachable, and reviewed by nobody. Two supported worlds is two
sets of behaviour to reason about at every call site, and the fleet's agents
each resolve their own environment — so "which world am I in?" would be a live
question with a different answer per process. That is the ambiguity the operator
named, and deletion is the only thing that answers it.

THE ONE-WAY DOOR, stated plainly because it is the real cost. A card that fails
to reach SQLite is GONE — there is no YAML behind it to fall back on. So the
write path must NOT take the mirror's best-effort, never-raise posture: a failed
write MUST raise and the caller MUST see it. Swallowing an exception here would
silently drop the operator's cards, which is the single worst thing this package
can do.
"""

from __future__ import annotations


def _current_stored_ids(db_path) -> set[str]:
    """The ids the ``tasks`` table ALREADY has, read fresh from SQLite.

    Ground truth for :func:`_assert_no_shrink` — deliberately a live query,
    not a value threaded through from an earlier read, so the check is
    correct even when ``doc`` was built from a stale snapshot (the exact
    shape of the 2170->18 collapse).
    """
    from ._db import open_db

    conn = open_db(db_path)
    try:
        rows = conn.execute("SELECT id FROM tasks").fetchall()
        return {str(r[0]) for r in rows}
    finally:
        conn.close()


def _assert_no_shrink(
    doc: dict,
    db_path,
    *,
    deleted_ids: list[str] | None = None,
    allow_shrink: bool = False,
) -> None:
    """RAISE :class:`StoreShrinkRefusedError` if ``doc`` is missing rows.

    THE PRIMARY INVARIANT (operator ruling, P0 2026-07-21 third board
    wipe): a written card never disappears. Not "not too many" — NONE,
    unless the write NAMES what it intentionally removed
    (``deleted_ids``, the one legitimate single-card path — see
    :mod:`scitex_cards._db_mirror`) or the caller explicitly opts out
    via ``allow_shrink=True``. No ratio, no threshold: growing a store
    from zero never removes anything, so this never fires on a fresh
    store — there is nothing to exempt by size.
    """
    if allow_shrink:
        return
    stored_ids = _current_stored_ids(db_path)
    if not stored_ids:
        return
    incoming_ids = {
        str(c["id"])
        for c in (doc.get("tasks") or [])
        if isinstance(c, dict) and c.get("id")
    }
    excused = {str(x) for x in (deleted_ids or [])}
    missing = stored_ids - incoming_ids - excused
    if not missing:
        return
    ordered = sorted(missing)
    sample = ", ".join(ordered[:20])
    more = "" if len(ordered) <= 20 else f" (+{len(ordered) - 20} more)"
    from ._task import StoreShrinkRefusedError

    raise StoreShrinkRefusedError(
        f"refusing to persist: this write is missing {len(missing)} id(s) "
        f"the store already has ({sample}{more}) out of {len(stored_ids)} "
        f"currently stored. A written card must never disappear — this is "
        f"exactly the shape of the 2170->18 collapse (a stale/replacement "
        f"document overwriting the live store). If this shrink is genuinely "
        f"intentional (a deliberate bulk archive), re-run with "
        f"allow_shrink=True."
    )


def write_doc_to_db(
    doc: dict, store_path, *, deleted_ids=None, allow_shrink: bool = False
) -> dict:
    """Commit ``doc`` to SQLite, the only store. RAISES on failure.

    ``store_path`` identifies WHICH logical store is addressed, and therefore
    stamps provenance. Nothing is written to that path.

    ``deleted_ids`` are ids a caller intentionally removed (``delete_task``);
    they are forwarded to the mirror, which drops exactly those rows. The mirror
    never infers a delete from a card's absence — only these named ids go.

    ``allow_shrink`` overrides :func:`_assert_no_shrink`'s refusal — the
    escape hatch for a genuinely deliberate bulk removal (never set by an
    ordinary card write; see that function's docstring). Keyword-only, not
    an env var: an env var leaks ambiently across every write in the
    process, an explicit call-site argument cannot.

    OWNERSHIP IS CHECKED FIRST and a mismatch RAISES rather than returning
    quietly. The destination comes from the ambient environment
    (``resolve_db_path(None)``) while ``doc`` comes from the caller, so a
    mispairing is possible on this path and there is no second copy to recover
    from when it happens.

    That is not hypothetical. Both halves were demonstrated on 2026-07-19:
    first the mirror path let a pytest fixture rebuild the live DB
    (2,136 cards -> 21), and after that path was guarded, the canonical path —
    unguarded — let the same suite do it again, harder (2,138 -> 1). Two doors
    into one room; guarding one taught the caller nothing about the other.

    Why RAISE rather than decline: declining is right when the card is already
    durable somewhere else, and wrong when this is the only copy. Silently
    not-writing would report success for a card that was never stored. Both
    outcomes of a mismatch — clobbering the wrong database, or dropping the
    card — are unacceptable, so the caller is told.
    """
    from ._db import resolve_db_path
    from ._db_mirror import mirror_doc_incremental
    from ._dual_write import _db_mirrors_this_store

    db_path = resolve_db_path(None)
    # The store identity IS the database path ($SCITEX_CARDS_DB). The caller's
    # ``store_path`` names the logical store for messages and sidecar dirs, but
    # it is NOT a second identity axis: reads (``_read_canonical_db_or_raise``)
    # and writes both key on ``db_path``, so a fresh database is adopted on
    # first write and every subsequent access agrees with the stamp. Keying the
    # guard on ``store_path`` here is what let a write stamp the database with a
    # different path than reads compared against — two axes that could disagree,
    # the exact failure class the cutover removed.
    if not _db_mirrors_this_store(db_path, db_path):
        raise RuntimeError(
            f"refusing to write {db_path}: it is stamped for a DIFFERENT "
            f"database, and writing it would replace that store's rows with "
            f"this one's. Point $SCITEX_CARDS_DB at this store's own database."
        )

    # WRITE-SIDE SHRINK GUARD (P0, 2026-07-21 third board wipe). Checked
    # AFTER ownership (a write to the wrong store is a worse bug than a
    # shrink, and should raise as that first) but BEFORE the mirror ever
    # touches a row. See `_assert_no_shrink` for the invariant.
    _assert_no_shrink(doc, db_path, deleted_ids=deleted_ids, allow_shrink=allow_shrink)

    # `mirror_doc_incremental` already raises on failure — no try/except here
    # ON PURPOSE. Adding one could only make this quieter, which is the one
    # direction this function must never move.
    return mirror_doc_incremental(
        doc, db_path, store_path=db_path, deleted_ids=deleted_ids
    )


__all__ = [
    "write_doc_to_db",
]

# EOF
