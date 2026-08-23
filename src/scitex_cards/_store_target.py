#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve the store TARGET without assuming it is a filesystem path.

WHY THIS IS NOT IN ``_db.resolve_db_path``. That function is typed ``-> Path``
and every one of its callers expects a ``Path``, so it cannot represent a
``postgresql://`` URL. What it does instead is worse than failing::

    SCITEX_CARDS_DB=postgresql://host/db  ->  Path("postgresql:/host/db")

a RELATIVE path, silently, with no error. A store URL that resolves to a path
is not a slightly-wrong answer -- it is a different store, and the caller then
creates an empty SQLite file at that name and reports a healthy, empty board.
That is the two-stores-both-look-healthy failure this package already has scar
tissue from.

Measured 2026-07-31: the ``_backend_connect`` seam and its paramstyle layer are
implemented and tested, and NOTHING in the package imports them -- every read
and write still calls ``sqlite3.connect`` directly. Path resolution is the
reason. Until the resolver can carry a URL, no call site can reach PostgreSQL
no matter what else is ported, which makes this the smallest change that
unblocks the rest.

This module deliberately does NOT change ``resolve_db_path``. Callers that
genuinely need a filesystem path (snapshots, backups, the on-disk health
probes) keep using it; callers that can address either backend take
:func:`resolve_store_target` and hand the result to ``_backend_connect.connect``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

from ._db import DEFAULT_DB_FILENAME, ENV_DB, PKG_SHORT
from ._store_url import BACKEND_SQLITE, backend_of, is_postgres_url

__all__ = [
    "StoreTargetIsNotAPath",
    "StoreTargetNotConfigured",
    "TIER_CONFIG",
    "TIER_DEFAULT",
    "TIER_ENV",
    "TIER_EXPLICIT",
    "database_for",
    "refuse_zero_config_default",
    "require_configured_store_target",
    "resolve_store_target",
    "resolve_store_backend",
    "resolve_store_tier",
    "require_db_path",
    "store_label",
]

#: Which tier of the precedence chain answered. Returned by
#: :func:`resolve_store_tier` so a caller can tell "somebody CHOSE this store"
#: from "nobody configured one and we invented a filename".
#:
#: THE DISTINCTION IS THE WHOLE POINT AND IT COST A WEEK. `resolve_store_target`
#: answers the same TYPE for all four tiers -- a string -- so a deployment that
#: never had a DSN and one that LOST its DSN are indistinguishable to every
#: caller. Measured 2026-08-09: the operator's board ran with no
#: ``SCITEX_CARDS_DB``, fell to ``TIER_DEFAULT``, and served a SQLite store
#: frozen on 2026-08-02 for a week -- rendering perfectly, raising nothing,
#: while the fleet wrote to PostgreSQL. His words: "NO SILENT FALLBACKS, it is
#: always the cause of troubles".
TIER_EXPLICIT = "explicit"
TIER_ENV = "env"
TIER_CONFIG = "config"
TIER_DEFAULT = "default"


class StoreTargetIsNotAPath(ValueError):
    """The resolved store is a URL, and the caller demanded a filesystem path.

    Raised instead of returning a mangled ``Path`` so the failure is loud at the
    call site that cannot cope, rather than silent at a call site that then
    creates the wrong store.
    """


