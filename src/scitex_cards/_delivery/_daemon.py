#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The always-on delivery daemon (slice 2) — a single-instance notify loop.

Slice 1 shipped :func:`scitex_cards._delivery.deliver_pending` (ONE pass) and
the ``scitex-todo deliver`` one-shot verb. This module wraps that pass in a
long-running, signal-aware loop so notifications keep flowing without an
external cron:

* :func:`run_notifyd` ticks ``deliver_pending`` every ``interval`` seconds,
  logging a one-line per-tick summary, until a stop event is set (by SIGTERM/
  SIGINT, by a test, or by ``max_iterations``).
* SINGLE-INSTANCE: the daemon holds a NON-BLOCKING exclusive ``flock`` on a
  dedicated pidfile (``<store_dir>/notifyd.pid``) for its WHOLE lifetime. A
  second daemon fails fast with :class:`DaemonAlreadyRunning` instead of
  running concurrently and double-sending. The lock is released + the pidfile
  removed on EVERY exit path (normal stop, signal, exception) via ``try /
  finally`` so a crash never strands a stale lock that blocks restart.
* TERMINAL re-surfacing: a notification whose retry budget is exhausted is a
  permanent comm-miss. The loop's per-item stderr line scrolls past, so the
  daemon periodically (every ``terminal_report_every`` ticks) re-scans the
  ledger via :func:`report_terminal_misses` and logs a THROTTLED WARNING that
  re-surfaces every outstanding comm-miss — a long-undeliverable user is never
  forgotten, but the warning does not spam every tick.
* SWEEPS (:mod:`._sweeps`): each tick first runs the reminder/escalation sweep,
  and — on its OWN much slower cadence (``--nudge-sweep-minutes``) — the
  fleet-liveness stale/backlog nudge sweep. Both are individually guarded: a
  raising sweep never stops the delivery pass that follows it.
* TICK RESILIENCE, AND TICK TRUTHFULNESS (:mod:`._liveness`): each tick's work
  is wrapped so an unexpected error is logged and the loop CONTINUES rather
  than dying — but every guard also RETURNS its verdict, so a tick that could
  not read the store reports ``state=failed pending=unknown`` instead of the
  ``sent=0 failed=0`` a healthy idle daemon prints. A run of failures escalates
  to ERROR. Surviving an outage is worthless if nobody can see it happening.

Test seams (NO mocks): inject ``sleep`` (a no-op so tests never sleep for
real), ``now_fn`` (deterministic clock), a ``stop`` event (tripped after K
ticks), ``max_iterations`` (bounded run), and ``channels`` (real fake
transports). Everything observable is logged + returned.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import logging
import os
import signal
import threading
import time
from pathlib import Path

from .._inbox import _resolved_store
from ._ledger import _KEY_SEP, TERMINAL_STATUS, Ledger, ledger_path
from ._liveness import DeliveryLiveness, liveness_path, tick_health
from ._loop import deliver_pending
from ._pidfile import DEFAULT_INTERVAL as _PIDFILE_DEFAULT_INTERVAL
from ._pidfile import render as _render_pidfile
from ._sweeps import (
    DEFAULT_NUDGE_SWEEP_MINUTES,
    ENV_NUDGE_SWEEP_MINUTES,
    _nudge_sweep_due,
    _nudge_sweep_minutes,
    _run_reminder_sweep,
    _run_stale_nudge_sweep,
)

logger = logging.getLogger("scitex_cards.delivery.notifyd")

#: Default seconds between delivery ticks — ALSO the heartbeat cadence, so it is
#: defined next to the pidfile format (whose READER needs it to judge freshness)
#: and re-exported here under its long-standing name.
DEFAULT_INTERVAL = _PIDFILE_DEFAULT_INTERVAL

#: Default cadence (in ticks) for the throttled terminal-miss re-report.
DEFAULT_TERMINAL_REPORT_EVERY = 10

#: Pidfile name; a sibling of the task store inside ``<store_dir>``.
PIDFILE_NAME = "notifyd.pid"


class DaemonAlreadyRunning(RuntimeError):
    """Raised when a second daemon tries to start while one already holds the lock."""


def pidfile_path(store: str | Path | None = None) -> Path:
    """Resolve the daemon pidfile: ``<store_dir>/runtime/notifyd.pid``.

    Lives under the store's ``runtime/`` dir (scitex convention for
    non-git-tracked runtime state) alongside the delivery ledger, under
    whichever scope the store resolved to.
    """
    from .._paths import runtime_dir

    return runtime_dir(store) / PIDFILE_NAME


