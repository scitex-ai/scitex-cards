#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STORE OWNERSHIP GUARD — does this database belong to the store we resolved?

THE DATABASE IS THE ONLY WRITE TARGET (operator ruling 2026-07-21): 「データ
ベースしか書く場所なんてありえない。デュアルライトっていうオプションがあること
自体がおかしい」 — there is no such thing as a second place to write; the mere
EXISTENCE of a dual-write option is the bug. What used to live in this module
alongside the guard below — an env-gated mirror-to-YAML path
(``SCITEX_CARDS_DUAL_WRITE`` / ``ENV_DUAL_WRITE``, ``enabled()``,
``mirror_after_save()``, the failure counter, ``check_mirror_healthy()``) — is
DELETED, not defaulted off. A toggle that can be flipped is a second write
target that merely happens to be switched off today; deleting the code that
reads the flag is the only way to make "which store did this write actually
reach?" stop being a live question.

THE INCIDENT THIS ANSWERS (root cause, diagnosed 2026-07-21). ``cards.db``
carried a stale ``schema_meta`` row (``yaml_path`` pointing at an old
``~/.scitex/cards/tasks.yaml``). An agent whose environment still carried the
dual-write flag had every MCP/CLI write silently routed to that YAML instead
of the canonical database: every call returned SUCCESS, ``health`` stayed
green, and an entire session of card writes never reached the board. The flag
made that possible; removing it makes it unrepresentable — there is no
environment variable left to read that could send a write anywhere but the
database at ``$SCITEX_CARDS_DB``.

WHAT SURVIVES HERE, and is load-bearing, is the OWNERSHIP GUARD that keeps one
database from being written with another store's rows.

THE INVARIANT
-------------
A database is the database of exactly ONE store. Point ``$SCITEX_CARDS_DB`` at a
database built as store B's, then write store A into it, and nothing merges —
B's rows are REPLACED with A's. On 2026-07-19 this package's own concurrency
test did exactly that to the live fleet database, rebuilding it from a 21-card
fixture, because the destination came from the ambient environment while the
source came from the caller and nothing checked the two matched.

So :func:`_db_mirrors_this_store` refuses a write whose resolved store disagrees
with the database's own provenance stamp (:data:`scitex_cards._db_freshness.KEY_STORE_PATH`).
An UNSTAMPED database is adoptable — a fresh one, or a populated board being
adopted at deploy — and the first write claims it by stamping its identity.
The store's ONE write chokepoint, :func:`scitex_cards._store_backend.write_doc_to_db`,
calls this guard and RAISES rather than returning quietly on a mismatch: a
write that cannot reach the canonical DB must NEVER report success.

IDENTITY IS A UUID; THE PATH IS THE LEGACY FALLBACK
---------------------------------------------------
A path cannot be identity when two mount namespaces both write, so the real
answer now lives in :mod:`scitex_cards._store_uuid`: the database carries its
own opaque ``store_uuid`` and :func:`~scitex_cards._store_uuid.identity_verdict`
decides ownership from that alone, WITHOUT CONSULTING ANY PATH. On the
``ADOPT`` branch — a store with no identity facing a caller with no expectation
— there is nothing weaker left to consult: the store is a server, and a server
carries no filesystem identity. ADOPT therefore means adoptable, and the first
write claims it.

