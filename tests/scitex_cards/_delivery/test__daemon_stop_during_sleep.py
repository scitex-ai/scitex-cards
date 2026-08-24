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
"""

from __future__ import annotations

import threading
import time

import pytest

from scitex_cards._delivery._daemon import run_notifyd


def test_stop_set_during_the_wait_ends_the_loop_promptly(tmp_path, monkeypatch):
    """Setting `stop` mid-wait must return in well under one interval.

    This is the regression for the SIGKILL above. It deliberately does NOT
    inject the `sleep` seam: injecting a no-op sleep is exactly what hid this
    for so long -- every existing test replaces the wait with a function that
    returns instantly, so no test ever exercised the real waiting path.
    """
    # The tick itself is neutered on purpose: channels={} so delivery has
    # nowhere to send, and the nudge sweep disabled because it runs on the FIRST
    # tick and walks the whole board (5,962 cards on this host), which took
    # minutes and made an earlier version of this test look hung. What is NOT
    # neutered is the `sleep` seam -- that is the behaviour under test, and
    # injecting a no-op there is exactly what hid this defect from every
    # existing test.
    # ISOLATE THE STORE VIA THE ENVIRONMENT, not via the `store=` argument.
    # Measured 2026-08-24: passing store=<tmp path> does NOT redirect the reads --
    # run_notifyd still loaded the production board over
    # SCITEX_CARDS_DB (postgres, 5,962 cards) and validated every one of them,
    # emitting a UserWarning per invalid card. That, not the delivery, is what
    # made an earlier version of this test look hung. The env var is the control
    # that actually works.
    monkeypatch.setenv("SCITEX_CARDS_DB", str(tmp_path / "cards.db"))
    monkeypatch.delenv("SCITEX_CARDS_INBOX_BACKEND", raising=False)

    stop = threading.Event()
    interval = 30.0  # >> the assertion window, so a blind sleep cannot pass
    result: dict = {}

    def _run():
        try:
            result["out"] = run_notifyd(
                store=tmp_path / "cards.db",
                interval=interval,
                stop=stop,
                channels={},          # nothing to deliver
                nudge_sweep_minutes=0,  # <=0 disables the fleet sweep
                nudge_sweep=lambda **_: None,
            )
        except Exception as exc:  # surfaced below rather than lost in a thread
            result["exc"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Let it get past its first tick and INTO the wait. If we set stop before
    # the loop reaches the wait, the pre-sleep re-check would catch it and the
    # test would pass without exercising the path under repair.
    time.sleep(2.0)
    started = time.monotonic()
    stop.set()
    t.join(timeout=10.0)
    elapsed = time.monotonic() - started

    assert not t.is_alive(), (
        f"notifyd did not exit within 10s of stop being set. This is the "
        f"systemd SIGKILL in miniature: the wait is not interruptible."
    )
    if "exc" in result:
        pytest.fail(f"run_notifyd raised: {result['exc']!r}")
    assert elapsed < 5.0, (
        f"notifyd took {elapsed:.1f}s to notice stop, with interval={interval}s. "
        f"A stop set during the wait must be seen immediately, not after the "
        f"full interval elapses."
    )
    assert result.get("out", {}).get("stopped_by") == "stop_event"