class _SingleInstanceLock:
    """A NON-BLOCKING exclusive ``flock`` on the pidfile, held for the daemon's life.

    Acquired ONCE at start with ``LOCK_NB`` so a second daemon fails fast rather
    than queueing behind the first; releasing it (and removing the pidfile) is
    what frees the slot for a restart. The pidfile is ALSO the daemon's
    HEARTBEAT (:mod:`._pidfile`) — a cross-namespace reader cannot interpret our
    pid at all, only the freshness of the stamp we refresh every tick.
    """

    def __init__(self, path: Path, *, interval: float = DEFAULT_INTERVAL):
        self._path = path
        self._interval = interval
        self._fd = None

    def acquire(self) -> None:
        """Take the lock + stamp our pid, or raise :class:`DaemonAlreadyRunning`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # `a+` keeps any prior content until we truncate; the advisory flock is
        # what actually guards single-instance (the bytes are just for humans).
        fd = self._path.open("a+")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            fd.close()
            existing = self._read_pid_text()
            raise DaemonAlreadyRunning(
                f"another scitex-todo notifyd already holds {self._path} "
                f"(pid {existing or 'unknown'}); refusing to start a second "
                f"instance ({type(exc).__name__})"
            ) from exc
        self._fd = fd
        # We own it — stamp pid + namespace identity + the first heartbeat.
        self.heartbeat()

    def heartbeat(self, now: _dt.datetime | None = None) -> None:
        """Rewrite the pidfile with a FRESH stamp — called once per tick.

        This is the only liveness signal that survives a namespace boundary: a
        cross-namespace reader cannot probe our pid, but it can read a clock.
        """
        fd = self._fd
        if fd is None:
            return
        fd.seek(0)
        fd.truncate()
        fd.write(_render_pidfile(os.getpid(), interval=self._interval, now=now))
        fd.flush()

    def _read_pid_text(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def release(self) -> None:
        """Release the flock and remove the pidfile — idempotent.

        Called from the daemon's ``finally`` so a normal stop, a signal, AND
        an exception mid-loop all leave the slot clean. Best-effort: a failure
        to remove the pidfile never masks the original exit reason.
        """
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                fd.close()
            except OSError:
                pass
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


def report_terminal_misses(store: str | Path | None = None) -> list[dict]:
    """Scan the ledger for ALL current ``failed_terminal`` entries.

    A terminal entry is a permanent comm-miss: the retry budget was exhausted
    and the notification was never delivered. This scan lets the daemon
    re-surface the STANDING set periodically (the one-off stderr line scrolls
    past), so a long-undeliverable user is never silently forgotten. Returns
    ``{recipient, note_id, channel, attempts, last_ts, detail}`` dicts, sorted,
    read fresh off disk — the ledger is the sole delivery truth.
    """
    ledger = Ledger.load(store)
    out: list[dict] = []
    for key, entry in ledger._entries.items():  # noqa: SLF001 — same package.
        if entry.get("status") != TERMINAL_STATUS:
            continue
        parts = key.split(_KEY_SEP)
        if len(parts) != 3:
            # Defensive: a hand-mangled key — surface it rather than drop it.
            recipient, note_id, channel = key, "", ""
        else:
            recipient, note_id, channel = parts
        out.append(
            {
                "recipient": recipient,
                "note_id": note_id,
                "channel": channel,
                "attempts": int(entry.get("attempts", 0)),
                "last_ts": entry.get("last_ts"),
                "detail": entry.get("detail"),
            }
        )
    out.sort(key=lambda d: (d["recipient"], d["note_id"], d["channel"]))
    return out


def _report_terminal_if_due(
    *,
    tick: int,
    every: int,
    store,
) -> None:
    """Every ``every`` ticks, re-surface the standing terminal comm-misses.

    THROTTLED on purpose: re-surfacing every tick would bury the live per-tick
    summaries and train the operator to ignore the warning. Re-surfacing on a
    cadence keeps a long-undeliverable user visible without spam.
    """
    if every <= 0:
        return
    if tick % every != 0:
        return
    misses = report_terminal_misses(store)
    if not misses:
        return
    logger.warning(
        "notifyd: %d OUTSTANDING terminal comm-miss(es) still undelivered "
        "(re-surfaced every %d ticks) — operator must fix the channel/address:",
        len(misses),
        every,
    )
    for m in misses:
        logger.warning(
            "  comm-miss: %s note=%s via %s (attempts=%d, last=%s, detail=%s)",
            m["recipient"],
            m["note_id"],
            m["channel"],
            m["attempts"],
            m["last_ts"],
            m["detail"],
        )


def _install_signal_handlers(stop: threading.Event):
    """Wire SIGTERM/SIGINT to trip the stop event (graceful shutdown).

    Returns a ``restore`` callable that puts the previous handlers back — only
    installed when called from the main thread (signal handlers can only be set
    there; tests that drive the loop off a stop event skip this path).
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    previous: dict[int, object] = {}

    def _handler(signum, _frame):
        logger.info("notifyd received signal %d — initiating graceful stop", signum)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread / unsupported — skip that signal.
            pass

    def _restore() -> None:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    return _restore


