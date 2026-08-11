#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The null-payload refusal must not diagnose a cause it cannot observe.

WHY THIS FILE EXISTS. The message said:

    "... has no record_json payload — this DB predates schema v3's payload
     columns and cannot be back-filled (the importer was removed with the YAML
     tier); use a database written by a current version."

On 2026-08-11 both clauses were false. The offending rows were SECONDS old,
written by the current version (`_inbox_postgres.enqueue` omitted the column),
and they back-filled trivially from their own columns. dotfiles reported losing
time to it — hunting a migration that does not exist, on a database that was
current — before a channel event revealed the row's true age.

An error that names the WRONG cause is worse than one that names none, because
it is actionable in the wrong direction. The age of the database is not
observable at this call site and must not be claimed.

These tests pin the message's CONTENT, which is unusual and deliberate: the
message IS the interface here. A reader's next action is determined entirely by
what it says, so a regression in its wording is a regression in behaviour.
"""

from functools import partial

import pytest

from scitex_cards._db_export import ExportRefused, _record


class _NullPayloadRow(dict):
    """A real row mapping whose payload is NULL — no mock, just a dict."""

    def __init__(self):
        super().__init__(id="n_af93d2f0b8c4", record_json=None, seen=0)


def _refusal_text() -> str:
    with pytest.raises(ExportRefused) as excinfo:
        _record(_NullPayloadRow(), "notifications")
    return str(excinfo.value)


def test_the_refusal_still_fires_on_a_null_payload():
    """POSITIVE CONTROL. Every assertion below reads the refusal's text; if the
    guard stopped raising, they would have nothing to inspect and the file would
    silently guard nothing. The guard itself is correct and must survive."""
    # Arrange
    row = _NullPayloadRow()
    raised = partial(_record, row, "notifications")
    # Act
    guard = pytest.raises(ExportRefused)
    # Assert
    with guard:
        raised()


def test_the_refusal_names_the_offending_row():
    """The id is what makes the refusal actionable — it is the value a repair
    or an investigation starts from."""
    # Arrange
    expected_id = "n_af93d2f0b8c4"
    # Act
    text = _refusal_text()
    # Assert
    assert expected_id in text


def test_the_refusal_does_not_claim_the_database_is_old():
    """THE FIX. Database age is not observable here, and asserting it sent three
    agents after a migration that could not be performed."""
    # Arrange
    forbidden = "predates"
    # Act
    text = _refusal_text()
    # Assert
    assert forbidden not in text


def test_the_refusal_does_not_prescribe_an_impossible_action():
    """"use a database written by a current version" was advice the reader could
    not act on: the database WAS current. A next step that cannot be taken is
    worse than no next step."""
    # Arrange
    forbidden = "written by a current version"
    # Act
    text = _refusal_text()
    # Assert
    assert forbidden not in text


def test_the_refusal_points_at_the_write_path():
    """dotfiles' formulation, adopted: name the writer, because that points at
    the bug instead of away from it."""
    # Arrange
    expected = "WRITER"
    # Act
    text = _refusal_text()
    # Assert
    assert expected in text


def test_the_refusal_warns_against_discarding_the_row():
    """Of the six null rows seen on 2026-08-11, FOUR were undelivered DMs.
    "Quarantine the bad row to unblock the write" is the obvious remedy and it
    destroys messages — the same family as the ack-on-read incident that lost
    five operator DMs on 2026-07-29. The message must say so at the moment
    someone is deciding what to do."""
    # Arrange
    expected = "quarantine"
    # Act
    text = _refusal_text().lower()
    # Assert
    assert expected in text


def test_the_refusal_still_explains_why_it_refuses_rather_than_degrades():
    """The original message's one correct sentence. Refusing beats serving a
    stripped record, and removing that rationale would invite someone to
    "fix" the guard by making it lenient."""
    # Arrange
    expected = "worse than exporting none"
    # Act
    text = _refusal_text()
    # Assert
    assert expected in text


# EOF
