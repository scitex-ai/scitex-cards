#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo wake-watcher — push side of the self-consuming board loop.

Polls the task store on a fixed interval (default 30s, hard floor 10s —
raised from 2s after the 2026-07-08 death-spiral incident), diffs
against the previous snapshot, and POSTs to the owning agent's local
sac a2a ``/v1/turn`` endpoint when:

  * a NEW task is added (``task.id`` absent from the previous snapshot);
  * a comment is appended (``len(task.comments)`` grew);
  * status flipped (``previous.status != current.status``).

Wake payload is small + stable so a future Gitea-webhook variant can
emit the same shape::

    {
      "trigger":      "scitex-todo-watcher",
      "trigger_kind": "task_added" | "comment" | "status_changed",
      "task_id":      "<id>",
      "task_title":   "<title>",
      "summary":      "<short human-readable change description>",
      "store_path":   "/scitex-todo/tasks.yaml"
    }

The agent's harness (e.g. claude-code container) handles the rest:
runs ``scitex-todo next --mine --json``, picks the top task, works it,
flips status, comments. See ``_skills/scitex-todo/32_agent-self-
consumption-loop.md`` for the canonical 7-step loop.

Agent registry (where to find each peer's a2a port):

  A ``agents:`` top-level list in tasks.yaml. Each entry:
  ``{name: scitex-todo, a2a_port: 41234}``. This static list is
  scitex-todo's own SSoT for the agent port table — no external
  runtime is consulted.

Per-agent debounce: at most ONE wake per ``min_wake_interval`` seconds
per agent. Prevents a hot-loop when an agent comments on its own task
several times in quick succession.

Fan-out (P2, ADR-0009): each detected change wakes the card OWNER
(``agent`` / ``assignee``) AND every entry in the card's persistent
``subscribers`` list (the P1 notify field). Recipients are DEDUPED —
an owner who also appears in ``subscribers`` is woken once — and the
existing per-agent debounce still applies, so a busy card never floods
any one recipient. A card with no subscribers behaves exactly as
before (owner-only); a card with subscribers but no owner still wakes
its watchers.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, TextIO

logger = logging.getLogger(__name__)

# Anti-spiral defaults (incident-todo-wake-watcher-interval2-spiral-20260708).
# A 2s interval re-parsed the ~9 MB / ~930-card store faster than the tick
# finished on a slow host, sustaining ~56% CPU and starving the box. The
# default is now 30s and a HARD FLOOR (below) rejects anything under 10s so a
# stray ``--interval 2`` can never foot-gun the fleet again.
DEFAULT_INTERVAL_S: float = 30.0
MIN_INTERVAL_FLOOR_S: float = 10.0
DEFAULT_MIN_WAKE_INTERVAL_S: float = 30.0
DEFAULT_REQUEST_TIMEOUT_S: float = 1.5
WAKE_PATH: str = "/v1/turn"


def clamp_interval(
    interval_s: float | int | str,
    *,
    floor: float = MIN_INTERVAL_FLOOR_S,
) -> float:
    """Clamp a polling interval up to the safety floor, loudly.

    The hard floor — not the JobSpec default — is the real guard against
    the 2026-07-08 death-spiral: any caller passing a sub-floor value is
    clamped to ``floor`` with a WARNING naming the incident, so the fleet
    can never be saturated by an accidental ``--interval 2`` again. A
    non-numeric value falls back to :data:`DEFAULT_INTERVAL_S`.
    """
    try:
        val = float(interval_s)
    except (TypeError, ValueError):
        logger.warning(
            "wake-watcher: non-numeric interval %r; using default %.3gs",
            interval_s,
            DEFAULT_INTERVAL_S,
        )
        return DEFAULT_INTERVAL_S
    if val < floor:
        logger.warning(
            "wake-watcher: --interval %.3gs is below the %.3gs safety floor; "
            "clamping to %.3gs (a sub-floor interval death-spiraled the fleet "
            "on 2026-07-08, incident-todo-wake-watcher-interval2-spiral).",
            val,
            floor,
            floor,
        )
        return floor
    return val


def _default_lock_path() -> Path:
    """Runtime-dir lockfile for the single-instance guard.

    Prefers ``$XDG_RUNTIME_DIR`` (tmpfs, per-user, cleared on logout) and
    falls back to ``~/.scitex/cards/`` when it is unset.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "scitex-cards-wake-watcher.lock"
    return Path("~/.scitex/cards/wake-watcher.lock").expanduser()


def acquire_single_instance_lock(
    lock_path: str | Path | None = None,
) -> Optional[TextIO]:
    """Take an exclusive, NON-BLOCKING ``flock`` on the watcher lockfile.

    Returns the held file object on success — the caller MUST keep it
    alive for the process lifetime (closing it releases the lock) — or
    ``None`` if another ``watch`` process already holds it. This makes
    two concurrent wake-watchers structurally impossible: the second
    process sees ``None`` and refuses to start, so overlapping full-store
    re-parses (the host-saturating failure mode) cannot occur.
    """
    path = Path(lock_path).expanduser() if lock_path else _default_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("w", encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("wake-watcher: cannot open lockfile %s: %s", path, exc)
        return None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    try:
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:  # pragma: no cover - defensive
        pass
    return handle


@dataclass
class WakeRecord:
    """A single wake event the watcher decided to fire (or DID fire).

    Surfaced from :func:`detect_changes` so the caller (the polling
    loop or a test) can decide what to do — tests just collect the
    records; the live loop forwards them to :func:`post_wake`.
    """

    agent: str
    trigger_kind: str  # task_added | comment | status_changed
    task_id: str
    task_title: str
    summary: str

    def to_payload(self, *, store_path: str) -> dict:
        return {
            "trigger": "scitex-todo-watcher",
            "trigger_kind": self.trigger_kind,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "summary": self.summary,
            "store_path": store_path,
        }


@dataclass
class WatcherState:
    """In-memory snapshot the polling loop diffs against.

    Stores the previous-pass task list keyed by id so the diff is O(N)
    each tick. Reset on first load (the first tick of any watcher run
    seeds the snapshot without firing any wakes — otherwise the
    operator would get a deluge of "everything looks new" wakes at
    startup).
    """

    snapshot: dict[str, dict] = field(default_factory=dict)
    seeded: bool = False
    # Per-agent debounce: agent name -> last-wake timestamp.
    last_wake_at: dict[str, float] = field(default_factory=dict)
    # Store mtime processed on the last tick. When it is unchanged the
    # next tick short-circuits BEFORE any parse — a quiet board costs a
    # single stat() per tick, not a full ~9 MB store re-parse. This is the
    # structural cure for the every-interval unconditional-reload spiral.
    last_mtime: Optional[float] = None


def _recipients(task: dict) -> list[str]:
    """Wake targets for a card change: the owner (``agent`` / ``assignee``)
    plus every entry in the card's persistent ``subscribers`` list (the P1
    notify field), DEDUPED — owner first, then first-seen subscribers;
    non-string / empty entries dropped. A card with no owner still wakes
    its subscribers; a card with neither returns ``[]``.
    """
    out: list[str] = []
    seen: set[str] = set()
    owner = task.get("agent") or task.get("assignee")
    if isinstance(owner, str) and owner:
        out.append(owner)
        seen.add(owner)
    subs = task.get("subscribers")
    if isinstance(subs, list):
        for s in subs:
            if isinstance(s, str) and s and s not in seen:
                out.append(s)
                seen.add(s)
    return out


def detect_changes(
    state: WatcherState,
    tasks: Iterable[dict],
    *,
    now: Optional[float] = None,
    min_wake_interval_s: float = DEFAULT_MIN_WAKE_INTERVAL_S,
) -> list[WakeRecord]:
    """Diff `tasks` against the watcher's previous snapshot.

    Updates ``state.snapshot`` in place. On the first call ``state``
    is just SEEDED (no wakes returned) — the watcher only fires on
    transitions, not on its own bootup.
    """
    now = time.monotonic() if now is None else now
    new_snapshot: dict[str, dict] = {}
    wakes: list[WakeRecord] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = task.get("id")
        if not tid:
            continue
        new_snapshot[tid] = task

        if not state.seeded:
            continue  # first pass — seed only

        prev = state.snapshot.get(tid)
        owner = task.get("agent") or task.get("assignee")
        recipients = _recipients(task)
        if not recipients:
            continue  # no owner and no subscribers — nowhere to wake

        # task added
        if prev is None:
            summary = (
                f"new task assigned to {owner}" if owner else "new subscribed task"
            )
            for who in recipients:
                wakes.append(
                    WakeRecord(
                        agent=who,
                        trigger_kind="task_added",
                        task_id=str(tid),
                        task_title=str(task.get("title") or ""),
                        summary=summary,
                    )
                )
            continue

        # comment appended
        prev_comments = prev.get("comments") or []
        cur_comments = task.get("comments") or []
        if len(cur_comments) > len(prev_comments):
            latest = cur_comments[-1] if cur_comments else {}
            author = (
                latest.get("author") if isinstance(latest, dict) else None
            ) or "<unknown>"
            text = (latest.get("text") if isinstance(latest, dict) else None) or ""
            short = text[:120].replace("\n", " ")
            summary = f"comment by {author}: {short}"
            for who in recipients:
                wakes.append(
                    WakeRecord(
                        agent=who,
                        trigger_kind="comment",
                        task_id=str(tid),
                        task_title=str(task.get("title") or ""),
                        summary=summary,
                    )
                )

        # status flipped
        if prev.get("status") != task.get("status"):
            summary = f"status: {prev.get('status')!r} -> {task.get('status')!r}"
            for who in recipients:
                wakes.append(
                    WakeRecord(
                        agent=who,
                        trigger_kind="status_changed",
                        task_id=str(tid),
                        task_title=str(task.get("title") or ""),
                        summary=summary,
                    )
                )

    state.snapshot = new_snapshot
    state.seeded = True

    # Per-agent debounce — drop wakes that fire within min_wake_interval
    # of a prior wake for the same agent.
    kept: list[WakeRecord] = []
    for w in wakes:
        last = state.last_wake_at.get(w.agent, 0.0)
        if now - last < min_wake_interval_s:
            logger.debug(
                "wake-watcher: debounced %s on %s (last %.1fs ago)",
                w.trigger_kind,
                w.agent,
                now - last,
            )
            continue
        kept.append(w)
        state.last_wake_at[w.agent] = now
    return kept


def post_wake(
    a2a_port: int,
    payload: dict,
    *,
    timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
) -> bool:
    """POST a wake payload to ``http://127.0.0.1:<port>/v1/turn``.

    Returns ``True`` on a 2xx response, ``False`` on any failure
    (connection refused / non-2xx / timeout). The watcher logs + skips
    on failure; the next polling tick re-derives the diff so the wake
    is NOT queued.
    """
    url = f"http://127.0.0.1:{a2a_port}{WAKE_PATH}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        logger.info("wake-watcher: POST to %s failed: %s", url, exc)
        return False


def resolve_agent_port(
    agent: str,
    *,
    static_agents: Optional[list[dict]] = None,
) -> Optional[int]:
    """Resolve an agent's a2a port from a static ``agents:`` list
    passed in from the watcher.

    Returns the port int, or ``None`` if the agent is unknown.
    """
    # Static lookup — pass an ``agents:`` list from tasks.yaml.
    if static_agents:
        for entry in static_agents:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == agent:
                port = entry.get("a2a_port")
                if isinstance(port, int):
                    return port
    return None


def run_watcher_once(
    path: str | Path,
    state: WatcherState,
    *,
    now: Optional[float] = None,
    min_wake_interval_s: float = DEFAULT_MIN_WAKE_INTERVAL_S,
    post: bool = True,
) -> list[WakeRecord]:
    """Single tick: load the store, detect changes, optionally POST wakes.

    Test-friendly entry point — tests can call this with `post=False`
    to assert the wake list without standing up a real HTTP server.

    Returns the list of wakes that ACTUALLY would have fired this tick
    (post-debounce). When ``post=True``, also forwards them to
    :func:`post_wake` — the return list is the same regardless.

    Two anti-spiral properties hold here:

    * **mtime short-circuit** — after seeding, a tick on an UNCHANGED
      store returns immediately (a single ``stat()``, no parse, no diff,
      no push). A quiet board therefore does no work per interval, so the
      "unconditional full reload every tick" spiral cannot start.
    * **single parse per tick** — the store is parsed ONCE via
      :func:`scitex_cards._model.load_doc`; both the task list AND the
      static ``agents:`` registry come from that one ``safe_load`` (the
      old path parsed the ~9 MB file twice per tick).
    """
    # Lazy import: keep watcher importable without the rest of the
    # package's heavy store / Django modules in scope.
    from scitex_cards._model import load_doc

    path = Path(path).expanduser()

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    # Nothing changed since the last processed tick — do no work.
    if state.seeded and mtime is not None and mtime == state.last_mtime:
        return []

    try:
        data = load_doc(path, validate=True)
    except Exception as exc:  # pragma: no cover - validation drift
        logger.warning("wake-watcher: load failed, skip tick: %s", exc)
        return []
    tasks = data.get("tasks") or []
    state.last_mtime = mtime

    wakes = detect_changes(
        state,
        tasks,
        now=now,
        min_wake_interval_s=min_wake_interval_s,
    )
    if post and wakes:
        # Static agents come from the SAME parse above — the file stays
        # the SSoT for both task data + agent registry, at one parse/tick.
        static_agents = [a for a in (data.get("agents") or []) if isinstance(a, dict)]
        for w in wakes:
            port = resolve_agent_port(w.agent, static_agents=static_agents)
            if port is None:
                logger.info(
                    "wake-watcher: no port for %s, skip wake (%s on %s)",
                    w.agent,
                    w.trigger_kind,
                    w.task_id,
                )
                continue
            post_wake(port, w.to_payload(store_path=str(path)))
    return wakes


def run_watcher_forever(
    path: str | Path,
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    min_wake_interval_s: float = DEFAULT_MIN_WAKE_INTERVAL_S,
    lock_path: str | Path | None = None,
) -> None:  # pragma: no cover - infinite loop
    """Run the watcher in a polling loop until interrupted.

    Drives the live ``scitex-todo watch --push`` CLI entry. Tests use
    :func:`run_watcher_once` directly.

    Three anti-spiral guards wrap the loop:

    1. ``interval_s`` is clamped to the safety floor
       (:func:`clamp_interval`) — a sub-floor value cannot foot-gun.
    2. A process-level single-instance ``flock`` refuses to start a
       SECOND watcher, so two loops can never run concurrently.
    3. The loop is strictly SEQUENTIAL: the next tick only begins after
       the previous :func:`run_watcher_once` fully returns. A slow tick
       therefore DELAYS the next one — it can never launch an overlapping
       one — so digests can never stack up and saturate the host.
    """
    interval_s = clamp_interval(interval_s)
    lock = acquire_single_instance_lock(lock_path)
    if lock is None:
        logger.error(
            "wake-watcher: another instance already holds the single-instance "
            "lock; refusing to start a second (overlapping watchers saturated "
            "the host on 2026-07-08, incident-todo-wake-watcher-interval2-spiral)."
        )
        return
    state = WatcherState()
    try:
        while True:
            run_watcher_once(path, state, min_wake_interval_s=min_wake_interval_s)
            time.sleep(interval_s)
    finally:
        try:
            lock.close()
        except Exception:  # pragma: no cover - defensive
            pass


# EOF
