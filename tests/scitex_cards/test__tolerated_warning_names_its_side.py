#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The tolerated-value warning must name the side it actually fired on.

WHY THIS FILE EXISTS, from a measurement rather than a style preference.
``_warn_tolerated`` hard-coded ``TOLERATED (read-side):`` while its only
caller, ``_validate_tasks``, is documented as "the single gate shared by
load_tasks (read side) and save_tasks (write side)". So on a WRITE the one
diagnostic that could have stopped the writer told them somebody else's row
had come past them.

Measured on the live board 2026-08-16: three cards carrying the ABOLISHED
status ``pending`` were CREATED after its abolition, all by the maintainer of
the package that abolished it, and a several-hundred-card sweep set the status
``archived``, which no build has ever known. The warning fired every time and
pointed away from the writer each time.

NOTHING HERE MAKES A WRITE REFUSABLE. Operator ruling 2026-07-10 --
「カードが書けないということはなしで大丈夫です、warning で十分です」-- stands, and
two tests below assert it stands by checking the task list survives unchanged.
"""

import pytest

from scitex_cards._validate import (
    WRITE_SOURCE,
    _side_of,
    _validate_tasks,
    _warn_tolerated,
)

READ_SOURCE = "/some/store.db"


def _one(status="pending", tid="t1"):
    """A minimal valid card carrying ``status``."""
    return [{"id": tid, "title": "a card", "status": status}]


def _stderr_of(capsys, status, source):
    """Validate one card and return what the gate printed to stderr."""
    _validate_tasks(_one(status), source=source)
    return capsys.readouterr().err


# --------------------------------------------------------------------------
# the side resolver
# --------------------------------------------------------------------------


def test_the_write_source_resolves_to_the_write_side():
    # Arrange
    source = WRITE_SOURCE
    # Act
    side = _side_of(source)
    # Assert
    assert side == "write-side"


@pytest.mark.parametrize(
    "source",
    [
        "/home/someone/.scitex/cards.db",
        "postgresql://cards@127.0.0.1:55432/scitex_cards",
        "<load_tasks>",
        "",
    ],
)
def test_every_other_source_resolves_to_the_read_side(source):
    """A store being READ is anything that is not this process's own write.

    The empty string is included deliberately: an unknown provenance is a read,
    because accusing a caller of writing is the error this module exists to stop.
    """
    # Arrange
    resolver = _side_of
    # Act
    side = resolver(source)
    # Assert
    assert side == "read-side"


# --------------------------------------------------------------------------
# the banner
# --------------------------------------------------------------------------


def test_the_banner_carries_the_side_it_was_given(capsys):
    # Arrange
    message = "something odd"
    # Act
    _warn_tolerated(message, "write-side")
    # Assert
    assert "TOLERATED (write-side): something odd" in capsys.readouterr().err


def test_the_banner_defaults_to_read_side_when_no_side_is_passed(capsys):
    """An old caller passing no side is treated as the tolerant read it
    historically was, rather than silently accused of writing."""
    # Arrange
    message = "something odd"
    # Act
    _warn_tolerated(message)
    # Assert
    assert "TOLERATED (read-side): something odd" in capsys.readouterr().err


# --------------------------------------------------------------------------
# the gate, end to end -- this is the pair that was wrong
# --------------------------------------------------------------------------


def test_a_write_of_an_abolished_status_is_labelled_write_side(capsys):
    # Arrange
    source = WRITE_SOURCE
    # Act
    err = _stderr_of(capsys, "pending", source)
    # Assert
    assert "TOLERATED (write-side)" in err


def test_a_write_of_an_abolished_status_is_not_labelled_read_side(capsys):
    """The regression itself: this is the label that was hard-coded."""
    # Arrange
    source = WRITE_SOURCE
    # Act
    err = _stderr_of(capsys, "pending", source)
    # Assert
    assert "TOLERATED (read-side)" not in err


def test_a_write_of_an_abolished_status_still_keeps_the_card(capsys):
    """Operator ruling 2026-07-10: a status value must never cost a card."""
    # Arrange
    tasks = _one("pending")
    # Act
    _validate_tasks(tasks, source=WRITE_SOURCE)
    # Assert
    assert tasks == _one("pending")


def test_a_read_of_an_abolished_status_is_still_labelled_read_side(capsys):
    # Arrange
    source = READ_SOURCE
    # Act
    err = _stderr_of(capsys, "pending", source)
    # Assert
    assert "TOLERATED (read-side)" in err


def test_a_read_of_an_abolished_status_is_not_labelled_write_side(capsys):
    """Control for the pair above: the fix must not invert the mislabel."""
    # Arrange
    source = READ_SOURCE
    # Act
    err = _stderr_of(capsys, "pending", source)
    # Assert
    assert "TOLERATED (write-side)" not in err


# --------------------------------------------------------------------------
# the unknown-status ADVICE, which is false rather than merely unhelpful
# on a write
# --------------------------------------------------------------------------


def test_a_write_of_an_unknown_status_says_the_caller_is_setting_it(capsys):
    """`archived` is the measured instance: several hundred cards took a status
    no build knows, and the read-side sentence is what its author would have
    been shown -- blaming an absent third party for the caller's own value."""
    # Arrange
    source = WRITE_SOURCE
    # Act
    err = _stderr_of(capsys, "archived", source)
    # Assert
    assert "YOU are setting it" in err