def resolve_store_target(explicit: str | Path | None = None) -> str:
    """The store target AS WRITTEN -- a path or a URL, never coerced.

    Mirrors ``_db.resolve_db_path``'s precedence exactly (explicit argument,
    then ``$SCITEX_CARDS_DB``, then the deprecated ``$SCITEX_CARDS_DB``, then the
    config file) and differs only in refusing to turn the answer into a
    ``Path``.

    THERE IS NO TIER BELOW THE CONFIG FILE. Until 2026-08-13 this fell through
    to the ecosystem user-canonical default -- ``~/.scitex/cards/cards.db``, a
    SQLite filename nobody chose. It now RAISES
    :class:`StoreTargetNotConfigured`; see :func:`refuse_zero_config_default`.

    The deprecation warning is deliberately NOT re-emitted here -- ``_db``
    already warns on that tier, and warning twice for one resolution trains
    readers to ignore it.
    """
    if explicit is not None:
        return str(explicit)
    value = os.environ.get(ENV_DB)
    if value:
        return value
    # CONFIG TIER — below the environment, above the hardcoded default.
    #
    # Below env, so a per-agent or per-test override still wins and nothing that
    # worked before changes. Above the default, because the default is a
    # HARDCODED local filename: before this tier existed, every caller that did
    # not export $SCITEX_CARDS_DB silently resolved to a private SQLite file.
    # That is what let eight host-side writers keep using the old store through
    # the 2026-08-01 cutover while the fleet was believed migrated.
    #
    # An env var is a rule each caller must remember; a config file is a fact
    # the host states once.
    from ._config import store_config_target

    configured = store_config_target()
    if configured:
        return configured
    # ZERO-CONFIG DEFAULT TIER -- ABOLISHED 2026-08-13. This used to be the
    # same final tier as _db.resolve_db_path:
    #
    #     from scitex_config._ecosystem import local_state
    #     return str(local_state.user_path(PKG_SHORT, DEFAULT_DB_FILENAME))
    #
    # i.e. a SQLite filename nobody chose, returned as though somebody had.
    # `refuse_zero_config_default` still computes that filename -- but only to
    # NAME it in the refusal, never to hand it back as a store.
    #
    # WHY THE TIER AND NOT ANOTHER DOOR. Guarding one door at a time was the
    # standing policy (see `require_configured_store_target`), and measured
    # 2026-08-13 it had reached 1 of 31 production call sites while the fleet's
    # own hosts kept arriving here: on compute-04 `~/.bashrc` exports
    # $SCITEX_CARDS_DB *below* its non-interactive early-return, so every cron
    # job, systemd unit and script on the box saw the variable EMPTY and
    # resolved this tier. A guard that must be remembered at each new call site
    # is a guard that will be missing from the next one. The operator's ruling,
    # repeated and now final: SQLite is abolished fleet-wide, and the
    # error-prone option is better off not existing -- fewer choices is the
    # feature. So the tier itself stops answering.
    refuse_zero_config_default()


def database_for(target: str | Path) -> str | Path:
    """Map a resolved store target to a DATABASE, because a label is not one.

    A ``…/tasks.yaml`` target is a DISPLAY LABEL. ``_paths`` builds it as
    ``resolve_db_path(None).parent / "tasks.yaml"`` — good enough to NAME a
    store in a message, never a thing on disk. The YAML tier itself was deleted
    in #512, so nothing reads that file; the name outlived the format.

    HANDING THE LABEL STRAIGHT TO A CONNECTION IS DESTRUCTIVE, not merely
    wrong, and that is why this function exists rather than a comment asking
    callers to be careful. Nothing downstream normalises it —
    :func:`resolve_store_target` returns an explicit argument AS WRITTEN, and so
    do ``resolve_db_path`` and ``connect`` — so SQLite CREATES a database at
    that path, on top of whatever was there.

    MEASURED TWICE, in two subsystems, from the same missing guard:

    * 2026-08-17, the user registry: a registration landed in a PHANTOM STORE
      named ``tasks.yaml`` beside the real board, and ``resolve_user`` degraded
      to the raw name string for every peer.
    * 2026-08-20, the notification inbox: ``inbox_target`` had no guard at all,
      so ``inbox info`` — a verb named *info* — opened the card store as a
      SQLite database and wrote an ``inbox`` table into it. The live artifact:
      ``/home/agent/.scitex/cards/tasks.yaml``, 122880 bytes, magic
      ``SQLite format 3``, holding 150 rows. A file whose extension says YAML
      and whose contents are a database.

    The second one is the reason this moved OUT of ``_db_users`` and into the
    module that owns store-target resolution. The registry grew a private fix,
    the inbox never got one, and a guard that each subsystem must remember is a
    guard the next subsystem will be missing — the same argument the zero-config
    tier above was abolished on.

    A DSN passes through untouched: a server target is already a database.
    """
    text = str(target)
    if is_postgres_url(text):
        return text
    path = Path(text).expanduser()
    if path.suffix in (".yaml", ".yml"):
        return path.parent / DEFAULT_DB_FILENAME
    return path