WHY THE PATH COMPARE ASKS THE KERNEL, NOT ``realpath``
------------------------------------------------------
:func:`_same_file` compares ``st_dev``/``st_ino``, because on this host one store
directory is reached by two names that ``realpath`` resolves DIFFERENTLY
(``/home/agent/.scitex/cards`` vs ``/home/ywatanabe/.scitex/cards``, a bind
mount). A string compare called them different stores and refused every write
from whichever population did not match the stamp — measured live on 2026-07-20.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Do these two paths name the SAME FILE — by identity, not by spelling?

    THE ONE DEFINITION OF SAMENESS in this package. Every caller that needs to
    ask "are these two paths one file" calls this; a second definition would be
    a second answer, which is the shape of the bug it exists to prevent.

    ``realpath`` alone is not enough, and the difference is not academic: this
    host reaches ONE store directory by two names that resolve DIFFERENTLY.

        /home/agent/.scitex/cards      -> /home/agent/.scitex/cards
        /home/ywatanabe/.scitex/cards  -> /home/ywatanabe/.dotfiles/src/.scitex/cards

    Same ``st_dev``/``st_ino`` — the same directory, reached through a bind
    mount — but two different realpath STRINGS. A string compare therefore
    called them different stores and REFUSED every write from whichever
    population did not match the stamp, on a database that was in fact theirs.
    MEASURED on the live board 2026-07-20, immediately after a restore: cards
    written via ``/home/ywatanabe/...`` were refused against a DB stamped
    ``/home/agent/...``.

    THE REALPATH STRING FALLBACK IS GONE (design §8). It used to answer this
    question when either path could not be ``stat``-ed. scitex-dev's framing,
    endorsed: *a fallback that triggers only in the case it cannot judge is
    worse than no fallback.* It fired PRECISELY when the stamped path was
    unstat-able — i.e. exactly when you are across a mount-namespace boundary
    and least entitled to an opinion — and it answered with the more
    destructive of the two possible answers, under the false claim "stamped for
    a DIFFERENT store". Removing it is not a tightening of the live failure:
    the string compare already returned ``False`` there, because
    ``/home/agent/...`` and ``/home/ywatanabe/.dotfiles/src/...`` are different
    strings. What changes is that the caller can now say CANNOT TELL honestly,
    and the escape from CANNOT TELL is binding the store to an identity once
    (``scitex-cards store adopt-uuid``), not a looser comparison.

    So: two paths are the same file when the KERNEL says so, and when the
    kernel cannot be asked the answer is ``False`` — never a guess.
    """
    try:
        sa, sb = Path(a).stat(), Path(b).stat()
    except OSError:
        # CANNOT TELL. Refusing is the honest answer; the caller owns the
        # message, and `_db_mirrors_this_store` distinguishes it from a
        # genuine "different store" so the operator is not told a falsehood.
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _db_mirrors_this_store(db_path: str | Path, store_path: str | Path) -> bool:
    """Is the DB at ``db_path`` the mirror of ``store_path`` — or of some OTHER store?

    THE INVARIANT, implied everywhere and enforced nowhere: a shadow DB mirrors
    exactly ONE store. Writing store A into the DB that mirrors store B does not
    merge them, it REPLACES B's contents with A's.

    It went unenforced because the (now-deleted) mirror-to-YAML write path
    resolved the destination with a bare ``resolve_db_path()`` — no argument —
    so the DB came from the AMBIENT ENVIRONMENT while the source came from the
    CALLER. Nothing checked that the two referred to the same pairing.

    Not theoretical. On 2026-07-19 this package's own concurrency test — which
    copies ``os.environ`` into two writer subprocesses, so they inherit
    ``SCITEX_CARDS_DUAL_WRITE=1`` — wrote to a pytest ``tmp_path`` store and
    rebuilt the LIVE FLEET DATABASE from its 21-card fixture, replacing 2,136
    real cards. It was recoverable only because the YAML was still canonical and
    the DB merely a mirror; under the DB-canonical mode merged that same
    morning, running the test suite would have destroyed the board outright.

    Same shape as the env-compat incident hours earlier: a global default
    silently overriding an explicit local intent. Same fix: refuse, keep the
    good state, say so.

    WHY THE DB'S OWN STAMP AND NOT "is this the canonical store" — the naive
    guard (refuse anything that is not the canonical store) also refuses the
    package's own legitimate tests, which deliberately pair a tmp store with a
    tmp DB and are correct to mirror. The honest question is not "is this store
    special" but "do these two belong together", and the DB already answers it:
    :func:`_db_freshness.stamp_store_provenance` records which store it reflects.

    An UNSTAMPED DB is adoptable (a fresh/bootstrapping mirror, incl. every test
    fixture) — the first write claims it. A DB stamped for a DIFFERENT store is
    refused.

    UUID-FIRST (design §7). The order below is the whole repair::

        1. the store cannot be opened/read -> True  (nothing to clobber)
        2. read schema_meta.store_uuid             (MAY BE ABSENT — legal)
        3. identity_verdict(store_uuid, expected_store_uuid())
             ACCEPT -> True.          THE PATH IS NOT CONSULTED AT ALL.
             REFUSE -> False, logged. THE PATH IS NOT CONSULTED AT ALL.
             ADOPT  -> True: adoptable, and the first write claims it.

    The verdict is consulted UNCONDITIONALLY, on an absent identity as well as
    a present one — it has to be, because "no identity, but an expectation was
    declared" is a REFUSE row that a guard which only asked when the answer was
    already stamped could never reach.

    THE LEGACY PATH COMPARISON IS GONE, and its absence is not a weakening. It
    was the ``ADOPT`` branch's evidence while a store was a file; a store is a
    server, which has no filesystem identity, so there is nothing to compare and
    a comparison that ran anyway would be answering from two values that cannot
    be stat'd. That is the exact shape of the "CANNOT DETERMINE OWNERSHIP"
    false alarm it used to raise across a mount-namespace boundary, on a
    database that was in fact the caller's own.

    Once the store carries a ``store_uuid``, the host and the container reach
    the SAME verdict because neither one looks at a path.

    BOTH DOORS KEEP CALLING THIS SAME PREDICATE. The read door
    (:func:`scitex_cards._store_canonical_read._read_canonical_db_or_raise`) and
    the write door (:func:`scitex_cards._store_backend.write_doc_to_db`) are not
    split, not parameterised, and do not get a lenient variant. On 2026-07-19
    the write door refused a foreign store correctly all day while the read door
    returned its rows, and a packaged fixture was read AS THE BOARD for hours.
    """
    from ._store_uuid import (
        ACCEPT,
        ENV_EXPECTED_STORE_UUID,
        REFUSE,
        expected_store_uuid,
        identity_verdict,
        read_store_uuid,
    )

    try:
        from ._db import connect  # noqa: PLC0415 -- import cycle

        conn = connect(str(db_path))
        try:
            db_uuid = read_store_uuid(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — unreadable stamp ⇒ let the write try
        # FAIL-OPEN, deliberately and unchanged. An unopenable or unstamped
        # store is not evidence of a mismatch, and refusing on "I could not
        # look" would take the board down every time the server blinked.
        return True

    expected = expected_store_uuid()
    verdict = identity_verdict(db_uuid, expected)
    if verdict == ACCEPT:
        return True  # the identity decided; the path is never read
    if verdict == REFUSE:
        logger.error(
            "!! REFUSING: %s carries store identity %r but this process expects "
            "%r. These are two DIFFERENT stores, whatever they are called in "
            "this mount namespace, and writing one into the other would REPLACE "
            "its rows. Fix the EXPECTATION ($%s) or point $SCITEX_CARDS_DB at "
            "the store you meant. If this database predates store identities it "
            "carries none, and the fix is to bind it once, deliberately: "
            "`scitex-cards store adopt-uuid`. Do NOT set $%s to a uuid the "
            "database has never carried — that mints the claim instead of "
            "checking it.",
            db_path,
            db_uuid,
            expected,
            ENV_EXPECTED_STORE_UUID,
            ENV_EXPECTED_STORE_UUID,
        )
        return False

    # ADOPT — no identity AND no expectation, so nothing has been claimed and
    # nothing is in conflict. There is no weaker evidence to fall back on: a
    # server has no filesystem identity, so the first write claims the store by
    # stamping it. ``store_path`` is accepted for the caller's own messages and
    # sidecar naming; it is NOT a second identity axis, and treating it as one
    # is what let a write stamp a different value than reads compared against.
    return True


__all__ = [
    "_db_mirrors_this_store",
    "_same_file",
]

# EOF
