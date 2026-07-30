#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MIN-CLIENT-VERSION FLOOR — an outdated client ERRORS, never warns.

THE INCIDENT (operator directive 2026-07-21). Three stale venvs misbehaved
against the shared store on the same day: a 0.17.1 project venv, a
partial-0.17.4 container venv, and a 0.17.2 service venv — one of them
served an EMPTY example board. Each had a chance to warn; none of the
warnings were read. 「普通は warning ですが、私たちはエラーを選びます」 —
the operator's ruling: normally this would be a warning; we choose an error.

THE MECHANISM
--------------
``schema_meta`` may carry a ``min_client_version`` row (a plain "X.Y.Z"
string). Every process that OPENS the database — read or write, CLI, MCP,
or library — compares its own running version against that floor
(:func:`enforce_min_client_version`, called from
:func:`scitex_cards._db.connect`, the ONE function both the read path
(``_store_read_sqlite.list_tasks_sqlite``) and the write path
(``_db_mirror.mirror_doc_incremental`` via ``_db.open_db``) open every
connection through). Below the floor: :class:`ClientTooOldError` — a RAISE,
not a log line — carrying the exact upgrade command.

MISSING KEY MEANS NO FLOOR. An old database that predates this feature (or
one where nobody has deliberately set a floor) has no ``min_client_version``
row, and :func:`read_floor` returns ``None`` — the gate is then a no-op. Old
databases keep working until a floor is DELIBERATELY set (``scitex-cards db
set-min-client-version``, see ``_cli/_min_client_version.py``); nothing in
this module ever sets one on its own. Auto-bumping on an ordinary write is
exactly the failure this must not become — a mid-fleet upgrade would
cascade-brick every agent still running the previous release.

VERSION RESOLUTION — no network, no new dependency. ``importlib.metadata``
first (the normal installed/wheel case); when that dist is not registered
(an editable checkout whose ``.dist-info`` can be stale or simply absent —
see :mod:`scitex_cards._install_probe` for the fuller story of why that
metadata drifts), fall back to parsing ``project.version`` out of the
nearest ``pyproject.toml`` walking up from this file. :data:`_UNKNOWN_VERSION`
only if NEITHER resolves — that is a broken install, not a normal editable
one, and must read as "too old" rather than silently bypass the gate.

COMPARISON. Both the floor and the running version are parsed into a plain
tuple of ints (:func:`parse_version_tuple`) — no semver dependency. A
non-numeric suffix (``"...+local"``, ``"...-rc1"``) is tolerated by keeping
only the LEADING digits of the segment that carries it.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

#: ``schema_meta`` key holding the store's minimum-client-version floor.
KEY_MIN_CLIENT_VERSION = "min_client_version"

#: Distribution names to try, in order — the current name first, then the
#: pre-rename name (mirrors ``scitex_cards.__version__``'s own fallback).
_DIST_NAMES = ("scitex-cards", "scitex-todo")

#: Version used when NEITHER importlib.metadata NOR a pyproject.toml can be
#: found. Deliberately the smallest possible version — an install this
#: broken must read as "too old" against any real floor, never bypass it.
_UNKNOWN_VERSION = "0.0.0"

#: How far up from this file to look for the tree's ``pyproject.toml``. 5
#: covers ``<root>/src/scitex_cards/_min_client_version.py`` with headroom;
#: an unbounded walk would climb out of the project on a stray layout.
_PYPROJECT_SEARCH_DEPTH = 5

_LEADING_DIGITS_RE = re.compile(r"^\d+")

#: The exact remediation the operator asked for in the error message.
UPGRADE_COMMANDS = (
    "pip install -U scitex-cards",
    "uv pip install -e ~/proj/scitex-cards[all]   (editable checkout)",
)


class ClientTooOldError(RuntimeError):
    """The running scitex-cards client is older than the store's floor.

    Raised at the DB-open chokepoint (:func:`scitex_cards._db.connect`) —
    before any row is read or written — so an outdated client never even
    sees a partial or misinterpreted board.
    """


def parse_version_tuple(version: str) -> tuple[int, ...]:
    """Numeric tuple parse of an "X.Y.Z" version string, tolerant of suffixes.

    Splits on ``.``; each segment contributes the value of its LEADING run
    of digits (``"4-rc1"`` -> ``4``, ``"0+local"`` -> ``0``), so a
    pre-release or local-build suffix on the final segment does not break
    the comparison. A segment with no leading digit at all contributes
    ``0``. Deliberately the plain tuple compare the spec calls for — not a
    full semver parser, and no new dependency.
    """
    parts: list[int] = []
    for segment in version.strip().split("."):
        match = _LEADING_DIGITS_RE.match(segment)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts)


