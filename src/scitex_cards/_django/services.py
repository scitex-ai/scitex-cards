#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board service -- resolve + load the ONE canonical task store, with a cache.

Mirrors figrecipe's ``services.get_or_create_editor`` shape but read-only: the
board never mutates the store in this MVP, so the "board" is just the validated
task list plus its resolved path and a content signature. The cache avoids
re-loading the store on every poll while still picking up external edits.

Single canonical store (SQLite cutover)
---------------------------------------
SQLite is the only store. :func:`scitex_cards._model.load_tasks` reads the ONE
canonical DB (``resolve_db_path(None)``) and ignores the path argument, so the
board no longer globs per-project YAML lanes and unions them — every "source"
would read the same DB. The board therefore loads exactly one store.

THE DATABASE IS THE ONLY READ TARGET, AND THERE IS NO EMPTY FALLBACK
--------------------------------------------------------------------
``get_board`` reads the cards from the database unconditionally and RAISES when
it cannot. It has no ``else []``, no cached-empty, no second backend, and no env
flag selecting between them — because every one of those is a way for the board
to answer "0 cards" without having asked the store.

That is not a hypothetical. Twice now a read has degraded to empty by comparing
against a YAML file that stopped existing at the SQLite cutover: the
``_store_read_sqlite`` accelerator (2026-07-21, post-mortem in
:func:`_load_global_tasks`), and this module's own ``if store_exists else []``
gate on the ``tasks.yaml`` SIDECAR, which served the operator a 0-card board for
over a day while 2,654 cards sat in the database.

AN EMPTY BOARD IS THE WIPE SHAPE. A visible refusal is strictly better: it is
recoverable, it names its reason, and nobody builds on it. So emptiness here is
always READ (the store really holds no cards) and never INFERRED from a file
that is missing.

