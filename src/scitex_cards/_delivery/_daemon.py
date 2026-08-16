#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The always-on delivery daemon (slice 2) — a single-instance notify loop.

Slice 1 shipped :func:`scitex_cards._delivery.deliver_pending` (ONE pass) and
the ``scitex-cards deliver`` one-shot verb. This module wraps that pass in a
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
  fleet-liveness stale/backlog nudge sweep, which before this had no scheduled
  caller at all. Both are individually guarded: a raising sweep never stops the
  delivery pass that follows it.
* TICK RESILIENCE: each tick's work is wrapped so an unexpected error (ledger
  corruption, a disk/clock fault) is logged with a traceback and the loop
  CONTINUES to the next tick rather than dying. Combined with the unit's
  ``Restart=on-failure`` this self-heals under both foreground and systemd runs.
* TICK TRUTH (:mod:`._tick`, :mod:`._liveness`): resilience must not become
  silence. EVERY guard above hands the exception it swallowed back as a fault
  string; a tick with any fault is reported FAILED (never idle), ``pending``
  is UNKNOWN rather than ``0`` when the inbox could not be read, and the
  consecutive-failure streak escalates the summary INFO → WARNING → ERROR.
  The streak, the last ok tick and the last successful delivery are persisted
  to ``<store_dir>/runtime/notifyd-liveness.json`` so ``scitex-cards health``
  — in another process, another container — can see them. Measured
  2026-07-28/29: the daemon failed to read the store on 1196 consecutive ticks
  while printing ``sent=0 failed=0``, the exact line a healthy idle daemon
  prints, and an operator's DMs went undelivered for a day.

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
from ._liveness import DeliveryLiveness, read_liveness, write_liveness
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

# Re-exported so every existing
# `from scitex_cards._delivery._daemon import report_terminal_misses` keeps
# resolving to the SAME object after the extraction into `._terminal`. A split
# that moves an import surface is a rename with extra steps.
from ._terminal import (
    DEFAULT_TERMINAL_REPORT_EVERY,  # noqa: F401  (re-export)
    report_terminal_misses,
)
from ._terminal import report_terminal_if_due as _report_terminal_if_due
from ._tick import DEFAULT_ESCALATE_AFTER, build_report, fault_text

logger = logging.getLogger("scitex_cards.delivery.notifyd")

#: Default seconds between delivery ticks — ALSO the heartbeat cadence, so it is
#: defined next to the pidfile format (whose READER needs it to judge freshness)
#: and re-exported here under its long-standing name.
DEFAULT_INTERVAL = _PIDFILE_DEFAULT_INTERVAL

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

    Unlike :func:`scitex_cards._model._store_lock` (a blocking per-write lock
    released at context exit), this lock is acquired ONCE at daemon start with
    ``LOCK_NB`` so a second daemon fails fast rather than queueing behind the
    first. The fd is kept open for the whole run; releasing it (and removing
    the pidfile) is what frees the slot for a restart.

    The pidfile is ALSO the daemon's HEARTBEAT (:mod:`._pidfile`): the bytes
    carry our namespace identity plus a stamp refreshed every tick, because a
    reader in a DIFFERENT PID namespace (the fleet's containers share the store
    by bind-mount) cannot interpret the pid at all — only freshness.
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
                f"another scitex-cards notifyd already holds {self._path} "
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


