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


def test_help_wait_source_captures_prior_status_before_the_flip_case1():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_wait)
    # Assert
    assert 'prior_status = existing.get("status")' in src


def test_help_wait_source_captures_prior_status_before_the_flip_case2():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_wait)
    # Assert
    assert src.index('prior_status = existing.get("status")') < src.index(
        'existing["status"] = "blocked"'
    )


def test_help_wait_source_calls_the_named_clock_case1():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_wait)
    # Assert
    assert "_clear_completion_stamp_on_leaving_done(existing, prior_status)" in src


def test_help_wait_source_calls_the_named_clock_case2():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_wait)
    # Assert
    assert '"completed_at"' not in src


def test_help_wait_source_calls_the_named_clock_case3():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_wait)
    # Assert
    assert "'completed_at'" not in src


def test_help_wait_source_imports_the_clock_from_store_clocks():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_wait)
    # Assert
    assert "from ._store_clocks import _clear_completion_stamp_on_leaving_done" in src


def test_help_wait_clear_runs_after_the_flip_before_the_save_case1():
    # Arrange
    # Arrange
    src = inspect.getsource(_help_wait.help_wait)
    # Act
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(existing, prior_status)")
    # Assert
    assert clear_at > src.index('existing["status"] = "blocked"')


def test_help_wait_clear_runs_after_the_flip_before_the_save_case2():
    # Arrange
    # Arrange
    src = inspect.getsource(_help_wait.help_wait)
    # Act
    clear_at = src.index("_clear_completion_stamp_on_leaving_done(existing, prior_status)")
    # Assert
    assert clear_at < src.index("_save_tasks_unlocked(")


def test_help_wait_doc_records_the_clear_case1():
    # Arrange
    # Arrange
    # Act
    doc = _help_wait.help_wait.__doc__ or ""
    # Assert
    assert "_clear_completion_stamp_on_leaving_done" in doc


def test_help_wait_doc_records_the_clear_case2():
    # Arrange
    # Arrange
    # Act
    doc = _help_wait.help_wait.__doc__ or ""
    # Assert
    assert "completed_at" in doc


def test_help_wait_status_lands_inside_the_live_enum_case1():
    # Arrange + Act: the re-wait target must be a live status, never an
    # abolished one, or the flip itself mints a forbidden row.
    # Arrange
    # Act
    # Assert
    assert "blocked" in VALID_STATUSES


def test_help_wait_status_lands_inside_the_live_enum_case2():
    # Arrange + Act: the re-wait target must be a live status, never an
    # abolished one, or the flip itself mints a forbidden row.
    # Arrange
    # Act
    # Assert
    assert COMPLETED_STATUS == "done"


def test_help_wait_status_lands_inside_the_live_enum_case3():
    # Arrange + Act: the re-wait target must be a live status, never an
    # abolished one, or the flip itself mints a forbidden row.
    # Arrange
    # Act
    # Assert
    assert COMPLETED_STATUS in VALID_STATUSES


def test_clock_clears_a_done_stamped_rewait_case1():
    # Arrange: the row AFTER the flip (blocked) with the prior captured.
    # Arrange
    task = _stamped_done()
    task["status"] = "blocked"
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is True


def test_clock_clears_a_done_stamped_rewait_case2():
    # Arrange: the row AFTER the flip (blocked) with the prior captured.
    # Arrange
    task = _stamped_done()
    # Act
    task["status"] = "blocked"
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    assert "completed_at" not in task.get("_log_meta", {})


def test_clock_clears_a_done_stamped_rewait_case3():
    # Arrange: the row AFTER the flip (blocked) with the prior captured.
    # Arrange
    task = _stamped_done()
    # Act
    task["status"] = "blocked"
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    assert "completed_by" not in task.get("_log_meta", {})


def test_clock_leaves_a_blocked_refresh_untouched_case1():
    # Arrange: the steady-state re-wait — already blocked, no prior done.
    # Arrange
    task = {"id": "help-alice-waiting", "status": "blocked", "title": "t"}
    # Act
    before = dict(task)
    cleared = _clear_completion_stamp_on_leaving_done(task, "blocked")
    # Assert
    assert cleared is False


def test_clock_leaves_a_blocked_refresh_untouched_case2():
    # Arrange: the steady-state re-wait — already blocked, no prior done.
    # Arrange
    task = {"id": "help-alice-waiting", "status": "blocked", "title": "t"}
    # Act
    before = dict(task)
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, "blocked")
    assert task == before


def test_clock_keeps_a_genuine_stamp_when_still_done_case1():
    # Arrange: a passing edit on a done row (not an exit) must not unstamp.
    # Arrange
    task = _stamped_done()
    # Act
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    # Assert
    assert cleared is False


def test_clock_keeps_a_genuine_stamp_when_still_done_case2():
    # Arrange: a passing edit on a done row (not an exit) must not unstamp.
    # Arrange
    # Act
    task = _stamped_done()
    # Assert
    cleared = _clear_completion_stamp_on_leaving_done(task, "done")
    assert task["_log_meta"]["completed_at"] == "2026-01-01T00:00:00Z"


def test_detectors_are_non_vacuous_case1():
    # Arrange + Act + Assert: these anchors tie the suite to the real
    # invariant — if the stamp keys or the done name ever move, the pins
    # fail loudly instead of passing on renamed ghosts.
    # Arrange
    # Act
    # Assert
    assert set(COMPLETION_STAMP_KEYS) == {"completed_at", "completed_by"}


def test_detectors_are_non_vacuous_case2():
    # Arrange + Act + Assert: these anchors tie the suite to the real
    # invariant — if the stamp keys or the done name ever move, the pins
    # fail loudly instead of passing on renamed ghosts.
    # Arrange
    # Act
    # Assert
    assert _stamped_done()["status"] == COMPLETED_STATUS


def test_help_clear_deliberately_does_not_stamp_case1():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_clear)
    # Assert
    assert "completed_at" not in src


def test_help_clear_deliberately_does_not_stamp_case2():
    # Arrange
    # Arrange
    # Act
    src = inspect.getsource(_help_wait.help_clear)
    # Assert
    assert "complete_task" not in src


# EOF
