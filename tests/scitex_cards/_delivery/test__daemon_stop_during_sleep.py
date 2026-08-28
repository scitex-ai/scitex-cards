#!/usr/bin/env python3
"""A stop set DURING the between-tick wait must end the loop promptly.

WHY THIS EXISTS. Measured on scitex-compute-04, 2026-08-24:

    10:54:10  notifyd tick 365 logged, then sleep(120) begins
    10:54:28  systemd sends SIGTERM; the handler logs "initiating graceful
              stop" and sets the stop event
    10:55:58  systemd's 90s TimeoutStopSec expires -> SIGKILL, status 9
    10:56:10  ...when the sleep would have finished and the loop would have
              exited cleanly, 12 seconds too late

The signal handler was never the problem: it fired and it set the event. The
problem is that `time.sleep()` RESUMES after a signal handler returns (PEP 475,
Python >= 3.5), so a stop set mid-sleep is invisible until the full interval has
elapsed. With DEFAULT_INTERVAL at 120s and systemd's default 90s stop timeout,
the daemon can NEVER shut down cleanly from a mid-sleep signal -- it is killed
every time, and a writer killed mid-transaction is how a database loses data.

The loop already re-checks `stop` before sleeping, and a comment there says it
is so "a stop set during the tick ends the loop without an extra wait". That
covers a stop set during the TICK. It cannot cover one set during the SLEEP,
which is where a signal actually lands: the tick is fast and the sleep is 120s,
so essentially every signal arrives mid-sleep.

The fix is to wait on the event instead of sleeping blind. `Event.wait(timeout)`
returns as soon as the event is set, so the daemon exits in milliseconds.

NO MOCKS, and the seam choice is the whole point. `run_notifyd` takes a `sleep`
seam and every existing test injects a no-op -- which is precisely what hid this
defect, since the suite covers the loop with the WAITING REMOVED. These tests
therefore refuse that seam and wait for real. The tick itself is made trivial
instead (no channels, no sweep), so only the wait is exercised.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from dataclasses import dataclass

from scitex_cards._delivery._daemon import run_notifyd

#: Long enough that a blind sleep cannot masquerade as a prompt exit.
INTERVAL = 30.0

#: The window a correct implementation returns in. Generous by 20x.
PROMPT_S = 5.0


@contextlib.contextmanager
def _store_env(path):
    """Point the store at `path` for the duration, then restore.

    NOT `monkeypatch`: the repo forbids mock fixtures (PA-306 §3), and this is
    plain save/restore of real state rather than a stand-in for it.

    The ENVIRONMENT is what actually selects the store. Measured 2026-08-24:
    passing `store=<tmp path>` to run_notifyd does NOT redirect its reads -- it
    still loaded the production board over SCITEX_CARDS_DB (postgres, 5,962
    cards) and validated every row, which made an earlier version of this test
    look hung.
    """
    keys = ("SCITEX_CARDS_DB", "SCITEX_CARDS_INBOX_BACKEND")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["SCITEX_CARDS_DB"] = str(path)
    # Not popped: SQLite is RETIRED as an inbox backend (operator ruling
    # 2026-08-23), so an unset var against this local file store would now
    # raise StoreUnavailableError instead of falling back to a working
    # default. `yaml` is the real non-server backend left.
    os.environ["SCITEX_CARDS_INBOX_BACKEND"] = "yaml"
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass(frozen=True)
class Outcome:
    """What one stop-during-the-wait run did."""

    alive: bool
    elapsed: float
    stopped_by: str | None
    error: BaseException | None


def _stop_during_the_wait(tmp_path) -> Outcome:
    """Start the daemon, let it reach the wait, set stop, and measure."""
    stop = threading.Event()
    result: dict = {}

    def _run():
        try:
            result["out"] = run_notifyd(
                store=tmp_path / "cards.db",
                interval=INTERVAL,
                stop=stop,
                channels={},
                nudge_sweep_minutes=0,
                nudge_sweep=lambda **_: None,
            )
        except BaseException as exc:  # surfaced, never swallowed
            result["exc"] = exc

    with _store_env(tmp_path / "cards.db"):
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        # Let it get past its first tick and INTO the wait. Setting stop before
        # the loop reaches the wait would be caught by the pre-sleep re-check,
        # and the test would pass without exercising the path under repair.
        time.sleep(3.0)
        started = time.monotonic()
        stop.set()
        thread.join(timeout=10.0)
        elapsed = time.monotonic() - started

    return Outcome(
        alive=thread.is_alive(),
        elapsed=elapsed,
        stopped_by=(result.get("out") or {}).get("stopped_by"),
        error=result.get("exc"),
    )


def test_the_daemon_exits_when_stop_is_set_during_the_wait(tmp_path):
    """It must not still be running ten seconds after stop. The SIGKILL, in miniature."""
    # Arrange
    target = tmp_path
    # Act
    outcome = _stop_during_the_wait(target)
    # Assert
    assert not outcome.alive


def test_the_daemon_notices_stop_without_serving_out_the_interval(tmp_path):
    """It must return in well under one interval, not after the full wait."""
    # Arrange
    target = tmp_path
    # Act
    outcome = _stop_during_the_wait(target)
    # Assert
    assert outcome.elapsed < PROMPT_S


def test_the_daemon_reports_the_stop_event_as_its_reason(tmp_path):
    """The recorded reason must be the stop event, not max_iterations or a crash."""
    # Arrange
    target = tmp_path
    # Act
    outcome = _stop_during_the_wait(target)
    # Assert
    assert outcome.stopped_by == "stop_event"


def test_the_daemon_does_not_raise_on_a_stop_during_the_wait(tmp_path):
    """A stop is a normal exit, so nothing may escape the loop."""
    # Arrange
    target = tmp_path
    # Act
    outcome = _stop_during_the_wait(target)
    # Assert
    assert outcome.error is None