def _close_tick(
    *, tick, summary, faults, liveness, escalate_after, store, now_fn
) -> "DeliveryLiveness":
    """Fold this tick into the liveness streak, emit its line, persist. → state.

    Replaces the old unconditional ``logger.info(...)``, which printed the same
    ``sent=0 failed=0`` whether the tick had nothing to do or could not read the
    store at all. The report's own :meth:`~._tick.TickReport.level` decides the
    level: INFO while healthy (an idle daemon must stay quiet or its alarms get
    ignored), WARNING on a failing tick, ERROR once the streak reaches
    ``escalate_after`` — carrying the count, the duration and the reason.

    The clock is read HERE and guarded, because ``now_fn`` is itself a fault
    surface (a broken clock is one of the things a tick can die of) and a tick
    that cannot read the time must still be able to report that.

    Persisting the liveness record lets ``scitex-cards health`` read the streak
    from another process. A failed persist is logged, never raised: it costs
    visibility, and dropping delivery to protect a status file would trade a
    comm-miss for a monitoring gap.
    """
    try:
        now = now_fn()
    except Exception as exc:  # noqa: BLE001 — a dead clock is a countable fault
        faults = [*faults, fault_text(exc, where="clock")]
        now = _dt.datetime.now(_dt.timezone.utc)
    liveness = liveness.observe(faults=faults, sent=summary.get("sent", 0), now=now)
    report = build_report(
        tick=tick,
        summary=summary,
        faults=faults,
        consecutive_failures=liveness.consecutive_failures,
        failing_since=liveness.failing_since,
        now=now,
    )
    logger.log(report.level(escalate_after=escalate_after), "%s", report.line())
    if write_liveness(liveness, store) is None:
        logger.warning(
            "notifyd tick %d: could not persist the delivery-liveness record; "
            "`scitex-cards health` will report delivery as unknown",
            tick,
        )
    return liveness


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
    escalate_after: int = DEFAULT_ESCALATE_AFTER,
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
    escalate_after : int
        Consecutive failing ticks after which the per-tick summary is logged at
        ERROR instead of WARNING (see :data:`~._tick.DEFAULT_ESCALATE_AFTER`).

    Returns
    -------
    dict
        ``{iterations, totals, stopped_by, liveness}`` — total ticks run, summed
        sent/failed/skipped/failed_terminal counts, why it stopped
        (``"stop_event" | "max_iterations"``), and the final delivery-liveness
        record (last ok tick, last successful delivery, consecutive failures).
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

    totals = {"sent": 0, "failed": 0, "skipped": 0, "failed_terminal": 0}
    iterations = 0
    stopped_by = "stop_event"
    # RESUME the streak across a restart: a daemon that has been failing for an
    # hour must not reset its alarm to zero merely because systemd bounced it.
    liveness = read_liveness(store)

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
            faults: list[str] = []
            summary: dict = {}
            try:
                lock.heartbeat(now_fn())
            except Exception as exc:  # noqa: BLE001 — a bad disk/clock != kill it
                logger.exception(
                    "notifyd tick %d: heartbeat stamp failed; continuing",
                    iterations,
                )
                faults.append(fault_text(exc, where="heartbeat"))
            # TICK RESILIENCE: a single bad tick must NEVER kill an always-on
            # daemon. deliver_pending is already fail-soft per recipient, but a
            # ledger/disk/clock error could still raise — catch it, log with a
            # traceback, and continue to the next tick. This self-heals under
            # BOTH foreground `scitex-cards notifyd` AND systemd (which also has
            # Restart=on-failure as a second safety net).
            try:
                # NAG sweep FIRST: enqueue any due reminders / escalations so
                # this same tick's deliver_pending sends them. Its own guard so
                # a sweep error never blocks delivery of already-queued notes.
                # Each guard RETURNS what it swallowed so the summary can count
                # it. Guarding the loop is right; letting the guard also hide
                # the failure is the 2026-07-28 outage.
                faults.append(_run_reminder_sweep(store=store, now=now_fn()))
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
                        faults.append(_run_stale_nudge_sweep(store=store, now=tick_now))
                    except Exception as exc:  # noqa: BLE001 — never block delivery
                        logger.exception(
                            "notifyd liveness sweep raised; continuing to delivery"
                        )
                        faults.append(fault_text(exc, where="liveness_sweep"))
                summary = deliver_pending(
                    store=store,
                    channels=channels,
                    now=now_fn(),
                )
                for key in totals:
                    totals[key] += summary.get(key, 0)
                faults.extend(summary.get("faults", ()))
                _report_terminal_if_due(
                    tick=iterations,
                    every=terminal_report_every,
                    store=store,
                )
            except Exception as exc:  # noqa: BLE001 — one bad tick != kill it
                logger.exception(
                    "notifyd tick %d raised; continuing to next tick", iterations
                )
                faults.append(fault_text(exc, where="tick"))

            # THE SUMMARY IS EMITTED OUTSIDE THE GUARD, so a tick that raised
            # still reports itself. It used to be logged inside, meaning the one
            # failure mode that most needed a line printed none at all.
            try:
                liveness = _close_tick(
                    tick=iterations,
                    summary=summary,
                    faults=[f for f in faults if f],
                    liveness=liveness,
                    escalate_after=escalate_after,
                    store=store,
                    now_fn=now_fn,
                )
            except Exception:  # noqa: BLE001 — reporting must not kill delivery
                logger.exception(
                    "notifyd tick %d: could not emit the tick summary", iterations
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
        "liveness": liveness,
    }


__all__ = [
    "DEFAULT_ESCALATE_AFTER",
    "DEFAULT_INTERVAL",
    "DEFAULT_NUDGE_SWEEP_MINUTES",
    "DEFAULT_TERMINAL_REPORT_EVERY",
    "ENV_NUDGE_SWEEP_MINUTES",
    "DaemonAlreadyRunning",
    "DeliveryLiveness",
    "PIDFILE_NAME",
    "pidfile_path",
    "report_terminal_misses",
    "run_notifyd",
]

# EOF
