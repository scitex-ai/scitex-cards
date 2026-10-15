#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PG-free pins for the help-wait completion-stamp hole.

``help_wait`` (``src/scitex_cards/_help_wait.py``) re-runs every time the
agent waits again. Its existing-card branch flipped the row to
``status=blocked`` via ``_save_tasks_unlocked`` with NO stamp clear — so a
``help-<agent>-waiting`` card that reached ``done``+stamped through a
stamping writer (``complete_task`` stamps ``_log_meta.completed_{at,by}``)
kept ``completed_at`` on a live ``blocked`` row after the next wait: the
phantom-delivery row ``fleet/timing.py`` + ``timeline.py`` aggregate on
``completed_at`` alone (never ``status``), counting it as delivered work
forever WHILE it nags its owner as backlog. Same honesty class as the
board ``/reopen``, ``/restore``, ``/archive`` and upsert stamp clears —
the last recorded follow-up of the Undo-family bound.

The fix calls the named ``_clear_completion_stamp_on_leaving_done`` clock
after the flip, before the save — never two inline pops. A refresh of an
already-``blocked`` card is not a transition, so it is untouched.

These pins NEVER drive ``help_wait`` end-to-end: past the read the path
takes the store lock, and a bogus file store path does NOT isolate (a
prior probe mirrored to the live board). Source-order pins + the pure
clock only, in the established ``*_pending_abolished`` style. The
store-backed round-trip twin (wait -> complete -> re-wait unstamps)
belongs in ``test__help_wait.py`` for CI-with-PG.

Deliberately NOT fixed here (different class, recorded): ``help_clear``
entering ``done`` does not stamp ``completed_at`` — mixed convention
(the board Resolve button and ``update_task``→``done`` do not stamp
either; only ``complete_task`` does), so a pure wait/clear cycle never
mints a stamp in the first place.
"""

from __future__ import annotations

import inspect

from scitex_cards import _help_wait
from scitex_cards._model import VALID_STATUSES
from scitex_cards._store_clocks import _clear_completion_stamp_on_leaving_done
from scitex_cards._store_lifecycle import COMPLETED_STATUS, COMPLETION_STAMP_KEYS


def _stamped_done() -> dict:
    # Arrange helper, not a test: a done+stamped row shaped like the
    # `complete_task` output a re-wait replays onto `blocked`.
    return {
        "id": "help-alice-waiting",
        "status": "done",
        "title": "[help] alice waiting on operator decision",
        "_log_meta": {"completed_at": "2026-01-01T00:00:00Z", "completed_by": "alice"},
    }


def test_help_wait_source_captures_prior_status_before_the_flip():
    # Arrange
    src = inspect.getsource(_help_wait.help_wait)
    # Act: the clock keys on the transition, so the prior status must be
    # captured before the assignment overwrites the evidence.
    # Assert
    assert 'prior_status = existing.get("status")' in src
    assert src.index('prior_status = existing.get("status")') < src.index(
        'existing["status"] = "blocked"'
    )


def test_help_wait_source_calls_the_named_clock():
    # Arrange
    src = inspect.getsource(_help_wait.help_wait)
    # Act + Assert: the named clock, not two inline pops (the helper's
    # docstring is the contract the next transition reuses).
    assert "_clear_completion_stamp_on_leaving_done(existing, prior_status)" in src
    assert '"completed_at"' not in src
    assert "'completed_at'" not in src


def test_help_wait_source_imports_the_clock_from_store_clocks():
    # Arrange
    src = inspect.getsource(_help_wait.help_wait)
    # Act + Assert: the same clock `update_task` uses, not a divergent copy.
    assert "from ._store_clocks import _clear_completion_stamp_on_leaving_done" in src


def test_help_wait_clear_runs_after_the_flip_before_the_save():
    # Arrange
    src = inspect.getsource(_help_wait.help_wait)
    # Act: the clear must see the NEW status (lands after the flip) and
    # must land before the row is persisted, or a crash between write and
    # clear mints the phantom-delivery row.
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(existing, prior_status)")
    # Assert
    assert clear_at > src.index('existing["status"] = "blocked"')
    assert clear_at < src.index("_save_tasks_unlocked(")


def test_help_wait_doc_records_the_clear():
    # Arrange
    doc = _help_wait.help_wait.__doc__ or ""
    # Act + Assert
    assert "_clear_completion_stamp_on_leaving_done" in doc
    assert "completed_at" in doc


def test_help_wait_status_lands_inside_the_live_enum():
    # Arrange + Act: the re-wait target must be a live status, never an
    # abolished one, or the flip itself mints a forbidden row.
    # Assert
    assert "blocked" in VALID_STATUSES
    assert COMPLETED_STATUS == "done"
    assert COMPLETED_STATUS in VALID_STATUSES


def test_clock_clears_a_done_stamped_rewait():
    # Arrange: the row AFTER the flip (blocked) with the prior captured.
    task = _stamped_done()
    task["status"] = "blocked"
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert: both stamp keys gone; the emptied `_log_meta` goes with them.
    assert cleared is True
    assert "completed_at" not in task.get("_log_meta", {})
    assert "completed_by" not in task.get("_log_meta", {})


def test_clock_leaves_a_blocked_refresh_untouched():
    # Arrange: the steady-state re-wait — already blocked, no prior done.
    task = {"id": "help-alice-waiting", "status": "blocked", "title": "t"}
    before = dict(task)
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "blocked")
    # Assert: not a transition, nothing to unstamp, mapping identical.
    assert cleared is False
    assert task == before


def test_clock_keeps_a_genuine_stamp_when_still_done():
    # Arrange: a passing edit on a done row (not an exit) must not unstamp.
    task = _stamped_done()
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is False
    assert task["_log_meta"]["completed_at"] == "2026-01-01T00:00:00Z"


def test_detectors_are_non_vacuous():
    # Arrange + Act + Assert: these anchors tie the suite to the real
    # invariant — if the stamp keys or the done name ever move, the pins
    # fail loudly instead of passing on renamed ghosts.
    assert set(COMPLETION_STAMP_KEYS) == {"completed_at", "completed_by"}
    assert _stamped_done()["status"] == COMPLETED_STATUS


def test_help_clear_deliberately_does_not_stamp():
    # Arrange
    src = inspect.getsource(_help_wait.help_clear)
    # Act + Assert: entering `done` here stamps nothing (mixed convention:
    # only `complete_task` stamps). Locked so a future reader cannot
    # "fix" the asymmetry silently — it is a recorded decision, and the
    # pure wait/clear cycle therefore never mints a stamp.
    assert "completed_at" not in src
    assert "complete_task" not in src


# EOF