Cache invalidation keys on the DB's logical CONTENT version
(:func:`scitex_cards._store_write.store_generation`), not the store-identity
file's stat: a DB write never touches that file, so an mtime/inode key would
never invalidate and the board would serve a pre-write snapshot (reorder
priorities, refresh, still see the old order). ``store_generation`` is
read-stable (it hashes the logical document, not the ``.db`` bytes that WAL
rewrites on a plain read), so it changes on any card/user change and only then.
See :class:`BoardState`.
"""

import glob
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# In-process cache: store_path_str -> (BoardState, last_access_time)
_board_cache: Dict[str, Tuple["BoardState", float]] = {}
_CACHE_TTL_SECONDS = 3_600  # 1 hour

#: Env override for the per-project lane discovery glob. Comma-separated.
#: Default ``~/proj/*/.scitex/cards/tasks.yaml`` covers the operator's
#: layout; other hosts can override (e.g. ``~/work/*/.scitex/cards/
#: tasks.yaml``) without code change.
ENV_LANE_GLOBS = "SCITEX_CARDS_LANE_GLOBS"
DEFAULT_LANE_GLOBS = "~/proj/*/.scitex/cards/tasks.yaml"


@dataclass
class BoardState:
    """A resolved, validated task store snapshot.

    ``tasks`` is the ONE canonical store's task list. ``store_path`` is the
    resolved store-identity path (the resolution anchor + provenance label);
    ``mtime`` is that file's mtime.

    ``mtime`` is REPORTED, not trusted: it is part of the ``/rev`` wire
    contract the frontend polls, and it stays a float for that reason.
    Cache invalidation keys off ``sig`` instead — see below.
    """

    tasks: list
    store_path: Path
    mtime: float
    #: CACHE IDENTITY — ``(store_generation, stat_sig)``:
    #:
    #:  * ``store_generation`` is the DB's read-stable logical-content hash
    #:    (:func:`scitex_cards._store_write.store_generation`). It changes on
    #:    ANY card/user change and only then, so a DB write self-invalidates
    #:    the board even though it never touches the identity FILE. This is the
    #:    load-bearing component: keying on the file's stat alone left the board
    #:    serving a pre-write snapshot after every DB write (the staleness this
    #:    replaces). It is read-stable — it hashes the logical document, NOT the
    #:    ``.db`` bytes WAL rewrites on a plain read — so a read never falsely
    #:    invalidates.
    #:  * ``stat_sig`` is the identity file's ``(mtime_ns, size, inode)``
    #:    (:func:`_stat_sig`). Retained as a second signal so a test/harness
    #:    that moves the identity file's stat still invalidates, matching the
    #:    old YAML-mtime self-invalidation. Never used as a lone ``st_mtime``
    #:    equality key (a 1-second-granular fs reports an unchanged mtime across
    #:    a real write); size + inode close the same-length-edit hole.
    #:
    #: Defaulted so existing constructions keep working; a board built without
    #: a sig simply never matches a cache probe, which fails CLOSED (rebuild)
    #: rather than serving something stale.
    sig: tuple = ()
    # P10 (lead a2a 2026-06-12) — user-defined project clusters loaded
    # from the same store. Empty list when the store has no ``groups:`` key
    # (back-compat). See :mod:`scitex_cards._groups`.
    groups: list = None  # type: ignore[assignment]
    #: Retired: the board reads ONE canonical store, so no per-project lanes
    #: are unioned. Kept (always empty) for the BoardState wire shape callers
    #: still read.
    lane_paths: List[Path] = field(default_factory=list)
    #: HONEST EMPTY STATE — True when the canonical store was READ SUCCESSFULLY
    #: and held zero cards. The read handlers forward it so the frontend can
    #: render the normal zero-card board instead of an error banner.
    #:
    #: IT IS DERIVED FROM THE READ, NEVER INFERRED FROM A MISSING FILE, and that
    #: is the whole point. It used to mean "the resolved store-identity FILE did
    #: not exist" — but under SQLite that file is the ``tasks.yaml`` SIDECAR,
    #: which stopped existing at the cutover, so on the live board this flag was
    #: permanently True and the board permanently served ``[]`` while 2,654 cards
    #: sat in the database. Worse than the missing cards: ``empty_store=True``
    #: tells the frontend to render a CLEAN, BELIEVABLE zero-card board — the
    #: exact visual signature of a wipe, with no error anywhere.
    #:
    #: Reaching this field at all now means the store was read. A store that
    #: CANNOT be read raises out of ``get_board`` instead (see
    #: :func:`scitex_cards._store._read_canonical_db_or_raise`): emptiness must
    #: be READ, never inferred. Snapshot-stamped here (not re-derived per
    #: request) so it stays consistent with ``tasks``.
    empty_store: bool = False


def _discover_lanes() -> List[Path]:
    """Return the list of per-project lane paths to union into the board.

    Honors :data:`ENV_LANE_GLOBS` (comma-separated) when set; otherwise
    uses :data:`DEFAULT_LANE_GLOBS`. Each entry is expanded via
    ``Path.expanduser`` and then ``glob.glob`` so ``~`` works as
    expected and `*` matches per-repo dirs. Non-existent / non-file
    entries are filtered out silently (a never-created lane is not a
    failure — it just isn't a source).
    """
    raw = os.environ.get(ENV_LANE_GLOBS)
    if raw is None:
        raw = DEFAULT_LANE_GLOBS
    # An explicitly-empty env opts out of lane discovery entirely
    # (useful for tests + for hosts that don't want the rollup).
    patterns = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Path] = []
    for pattern in patterns:
        expanded = str(Path(pattern).expanduser())
        for match in glob.glob(expanded):
            mpath = Path(match)
            if mpath.is_file():
                out.append(mpath)
    return sorted(set(out))


def _stat_sig(path: Path) -> tuple:
    """``(mtime_ns, size, inode)`` for one file — the unit of cache identity.

    Pairing the stamp with the SIZE is what makes this survive a filesystem
    with 1-second timestamp granularity, where a write and the stat after it
    can report the same mtime. See ``BoardState.sig``.

    SIZE ALONE IS NOT ENOUGH, and it is worth being exact about why rather
    than claiming this closes the hole: ``st_mtime_ns`` is nanosecond-TYPED,
    not nanosecond-ACCURATE — on a filesystem that stamps whole seconds the
    sub-second digits are simply zero. So (mtime_ns, size) still collides for
    a SAME-LENGTH edit inside one granule: flipping a priority ``1`` -> ``2``,
    or swapping a status for another of equal length, rewrites the file
    without moving either component.

    INODE closes that. Every write to this store lands via atomic
    ``os.replace`` of a freshly-written temp file, which allocates a NEW
    inode — so the inode changes on every write regardless of what the clock
    or the length did. Inode numbers can be recycled, so this is not a
    guarantee in the abstract; but a collision would need the same timestamp
    granule AND an identical size AND a recycled inode at once.

    NEVER RAISES. A lane can be deleted between being loaded and being
    fingerprinted, and the board must not 500 because a file went away — the
    surrounding code is deliberately per-lane fail-soft and this has to match
    it. A vanished file yields ``(0, 0, 0)``, which DIFFERS from whatever it
    was, so the cache correctly invalidates instead of pinning the old board.
    """
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return (0, 0, 0)


#: Board keys whose background refresh is already in flight. Guards against a
#: refresh storm: the operator's browser polls every few seconds, and without
#: this every poll during a slow rebuild would spawn another rebuild.
_refreshing: set = set()
_refresh_guard = threading.Lock()

#: Stale-while-revalidate on the human board view. On by default; set
#: ``SCITEX_CARDS_BOARD_SWR=0`` to force blocking rebuilds (debugging, or a
#: deployment that would rather be slow than one cycle behind).
_ENV_SWR = "SCITEX_CARDS_BOARD_SWR"


def _swr_enabled() -> bool:
    return os.environ.get(_ENV_SWR, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _kick_board_refresh(
    key, resolved, effective_mtime, effective_sig, loader=None
) -> None:
    """Rebuild this board off the request path, once at a time.

    ``loader`` is the card reader, defaulting to :func:`_load_global_tasks`.
    It is forwarded from :func:`get_board` so the BACKGROUND read uses the
    same reader the foreground one did — a seam that stopped at the request
    path would leave this thread, the part that actually revalidates, on the
    real loader and untestable.

    Fail-soft by construction: if the rebuild raises, the cache keeps the
    older board and the next request tries again — a background refresh must
    never be able to blank the operator's board. ``effective_sig`` is the sig
    the foreground read already computed, stamped on the fresh board verbatim
    so the NEXT read hits instead of re-kicking.
    """
    with _refresh_guard:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def _run():
        try:
            # SAME READ RULE AS THE FOREGROUND LOAD IN ``get_board``: the tasks
            # come from the canonical DATABASE, unconditionally. This used to
            # read them only ``if resolved.exists()`` — gating the card read on
            # the YAML SIDECAR — so a refresh could cache an EMPTY board and,
            # because the ``except`` below deliberately keeps whatever is
            # cached, serve it silently forever on /graph and /timeline. A
            # background refresh must never be able to blank the board; the way
            # to guarantee that is to have no empty-fallback here at all.
            read_tasks = _load_global_tasks if loader is None else loader
            tasks = read_tasks(resolved)
            task_ids = {t["id"] for t in tasks if isinstance(t, dict) and t.get("id")}
            groups = _load_sidecar_groups(resolved, task_ids)
            fresh = BoardState(
                tasks=tasks,
                store_path=resolved,
                mtime=effective_mtime,
                sig=effective_sig,
                groups=groups,
                lane_paths=[],
                empty_store=not tasks,
            )
            _board_cache[key] = (fresh, time.time())
        except Exception:  # noqa: BLE001 — never break the served board
            logger.warning(
                "[scitex-cards] background board refresh failed; keeping the "
                "previous board (next request retries)",
                exc_info=True,
            )
        finally:
            with _refresh_guard:
                _refreshing.discard(key)

    threading.Thread(
        target=_run, name=f"board-refresh-{Path(key).name}", daemon=True
    ).start()


def _load_global_tasks(path: Path) -> list:
    """Global store rows — from the ONE canonical SQLite database.

    SQLite is the store; there is no other backend and no mirror to prefer over
    it (see :mod:`scitex_cards._store_backend`). This used to also try a
    SQLite-INDEXED accelerator (``_store_read_sqlite`` — S2) ahead of
    :func:`load_tasks`, guarded by a freshness check comparing the database's
    provenance stamp against a YAML file. That accelerator is DELETED
    (2026-07-21 incident): once SQLite became canonical the YAML the stamp
    compared against stopped existing, so the guard refused unconditionally and
    fell back to a YAML chain that resolved to an empty bundled example —
    silently serving a blank board. ``path`` is accepted for the caller's
    identity/cache-invalidation bookkeeping; :func:`load_tasks` itself ignores
    it and reads the resolved canonical database.
    """
    from scitex_cards._model import load_tasks

    return load_tasks(path)


def _load_sidecar_groups(resolved: Path, task_ids: set) -> list:
    """``groups:`` from the YAML SIDECAR — absent sidecar means no groups.

    THE ONLY THING IN THE BOARD THAT MAY LEGITIMATELY DEGRADE TO EMPTY, and
    that is not a hedge — groups genuinely live in the ``tasks.yaml`` sidecar
    (see :func:`scitex_cards._paths.resolve_tasks_path`), so its absence is a
    direct, positive reading of "no groups are defined": the board falls back
    to its flat column view. Groups are a VIEWER concern; they carry no card
    data, and rendering none of them loses nothing.

    Cards are different in kind and must NOT share this shape. They live in the
    database, so an absent sidecar says nothing whatsoever about them — and
    reusing this existence test on the card read is precisely the defect this
    helper exists to keep separated. It is a named function rather than an
    inline ``if resolved.exists()`` so the next reader cannot mistake one for
    the other, or extend the guard from groups to tasks by moving a line.
    """
    from scitex_cards._groups import load_groups

    if not resolved.exists():
        return []
    return load_groups(resolved, task_ids=task_ids)


def get_board(
    tasks_path: Optional[str] = None, *, allow_stale: bool = False, loader=None
) -> BoardState:
    """Resolve the task store, load + validate it, and cache by mtime.

    ``allow_stale`` opts THIS call into stale-while-revalidate: when the store
    has moved on, the cached board is returned immediately and the rebuild
    runs behind the response. It is OFF by default and must stay that way —
    an endpoint that writes and then reads back (the chat POST is the live
    example) MUST see its own write, and a caller that opts in without that
    property is a read-your-own-writes bug. Only the human BOARD read opts
    in, because a self-refreshing view one cycle behind is invisible while a
    31-second wait is not.

    Reads the ONE canonical store (SQLite): ``load_tasks`` ignores the resolved
    path and reads ``resolve_db_path(None)``. Cache invalidation keys on
    ``sig`` = ``(store_generation(resolved), _stat_sig(resolved))`` — the DB's
    read-stable content hash so a DB write self-invalidates, plus the identity
    file's stat so a harness moving that file also invalidates. See
    :class:`BoardState`.

    Parameters
    ----------
    tasks_path : str or None
        Optional explicit store-identity path. When ``None``, the standard
        resolution chain is used. Names which logical store is addressed;
        the card DATA always comes from the one canonical DB.

    Returns
    -------
    BoardState
        The validated task list + the resolved store anchor + its content sig.
        ``empty_store`` is True only when the store was read and held no cards.

    Raises
    ------
    RuntimeError
        When the canonical database cannot be read as this process's store —
        it does not exist, it is stamped for a DIFFERENT store, or its export
        disagrees with its own ``COUNT(*)``. THIS FUNCTION HAS NO EMPTY
        FALLBACK, deliberately: a board that cannot read the store must say so
        (the caller renders a 500 carrying this message), because 0 cards
        rendered as a healthy board is indistinguishable from a wipe and is
        how the operator's board sat silently blank for a day.
    """
    from scitex_cards._paths import resolve_tasks_path
    from scitex_cards._store_write import store_generation

    from ._revision import store_change_stamp

    _cleanup_expired()

    resolved = resolve_tasks_path(tasks_path)
    # ``resolved`` IS NOT THE STORE. It is the ``tasks.yaml`` SIDECAR beside the
    # canonical database — the ``groups:`` container, the cache key, the
    # provenance label the board displays. ``resolve_tasks_path``'s own
    # docstring says so in its first line: "the non-task YAML CONTAINER path —
    # NOT the store identity ... Card DATA lives in the database."
    #
    # THE BUG THIS REPLACES read that sentence and then used the sidecar's
    # existence as the existence test for the STORE:
    #
    #     store_exists = resolved.exists()
    #     tasks = _load_global_tasks(resolved) if store_exists else []
    #
    # Under SQLite that sidecar is not created, so on the operator's live board
    # ``store_exists`` was permanently False and the board served the literal
    # ``else []`` — 0 cards, while 2,654 sat in the database, for over a day.
    # The card read was never even attempted: the fail-loud guard in
    # ``_read_canonical_db_or_raise`` never ran, so nothing anywhere reported a
    # problem. It rendered as a clean, healthy, empty board — the visual
    # signature of a wipe — because ``empty_store=True`` suppresses the error
    # banner by design.
    #
    # This is the SAME defect as the deleted ``_store_read_sqlite`` accelerator
    # (2026-07-21), whose post-mortem sits 40 lines above in
    # ``_load_global_tasks``: a guard comparing against a YAML file that stopped
    # existing when SQLite became canonical, silently degrading to an empty
    # board. Fixing one instance of a pattern is not fixing the pattern.
    #
    # So the sidecar now gates ONLY what actually lives in the sidecar
    # (``groups:`` — see ``_load_sidecar_groups``), and the cards are read
    # unconditionally from the database, which raises when it cannot be read.
    sidecar_exists = resolved.exists()

    # THE STORE'S CHANGE STAMP IS THE DATABASE'S, never the sidecar's. Reported
    # only (it is the ``/rev`` wire contract the frontend fingerprints on),
    # never the cache key — that is ``sig``. Both backends are handled in
    # ``store_change_stamp``, which documents why a server store reports a
    # content fingerprint there rather than a time.
    #
    # CACHE IDENTITY: the DB's logical-content version (the load-bearing signal
    # — a DB write self-invalidates even though it never touches the identity
    # file) paired with the identity file's stat (so a harness moving that file
    # still invalidates, matching the old self-invalidation). Both read-stable:
    # store_generation hashes the logical doc (not the .db bytes WAL rewrites),
    # and a plain read never writes the identity file. See BoardState.sig.
    generation = store_generation(resolved)
    effective_mtime = store_change_stamp(generation)
    effective_sig = (
        generation,
        _stat_sig(resolved) if sidecar_exists else (0, 0, 0),
    )

    key = str(resolved)
    cached = _board_cache.get(key)
    if cached is not None:
        board, _ = cached
        if board.sig and board.sig == effective_sig:
            _board_cache[key] = (board, time.time())
            return board
        # STALE-WHILE-REVALIDATE. The store changed, so this cached board is
        # a few seconds behind — but rebuilding it costs a full store read, and
        # the fleet writes every few seconds, so a blocking rebuild means the
        # operator pays that on nearly every view.
        #
        # A BOARD IS A LIVE VIEW, NOT A DECISION READ: it self-refreshes every
        # few seconds and shows a LIVE badge, so serving data that is one
        # refresh-cycle old is invisible, while a multi-second wait is not. The
        # refresh runs behind this response and the NEXT poll picks it up.
        #
        # Deliberately NOT applied to agent reads (list_tasks and friends go
        # through the store API, never here) — an agent deciding what to work
        # on must not act on a stale slice. This is the human-view path only.
        if allow_stale and _swr_enabled():
            _kick_board_refresh(
                key, resolved, effective_mtime, effective_sig, loader
            )
            return board

    # THE CARD READ — UNCONDITIONAL, AND THERE IS NO ELSE BRANCH. Every way this
    # can fail (database absent, stamped for another store, an export that
    # under-reports against its own ``COUNT(*)``) raises out of
    # ``_read_canonical_db_or_raise`` and reaches the operator as a 500 naming
    # the reason. That is the point: a refusal is recoverable and visible, a
    # believable empty board is neither. An ``else []`` here would be a second
    # read target that merely happens to be unreachable today.
    tasks = (_load_global_tasks if loader is None else loader)(resolved)

    task_ids = {t["id"] for t in tasks if isinstance(t, dict) and t.get("id")}
    groups = _load_sidecar_groups(resolved, task_ids)

    board = BoardState(
        tasks=tasks,
        store_path=resolved,
        mtime=effective_mtime,
        sig=effective_sig,
        groups=groups,
        lane_paths=[],
        # READ, not inferred: we got here, so the store was read; it is empty
        # iff it actually holds no cards. See BoardState.empty_store.
        empty_store=not tasks,
    )
    _board_cache[key] = (board, time.time())
    logger.info(
        "[scitex-cards] Loaded board from %s (%d tasks, %d groups)",
        resolved,
        len(tasks),
        len(groups),
    )
    return board


def _cleanup_expired() -> None:
    """Drop board snapshots untouched for longer than the TTL."""
    now = time.time()
    expired = [
        k for k, (_, ts) in _board_cache.items() if now - ts > _CACHE_TTL_SECONDS
    ]
    for k in expired:
        _board_cache.pop(k, None)


def _reset_cache() -> None:
    """Clear the cache (test hook)."""
    _board_cache.clear()


# EOF