def run_notifyd(
    store: str | Path | None = None,
    *,
    interval: float = DEFAULT_INTERVAL,
    channels: dict | None = None,
    stop: threading.Event | None = None,
    sleep=time.sleep,
    now_fn=None,
    max_iterations: int | None = None,
    terminal_report_every: int = DEFAULT_TERMINAL_REPORT_EVERY,
    nudge_sweep_minutes: float | None = None,
) -> dict:
    """Run the always-on delivery loop until stopped.

    Acquires the single-instance lock, then loops: each tick runs ONE
    :func:`deliver_pending` pass, logs a one-line summary, periodically
    re-surfaces standing terminal comm-misses (throttled), and sleeps
    ``interval`` between ticks. Stops when ``stop`` is set (by SIGTERM/SIGINT,
    a test, or ``max_iterations``). The lock is ALWAYS released + the pidfile
    removed on exit (normal, signal, or exception).

    Parameters
    ----------
    store : str | Path | None
        Task-store override; resolves the inbox + ledger + recipients +
        pidfile dir.
    interval : float
        Seconds slept between ticks (passed to ``sleep``). Tests inject a
        no-op ``sleep`` so this never blocks.
    channels : dict | None
        Injected channel mapping (TEST seam) forwarded to ``deliver_pending``;
        ``None`` → entry-point-discovered channels.
    stop : threading.Event | None
        Cooperative stop flag, checked each iteration. Default: a fresh event.
    sleep : callable
        ``sleep(seconds)`` between ticks (TEST seam → ``lambda _: None``).
    now_fn : callable | None
        ``() -> datetime`` for the per-tick ``now`` (deterministic backoff in
        tests). Default: aware UTC now.
    max_iterations : int | None
        Stop after this many ticks (TEST seam). ``None`` → run until ``stop``.
    terminal_report_every : int
        Re-surface standing terminal comm-misses every N ticks (throttle).
        ``<= 0`` disables the re-report.
    nudge_sweep_minutes : float | None
        Cadence (MINUTES) of the fleet-liveness sweep — kept OUT of the hot
        delivery path on purpose. ``None`` → :data:`ENV_NUDGE_SWEEP_MINUTES` /
        :data:`DEFAULT_NUDGE_SWEEP_MINUTES`; ``<= 0`` disables it.

    Returns
    -------
    dict
        ``{iterations, totals, stopped_by, liveness}`` — total ticks run, summed
        sent/failed/skipped/failed_terminal/unreadable counts, why it stopped
        (``"stop_event" | "max_iterations"``), and the delivery-liveness
        snapshot (consecutive failures, last successful delivery, per-state tick
        counts) that :func:`scitex_cards._health.health` also reads off disk.
    """
    stop = stop or threading.Event()
    now_fn = now_fn or (lambda: _dt.datetime.now(_dt.timezone.utc))
    sweep_minutes = (
        nudge_sweep_minutes
        if nudge_sweep_minutes is not None
        else _nudge_sweep_minutes()
    )
    last_nudge_sweep: _dt.datetime | None = None

    lock = _SingleInstanceLock(pidfile_path(store), interval=interval)
    lock.acquire()  # raises DaemonAlreadyRunning — BEFORE the try/finally so a
    # failed acquire never releases a lock we do not own.

    restore_signals = _install_signal_handlers(stop)

    totals = {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "failed_terminal": 0,
        "unreadable": 0,
    }
    iterations = 0
    stopped_by = "stop_event"
    # The thing that makes a broken tick COUNT: rolling liveness across ticks,
    # which logs the per-tick verdict and escalates a run of failures to ERROR.
    tracker = DeliveryLiveness()
    liveness_file = liveness_path(store)

    logger.info(
        "notifyd started: pid=%d store=%s interval=%.1fs "
        "terminal_report_every=%d nudge_sweep_minutes=%.1f",
        os.getpid(),
        _resolved_store(store),
        interval,
        terminal_report_every,
        sweep_minutes,
    )

    try:
        while not stop.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                stopped_by = "max_iterations"
                break
            iterations += 1
            # HEARTBEAT FIRST, and separately guarded: it is the ONLY liveness
            # signal a reader in another PID namespace can use, so a failure to
            # write it must be loud (logged) yet must not cost us this tick's
            # delivery. A missed stamp is a health-visibility bug; a skipped
            # delivery is a comm-miss. Never trade the second for the first.
            tick_at: _dt.datetime | None = None
            try:
                tick_at = now_fn()
                lock.heartbeat(tick_at)
            except Exception:  # noqa: BLE001 — a bad disk/clock != kill the daemon
                logger.exception(
                    "notifyd tick %d: heartbeat stamp failed; continuing",
                    iterations,
                )
            # TICK RESILIENCE: a single bad tick must NEVER kill an always-on
            # daemon. deliver_pending is already fail-soft per recipient, but a
            # ledger/disk/clock error could still raise — catch it, log with a
            # traceback, and continue to the next tick. This self-heals under
            # BOTH foreground `scitex-todo notifyd` AND systemd (which also has
            # Restart=on-failure as a second safety net).
            # Every guarded step CONTRIBUTES its verdict here. A swallowed
            # exception that leaves no trace in `faults` is the whole defect
            # this collects against: the tick line then reads sent=0 failed=0,
            # which is indistinguishable from a healthy idle daemon.
            faults: list[str] = []
            summary: dict | None = None
            try:
                # NAG sweep FIRST: enqueue any due reminders / escalations so
                # this same tick's deliver_pending sends them. Its own guard so
                # a sweep error never blocks delivery of already-queued notes.
                faults += _run_reminder_sweep(store=store, now=now_fn()).faults
                # LIVENESS sweep on its OWN (much slower) cadence — the stale/
                # backlog nudge scans the whole store, so it stays OUT of the
                # per-tick delivery path.
                tick_now = now_fn()
                if _nudge_sweep_due(last_nudge_sweep, tick_now, minutes=sweep_minutes):
                    last_nudge_sweep = tick_now
                    # Guarded AT THE CALL SITE, not only inside the sweep: the
                    # tick's outer guard would skip THIS TICK'S DELIVERY if the
                    # sweep escaped, which is precisely the coupling the sweep
                    # must never have. Delivery runs even when detection dies.
                    try:
                        faults += _run_stale_nudge_sweep(
                            store=store, now=tick_now
                        ).faults
                    except Exception as exc:  # noqa: BLE001 — never block delivery
                        logger.exception(
                            "notifyd liveness sweep raised; continuing to delivery"
                        )
                        faults.append(f"liveness_sweep: {type(exc).__name__}: {exc}")
                summary = deliver_pending(
                    store=store,
                    channels=channels,
                    now=now_fn(),
                )
                for key in totals:
                    totals[key] += summary.get(key, 0)
                _report_terminal_if_due(
                    tick=iterations,
                    every=terminal_report_every,
                    store=store,
                )
            except Exception as exc:  # noqa: BLE001 — one bad tick != kill it
                logger.exception(
                    "notifyd tick %d raised; continuing to next tick", iterations
                )
                faults.append(f"tick: {type(exc).__name__}: {exc}")
            # The tick's ONE reported answer: quiet when idle, a WARNING when it
            # could not tell what was pending, an ERROR once that persists.
            tracker.record(
                tick=iterations,
                health=tick_health(summary, faults=faults),
                # `tick_at` is None only when the injected clock itself failed
                # (already logged above); liveness must still be stamped, so
                # fall back to the real one rather than skip the accounting.
                now=tick_at or _dt.datetime.now(_dt.timezone.utc),
                path=liveness_file,
            )

            # Re-check stop BEFORE sleeping so a stop set during the tick (or
            # by max_iterations) ends the loop without an extra wait.
            if stop.is_set():
                break
            if max_iterations is not None and iterations >= max_iterations:
                stopped_by = "max_iterations"
                break
            sleep(interval)
        else:
            stopped_by = "stop_event"
    finally:
        restore_signals()
        lock.release()
        logger.info(
            "notifyd stopped (%s): iterations=%d totals=%s",
            stopped_by,
            iterations,
            totals,
        )

    return {
        "iterations": iterations,
        "totals": totals,
        "stopped_by": stopped_by,
        "liveness": tracker.snapshot(),
    }


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_NUDGE_SWEEP_MINUTES",
    "DEFAULT_TERMINAL_REPORT_EVERY",
    "ENV_NUDGE_SWEEP_MINUTES",
    "DaemonAlreadyRunning",
    "PIDFILE_NAME",
    "pidfile_path",
    "report_terminal_misses",
    "run_notifyd",
]

# EOF
