#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clearing a CLOSED-ENUM field with `""` deletes the key (does not write "").

The live failure this pins (4-card triage run, 2026-07-12): every write
surface documents *"pass an empty string to CLEAR a field"*, but on the
closed-enum fields a `""` was written LITERALLY and the validator then
rejected the save::

    TaskValidationError: task '...' has invalid blocker '';
    must be one of ('compute','dependency','dep','operator-decision',
    'agent-wait','none') or absent

— so the DOCUMENTED way to clear a blocker was the one way that could not
work, and it failed at SAVE time, aborting the whole bulk batch having
applied nothing.

Real round-trips against a `tmp_path` YAML store — no mocks of the thing
under test (Req STX-NM).
"""

from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from scitex_cards import _model, _store
from scitex_cards._cli import main


def _blocked_card(store, *, task_id="triage-1", blocker="operator-decision"):
    """Insert a real blocked card carrying a blocker."""
    return _store.add_task(
        store,
        id=task_id,
        title="A blocked card",
        status="blocked",
        blocker=blocker,
        assignee="agent:test-suite",
    )


def _reload(store, task_id):
    """Re-read the task FROM DISK — the key question is what got persisted."""
    tasks = _model.load_tasks(store)
    return next(t for t in tasks if t["id"] == task_id)


# --------------------------------------------------------------------------- #
# blocker: "" CLEARS (the reported bug)                                       #
# --------------------------------------------------------------------------- #
def test_a_freshly_inserted_blocked_card_names_its_gate(tmp_path):
    # Arrange — a blocked card that names its gate.
    store = tmp_path / "tasks.yaml"

    # Act
    _blocked_card(store)

    # Assert — the premise for every clear test below.
    assert _reload(store, "triage-1")["blocker"] == "operator-decision"


def test_blocker_empty_string_deletes_the_key_from_the_merged_card(tmp_path):
    # Arrange — a blocked card that names its gate.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store)

    # Act — the DOCUMENTED clear. Must not raise (it used to, at save time).
    merged = _store.update_task(store, "triage-1", status="in_progress", blocker="")

    # Assert — the key is ABSENT, not "" and not the "none" workaround.
    assert "blocker" not in merged


def test_blocker_empty_string_deletes_the_key_from_the_persisted_card(tmp_path):
    # Arrange — a blocked card that names its gate.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store)

    # Act
    _store.update_task(store, "triage-1", status="in_progress", blocker="")

    # Assert — the clear survived the round-trip to disk.
    assert "blocker" not in _reload(store, "triage-1")


def test_clearing_a_blocker_still_applies_the_sibling_status(tmp_path):
    # Arrange — a blocked card that names its gate.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store)

    # Act
    _store.update_task(store, "triage-1", status="in_progress", blocker="")

    # Assert — the save succeeded as a whole, not just the delete half.
    assert _reload(store, "triage-1")["status"] == "in_progress"


def test_blocker_empty_string_never_reaches_disk_as_a_value(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-raw")

    # Act
    _store.update_task(store, "triage-raw", status="deferred", blocker="")

    # Assert — read the RAW YAML: no `blocker: ''` key survived the write.
    raw = yaml.safe_load(store.read_text())
    card = next(t for t in raw["tasks"] if t["id"] == "triage-raw")
    assert "blocker" not in card


def test_blocker_whitespace_only_is_also_a_clear(tmp_path):
    # Arrange — "  " is a typo'd "", never a legal enum member.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-ws")

    # Act
    merged = _store.update_task(store, "triage-ws", status="in_progress", blocker="   ")

    # Assert
    assert "blocker" not in merged


# --------------------------------------------------------------------------- #
# The validator is NOT weakened                                               #
# --------------------------------------------------------------------------- #
def test_genuinely_invalid_blocker_still_raises(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-bad")

    # Act
    # Assert — "" is a delete instruction; "banana" is a bad VALUE.
    with pytest.raises(_model.TaskValidationError):
        _store.update_task(store, "triage-bad", blocker="banana")


def _refusal_message(fn) -> str:
    """Run ``fn`` and return the validation error's text ("" if it did not raise).

    Used where THAT the call raises is already pinned by its own test, and this
    test asks only what the refusal SAYS — so the assertion budget goes to the
    message, not to a second `pytest.raises`.
    """
    try:
        fn()
    except _model.TaskValidationError as exc:
        return str(exc)
    return ""


def test_an_invalid_blocker_refusal_names_the_offending_value(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-bad")

    # Act
    message = _refusal_message(
        lambda: _store.update_task(store, "triage-bad", blocker="banana")
    )

    # Assert
    assert "banana" in message


def test_an_invalid_blocker_refusal_leaves_the_store_untouched(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-bad")

    # Act
    _refusal_message(lambda: _store.update_task(store, "triage-bad", blocker="banana"))

    # Assert — the rejected write did not half-apply. (Had it been accepted,
    # the blocker would read "banana" here.)
    assert _reload(store, "triage-bad")["blocker"] == "operator-decision"


# --------------------------------------------------------------------------- #
# The done-while-blocked guard still fires (regression: do NOT weaken)        #
# --------------------------------------------------------------------------- #
def test_done_while_blocker_still_set_is_still_refused(tmp_path):
    # Arrange — a blocked card with a real blocker.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-done")

    # Act
    # Assert — flipping to done WITHOUT clearing the blocker is incoherent
    # (done-but-blocked) and must still be rejected.
    with pytest.raises(_model.TaskValidationError):
        _store.update_task(store, "triage-done", status="done")


def test_the_done_while_blocked_refusal_names_the_blocker(tmp_path):
    # Arrange — a blocked card with a real blocker.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-done")

    # Act
    message = _refusal_message(
        lambda: _store.update_task(store, "triage-done", status="done")
    )

    # Assert
    assert "blocker" in message


def test_done_with_the_gate_cleared_in_the_same_call_applies_the_status(tmp_path):
    # Arrange — a blocked card with a real blocker.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-done")

    # Act — the coherent form: clear the gate in the SAME call.
    merged = _store.update_task(store, "triage-done", status="done", blocker="")

    # Assert
    assert merged["status"] == "done"


def test_done_with_the_gate_cleared_in_the_same_call_drops_the_blocker(tmp_path):
    # Arrange — a blocked card with a real blocker.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-done")

    # Act — the coherent form: clear the gate in the SAME call.
    merged = _store.update_task(store, "triage-done", status="done", blocker="")

    # Assert
    assert "blocker" not in merged


# --------------------------------------------------------------------------- #
# status is NOT clearable — a card must carry a decision                      #
# --------------------------------------------------------------------------- #
def test_status_cannot_be_cleared_and_says_why(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-status")

    # Act
    # Assert — refused LOUDLY, not silently ignored.
    with pytest.raises(_model.TaskValidationError):
        _store.update_task(store, "triage-status", status="")


def test_the_status_clear_refusal_says_it_cannot_clear(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-status")

    # Act
    message = _refusal_message(
        lambda: _store.update_task(store, "triage-status", status="")
    )

    # Assert — the message names the reason.
    assert "cannot clear" in message


def test_the_status_clear_refusal_offers_the_valid_set(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-status")

    # Act
    message = _refusal_message(
        lambda: _store.update_task(store, "triage-status", status="")
    )

    # Assert — the caller is told what to pick from instead.
    assert "in_progress" in message and "deferred" in message


def test_a_refused_status_clear_leaves_the_cards_status_intact(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-status")

    # Act
    _refusal_message(lambda: _store.update_task(store, "triage-status", status=""))

    # Assert — the card is untouched; its status survives the refusal.
    assert _reload(store, "triage-status")["status"] == "blocked"


def test_status_clear_is_refused_before_the_store_is_touched(tmp_path):
    # Arrange — the refusal must happen BEFORE the lock/write, so a doomed
    # mutation never partially applies alongside its sibling fields.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-early")

    # Act
    # Assert
    with pytest.raises(_model.TaskValidationError):
        _store.update_task(store, "triage-early", status="", note="new note")


def test_a_refused_status_clear_does_not_land_its_sibling_field(tmp_path):
    # Arrange — the refusal must happen BEFORE the lock/write.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-early")

    # Act
    _refusal_message(
        lambda: _store.update_task(store, "triage-early", status="", note="new note")
    )

    # Assert — the sibling field did NOT land.
    assert "note" not in _reload(store, "triage-early")


# --------------------------------------------------------------------------- #
# kind: "" CLEARS (absent kind == the "task" default)                         #
# --------------------------------------------------------------------------- #
def _misfiled_kind_card(store, task_id: str) -> None:
    """A card mis-filed as kind=status."""
    _store.add_task(
        store,
        id=task_id,
        title="Mis-filed card",
        status="in_progress",
        kind="status",
        assignee="agent:test-suite",
    )


def test_kind_empty_string_deletes_the_key_from_the_merged_card(tmp_path):
    # Arrange — a card mis-filed as kind=status.
    store = tmp_path / "tasks.yaml"
    _misfiled_kind_card(store, "kind-1")

    # Act — put it back to the default (absent kind == "task").
    merged = _store.update_task(store, "kind-1", kind="")

    # Assert
    assert "kind" not in merged


def test_kind_empty_string_deletes_the_key_from_the_persisted_card(tmp_path):
    # Arrange — a card mis-filed as kind=status.
    store = tmp_path / "tasks.yaml"
    _misfiled_kind_card(store, "kind-1")

    # Act — put it back to the default (absent kind == "task").
    _store.update_task(store, "kind-1", kind="")

    # Assert
    assert "kind" not in _reload(store, "kind-1")


# --------------------------------------------------------------------------- #
# add_task (the sibling write path) honours the same ONE rule                 #
# --------------------------------------------------------------------------- #
def test_add_task_empty_enum_is_not_written(tmp_path):
    # Arrange — "" on insert = "no value", never a literal "".
    store = tmp_path / "tasks.yaml"

    # Act
    inserted = _store.add_task(
        store,
        id="add-1",
        title="Fresh card",
        status="deferred",
        blocker="",
        kind="",
        assignee="agent:test-suite",
    )

    # Assert
    assert "blocker" not in inserted and "kind" not in inserted


def test_add_task_refuses_a_status_less_card(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    # Assert — a card cannot be BORN status-less either.
    with pytest.raises(_model.TaskValidationError):
        _store.add_task(
            store,
            id="add-2",
            title="Statusless",
            status="",
            assignee="agent:test-suite",
        )


# --------------------------------------------------------------------------- #
# The batch case that broke live                                              #
# --------------------------------------------------------------------------- #
def _run_the_bulk_triage_batch(store) -> None:
    """The shape of the live 4-card triage script: a run of updates where ONE
    clears a blocker. That one used to raise at save time and abort the whole
    batch, applying NOTHING. The clear sits in the MIDDLE of the batch.
    """
    for n in range(4):
        _blocked_card(store, task_id=f"bulk-{n}")
    _store.update_task(store, "bulk-0", status="in_progress", blocker="")
    _store.update_task(store, "bulk-1", status="deferred", blocker="")
    _store.update_task(store, "bulk-2", blocker="agent-wait")
    _store.update_task(store, "bulk-3", status="done", blocker="")


def test_bulk_triage_clears_the_first_cards_blocker(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _run_the_bulk_triage_batch(store)

    # Assert
    assert "blocker" not in _reload(store, "bulk-0")


def test_bulk_triage_applies_the_first_cards_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _run_the_bulk_triage_batch(store)

    # Assert
    assert _reload(store, "bulk-0")["status"] == "in_progress"


def test_bulk_triage_clears_the_second_cards_blocker(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _run_the_bulk_triage_batch(store)

    # Assert
    assert "blocker" not in _reload(store, "bulk-1")


def test_bulk_triage_still_applies_a_real_blocker_value(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _run_the_bulk_triage_batch(store)

    # Assert — a clear in the batch must not disturb a sibling's real value.
    assert _reload(store, "bulk-2")["blocker"] == "agent-wait"


def test_bulk_triage_applies_the_last_cards_status(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _run_the_bulk_triage_batch(store)

    # Assert — the batch did not abort part-way.
    assert _reload(store, "bulk-3")["status"] == "done"


def test_bulk_triage_clears_the_last_cards_blocker(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _run_the_bulk_triage_batch(store)

    # Assert
    assert "blocker" not in _reload(store, "bulk-3")


# --------------------------------------------------------------------------- #
# End-to-end at the CLI surface (the contract is documented THERE too)        #
# --------------------------------------------------------------------------- #
def _cli_clear_blocker(store):
    _blocked_card(store, task_id="cli-1")
    return CliRunner().invoke(
        main,
        [
            "update",
            "cli-1",
            "--status",
            "in_progress",
            "--blocker",
            "",
            "--tasks",
            str(store),
        ],
    )


def test_cli_update_with_an_empty_blocker_exits_clean(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    result = _cli_clear_blocker(store)

    # Assert
    assert result.exit_code == 0, result.output


def test_cli_update_blocker_empty_string_clears(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _cli_clear_blocker(store)

    # Assert
    assert "blocker" not in _reload(store, "cli-1")


def _cli_clear_kind(store):
    # Previously the strict Choice rejected '' at parse time, so there was no
    # CLI form for this at all.
    _misfiled_kind_card(store, "cli-2")
    return CliRunner().invoke(
        main,
        ["update", "cli-2", "--kind", "", "--tasks", str(store)],
    )


def test_cli_update_with_an_empty_kind_exits_clean(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    result = _cli_clear_kind(store)

    # Assert
    assert result.exit_code == 0, result.output


def test_cli_update_kind_empty_string_clears(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"

    # Act
    _cli_clear_kind(store)

    # Assert
    assert "kind" not in _reload(store, "cli-2")


# EOF