def _pyproject_version(start_file: Path) -> str | None:
    """``project.version`` from the nearest ``pyproject.toml`` walking up.

    The editable-install fallback: the code came from this tree, so the
    tree's declared version is what the code actually IS, whatever a stale
    (or absent) ``.dist-info`` claims. Mirrors
    :func:`scitex_cards._install_probe._read_pyproject_version` /
    ``_find_source_root``, kept standalone here so the hot DB-open path does
    not pull in that module's heavier probing.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py<3.11, unsupported here
        return None
    cur = start_file.parent
    for _ in range(_PYPROJECT_SEARCH_DEPTH):
        candidate = cur / "pyproject.toml"
        if candidate.is_file():
            try:
                with candidate.open("rb") as fh:
                    data = tomllib.load(fh)
            except (OSError, ValueError):
                return None
            project = data.get("project")
            if isinstance(project, dict):
                v = project.get("version")
                if isinstance(v, str):
                    return v
            return None
        if cur.parent == cur:  # filesystem root
            break
        cur = cur.parent
    return None


def resolve_running_version() -> str:
    """The RUNNING scitex-cards client's own version. Never raises.

    Precedence: ``importlib.metadata`` for the current dist name, then the
    pre-rename name, then a parsed ``pyproject.toml`` (editable installs),
    then :data:`_UNKNOWN_VERSION`. Re-resolved on every call (no caching) —
    the DB-open path already does real filesystem + SQL work per call, and a
    stale in-process cache here would recreate exactly the "warning nobody
    re-reads" failure mode this feature exists to replace.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _dist_version

    for dist in _DIST_NAMES:
        try:
            return _dist_version(dist)
        except PackageNotFoundError:
            continue

    pyproject_version = _pyproject_version(Path(__file__))
    return pyproject_version or _UNKNOWN_VERSION


def read_floor(conn: sqlite3.Connection) -> str | None:
    """The stamped ``min_client_version`` floor, or ``None`` if unset.

    ``None`` covers BOTH "the key is absent" and "the ``schema_meta`` table
    does not exist yet" (a brand-new, not-yet-initialised database — see
    :func:`scitex_cards._db.connect`, which runs before
    :func:`scitex_cards._db.init_schema` on a fresh file). Either way: no
    floor has ever been set, so the gate is a no-op.
    """
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (KEY_MIN_CLIENT_VERSION,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]) if row is not None else None


def stamp_floor(conn: sqlite3.Connection, version: str) -> None:
    """Set (or replace) the store's ``min_client_version`` floor.

    Call inside the caller's own write transaction — this does not commit.
    The ONLY writer should be the deliberate admin verb (``scitex-cards db
    set-min-client-version``, see ``_cli/_min_client_version.py``);
    ordinary card reads/writes must never call this, or a routine write from
    a newer agent would cascade-brick every OLDER agent still running
    against the same store.
    """
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY_MIN_CLIENT_VERSION, version),
    )


def enforce_min_client_version(conn: sqlite3.Connection) -> None:
    """RAISE :class:`ClientTooOldError` if this client is below the store's floor.

    A no-op when no floor is stamped (:func:`read_floor` returns ``None``)
    or when the running client (:func:`resolve_running_version`) meets it.
    Called from :func:`scitex_cards._db.connect` — the one function both the
    read path and the write path open every SQLite connection through — so
    this single call site gates both.
    """
    floor = read_floor(conn)
    if floor is None:
        return
    running = resolve_running_version()
    if parse_version_tuple(running) >= parse_version_tuple(floor):
        return
    commands = "\n    ".join(UPGRADE_COMMANDS)
    raise ClientTooOldError(
        f"this scitex-cards client is {running}, but the store at this "
        f"database requires at least {floor}. Upgrade before touching this "
        f"store:\n    {commands}"
    )


__all__ = [
    "KEY_MIN_CLIENT_VERSION",
    "UPGRADE_COMMANDS",
    "ClientTooOldError",
    "enforce_min_client_version",
    "parse_version_tuple",
    "read_floor",
    "resolve_running_version",
    "stamp_floor",
]

# EOF
