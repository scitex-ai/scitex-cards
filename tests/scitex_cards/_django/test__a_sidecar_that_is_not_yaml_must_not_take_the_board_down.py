#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A groups sidecar that is not a YAML document must not 500 the whole board.

WHY THIS FILE EXISTS. Measured 2026-08-23, on two hosts independently: the
sidecar path ``~/.scitex/cards/tasks.yaml`` held a BINARY DATABASE FILE rather
than YAML -- a phantom written by a caller that handed the store's DISPLAY
LABEL to a database opener. ``yaml.safe_load`` then raised
``UnicodeDecodeError`` on the first high byte of that file's header and the
entire board answered 500::

    compute-04      GET /tasks -> 500 "... can't decode byte 0x89 in position 99"
    ywata-note-win  GET /tasks -> 500 "... can't decode byte 0xf8 in position 102"

(The byte and offset differed between the two hosts because they wrote the file
with different builds of the same engine. The offset is an accident of one
build; the PROPERTY -- "these bytes are not a UTF-8 document" -- is what this
file pins, which is why the fixture below is calibrated rather than asserted.)

The cards themselves were never at risk -- they live in the database and were
read successfully. What took the board down was an OPTIONAL VIEWER CONCERN
whose own docstring already calls it "THE ONLY THING IN THE BOARD THAT MAY
LEGITIMATELY DEGRADE TO EMPTY". The existence test was the right INTENT and the
wrong PROPERTY: ``exists()`` answers "is there a file", and what this read
requires is "is there a YAML document".

WHAT IS DELIBERATELY *NOT* ABSORBED. A ``TaskValidationError`` from a real YAML
file with a malformed ``groups:`` block still propagates. That is an authoring
mistake with a fixable location, and swallowing it would hide a defect its
author can act on -- a different failure wearing the same clothes.
"""

import logging

import pytest

from scitex_cards._django.services import _load_sidecar_groups
from scitex_cards._task import TaskValidationError

VALID_SIDECAR = (
    "groups:\n"
    "  - id: collab\n"
    "    label: 'Collab'\n"
    "    projects: [a, b]\n"
    "tasks: []\n"
)


#: A binary file header followed by text -- the SHAPE of the phantom that took
#: the board down: printable ASCII, then a high byte that is not valid UTF-8.
#: The engine that wrote the original phantom is abolished, so the bytes are
#: written directly now. That is not a downgrade from "real bytes": the
#: incident's own two hosts disagreed about the exact offset, so the offset was
#: never the property. What matters is that the file DECODES AS NOTHING, and
#: :func:`test_the_fixture_really_is_undecodable_as_utf8` measures precisely
#: that rather than trusting this constant.
PHANTOM_BYTES = b"format 3\x00" + bytes(range(0x80, 0x90)) + b"\x00inbox\x00n_1\x00"


@pytest.fixture
def phantom(tmp_path):
    """A real, non-UTF-8 binary file at the sidecar path -- the measured condition."""
    path = tmp_path / "tasks.yaml"
    path.write_bytes(PHANTOM_BYTES)
    return path


def test_the_fixture_really_is_undecodable_as_utf8(phantom):
    # Arrange — CALIBRATION. Without this, every test below could pass because
    # the fixture is benign rather than because the fix works.
    raised = None
    # Act
    try:
        phantom.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raised = exc
    # Assert
    assert raised is not None


def test_a_binary_sidecar_yields_no_groups_instead_of_raising(phantom):
    # Arrange
    expected = []
    # Act
    groups = _load_sidecar_groups(phantom, task_ids=set())
    # Assert
    assert groups == expected


def test_the_degradation_is_recorded_at_error_level(phantom, caplog):
    # Arrange — a RECORD, not a gate: nothing branches on it. It exists so the
    # journal names the path during an incident, because the behaviour this
    # replaces was loud and the fix must not buy quiet at the cost of silence.
    caplog.set_level(logging.ERROR, logger="scitex_cards._django.services")
    # Act
    _load_sidecar_groups(phantom, task_ids=set())
    # Assert
    assert [r for r in caplog.records if r.levelno == logging.ERROR]


def test_the_record_names_the_offending_path(phantom, caplog):
    # Arrange — "something went wrong" is not actionable; the path is.
    caplog.set_level(logging.ERROR, logger="scitex_cards._django.services")
    # Act
    _load_sidecar_groups(phantom, task_ids=set())
    # Assert
    assert str(phantom) in caplog.text


def test_the_record_says_to_move_the_file_aside_not_delete_it(phantom, caplog):
    # Arrange — the file may hold undelivered inbox rows, so the remedy the
    # operator reads must not be "rm". 404 such rows existed across two hosts.
    caplog.set_level(logging.ERROR, logger="scitex_cards._django.services")
    # Act
    _load_sidecar_groups(phantom, task_ids=set())
    # Assert
    assert "never deleted" in caplog.text


def test_an_absent_sidecar_still_yields_no_groups(tmp_path):
    # Arrange — the pre-existing documented path must survive the change.
    missing = tmp_path / "tasks.yaml"
    # Act
    groups = _load_sidecar_groups(missing, task_ids=set())
    # Assert
    assert groups == []


def test_a_valid_sidecar_still_yields_its_groups(tmp_path):
    # Arrange — THE OVER-REACH CONTROL. A try/except wide enough to swallow a
    # real parse is also wide enough to swallow a real answer; this goes red if
    # the degradation ever starts firing on a healthy file.
    path = tmp_path / "tasks.yaml"
    path.write_text(VALID_SIDECAR, encoding="utf-8")
    # Act
    groups = _load_sidecar_groups(path, task_ids=set())
    # Assert
    assert len(groups) == 1


def test_a_malformed_groups_block_still_raises(tmp_path):
    # Arrange — THE NARROWNESS CONTROL. Real YAML, broken schema (no id). This
    # is an authoring error with a fixable line, not "the file is not YAML",
    # and absorbing it would hide something its author can act on.
    path = tmp_path / "tasks.yaml"
    path.write_text("groups:\n  - label: 'no id here'\ntasks: []\n", encoding="utf-8")
    raised = None
    # Act
    try:
        _load_sidecar_groups(path, task_ids=set())
    except TaskValidationError as exc:
        raised = exc
    # Assert
    assert raised is not None