class StoreTargetNotConfigured(RuntimeError):
    """NOBODY configured a store, and this caller refuses to invent one.

    Raised by :func:`require_configured_store_target` for long-running SERVERS,
    which are the callers that must never guess. A one-shot CLI landing on the
    zero-config default is a fresh install behaving correctly; a BOARD landing
    there is a deployment that lost its target and will now serve whatever
    happens to be at that filename, to everyone, indefinitely.

    SINCE 2026-08-13 EVERY CALLER GETS THIS, not just the servers. The sentence
    above described the trade that justified guarding one door at a time; the
    operator retired that trade (SQLite abolished fleet-wide) after the "fresh
    install behaving correctly" case turned out to be indistinguishable, from
    inside the process, from a cron job whose environment lost the DSN.
    :func:`refuse_zero_config_default` is now where it is raised, and both
    resolvers end there.
    """


def refuse_zero_config_default() -> NoReturn:
    """Refuse, loudly, where the zero-config SQLite default used to answer.

    THE ONE PLACE THIS TEXT LIVES, and the reason it is a function rather than
    two ``raise`` statements: the abolished tier had TWO implementations --
    :func:`resolve_store_target` and ``_db.resolve_db_path``, whose docstrings
    promise to mirror each other's precedence exactly. Two copies of a refusal
    is two things that can drift, and a tier that is closed in one resolver and
    open in the other is the same silent fallback with an extra step.

    THE FILENAME IS STILL COMPUTED, and only for the message. "No store is
    configured" is a diagnosis; naming the file that WOULD have been served is
    what makes it actionable, and it is how a reader recognises the store they
    have been unknowingly reading for a week. Imported lazily for exactly the
    reason the tier always was: a caller with an explicit or env target must
    not hard-require ``scitex_config`` to be importable.

    Raises
    ------
    StoreTargetNotConfigured
        Always. The return annotation is :data:`~typing.NoReturn` so a caller
        that forgets that is a type error rather than a caller that falls
        through and returns ``None`` where a target was promised.
    """
    from scitex_config._ecosystem import local_state

    target = str(local_state.user_path(PKG_SHORT, DEFAULT_DB_FILENAME))
    raise StoreTargetNotConfigured(
        f"REFUSING to serve: no store target is configured, so this would fall "
        f"back to the zero-config default {target!r} -- a filename, not a "
        f"decision. On 2026-08-09 that fallback served a store frozen eight "
        f"days earlier while the fleet wrote elsewhere, and it looked healthy "
        f"the whole time.\n"
        f"Set one of, in precedence order:\n"
        # PORT 55432, NEVER 5432. Operator ruling: 5432 is never scitex and
        # every reference to it is a defect. An example inside a refusal is the
        # worst place to carry one -- it is read by someone who is already lost
        # and looking for exactly this line to copy.
        f"  ${ENV_DB}   e.g. postgresql://scitex_cards@127.0.0.1:55432/scitex_cards\n"
        # The KEY PATH, not the section name. `store` alone sends the reader to
        # write {"store": "<dsn>"}, which _config's fail-soft branch discards in
        # silence -- landing them back here with no idea why.
        f"  the `store.target` key in the scitex-cards config file\n"
        f"Run `scitex-cards resolve-store` to see what this process resolves."
    )


def resolve_store_tier(explicit: str | Path | None = None) -> str:
    """WHICH TIER answers :func:`resolve_store_target` -- the missing signal.

    Returns one of :data:`TIER_EXPLICIT`, :data:`TIER_ENV`, :data:`TIER_CONFIG`,
    :data:`TIER_DEFAULT`.

    Deliberately mirrors ``resolve_store_target``'s precedence rather than
    sharing code with it. Sharing would mean returning a (target, tier) pair
    from one function and changing every existing caller's shape; duplicating
    four ``if`` statements is cheaper than that churn, and the pair is pinned by
    ``test_the_tier_and_the_target_agree_on_every_tier`` so they cannot drift.
    """
    if explicit is not None:
        return TIER_EXPLICIT
    if os.environ.get(ENV_DB):
        return TIER_ENV
    from ._config import store_config_target

    if store_config_target():
        return TIER_CONFIG
    return TIER_DEFAULT