def test_a_write_of_an_unknown_status_does_not_blame_another_agent(capsys):
    # Arrange
    source = WRITE_SOURCE
    # Act
    err = _stderr_of(capsys, "archived", source)
    # Assert
    assert "older than the writer" not in err


def test_a_write_of_an_unknown_status_does_not_prescribe_an_upgrade(capsys):
    """An upgrade cannot fix a value this process is choosing right now."""
    # Arrange
    source = WRITE_SOURCE
    # Act
    err = _stderr_of(capsys, "archived", source)
    # Assert
    assert "upgrade rather than rewriting" not in err


def test_a_write_of_an_unknown_status_still_keeps_the_card(capsys):
    # Arrange
    tasks = _one("archived")
    # Act
    _validate_tasks(tasks, source=WRITE_SOURCE)
    # Assert
    assert tasks == _one("archived")


def test_a_read_of_an_unknown_status_keeps_the_version_skew_advice(capsys):
    """The read-side sentence is CORRECT on a read and must not be lost.

    A reader meeting a value their build does not know really may be behind the
    writer, and telling them to rewrite the card would destroy a newer agent's
    work. This is the control proving the write-side branch NARROWED the
    message rather than deleting it.
    """
    # Arrange
    source = READ_SOURCE
    # Act
    err = _stderr_of(capsys, "archived", source)
    # Assert
    assert "older than the writer" in err


def test_a_read_of_an_unknown_status_does_not_accuse_the_reader_of_setting_it(capsys):
    # Arrange
    source = READ_SOURCE
    # Act
    err = _stderr_of(capsys, "archived", source)
    # Assert
    assert "YOU are setting it" not in err


# --------------------------------------------------------------------------
# the other call site, and the anti-drift guard
# --------------------------------------------------------------------------


def test_the_blocked_without_a_blocker_warning_is_sided_too(capsys):
    """The second `_warn_tolerated` call site, so the fix is not half-applied."""
    # Arrange
    tasks = [{"id": "t1", "title": "a card", "status": "blocked"}]
    # Act
    _validate_tasks(tasks, source=WRITE_SOURCE)
    # Assert
    assert "TOLERATED (write-side)" in capsys.readouterr().err


def test_the_writer_passes_the_constant_not_a_literal_copy():
    """Anti-drift: two spellings of one string is how the mislabel comes back.

    The resolver would compare against one and the writer would send the other,
    and every write would quietly report itself as a read again.
    """
    # Arrange
    from pathlib import Path

    import scitex_cards._store_write as sw

    # Act
    src = Path(sw.__file__).read_text(encoding="utf-8")
    # Assert
    assert "source=_WRITE_SOURCE" in src, (
        "_store_write must pass the WRITE_SOURCE constant to _validate_tasks; "
        "a re-inlined literal would drift from _validate._side_of and silently "
        "restore the read-side mislabel."
    )


def test_the_writer_no_longer_inlines_the_write_source_literal():
    # Arrange
    from pathlib import Path

    import scitex_cards._store_write as sw

    # Act
    src = Path(sw.__file__).read_text(encoding="utf-8")
    # Assert
    assert 'source="<save_tasks>"' not in src
