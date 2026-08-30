#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DM message REACTIONS — an APPEND-ONLY event log.

Backs the ``/chat`` view's right-click "React" action. Companion to
:mod:`scitex_cards._threads` (the DM messages themselves), deliberately kept
in its OWN file with its OWN lock.

Why an event log and not a field on the message
------------------------------------------------
``docs/design/dm-into-cards-db.md`` (the merged v5 design) moves DM read state
OUT of the message record and into an insert-only ``dm_receipts`` table,
precisely so ``dm_messages`` rows become immutable apart from their tombstone
— that immutability is what makes a cross-host merge a pure union (§3.2, §6.3).
Storing reactions as a mutable ``reactions`` dict on the record would add a
SECOND mutable field to the very record the design is working to freeze, i.e.
it would have to be undone again at migration time.

So reactions are modelled the way that design models membership and receipts:
an append-only EVENT LOG whose fold is the current state. One row here is one
row of a future ``dm_reaction_events`` table —

    id | thread_id | message_id | actor | emoji | action | ts

— so the eventual migration is an ``INSERT OR IGNORE`` of the same rows with
no transformation. Nothing about the message records changes, then or now.

Why a separate file and not a key in ``threads.json``
-----------------------------------------------------
``_threads._save_threads_unlocked`` writes ``{"threads": ...}`` and nothing
else. A second top-level key in that document would be SILENTLY DROPPED by
every existing writer (``append_message``, ``mark_read``) — the exact failure
class the design doc names when it recounts how a doc write that rebuilt
``messages`` would have deleted every DM thread (§1.5). A separate file cannot
be dropped by a writer that does not know about it.

This is the PRE-MIGRATION home of this data, and it says so. It does not widen
``threads.json``, it does not mutate a message, and it leaves every existing
reader and writer untouched.

APPEND-ONLY (operator ruling 「一度書いたものは消えない」)
---------------------------------------------------------
A written event never disappears. "Un-reacting" appends a ``remove`` event; it
does not delete the ``add``. :func:`_save_events_unlocked` refuses to write a
document with fewer events than the one it replaces — a count decrease is a
bug, not a warning (mirrors ``_store_backend._assert_no_shrink``).