def require_configured_store_target(explicit: str | Path | None = None) -> str:
    """The store target, but ONLY if somebody actually chose it.

    For SERVERS. ``gui serve`` and friends run unattended for days and are
    believed by whoever loads the page, so "I could not find a store, here is a
    filename I made up" is the one answer they must never give.

    RAISES :class:`StoreTargetNotConfigured` on :data:`TIER_DEFAULT`.

    NO LONGER THE ONLY DOOR, AND THAT IS THE POINT. This function was written
    under a deliberate restraint: making the DEFAULT tier raise everywhere would
    break every zero-config install and every test that relied on one, so the
    refusal was enforced at the doors where a guess does damage, one door at a
    time, each with a stated reason. Measured 2026-08-13, that policy had
    reached 1 of 31 production call sites, while compute-04's own cron jobs
    entered the default tier on every run. The operator abolished the tier
    instead, so :func:`resolve_store_target` now refuses at source and this
    function can no longer be the difference between safe and unsafe.

    IT IS KEPT, AND CALLED, ANYWAY. It is the NAMED, greppable statement that a
    server requires a chosen store -- ``_cli/_store_guard`` calls it to turn the
    refusal into a ``ClickException``, and a reader asking "what does serve
    require?" needs an answer that is a symbol, not an absence. The rule the
    constitution states -- fail fast, fail loud, no silent fallbacks, no
    surprises -- is now enforced at the resolver AND restated here.
    """
    # TIER FIRST, THEN THE TARGET. Resolving first would be dead code: since the
    # default tier refuses, `resolve_store_target` raises before any check here
    # could run. Asking the tier is the only question this function still owns.
    if resolve_store_tier(explicit) != TIER_DEFAULT:
        return resolve_store_target(explicit)
    refuse_zero_config_default()


def resolve_store_backend(explicit: str | Path | None = None) -> str:
    """Which backend the resolved target names, without opening anything."""
    return backend_of(resolve_store_target(explicit))


def require_db_path(explicit: str | Path | None = None) -> Path:
    """The resolved target as a ``Path``, or a loud refusal if it is a URL.

    For the callers that are genuinely filesystem-only. Use this rather than
    ``resolve_db_path`` wherever handing a URL to path logic would create a
    second store instead of erroring.
    """
    target = resolve_store_target(explicit)
    if is_postgres_url(target):
        raise StoreTargetIsNotAPath(
            f"the store resolves to a {backend_of(target)} URL, not a file; "
            "this caller requires a filesystem path. Use "
            "resolve_store_target() with _backend_connect.connect() instead of "
            "coercing the URL to a Path -- coercion yields a RELATIVE path and "
            "silently creates a different, empty store."
        )
    return Path(target).expanduser()


def store_label(explicit: str | Path | None = None) -> str:
    """The resolved store rendered FOR HUMANS -- safe to print, never a ``Path``.

    Exists because naming the store is not the same operation as opening it, and
    conflating the two broke working verbs. ``scitex-cards list-tasks`` read 301
    rows from PostgreSQL and then raised ``StoreTargetIsNotAPath`` -- not on the
    read, which had already succeeded, but on the header line above it, which
    called ``resolve_db_path`` purely to name where the rows came from. A label
    is a caption; it must not be able to fail the command it captions.

    Two properties this guarantees that ``str(resolve_store_target())`` does not:

    1. NO CREDENTIALS. A DSN may carry a password in its userinfo and secrets in
       its query string, and this string is printed to stdout and into logs. Both
       are stripped. The rest of the DSN is kept verbatim, because "which store
       am I looking at" is the entire question the label exists to answer, and a
       fully-masked label answers it no better than no label at all.
    2. NO ``Path``. ``Path`` collapses ``//`` to ``/``, turning
       ``postgresql://host/db`` into ``postgresql:/host/db`` -- a mangled string
       that has already sent two agents hunting a malformed-URL bug in a config
       that was correct, and that a stale client turned into a real directory
       tree on disk.
    """
    target = str(resolve_store_target(explicit))
    if not is_postgres_url(target):
        return target
    target = target.split("?", 1)[0]
    # Strip userinfo: scheme://user:secret@host -> scheme://user@host. Split on
    # the LAST '@' before the host, since a password may itself contain one.
    scheme, sep, rest = target.partition("://")
    if sep and "@" in rest:
        userinfo, _, hostpart = rest.rpartition("@")
        user = userinfo.split(":", 1)[0]
        rest = f"{user}@{hostpart}" if user else hostpart
    return f"{scheme}{sep}{rest}"


def _assert_sqlite_default() -> str:
    """Kept as a named check so the default cannot drift unnoticed."""
    return BACKEND_SQLITE


# EOF