Ordering is APPEND ORDER, which within one file reproduces exactly the
``rowid`` semantics the DM messages already use (design §3.6: "within one
database it reproduces exactly today's append order").
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
from pathlib import Path

from ._paths import local_store_path

#: Sidecar filename, sibling of the resolved task store and of ``threads.json``.
REACTIONS_FILENAME = "dm_reactions.json"

#: Top-level key of the sidecar document.
_EVENTS_KEY = "reaction_events"

#: Event-id prefix (``dmr_`` + 12 hex chars — the ``m_``/``u_``/``n_`` shape).
_EVENT_ID_PREFIX = "dmr_"
_EVENT_ID_TOKEN_HEX = 12

#: The two legal actions. ``remove`` is an event, never a deletion.
ACTION_ADD = "add"
ACTION_REMOVE = "remove"
ACTIONS = (ACTION_ADD, ACTION_REMOVE)

#: The curated palette the FULL picker offers — LITERAL unicode, no icon font.
#:
#: Same reasoning as the chat composer's literal-text paperclip: the board is
#: served over a tunnel to a phone, and a glyph that fails to resolve leaves an
#: empty box exactly where the affordance was.
#:
#: This is the PICKER's set, not a validation allowlist — see
#: :func:`validate_emoji`.
#:
#: 👎 IS DELIBERATELY ABSENT. Operator, verbatim: 「親指の下向きのやつはあまり
#: 好きじゃない、下品」. ❌ already carries "no" without the gesture. A test
#: asserts the absence in BOTH languages, so re-adding it as an obvious default
#: fails CI rather than reaching their phone.
REACTION_EMOJI = (
    "⭕",
    "❌",
    "❓",
    "\U0001f44d",
    "❤️",
    "\U0001f389",
    "✅",
    "\U0001f64f",
    "\U0001f440",
    "\U0001f525",
)

#: The QUICK row — the one-tap reactions rendered directly above the message
#: action list, per the operator's sketch of Telegram's menu.
#:
#: The first three are theirs by name: 「〇、×、？ がいい」. They are also the
#: entire operator↔agent decision vocabulary — approve, reject, query — which is
#: why they lead rather than trail the warm three.
#:
#: A SUBSET of :data:`REACTION_EMOJI` by construction, and a test pins that: the
#: row and the chevron's fuller picker are two views of one palette, and a row
#: emoji the picker did not know about would be a second palette free to drift.
#:
#: Six plus the chevron fits one 44px row inside a 375pt phone screen without
#: wrapping.
QUICK_REACTION_EMOJI = (
    "⭕",
    "❌",
    "❓",
    "\U0001f44d",
    "❤️",
    "\U0001f389",
)

#: Upper bound on a stored emoji, in characters. A single emoji can legitimately
#: be several code points (``❤️`` is U+2764 U+FE0F; a skin-tone modifier or a ZWJ
#: family sequence is longer still), so a length bound is the honest check — an
#: allowlist would reject valid input the moment the palette widened.
MAX_EMOJI_LEN = 32


# --------------------------------------------------------------------------- #
# Paths / small helpers                                                        #
# --------------------------------------------------------------------------- #
def reactions_path(store: str | Path | None = None) -> Path:
    """Resolve the sidecar path: ``<store_dir>/dm_reactions.json``.

    Deliberately resolved WITHOUT going through
    :func:`scitex_cards._threads.threads_path`: that function fires the legacy
    YAML migration as a side effect of being asked for a path (design §1.2 W3),
    and a path query must not write a file.
    """
    # ``local_store_path``, not a copy of its body -- a DSN is not a path, and
    # ``Path("postgresql://h/d")`` silently yields the RELATIVE
    # ``postgresql:/h/d``, whose parent the caller then creates. Two phantom
    # trees were measured under the repository working directory on
    # 2026-08-30 from exactly this line, duplicated.
    return local_store_path(store).parent / REACTIONS_FILENAME


def _utc_now_iso() -> str:
    """Second-resolution ISO-8601 UTC stamp — the same format DMs are stamped in."""
    from ._threads import _utc_now_iso as _stamp

    return _stamp()


def _generate_event_id() -> str:
    """Fresh reaction-event id (``dmr_`` + 12 hex chars)."""
    return _EVENT_ID_PREFIX + secrets.token_hex(_EVENT_ID_TOKEN_HEX // 2)


@contextlib.contextmanager
def _reactions_lock(path: Path):
    """Exclusive flock on the sidecar's OWN ``.dm_reactions.json.lock`` sentinel.

    Separate from both the task-store lock and the threads sidecar's lock, for
    the same reason those two are separate: a reaction tap must never convoy
    behind a card write or a message append.
    """
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


# --------------------------------------------------------------------------- #
# Load / save                                                                  #
# --------------------------------------------------------------------------- #
def _load_events(path: Path) -> list[dict]:
    """Read the event list off disk. NEVER raises on absence.

    Missing file / absent key / non-list value → ``[]``; non-dict entries drop.
    A malformed row never breaks a read.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle) or {}
    raw = data.get(_EVENTS_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


#: Parsed events per path, guarded by the file's ``(mtime_ns, size)`` — the same
#: cache contract as ``_threads._READ_CACHE``. READ-ONLY: callers copy on the way
#: out. A WRITER MUST NEVER BE SERVED FROM HERE (a stale read would drop events).
_READ_CACHE: dict[str, tuple[int, int, list[dict]]] = {}


def _load_events_cached(path: Path) -> list[dict]:
    """:func:`_load_events` memoized on the file's ``(mtime_ns, size)``.

    FOR READERS ONLY — the GUI re-folds this on every ~5s thread poll. Any
    write rolls the mtime forward, so no reader is served stale content across
    a write.
    """
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    cached = _READ_CACHE.get(key)
    if (
        cached is not None
        and cached[0] == stat.st_mtime_ns
        and cached[1] == stat.st_size
    ):
        return cached[2]
    events = _load_events(path)
    _READ_CACHE[key] = (stat.st_mtime_ns, stat.st_size, events)
    return events


def _save_events_unlocked(events: list[dict], path: Path, *, previous: int) -> None:
    """Crash-safe, SHRINK-REFUSING write of the whole event document.

    Mirrors ``_threads._save_threads_unlocked``: dump → sibling ``.tmp`` →
    fsync → REPARSE and verify the event count → ``os.replace`` (POSIX-atomic).

    ``previous`` is the event count this document replaces. Writing fewer
    events than that raises: the store is append-only, so a count decrease is
    itself the bug (``_store_backend._assert_no_shrink``, applied here).
    Callers must already hold :func:`_reactions_lock`.
    """
    if len(events) < previous:
        raise RuntimeError(
            f"refusing to replace {path}: reaction events are APPEND-ONLY and "
            f"this write would shrink the log from {previous} to {len(events)} "
            f"events. Canonical file left untouched."
        )
    doc = {_EVENTS_KEY: events}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass  # best-effort (overlay/fuse); os.replace is the swap
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = json.load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did not "
                f"reparse cleanly after dump ({type(verify_exc).__name__}: "
                f"{verify_exc}). Canonical file left untouched."
            ) from verify_exc
        verify_events = (
            verify_doc.get(_EVENTS_KEY) if isinstance(verify_doc, dict) else None
        )
        if not isinstance(verify_events, list) or len(verify_events) != len(events):
            have = len(verify_events) if isinstance(verify_events, list) else -1
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected payload ({have} events vs in-memory "
                f"{len(events)}). Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def validate_emoji(emoji: object) -> str:
    """Return ``emoji`` as a clean string, or raise ``ValueError``.

    Bounded by length rather than by an allowlist. :data:`REACTION_EMOJI` is
    what the PICKER offers; pinning the endpoint to that tuple would make a
    wider palette a server change, and would reject a perfectly valid
    skin-toned or ZWJ-sequence emoji a future client sends.
    """
    if not isinstance(emoji, str) or not emoji.strip():
        raise ValueError("reaction requires a non-empty 'emoji'")
    text = emoji.strip()
    if len(text) > MAX_EMOJI_LEN:
        raise ValueError(
            f"reaction 'emoji' is {len(text)} chars, over the {MAX_EMOJI_LEN} "
            "char limit — this field holds one emoji, not a message"
        )
    return text


def append_reaction_event(
    *,
    thread: str,
    message_id: str,
    actor: str,
    emoji: str,
    action: str = ACTION_ADD,
    store: str | Path | None = None,
    ts: str | None = None,
) -> dict:
    """Append ONE reaction event and return a copy of the stored record.

    ``action='remove'`` records an un-react. It APPENDS; it never deletes the
    matching ``add``. Idempotent in effect (the fold is last-writer-wins per
    ``(message_id, emoji, actor)``) but not in storage — every tap is a row,
    which is what makes the log auditable.
    """
    if not thread or not str(thread).strip():
        raise ValueError("append_reaction_event requires a non-empty 'thread'")
    if not message_id or not str(message_id).strip():
        raise ValueError("append_reaction_event requires a non-empty 'message_id'")
    if not actor or not str(actor).strip():
        raise ValueError("append_reaction_event requires a non-empty 'actor'")
    if action not in ACTIONS:
        raise ValueError(
            f"append_reaction_event got action={action!r}; valid: {list(ACTIONS)}"
        )
    record = {
        "id": _generate_event_id(),
        "thread": str(thread).strip(),
        "message_id": str(message_id).strip(),
        "actor": str(actor).strip(),
        "emoji": validate_emoji(emoji),
        "action": action,
        "ts": ts or _utc_now_iso(),
    }
    path = reactions_path(store)
    with _reactions_lock(path):
        events = _load_events(path)  # authoritative: never the cache
        previous = len(events)
        events.append(record)
        _save_events_unlocked(events, path, previous=previous)
    return dict(record)


def fold_events(events: list[dict], *, thread: str | None = None) -> dict:
    """Fold an event list into ``{message_id: {emoji: [actors...]}}``.

    PURE — no I/O, so the fold rule is testable on its own.

    Applied in append order, last event wins per ``(message_id, emoji,
    actor)``: ``add`` puts the actor in (first-reactor-first), ``remove`` takes
    them out. An emoji whose actor list empties is dropped, so the map never
    carries a zero-count chip. ``thread`` filters to one thread when given.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if thread is not None and event.get("thread") != thread:
            continue
        message_id = event.get("message_id")
        emoji = event.get("emoji")
        actor = event.get("actor")
        if not message_id or not emoji or not actor:
            continue
        per_message = out.setdefault(message_id, {})
        actors = per_message.setdefault(emoji, [])
        if event.get("action") == ACTION_REMOVE:
            if actor in actors:
                actors.remove(actor)
        elif actor not in actors:
            actors.append(actor)
        if not actors:
            per_message.pop(emoji, None)
        if not per_message:
            out.pop(message_id, None)
    return out


def thread_reactions(thread: str, *, store: str | Path | None = None) -> dict:
    """Current reaction state for one thread: ``{message_id: {emoji: [actors]}}``.

    Read path — served from the mtime-guarded cache and folded fresh each call.
    """
    return fold_events(_load_events_cached(reactions_path(store)), thread=thread)


def next_action(actors: list[str] | None, actor: str) -> str:
    """The action a TOGGLE by ``actor`` should record, given the current actors.

    PURE. Present → ``remove``; absent → ``add``. Shared by the endpoint and
    (in spirit) the browser, so the two cannot disagree about what a tap means.
    """
    return ACTION_REMOVE if actor in (actors or []) else ACTION_ADD


__all__ = [
    "ACTIONS",
    "ACTION_ADD",
    "ACTION_REMOVE",
    "MAX_EMOJI_LEN",
    "QUICK_REACTION_EMOJI",
    "REACTIONS_FILENAME",
    "REACTION_EMOJI",
    "append_reaction_event",
    "fold_events",
    "next_action",
    "reactions_path",
    "thread_reactions",
    "validate_emoji",
]

# EOF
